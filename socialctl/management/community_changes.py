"""Durable exact approval, per-target moderation observations and no blind retry."""
import hmac
import json
import uuid
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from socialctl.management.changes import ApprovalMismatch, now_utc
from socialctl.management.community_schema import CommentTarget, edit_raw, opaque_id, validate_edit
from socialctl.management.resource_changes import ResourceStore, fingerprint, _event
from socialctl.management.youtube_community import complete, FILTERS
from socialctl.management.youtube_resources import ResourceError, ResourceConflict, ResourceRejected, ResourceUncertain, resource_id


class CommunityChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    kind: Literal["youtube-community"] = "youtube-community"
    id: str
    target_brand: str
    brand_root: str
    target_account: str
    edit: dict
    before: dict
    after: dict
    effects: list[str]
    fingerprint: str = ""
    created_at: datetime
    updated_at: datetime
    status: Literal["proposed", "applying", "uncertain", "failed", "conflict", "verified", "partial", "accepted_unverifiable"] = "proposed"
    verified: bool = False
    result_id: str | None = None
    receipt: dict | None = None
    journal: list[dict] = Field(default_factory=list)


class CommunityStore(ResourceStore):
    model_type = CommunityChange
    fingerprint_for = staticmethod(fingerprint)

    def __init__(self, brand_root):
        self.root = Path(brand_root) / ".socialctl" / "youtube-community"


def binding(edit):
    return {k: getattr(edit, k) for k in ("video_id", "thread_id", "comment_id", "parent_id") if getattr(edit, k) is not None}


def author(row):
    value = row["snippet"].get("authorChannelId")
    if not isinstance(value, dict) or not isinstance(value.get("value"), str) or not value["value"]:
        raise ResourceError("comment author unverifiable")
    return resource_id(value["value"])


def baseline(client, edit):
    action = edit.action
    if action in {"add", "reply"}:
        video = client.inspect(edit.video_id)
        before = {"video_id": edit.video_id, "owner": video.channel_id}
        if action == "reply":
            thread = client.thread(edit.video_id, edit.thread_id)
            if thread["snippet"]["topLevelComment"]["id"] != edit.parent_id or thread["snippet"].get("canReply") is not True:
                raise ResourceError("parent mismatch or thread does not accept replies")
            before["parent"] = thread["snippet"]["topLevelComment"]
            report = client.list_replies(edit.video_id, edit.thread_id, edit.parent_id)
            if not report['complete']:
                # Only the missing-envelope case permits preparation. Invalid identities,
                # contradictory metadata and uncertain pagination still block a write.
                if not str(report.get('error', '')).startswith('pagination metadata missing'):
                    complete(report)
                before['reply_coverage'] = {'complete':False, 'absence_proven':False,
                    'limitation':'Reply enumeration lacks metadata; existing remote duplicates cannot be ruled out.'}
            rows = report['items']
        else:
            rows = []
            for state in FILTERS:
                rows.extend(t["snippet"]["topLevelComment"] for t in complete(client.list_threads(edit.video_id, moderation_status=state)))
        if any(r["snippet"].get("authorChannelId", {}).get("value") == client.configured_channel_id
            and r["snippet"].get("textOriginal") == edit.text for r in rows):
            raise ResourceConflict("owned comment with exact text already exists at target; do not duplicate")
        return before
    if action in {"edit", "delete"}:
        row = client.find_comment(**binding(edit))
        if author(row) != client.configured_channel_id:
            raise ResourceError("only original author can edit/delete; another author requires explicit moderation")
        if action == "edit" and not isinstance(row["snippet"].get("textOriginal"), str):
            raise ResourceError("author textOriginal missing; textDisplay is not original text")
        return {"comment": row}
    if action == "moderate":
        rows = []
        cache = {}
        for target in edit.targets:
            row = client.find_comment(**target.model_dump(exclude_none=True), moderation=True, cache=cache)
            author(row)
            previous = row["snippet"].get("moderationStatus")
            if previous not in {"heldForReview", "published", "likelySpam", "rejected"}:
                raise ResourceError("previous moderation status unverifiable")
            if previous == "rejected" and edit.moderation_status != "rejected":
                raise ResourceError("transition from rejected unsupported; restoration is not promised")
            if edit.moderation_status == "heldForReview" and previous in {"published", "rejected"}:
                raise ResourceError("transition to heldForReview unsupported from published/rejected")
            rows.append({"target": target.model_dump(exclude_none=True), "comment": row})
        return {"targets": rows}
    if action in {"subscribe", "unsubscribe"}:
        target = client.external("channels", edit.channel_id)
        if edit.channel_id == client.configured_channel_id:
            raise ResourceError("YouTube does not allow subscribing to your own channel")
        rows = [r for r in complete(client.list_subscriptions()) if r["snippet"]["resourceId"]["channelId"] == edit.channel_id]
        if action == "subscribe" and rows:
            raise ResourceConflict("subscription already exists")
        if action == "unsubscribe" and (len(rows) != 1 or rows[0]["id"] != edit.subscription_id):
            raise ResourceError("subscription_id does not match actor and target")
        return {"target": target, "subscriptions": rows}
    if action == "rate":
        return {"rating": client.get_rating(edit.video_id)}
    if action == "report-abuse":
        target = client.external("videos", edit.video_id)
        reasons = complete(client.catalog("abuse-reasons", language=edit.language or "en"))
        rows = [r for r in reasons if r["id"] == edit.reason_id]
        if len(rows) != 1:
            raise ResourceError("reason_id is absent from public catalog")
        if edit.secondary_reason_id and not any(r.get("id") == edit.secondary_reason_id for r in rows[0]["snippet"].get("secondaryReasons", []) if isinstance(r, dict)):
            raise ResourceError("secondary_reason_id does not belong to approved reason")
        return {"target": target, "reason": rows[0]}
    raise ResourceError("unsupported action")


def body_for(edit, account):
    if edit.action == "add":
        return {"snippet": {"channelId": account, "videoId": edit.video_id,
            "topLevelComment": {"snippet": {"textOriginal": edit.text}}}}
    if edit.action == "reply":
        return {"snippet": {"parentId": edit.parent_id, "textOriginal": edit.text}}
    if edit.action == "edit":
        return {"id": edit.comment_id, "snippet": {"textOriginal": edit.text}}
    if edit.action == "subscribe":
        return {"snippet": {"resourceId": {"kind": "youtube#channel", "channelId": edit.channel_id}}}
    if edit.action == "report-abuse":
        return {k: v for k, v in {"videoId": edit.video_id, "reasonId": edit.reason_id,
            "secondaryReasonId": edit.secondary_reason_id, "comments": edit.comments, "language": edit.language}.items() if v is not None}
    return {}


def effects_for(edit):
    notes = ["Only exact supplied IDs, actor, text and values. YouTube validates effective grants/eligibility; no permissions granted.",
        "Status preflight before writing; remote race possible. Provider If-Match atomicity is not guaranteed.",
        "Uncertain outcome not repeated with another UUID. Reconcile only reads."]
    if edit.action in {"add", "reply", "edit"}:
        notes.append("Comment visibility depends on privacy/moderation. textOriginal is verified only for its author; textDisplay does not prove original text. Pin, heart and clickable links are not promised.")
    if edit.action == "delete":
        notes.append("Original author's comment deletion; snapshot cannot be restored. Replies may become hidden. Absence does not prove deletion.")
    if edit.action == "moderate":
        notes.append("One POST for up to 50 explicit comments; individual checks. Rejected also hides replies; restoration is not promised. No automatic selection or rollback.")
        notes.append("banAuthor=true automatically rejects future comments from EACH displayed author." if edit.ban_author else "banAuthor=false: future author blocking is not requested.")
        notes.append("204 is an acceptance receipt; absence or invisible status does not prove status or banAuthor. Individual readback may remain partial/unverifiable.")
    if edit.action in {"subscribe", "unsubscribe"}:
        notes.append("Authenticated actor differs from external target channel. YouTube prevents self-subscription and duplicates and may limit volume/eligibility.")
    if edit.action == "rate":
        notes.append("Actor rating on explicit video, including external videos; none removes rating. Does not change official like/dislike count. Verified email required; rental/disabled ratings may prevent it.")
    if edit.action == "report-abuse":
        notes.append("Explicit video REPORT with displayed reason, independent of comments/audit. 204 accepts submission; no report query or guarantee of sanctions/reversal.")
    return notes


def prepare_community(client, store, edit):
    edit = validate_edit(edit)
    account = client.identity()
    before = baseline(client, edit)
    moment = now_utc()
    change = CommunityChange(id=str(uuid.uuid4()), target_brand=client.brand.nombre, brand_root=str(client.brand.raiz.resolve()),
        target_account=account, edit=edit_raw(edit), before=before, after=body_for(edit, account), effects=effects_for(edit), created_at=moment, updated_at=moment)
    change.fingerprint = fingerprint(change)
    _event(change, "prepared")
    store.save(change)
    return change


def identity(client, change):
    if (client.brand.nombre, str(client.brand.raiz.resolve()), client.configured_channel_id) != (change.target_brand, change.brand_root, change.target_account):
        raise ResourceError("brand/account differs from proposal")
    if client.identity() != change.target_account:
        raise ResourceError("actor autenticado distinto")


def operation_keys(change):
    edit = validate_edit(change.edit)
    if edit.action == "moderate":
        keys = [("comments", t.video_id) for t in edit.targets]
    elif edit.action in {"add", "reply", "edit", "delete"}:
        keys = [("comments", edit.video_id)]
    elif edit.action in {"subscribe", "unsubscribe"}:
        keys = [("subscriptions", edit.channel_id)]
    else:
        keys = [(edit.action, edit.video_id)]
    return {json.dumps([change.target_account, *key]) for key in keys}


def receipt_for(client, change, response):
    edit = validate_edit(change.edit)
    if response.get("http_status") == 204:
        return response
    receipt = {"identity_valid": False}
    try:
        if edit.action == "add":
            receipt["thread_id"] = opaque_id(response.get("id"))
            row = response["snippet"]["topLevelComment"]
            change.result_id = opaque_id(row.get("id"))
            client._thread(response, edit.video_id)
        elif edit.action in {"reply", "edit"}:
            row = response
            change.result_id = opaque_id(row.get("id"))
            client._comment(row, parent=edit.parent_id)
            if edit.action == "edit" and change.result_id != edit.comment_id:
                return receipt
        else:
            change.result_id = opaque_id(response.get("id"))
            client._subscription(response)
            receipt["identity_valid"] = response["snippet"]["resourceId"]["channelId"] == edit.channel_id
            return receipt
        receipt["identity_valid"] = author(row) == change.target_account
    except (ResourceError, KeyError, TypeError, AttributeError):
        pass
    return receipt


def readback(client, store, change):
    edit = validate_edit(change.edit)
    try:
        identity(client, change)
        receipt = change.receipt or {}
        if edit.action == "moderate" and receipt.get("http_status") == 204:
            observations = []
            cache = {}
            for target in edit.targets:
                observed = {"target": target.model_dump(exclude_none=True), "matches": False}
                try:
                    row = client.find_comment(**target.model_dump(exclude_none=True), moderation=True, cache=cache)
                    observed["moderation_status"] = row["snippet"].get("moderationStatus")
                    observed["matches"] = observed["moderation_status"] == edit.moderation_status
                    prior = next(r["comment"] for r in change.before["targets"] if r["target"]["comment_id"] == target.comment_id)
                    observed["matches"] = observed["matches"] and author(row) == author(prior)
                except ResourceError as exc:
                    observed["error"] = str(exc)
                observations.append(observed)
                _event(change, "moderation_target_readback", observation=observed, ban_verified=False)
                store.save(change)  # Each observed target survives later readback failure.
            matches = sum(o["matches"] for o in observations)
            _event(change, "moderation_readback", observations=observations, ban_verified=False)
            change.status = "partial" if 0 < matches < len(observations) else "accepted_unverifiable"
            if matches == len(observations) and not edit.ban_author:
                change.status, change.verified = "verified", True
        elif edit.action in {"delete", "report-abuse"} and receipt.get("http_status") == 204:
            change.status = "accepted_unverifiable"
            if edit.action == "delete":
                observation = {"absence_is_not_proof": True}
                try:
                    row = client.find_comment(**binding(edit))
                    observation["comment_still_observed"] = row["id"]
                except ResourceError as exc:
                    observation["readback_error"] = str(exc)
                _event(change, "delete_readback", **observation)
            else:
                _event(change, "accepted_without_provable_readback", no_report_read_endpoint=True)
        elif edit.action in {"subscribe", "unsubscribe"}:
            rows = complete(client.list_subscriptions())
            rows = [r for r in rows if r["snippet"]["resourceId"]["channelId"] == edit.channel_id]
            matches = (not rows and receipt.get("http_status") == 204) if edit.action == "unsubscribe" else (
                receipt.get("identity_valid") and len(rows) == 1 and rows[0]["id"] == change.result_id)
            _event(change, "subscription_readback", matches=bool(matches))
            if matches:
                change.status, change.verified = "verified", True
        elif edit.action == "rate":
            current = client.get_rating(edit.video_id)
            _event(change, "rating_readback", observed=current)
            if receipt.get("http_status") == 204 and current["rating"] == edit.rating:
                change.status, change.verified = "verified", True
        elif receipt.get("identity_valid") and change.result_id:
            target = {"video_id": edit.video_id, "thread_id": receipt.get("thread_id", edit.thread_id),
                "comment_id": change.result_id, "parent_id": edit.parent_id}
            row = client.find_comment(**target)
            matches = author(row) == change.target_account and row["snippet"].get("textOriginal") == edit.text
            _event(change, "comment_readback", matches=matches, comment_id=change.result_id)
            if matches:
                change.status, change.verified = "verified", True
        else:
            _event(change, "identity_unresolved", no_candidate_adoption=True)
    except ResourceError as exc:
        _event(change, "readback_unavailable", error=str(exc))
    store.save(change)
    return change


def reconcile_community(client, store, change_id):
    with store.apply_lock(change_id):
        change = store.load(change_id)
        identity(client, change)
        if change.status in {"applying", "uncertain", "partial", "accepted_unverifiable"}:
            if change.status == "applying":
                change.status = "uncertain"
            return readback(client, store, change)
        return change


def apply_community(client, store, change_id, approval_digest):
    with ExitStack() as locks:
        locks.enter_context(store.apply_lock(change_id))
        change = store.load(change_id)
        edit = validate_edit(change.edit)
        if not hmac.compare_digest(approval_digest, change.fingerprint):
            raise ApprovalMismatch("approval does not match exact fingerprint")
        if body_for(edit, change.target_account) != change.after or effects_for(edit) != change.effects:
            raise ResourceError("body/effects do not match proposal")
        keys = operation_keys(change)
        for key in sorted(keys):
            locks.enter_context(store.apply_lock(str(uuid.uuid5(uuid.NAMESPACE_URL, key))))
        identity(client, change)
        if change.status != "proposed":
            if change.status in {"applying", "uncertain", "partial", "accepted_unverifiable"}:
                if change.status == "applying":
                    change.status = "uncertain"
                return readback(client, store, change)
            return change
        for path in store.root.glob("*.json"):
            other = store.load(path.stem)
            if other.id != change.id and keys & operation_keys(other) and other.status in {"applying", "uncertain", "partial"}:
                raise ResourceError(f"uncertain outcome pending in {other.id}; another UUID does not allow repetition")
        try:
            if baseline(client, edit) != change.before:
                raise ResourceConflict("conflict: status/author changed since preview")
        except ResourceConflict:
            change.status = "conflict"
            _event(change, "pre_write_conflict")
            store.save(change)
            raise
        change.status = "applying"
        _event(change, "write_intent", approval_digest=approval_digest)
        store.save(change)
        try:
            response = client.effect(edit, change.after, change.before)
        except ResourceRejected as exc:
            change.status = "conflict" if isinstance(exc, ResourceConflict) else "failed"
            _event(change, "write_rejected", error=str(exc))
        except ResourceUncertain as exc:
            change.status = "uncertain"
            _event(change, "write_uncertain", error=str(exc))
        else:
            change.status = "uncertain"
            change.receipt = receipt_for(client, change, response)
            _event(change, "write_response", result_id=change.result_id, receipt=change.receipt)
        store.save(change)
        return readback(client, store, change) if change.status == "uncertain" else change
