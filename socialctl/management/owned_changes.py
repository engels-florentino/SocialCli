"""Durable, exact approval and read-only recovery for owned YouTube resources."""
from __future__ import annotations

import copy
import hmac
import json
import re
import uuid
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from socialctl.management.changes import ApprovalMismatch, now_utc
from socialctl.management.resource_changes import ResourceStore, digest, _event
from socialctl.management.owned_schema import build_after, edit_raw, editable, owned_resource_id, mapping, merge, validate_edit, target
from socialctl.management.owned_assets import inspect_owned_asset, banner_url
from socialctl.management.youtube_resources import ResourceError, ResourceConflict, ResourceRejected, ResourceUncertain, resource_id


class OwnedChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    kind: Literal["youtube-owned-resource"] = "youtube-owned-resource"
    id: str
    target_brand: str
    target_account: str
    edit: dict
    resource: Literal["playlists", "playlistItems", "channelSections", "channels", "playlistImages", "videos"]
    target_id: str | None = None
    before: dict
    after: dict
    asset: dict | None = None
    effects: list[str]
    plan: list[dict] = Field(default_factory=list)
    fingerprint: str = ""
    created_at: datetime
    updated_at: datetime
    status: Literal["proposed", "applying", "uncertain", "failed", "conflict", "partial", "verified", "accepted_unverifiable"] = "proposed"
    verified: bool = False
    result_id: str | None = None
    receipt: dict | None = None
    steps: list[dict] = Field(default_factory=list)
    journal: list[dict] = Field(default_factory=list)


def fingerprint(change):
    raw = change.model_dump(mode="json")
    return digest(json.dumps({k: v for k, v in raw.items() if k not in {
        "fingerprint", "updated_at", "status", "verified", "result_id", "receipt", "steps", "journal"}},
        sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())


class OwnedStore(ResourceStore):
    model_type = OwnedChange
    fingerprint_for = staticmethod(fingerprint)

    def __init__(self, brand_root):
        self.root = Path(brand_root) / ".socialctl" / "youtube-owned"


def complete(report):
    if not report["complete"]:
        raise ResourceError("incomplete list; cannot prepare/apply: " + str(report.get("error")))
    return report["items"]


def ordered(rows):
    rows = sorted(copy.deepcopy(rows), key=lambda r: r["snippet"]["position"])
    if [r["snippet"]["position"] for r in rows] != list(range(len(rows))):
        raise ResourceError("incomplete list: noncontiguous or duplicate positions")
    return rows


def baseline(client, edit):
    resource, rid = target(edit)
    if resource == "channels":
        row = client.inspect_channel(rid)
    elif resource == "videos":
        row = client.inspect_video(rid)
    elif rid:
        row = client.one(resource, rid, parent=edit.playlist_id)
    elif resource in {"playlistItems", "playlistImages"}:
        row = client.one("playlists", edit.playlist_id)
    else:
        row = client.inspect_channel(client.configured_channel_id)
    before = {"resource": row}
    if edit.action.startswith("watermark-"):
        before["watermark_state"] = {"known": False, "reason": "no_public_read_method"}
    if resource == "playlistItems":
        before["parent"] = client.one("playlists", edit.playlist_id)
    if edit.action in {"item-insert", "items-reorder"} or (edit.action == "item-update" and "snippet" in edit.patch):
        before["collection"] = ordered(complete(client.list_items(edit.playlist_id)))
        if edit.action == "item-insert":
            if any(r["snippet"]["resourceId"]["videoId"] == edit.video_id for r in before["collection"]):
                raise ResourceConflict("membership with this videoId already exists; no title-based deduplication")
            if edit.position > len(before["collection"]):
                raise ResourceError("insertion position outside list")
    if resource == "playlistImages":
        before["parent"] = client.one("playlists", edit.playlist_id)
        before["collection"] = complete(client.list_images(edit.playlist_id))
        if edit.action == "image-insert" and any(r["snippet"]["type"] == edit.image_type for r in before["collection"]):
            raise ResourceConflict("image with this type already exists; explicit image-update required")
    if edit.action == "playlist-create":
        before["collection"] = complete(client.list_playlists())
    if edit.action == "playlist-update" and isinstance(edit.patch.get("status"), dict) and edit.patch["status"].get("podcastStatus") == "enabled":
        before["image_eligibility"] = complete(client.list_images(edit.playlist_id))
        if not before["image_eligibility"]:
            raise ResourceError("podcastStatus=enabled requires existing image; not created automatically")
    if edit.action == "section-create":
        before["collection"] = complete(client.list_sections())
        if len(before["collection"]) >= 10:
            raise ResourceError("documented maximum of 10 sections")
    return before


def effects_for(edit):
    notes = ["Approval covers only displayed brand/account, IDs and complete values; effective permissions/eligibility depend on provider."]
    if edit.action.endswith("-delete"):
        notes.append("Irreversible deletion of exact ID; snapshot is evidence, not a restorable backup or automatic restoration.")
    if edit.action in {"playlist-delete", "item-delete"}:
        notes.append("Deleting playlist/membership does not delete underlying videos.")
    if edit.action in {"item-insert", "items-reorder", "item-update"}:
        notes.append("playlistItemId, playlistId and videoId are distinct IDs; position changes shift other memberships. ManualSortRequired requires explicit external adjustment; never changed automatically.")
    if edit.action == "item-update":
        notes.append("Deprecated startAt/endAt are forwarded only if already present when preserving contentDetails; YouTube ignores them. Editing disabled; restoration or retention not promised.")
    if edit.action == "items-reorder":
        notes.append("Multi-item ordering is NOT atomic: durable partial results per item; reconcile only rereads and never continues writes.")
    if edit.action == "channel-audience":
        notes.append("Explicit selfDeclaredMadeForKids audience declaration; not inferred. Documentation mismatch or status rejection preserved without claiming success.")
    if edit.action.startswith("image-"):
        notes.append("playlistImage does not document ETag: preflight value/owner checks with remote read-write race; no conditional guarantee. Playlist ETag is never used as image ETag.")
    if edit.file:
        notes.append("Exact supplied bytes without generation/conversion. Local container/dimension validation; provider validates processing. Metadata readback does not prove visual or byte equality.")
    if edit.action == "image-update" and edit.file:
        notes.append("Media update: supplied square JPEG/PNG up to 52428800 bytes (50MiB), Discovery limit. Image insert retains documented 2MiB limit.")
    if edit.action == "banner-upload":
        notes.append("Only uploads banner and durably stores URL; does not apply branding. banner-apply prepares separate exact approval for that URL.")
    if edit.action == "banner-apply":
        notes.append("Applies durable upload receipt URL via separate channels.update; does not change channel title.")
    if edit.action.startswith("watermark-"):
        notes.append("Previous watermark state UNKNOWN: no public read method and channels.list does not accept invideoBranding. Channel freshness/ownership does not prove watermark status or provide If-Match condition. A 204 yields only accepted_unverifiable, never proves bytes, metadata or absence.")
    if edit.action == "watermark-set":
        notes.append("Explicit full watermark replacement with approved file/timing/targetChannelId; unknown previous values are not preserved. durationMs='omit' intentionally omits that field rather than preserving it.")
    return notes


def watermark_after(edit, before):
    timing = dict(mapping(edit.timing, {"type", "offsetMs", "durationMs"}, "timing"))
    if (set(timing) != {"type", "offsetMs", "durationMs"} or not isinstance(timing.get("type"), str)
        or timing["type"] not in {"offsetFromStart", "offsetFromEnd"}):
        raise ResourceError("replacement requires explicit timing type/offsetMs/durationMs; durationMs='omit' means intentional omission")
    if timing["durationMs"] == "omit":
        del timing["durationMs"]
    for key in {"offsetMs", "durationMs"} & set(timing):
        if not isinstance(timing[key], str) or not re.fullmatch(r"\d{1,20}", timing[key]) or int(timing[key]) > 18446744073709551615:
            raise ResourceError("timing requires decimal uint64 as text")
    target_channel = resource_id(edit.target_channel_id)
    return {"timing": timing, "targetChannelId": target_channel}


def reorder_plan(edit, before):
    rows = ordered(before["collection"])
    positions = [p.model_dump() for p in edit.positions]
    if ({p["item_id"] for p in positions} != {r["id"] for r in rows}
        or len(positions) != len(rows) or sorted(p["position"] for p in positions) != list(range(len(rows)))):
        raise ResourceError("reorder requires each exact playlistItemId once and all positions contiguous")
    plan = []
    for wanted in sorted(positions, key=lambda p: p["position"]):
        row = next(r for r in rows if r["id"] == wanted["item_id"])
        if row["snippet"]["position"] == wanted["position"]:
            continue
        body = {"snippet": {**editable("playlistItems", "snippet", row), "position": wanted["position"]}}
        plan.append({"item_id": row["id"], "body": body})
        rows.remove(row)
        rows.insert(wanted["position"], row)
        for pos, other in enumerate(rows):
            other["snippet"]["position"] = pos
    return {"positions": positions}, plan


def expected_body(edit, resource, rid, before, account, asset):
    """Reconstruct outgoing parts from the strict edit, never trust stored after."""
    after, plan = {}, []
    if edit.action == "items-reorder":
        after, plan = reorder_plan(edit, before)
    elif edit.action == "watermark-set":
        after = watermark_after(edit, before)
    elif edit.action == "banner-apply":
        url = banner_url(before["upload_receipt"]["url"])
        after = {"brandingSettings": merge(editable("channels", "brandingSettings", before["resource"]), {"image": {"bannerExternalUrl": url}})}
    elif not edit.action.endswith("-delete") and edit.action not in {"watermark-unset", "banner-upload"}:
        after = build_after(edit, resource, before["resource"] if rid else {}, account)
    if resource == "playlistImages" and asset:
        explicit_dimensions = (edit.patch or {}).get("snippet", {})
        for field in ("width", "height"):
            if field in explicit_dimensions and explicit_dimensions[field] != asset[field]:
                raise ResourceError("explicit dimensions do not match supplied bytes")
            after["snippet"][field] = asset[field]
    if edit.action == "item-update" and "collection" in before and after["snippet"]["position"] >= len(before["collection"]):
        raise ResourceError("update position outside list")
    return after, plan


def prepare_owned(client, store, edit):
    edit = validate_edit(edit)
    resource, rid = target(edit)
    account = client.identity()
    before = baseline(client, edit)
    if edit.action == "banner-apply":
        uploaded = store.load(edit.upload_change_id)
        if (uploaded.edit["action"] != "banner-upload" or uploaded.target_brand != client.brand.nombre
            or uploaded.target_account != account or uploaded.status != "accepted_unverifiable" or not uploaded.receipt):
            raise ResourceError("banner-apply requires durable upload receipt from same brand/account")
        url = banner_url(uploaded.receipt.get("url"))
        before["upload_receipt"] = {"change_id": uploaded.id, "fingerprint": uploaded.fingerprint, "url": url}
    asset = None
    if edit.file:
        asset, _ = inspect_owned_asset(edit.file, edit.action)
        edit.file = asset["path"]
    after, plan = expected_body(edit, resource, rid, before, account, asset)
    moment = now_utc()
    change = OwnedChange(id=str(uuid.uuid4()), target_brand=client.brand.nombre, target_account=account,
        edit=edit_raw(edit), resource=resource, target_id=rid, before=before, after=after, asset=asset,
        effects=effects_for(edit), plan=plan, created_at=moment, updated_at=moment)
    change.fingerprint = fingerprint(change)
    _event(change, "prepared")
    store.save(change)
    return change


def identity(client, change):
    if client.brand.nombre != change.target_brand or client.configured_channel_id != change.target_account:
        raise ResourceError("brand/account does not match proposal")
    if client.identity() != change.target_account:
        raise ResourceError("authenticated channel mismatch")


def operation_key(change):
    edit = validate_edit(change.edit)
    # Serialize all ordering/membership operations per playlist. Unknown create
    # outcomes block the entire create family, even if the user changes a title.
    if change.resource in {"playlistItems", "playlistImages"}:
        target_key = [change.resource, edit.playlist_id]
    elif edit.action in {"playlist-create", "section-create"}:
        target_key = [change.resource, "create"]
    else:
        target_key = [change.resource, change.target_id]
    return json.dumps([change.target_account, *target_key])


def receipt_for(change, response):
    edit = validate_edit(change.edit)
    if edit.action == "banner-upload":
        # Store the returned URL before validating it or preparing application.
        return {"url": response.get("url"), "accepted": True}
    if edit.action.endswith("-delete") or edit.action.startswith("watermark-"):
        return response
    try:
        rid = owned_resource_id(change.resource, response.get("id"))
    except ResourceError:
        rid = None
    change.result_id = rid
    valid = bool(rid) and (change.target_id is None or rid == change.target_id)
    snip = response.get("snippet", {})
    if change.target_id is None:
        valid = valid and rid not in {row["id"] for row in change.before.get("collection", [])}
        if not isinstance(snip, dict):
            valid = False
        elif change.resource in {"playlistItems", "playlistImages"}:
            valid = valid and all(snip.get(k) == v for k, v in change.after["snippet"].items())
        else:
            valid = valid and snip.get("channelId") == change.target_account
    if "kind" in response:
        from socialctl.management.youtube_owned import KINDS
        valid = valid and response["kind"] == KINDS[change.resource]
    return {"id": rid, "identity_valid": valid}


def readback(client, store, change):
    edit = validate_edit(change.edit)
    try:
        identity(client, change)
        if edit.action == "banner-upload":
            if change.receipt and banner_url(change.receipt.get("url")):
                change.status = "accepted_unverifiable"
            _event(change, "upload_only_no_branding_readback")
        elif edit.action == "items-reorder":
            rows = ordered(complete(client.list_items(edit.playlist_id)))
            actual = {r["id"]: r["snippet"]["position"] for r in rows}
            expected = {p["item_id"]: p["position"] for p in change.after["positions"]}
            _event(change, "order_readback", positions=actual)
            if actual == expected and len(change.steps) == len(change.plan) and all(s["status"] == "verified" for s in change.steps):
                change.status, change.verified = "verified", True
        elif edit.action.startswith("watermark-"):
            _event(change, "watermark_readback_unavailable", state_known=False,
                reason="no_public_read_method", bytes_verified=False, absence_verified=False)
            if change.receipt and change.receipt.get("http_status") == 204:
                change.status, change.verified = "accepted_unverifiable", False
        elif edit.action.endswith("-delete"):
            row = client.one(change.resource, change.target_id, parent=edit.playlist_id, optional=True)
            _event(change, "delete_readback", absent_observed=row is None, absence_alone_is_not_proof=True)
            if row is None and change.receipt and change.receipt.get("http_status") == 204:
                change.status, change.verified = "verified", True
        elif change.receipt and change.receipt.get("identity_valid"):
            row = client.one(change.resource, change.result_id, parent=edit.playlist_id)
            actual = {p: editable(change.resource, p, row) for p in change.after}
            match = actual == change.after
            _event(change, "metadata_readback", matches=match, observed=actual, bytes_verified=False)
            if match:
                if change.asset:
                    change.status = "accepted_unverifiable"
                else:
                    change.status, change.verified = "verified", True
        else:
            _event(change, "identity_unresolved", limitation="Identity is not inferred from titles or candidates; create is not repeated.")
    except ResourceError as exc:
        _event(change, "readback_unavailable", error=str(exc))
    store.save(change)
    return change


def reconcile_owned(client, store, change_id):
    with store.apply_lock(change_id):
        change = store.load(change_id)
        identity(client, change)
        if change.status in {"applying", "uncertain", "partial", "accepted_unverifiable"}:
            if change.status == "applying":
                change.status = "uncertain"
            return readback(client, store, change)
        return change


def apply_owned(client, store, change_id, approval_digest):
    with ExitStack() as locks:
        locks.enter_context(store.apply_lock(change_id))
        change = store.load(change_id)
        edit = validate_edit(change.edit)
        if target(edit) != (change.resource, change.target_id) or bool(edit.file) != bool(change.asset):
            raise ResourceError("proposal does not match resource/file schema")
        if not hmac.compare_digest(approval_digest, change.fingerprint):
            raise ApprovalMismatch("approval does not match exact fingerprint")
        key = operation_key(change)
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
            unresolved_steps = any(s.get("status") in {"applying", "uncertain"} for s in other.steps)
            if other.id != change.id and operation_key(other) == key and (other.status in {"applying", "uncertain"} or unresolved_steps):
                raise ResourceError(f"uncertain outcome pending in {other.id}; another UUID does not allow repeating write")
        try:
            current = baseline(client, edit)
            expected = {k: v for k, v in change.before.items() if k != "upload_receipt"}
            if current != expected:
                raise ResourceConflict("conflict: resource/listing changed since preview")
            if edit.action == "banner-apply":
                uploaded = store.load(edit.upload_change_id)
                binding = change.before["upload_receipt"]
                if (uploaded.fingerprint != binding["fingerprint"] or not uploaded.receipt or uploaded.receipt.get("url") != binding["url"]
                    or uploaded.status != "accepted_unverifiable" or uploaded.target_account != change.target_account or uploaded.target_brand != change.target_brand):
                    raise ResourceConflict("conflict: banner receipt changed")
        except ResourceConflict:
            change.status = "conflict"
            _event(change, "pre_write_conflict")
            store.save(change)
            raise
        data = None
        if change.asset:
            asset, data = inspect_owned_asset(edit.file, edit.action)
            if asset != change.asset:
                raise ResourceError("file/bytes changed since approved fingerprint")
        after, plan = expected_body(edit, change.resource, change.target_id, change.before, change.target_account, change.asset)
        if after != change.after or plan != change.plan or effects_for(edit) != change.effects:
            raise ResourceError("body/plan/effects do not match proposal schema")
        if edit.action == "items-reorder":
            from socialctl.management.owned_reorder import apply_order
            return apply_order(client, store, change, edit, approval_digest)
        change.status = "applying"
        _event(change, "write_intent", approval_digest=approval_digest)
        store.save(change)
        try:
            response = client.effect(edit, change.resource, change.target_id, change.after,
                etag=None if change.target_id is None else change.before["resource"].get("etag"), asset=change.asset, data=data)
        except ResourceRejected as exc:
            change.status = "conflict" if isinstance(exc, ResourceConflict) else "failed"
            _event(change, "write_rejected", error=str(exc))
        except ResourceUncertain as exc:
            change.status = "uncertain"
            _event(change, "write_uncertain", error=str(exc))
        else:
            change.status = "uncertain"
            change.receipt = receipt_for(change, response)
            _event(change, "write_response", result_id=change.result_id, receipt=change.receipt)
        store.save(change)
        return readback(client, store, change) if change.status == "uncertain" else change
