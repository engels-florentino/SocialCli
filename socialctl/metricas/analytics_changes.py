"""Immutable exact proposals for one Analytics group or Reporting job effect."""
from contextlib import ExitStack
from datetime import datetime
import hmac
import json
from pathlib import Path
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field

from socialctl.management.changes import ApprovalMismatch, now_utc
from socialctl.management.resource_changes import ResourceStore, fingerprint, _event
from socialctl.management.youtube_resources import ResourceError, ResourceRejected, ResourceUncertain
from socialctl.metricas.analytics_client import ANALYTICS, REPORTING, complete, opaque, encoded
from socialctl.metricas.reporting_client import ANALYTICS_SCOPES

FIELDS = {"group-create": {"title", "item_type"}, "group-update": {"group_id", "title"},
    "group-delete": {"group_id"}, "group-item-create": {"group_id", "resource_id"},
    "group-item-delete": {"group_id", "item_id"}, "job-create": {"report_type_id", "name"},
    "job-delete": {"job_id", "report_type_id"}}


def validate_edit(raw):
    if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1 or raw.get("action") not in FIELDS:
        raise ResourceError("unsupported Analytics edit action/version")
    required = FIELDS[raw["action"]] | {"version", "action"}
    if set(raw) != required:
        raise ResourceError("edit requires exact action fields; group update changes only title")
    for field in required - {"version", "action", "title", "name", "item_type"}:
        opaque(raw[field])
    for field in {"title", "name"} & required:
        if not isinstance(raw[field], str) or not raw[field].strip() or len(raw[field]) > 150 or "\x00" in raw[field]:
            raise ResourceError("title/name requires nonempty text, local limit 150")
    if "item_type" in raw and raw["item_type"] not in {"youtube#video", "youtube#channel", "youtube#playlist"}:
        raise ResourceError("content-owner asset groups not enabled")
    return json.loads(json.dumps(raw))


class AnalyticsChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    kind: Literal["youtube-analytics"] = "youtube-analytics"
    id: str
    target_brand: str
    brand_root: str
    target_account: str
    edit: dict
    before: dict
    after: dict
    effects: list[str]
    credential_observation: dict
    fingerprint: str = ""
    created_at: datetime
    updated_at: datetime
    status: Literal["proposed", "applying", "uncertain", "failed", "conflict", "verified"] = "proposed"
    verified: bool = False
    result_id: str | None = None
    receipt: dict | None = None
    journal: list[dict] = Field(default_factory=list)


class AnalyticsStore(ResourceStore):
    model_type = AnalyticsChange
    fingerprint_for = staticmethod(fingerprint)

    def __init__(self, brand_root):
        self.root = Path(brand_root) / ".socialctl" / "youtube-analytics-changes"

    def load(self, change_id):
        if self.path_for(change_id).is_symlink():
            raise ResourceError("proposal symlink rejected")
        return super().load(change_id)


def scopes(client, edit):
    client.require_scope(ANALYTICS_SCOPES if edit["action"].startswith("job-") else {"https://www.googleapis.com/auth/youtube"})


def baseline(client, edit):
    action = edit["action"]
    if action == "group-create":
        rows = complete(client.groups())
        if any(r["snippet"]["title"] == edit["title"] and r["contentDetails"]["itemType"] == edit["item_type"] for r in rows):
            raise ResourceError("matching group already exists; no implicit replacement")
        return {"existing_group_ids": sorted(r["id"] for r in rows)}
    if action == "job-create":
        types = [r for r in complete(client.report_types()) if r["id"] == edit["report_type_id"]]
        if len(types) != 1 or types[0].get("systemManaged", False):
            raise ResourceError("report type unavailable or system managed")
        jobs = complete(client.jobs())
        if any(j["reportTypeId"] == edit["report_type_id"] for j in jobs):
            raise ResourceError("job already exists for report type; no duplicate create")
        return {"report_type": types[0], "existing_job_ids": sorted(j["id"] for j in jobs)}
    if action == "job-delete":
        row = client.job(edit["job_id"], edit["report_type_id"])
        if row.get("systemManaged", False):
            raise ResourceError("system-managed job cannot be deleted")
        return row
    group = client.group(edit["group_id"])
    if action.startswith("group-item"):
        items = complete(client.group_items(edit["group_id"]))
        if action == "group-item-create":
            owned = client.bind_group_resource(group, edit["resource_id"])
            members = [r for r in items if r["resource"]["id"] == edit["resource_id"]]
            if len(members) > 1:
                raise ResourceError("ambiguous group membership")
            return {"group": group, "members": members, "owned_resource_id": owned["id"]}
        matches = [r for r in items if r["id"] == edit["item_id"]]
        if len(matches) != 1:
            raise ResourceError("item ID not verified in exact group")
        return {"group": group, "item": matches[0]}
    return group


def after_state(edit):
    action = edit["action"]
    if action == "group-create":
        return {"snippet": {"title": edit["title"]}, "contentDetails": {"itemType": edit["item_type"]}}
    if action == "group-update":
        return {"id": edit["group_id"], "snippet": {"title": edit["title"]}}
    if action == "group-item-create":
        return {"groupId": edit["group_id"], "resource": {"id": edit["resource_id"]}}
    if action == "job-create":
        return {"reportTypeId": edit["report_type_id"], "name": edit["name"]}
    return {"deleted_id": edit.get("item_id", edit.get("group_id", edit.get("job_id")))}


def prepare(client, store, raw):
    edit = validate_edit(raw)
    account = client.identity()
    scopes(client, edit)
    before = baseline(client, edit)
    moment = now_utc()
    effects = ["Exactly one remote " + edit["action"],
        "No rollback guarantee; group IDs are independent of playlists; membership does not edit media.",
        "Provider has no documented conditional version guard here; read/preflight cannot eliminate remote race."]
    action_effect = {
        "job-create": "Job creation starts recurring provider report generation; first report may take up to 48h.",
        "job-delete": "Job deletion stops generation; stored report provenance retained, no local purge.",
        "group-delete": "Group deletion removes its grouping/membership; original videos/channels/playlists remain.",
    }.get(edit["action"])
    if action_effect:
        effects.append(action_effect)
    change = AnalyticsChange(id=str(uuid.uuid4()), target_brand=client.brand.nombre,
        brand_root=str(client.brand.raiz.resolve()), target_account=account, edit=edit,
        before=before, after=after_state(edit), credential_observation=client.credential_observation,
        effects=effects,
        created_at=moment, updated_at=moment)
    _event(change, "prepared")
    change.fingerprint = fingerprint(change)
    store.save(change)
    return change


def identity(client, change):
    if client.brand.nombre != change.target_brand or str(client.brand.raiz.resolve()) != change.brand_root or client.identity() != change.target_account:
        raise ResourceError("proposal brand/account/root mismatch")


def operation_key(change):
    e = change.edit
    if e["action"].startswith("job-"):
        target = ["job", e["report_type_id"]]
    elif e["action"] == "group-create":
        target = ["group-create", e["title"], e["item_type"]]
    elif e["action"].startswith("group-item"):
        target = ["group-item", e["group_id"], e.get("resource_id") or change.before["item"]["resource"]["id"]]
    else:
        target = ["group", e["group_id"]]
    return json.dumps([change.target_account, *target], sort_keys=True)


def mutate(client, change):
    e, a = change.edit, change.edit["action"]
    if a.startswith("job-"):
        url = REPORTING + "/jobs" + ("/" + encoded(e["job_id"]) if a == "job-delete" else "")
    else:
        url = ANALYTICS + ("/groupItems" if a.startswith("group-item") else "/groups")
    method = "DELETE" if a.endswith("delete") else "PUT" if a == "group-update" else "POST"
    kwargs = ({"params": {"id": change.after["deleted_id"]}} if method == "DELETE" and not a.startswith("job-") else {})
    if method != "DELETE":
        kwargs["json"] = change.after
    response = client.service_request(method, url, **kwargs)
    if method == "DELETE":
        # Reporting documents an empty successful body, unlike Analytics' exact
        # 204 contract. Some Google transports serialize google.protobuf.Empty
        # as {}, also explicitly accepted for Reporting only.
        accepted = response.status_code == 204 and not response.content
        if a == "job-delete" and response.status_code == 200:
            accepted = not response.content or response.content.strip() == b"{}"
        if not accepted:
            raise ResourceUncertain("delete response did not match documented empty success")
        return {"http_status": response.status_code, "deleted_id": change.after["deleted_id"], "empty_success": True}, None
    if a == "group-item-create" and response.status_code == 204:
        return {"http_status": 204, "duplicate_membership": True, "group_id": e["group_id"], "resource_id": e["resource_id"]}, None
    raw = client._json(response, writing=True)
    try:
        rid = opaque(raw.get("id"))
    except ResourceError:
        rid = None
    return {"http_status": response.status_code, "response": raw}, rid


def readback(client, store, change):
    identity(client, change)
    a, e = change.edit["action"], change.edit
    verified = False
    try:
        if change.receipt:
            response = change.receipt.get("response", {})
            if a == "group-item-create" and change.receipt.get("duplicate_membership"):
                rows = complete(client.group_items(e["group_id"]))
                verified = len([r for r in rows if r["resource"]["id"] == e["resource_id"]]) == 1
            elif a.endswith("delete") and change.receipt.get("empty_success"):
                rows = complete(client.jobs() if a == "job-delete" else client.group_items(e["group_id"]) if a == "group-item-delete" else client.groups())
                verified = all(r["id"] != change.after["deleted_id"] for r in rows)
            elif change.result_id:
                rid = change.result_id
                if a.startswith("group-item"):
                    rows = [r for r in complete(client.group_items(e["group_id"])) if r["id"] == rid]
                    verified = response.get("groupId") == e["group_id"] and response.get("resource", {}).get("id") == e["resource_id"] and len(rows) == 1 and rows[0]["resource"]["id"] == e["resource_id"]
                elif a.startswith("group-"):
                    row = client.group(rid)
                    kind = e.get("item_type") or change.before["contentDetails"]["itemType"]
                    verified = (a != "group-update" or rid == e["group_id"]) and response.get("snippet", {}).get("title") == e["title"] and row["snippet"]["title"] == e["title"] and row["contentDetails"]["itemType"] == kind
                    if a == "group-create":
                        verified = verified and response.get("contentDetails", {}).get("itemType") == kind
                elif a == "job-create":
                    row = client.job(rid, e["report_type_id"])
                    verified = all(response.get(k) == v and row.get(k) == v for k, v in change.after.items())
        _event(change, "readback", matched=verified, limitation="receipt required; missing/partial lists do not prove absence")
    except (ResourceError, TypeError, KeyError, AttributeError):
        _event(change, "readback_unavailable")
    if verified:
        change.status, change.verified = "verified", True
    else:
        change.status = "uncertain"
    store.save(change)
    return change


def reconcile(client, store, change_id):
    with store.apply_lock(change_id):
        change = store.load(change_id)
        identity(client, change)
        if change.status in {"applying", "uncertain"}:
            return readback(client, store, change)
        return change


def apply(client, store, change_id, approval):
    with ExitStack() as locks:
        locks.enter_context(store.apply_lock(change_id))
        change = store.load(change_id)
        if not hmac.compare_digest(approval, change.fingerprint):
            raise ApprovalMismatch("approval does not match exact immutable fingerprint")
        locks.enter_context(store.apply_lock(str(uuid.uuid5(uuid.NAMESPACE_URL, change.target_account + ":analytics-effects"))))
        identity(client, change)
        if change.status in {"applying", "uncertain"}:
            return readback(client, store, change)
        if change.status != "proposed":
            return change
        validate_edit(change.edit)
        scopes(client, change.edit)
        for path in store.root.glob("*.json"):
            other = store.load(path.stem)
            if other.id != change.id and other.status in {"applying", "uncertain"} and operation_key(other) == operation_key(change):
                raise ResourceError("uncertain earlier effect blocks a new UUID; reconcile without retry")
        if baseline(client, change.edit) != change.before:
            change.status = "conflict"
            _event(change, "preflight_conflict")
            store.save(change)
            raise ResourceError("remote baseline changed; exact approval no longer applies")
        change.status = "applying"
        _event(change, "write_intent", approval_digest=approval)
        store.save(change)
        try:
            change.receipt, change.result_id = mutate(client, change)
        except ResourceRejected as exc:
            change.status = "failed"
            _event(change, "write_rejected", http_status=exc.http_status)
        except Exception:
            change.status = "uncertain"
            _event(change, "write_uncertain")
        else:
            change.status = "uncertain"
            _event(change, "write_receipt", result_id=change.result_id)
        # Receipt is durable before any verification request.
        store.save(change)
        return readback(client, store, change) if change.status == "uncertain" else change
