"""Immutable Meta proposals and durable operation receipts, independent of queues."""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from socialctl.management.changes import now_utc
from socialctl.management.resource_changes import ResourceStore
from socialctl.management.meta_schema import MetaError, MetaRejected, MetaUncertain, validate_edit


class MetaChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    version: Literal[1] = 1
    kind: Literal["meta-management"] = "meta-management"
    brand: str
    brand_root: str
    platform: Literal["facebook", "instagram"]
    identity: dict
    edit: dict
    before: dict
    created_at: datetime
    updated_at: datetime
    fingerprint: str = ""
    status: Literal["proposed", "applying", "uncertain", "rejected", "conflict", "verified", "accepted_unverifiable"] = "proposed"
    receipt: dict | None = None
    result_id: str | None = None
    native_schedule: dict | None = None
    phases: list[dict] = Field(default_factory=list)
    journal: list[dict] = Field(default_factory=list)


def fingerprint(change):
    raw = change.model_dump(mode="json", exclude={"fingerprint", "updated_at", "status", "receipt", "result_id", "native_schedule", "phases", "journal"})
    return hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


class MetaStore(ResourceStore):
    model_type = MetaChange
    fingerprint_for = staticmethod(fingerprint)

    def __init__(self, brand_root):
        self.root = Path(brand_root) / ".socialctl" / "meta-management"


def event(change, name, **details):
    change.updated_at = now_utc()
    change.journal.append({"at": change.updated_at.isoformat(), "event": name, **details})


def prepare_meta(client, store, raw):
    edit = validate_edit(raw)
    before = client.snapshot(edit)
    moment = now_utc()
    change = MetaChange(id=str(uuid.uuid4()), brand=client.brand.nombre,
        brand_root=str(client.brand.raiz.resolve()), platform=client.platform.value,
        identity=client.identity(user_required=edit.action == "media-delete"),
        edit=edit.model_dump(exclude_none=True), before=before, created_at=moment, updated_at=moment)
    change.fingerprint = fingerprint(change)
    event(change, "prepared")
    store.save(change)
    return change


def bound(client, change):
    if (change.brand, change.brand_root, change.platform) != (client.brand.nombre, str(client.brand.raiz.resolve()), client.platform.value):
        raise MetaError("la marca/cuenta no coincide con la propuesta")
    edit = validate_edit(change.edit)
    if client.identity(user_required=edit.action == "media-delete") != change.identity:
        raise MetaError("el actor/cuenta del token cambió desde la propuesta")
    return edit


def operation_key(change):
    return json.dumps([change.brand_root, change.platform, change.identity["account_id"], change.edit["target_id"]])


def readback(client, store, change):
    edit = bound(client, change)
    if change.status in {"verified", "rejected", "conflict", "proposed"}:
        return change
    try:
        if edit.action.endswith("-delete") or edit.action == "mention-reply" or edit.action == "schedule-cancel":
            # A DELETE receipt confirms acceptance; an inaccessible node cannot
            # independently prove absence. Preserve that distinction indefinitely.
            if change.receipt:
                change.status = "accepted_unverifiable"
            event(change, "withdrawal_receipt_not_independent_absence_proof")
        else:
            current = client.snapshot(edit)
            expected = client.expected(edit, change.before)
            if current == expected and (change.receipt or current != change.before):
                change.status = "verified"
                event(change, "readback_verified")
            else:
                event(change, "readback_mismatch_no_retry")
    except MetaError:
        event(change, "readback_unavailable")
    store.save(change)
    return change


def reconcile_meta(client, store, change_id):
    with store.apply_lock(change_id):
        change = store.load(change_id)
        if change.status == "applying":
            change.status = "uncertain"
        return readback(client, store, change)


def apply_meta(client, store, change_id, approval_digest):
    with ExitStack() as locks:
        locks.enter_context(store.apply_lock(change_id))
        change = store.load(change_id)
        if not hmac.compare_digest(change.fingerprint, approval_digest):
            raise MetaError("la aprobación no coincide con la huella exacta")
        edit = bound(client, change)
        key = operation_key(change)
        locks.enter_context(store.apply_lock(str(uuid.uuid5(uuid.NAMESPACE_URL, key))))
        if change.status != "proposed":
            if change.status == "applying":
                change.status = "uncertain"
            return readback(client, store, change)
        for path in store.root.glob("*.json"):
            other = store.load(path.stem)
            if other.id != change.id and operation_key(other) == key and other.status in {"applying", "uncertain"}:
                raise MetaError(f"resultado incierto pendiente {other.id}; no se elude con otro UUID")
        if client.snapshot(edit) != change.before:
            change.status = "conflict"
            event(change, "remote_changed_no_write")
            store.save(change)
            raise MetaError("el recurso cambió desde el preview")
        change.status = "applying"
        event(change, "write_intent", approval_digest=approval_digest)
        store.save(change)
        try:
            change.receipt = client.mutate(edit)
        except MetaRejected:
            change.status = "rejected"
            event(change, "write_rejected")
        except Exception:
            change.status = "uncertain"
            event(change, "write_uncertain_no_retry")
        else:
            change.status = "uncertain"
            if edit.action in {"schedule-reprogram", "schedule-cancel"}:
                change.native_schedule = {
                    "remote_id": edit.target_id,
                    "planner_visibility": "not_verifiable",
                }
            change.result_id = change.receipt.get("id", edit.target_id)
            event(change, "write_receipt_saved")
        store.save(change)
        return readback(client, store, change)
