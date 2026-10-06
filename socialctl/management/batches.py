"""Ordered immutable proposals with independent, durable per-video outcomes."""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from socialctl.management.changes import ApprovalMismatch, ChangeError, ChangeStore, now_utc
from socialctl.management.metadata_parts import KINDS, MetadataEdit, editable_parts, propose_parts
from socialctl.management.youtube import (YouTubeManagementError, YouTubeUpdateConflict,
    YouTubeUpdateRejected)


class BatchFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[2]
    platform: Literal["youtube"]
    operations: list[MetadataEdit] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_videos(self):
        ids = [edit.video_id for edit in self.operations]
        if len(ids) != len(set(ids)):
            raise ChangeError("duplicate video_id: one operation per video and proposal")
        return self


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str
    video_id: str
    kind: str
    patch: dict[str, Any]
    never_published: bool = False
    observed: dict[str, Any]
    before: dict[str, Any]
    after: dict[str, Any]
    resets: list[str] = Field(default_factory=list)
    fingerprint: str = ""
    status: Literal["pending", "applying", "applied", "failed", "conflict", "uncertain", "manual_review"] = "pending"
    journal: list[dict[str, str]] = Field(default_factory=list)


class Batch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str
    version: Literal[2] = 2
    platform: Literal["youtube"] = "youtube"
    brand: str
    brand_root: str
    account: str
    account_config: dict[str, Any]
    accounts_digest: str
    source_files: dict[str, str]
    restored_from: str | None = None
    unavailable: list[str] = Field(default_factory=list)
    operations: list[Operation]
    created_at: str
    fingerprint: str = ""


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def operation_fingerprint(op: Operation) -> str:
    return digest(op.model_dump(exclude={"fingerprint", "status", "journal"}))


def batch_fingerprint(batch: Batch) -> str:
    payload = batch.model_dump(exclude={"fingerprint", "operations"})
    payload["operations"] = [op.model_dump(exclude={"status", "journal"}) for op in batch.operations]
    return digest(payload)


class BatchStore(ChangeStore):
    model_type = Batch
    fingerprint_for = staticmethod(batch_fingerprint)

    def __init__(self, brand_root: Path):
        self.root = brand_root / ".socialctl" / "metadata-batches"

    def load(self, change_id: str) -> Batch:
        batch = super().load(change_id)
        ids = [op.video_id for op in batch.operations]
        if len(ids) != len(set(ids)) or not ids:
            raise ChangeError("empty or duplicate membership")
        for op in batch.operations:
            self._validate_id(op.id)
            if not hmac.compare_digest(op.fingerprint, operation_fingerprint(op)):
                raise ChangeError("operation was altered")
            _edit(op)
        return batch


def _file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        raise ChangeError("linked file missing or unreadable; approval invalidated") from None


def _edit(op: Operation) -> MetadataEdit:
    try:
        return MetadataEdit(video_id=op.video_id, kind=op.kind, patch=op.patch, never_published=op.never_published)
    except ValidationError as exc:
        raise ChangeError(f"invalid operation: {exc}") from None


def _operation(client, edit, *, resets=None):
    observed = client.inspect(edit.video_id)
    before, after = propose_parts(observed, edit)
    op = Operation(id=str(uuid.uuid4()), video_id=edit.video_id, kind=edit.kind,
        patch=copy.deepcopy(edit.patch), never_published=edit.never_published,
        observed=copy.deepcopy(observed.resource), before=before, after=after, resets=resets or [])
    op.fingerprint = operation_fingerprint(op)
    return op


def _batch(client, operations, source_files, *, restored_from=None, unavailable=None):
    return Batch(id=str(uuid.uuid4()), brand=client.brand.nombre,
        brand_root=str(client.brand.raiz.resolve()), account=client.configured_channel_id,
        account_config=copy.deepcopy(client.brand.cuentas.get("youtube") or {}),
        accounts_digest=_file_digest(client.brand.raiz / "accounts.yml"),
        operations=operations, source_files=source_files, restored_from=restored_from,
        unavailable=unavailable or [], created_at=now_utc().isoformat())


def _save_new(store, batch):
    if not batch.operations:
        raise ChangeError("restoration unavailable: " + "; ".join(batch.unavailable))
    batch.fingerprint = batch_fingerprint(batch)
    store.save(batch)
    return batch


def prepare_batch(client, store: BatchStore, file: Path) -> Batch:
    path = file.resolve()
    fingerprint = _file_digest(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        edits = BatchFile.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise ChangeError(f"invalid V2 file: {exc}") from None
    operations = [_operation(client, edit) for edit in edits.operations]
    if _file_digest(path) != fingerprint:
        raise ChangeError("file modified during preview")
    return _save_new(store, _batch(client, operations, {str(path): fingerprint}))


def _bound(client, batch):
    if (batch.brand, batch.brand_root, batch.account, batch.accounts_digest, batch.account_config) != (
        client.brand.nombre, str(client.brand.raiz.resolve()), client.configured_channel_id,
        _file_digest(client.brand.raiz / "accounts.yml"), client.brand.cuentas.get("youtube") or {}):
        raise ChangeError("brand/account/accounts.yml changed; approval invalidated")
    for path, expected in batch.source_files.items():
        if not hmac.compare_digest(_file_digest(Path(path)), expected):
            raise ChangeError("source file changed; approval invalidated")


def _event(op, event, **details):
    op.journal.append({"at": now_utc().isoformat(), "event": event, **details})


def _matches(current, expected):
    parts = editable_parts(current.resource)
    for part, values in expected.items():
        actual, wanted = copy.deepcopy(parts[part]), copy.deepcopy(values)
        if part == "status" and "publishAt" in actual and "publishAt" in wanted:
            # YouTube commonly canonicalizes an offset timestamp such as
            # ``2026-09-21T18:00:00-04:00`` to the equivalent UTC ``Z`` form.
            # Compare the instants, not their spelling, when reconciling a
            # successful native schedule.
            try:
                actual["publishAt"] = datetime.fromisoformat(
                    actual["publishAt"].replace("Z", "+00:00")
                ).astimezone(timezone.utc)
                wanted["publishAt"] = datetime.fromisoformat(
                    wanted["publishAt"].replace("Z", "+00:00")
                ).astimezone(timezone.utc)
            except (AttributeError, TypeError, ValueError):
                pass
        if part == "snippet":
            # The API may omit cleared tags/description. Only these two
            # explicitly supported empty-value resets have this equivalence.
            for field, empty in (("tags", []), ("description", "")):
                actual.setdefault(field, empty)
                wanted.setdefault(field, empty)
        if actual != wanted:
            return False
    return True


def _apply_operation(client, store, batch, op):
    if op.status not in {"pending", "applying", "uncertain"}:
        return
    try:
        current = client.inspect(op.video_id)
    except (YouTubeManagementError, ChangeError):
        _event(op, "read_failed", error="read/ownership unverifiable; no write performed")
        store.save(batch)
        return
    if op.status in {"applying", "uncertain"}:
        op.status = "applied" if _matches(current, op.after) else "manual_review"
        _event(op, "reconciled_read_only")
        store.save(batch)
        return
    if current.resource != op.observed:
        op.status = "conflict"
        _event(op, "observed_state_changed")
        store.save(batch)
        return
    # Revalidate time-sensitive scheduling and the exact outgoing parts.
    try:
        _, after = propose_parts(current, _edit(op))
    except ChangeError:
        op.status = "failed"
        _event(op, "proposal_no_longer_valid", error="operation conditions no longer valid; prepare another preview")
        store.save(batch)
        return
    if after != op.after:
        raise ChangeError("cuerpo propuesto alterado")
    _bound(client, batch)
    op.status = "applying"
    _event(op, "write_intent", approval=batch.fingerprint, if_match=current.etag)
    store.save(batch)  # durable intent MUST finish before PUT
    try:
        client.update_parts(op.video_id, op.after, etag=current.etag)
    except YouTubeUpdateConflict:
        op.status = "conflict"
        _event(op, "etag_conflict")
    except YouTubeUpdateRejected:
        op.status = "failed"
        _event(op, "write_rejected")
    except Exception:
        op.status = "uncertain"
        _event(op, "write_uncertain")
    else:
        try:
            verified = client.inspect(op.video_id)
            op.status = "applied" if _matches(verified, op.after) else "uncertain"
            _event(op, "read_back_" + op.status)
        except Exception:
            op.status = "uncertain"
            _event(op, "read_back_unavailable")
    store.save(batch)


def apply_batch(client, store: BatchStore, batch_id: str, approval_digest: str) -> Batch:
    with store.apply_lock(batch_id):
        batch = store.load(batch_id)
        if not hmac.compare_digest(batch.fingerprint, approval_digest):
            raise ApprovalMismatch("approval does not match exact batch fingerprint")
        _bound(client, batch)
        for op in batch.operations:
            _apply_operation(client, store, batch, op)
        return batch


def prepare_restore(client, store: BatchStore, original_id: str) -> Batch:
    """New proposal, saved edited fields only; unavailable resets stay explicit."""
    source = store.path_for(original_id)
    if source.exists():
        original = store.load(original_id)
        if (original.brand, original.brand_root, original.account) != (
            client.brand.nombre, str(client.brand.raiz.resolve()), client.configured_channel_id):
            raise ChangeError("original brand/account mismatch")
        rows = [(op.video_id, op.kind, op.patch, op.before) for op in original.operations]
    else:
        legacy_store = ChangeStore(client.brand.raiz)
        original = legacy_store.load(original_id)
        if original.target_account != client.configured_channel_id:
            raise ChangeError("original account mismatch")
        source = legacy_store.path_for(original_id)
        rows = [(original.video_id, "snippet", original.patch, {"snippet": original.before})]
    source_hash = _file_digest(source)
    operations, unavailable = [], []
    for video_id, kind, patch, before in rows:
        part = KINDS[kind][0]
        recovered, resets = {}, []
        for field in patch:
            old = before[part].get(field)
            if old is not None:
                if field == "publishAt":
                    unavailable.append(f"{video_id}.publishAt: history/date require a new schedule declaration")
                else:
                    recovered[field] = copy.deepcopy(old)
            elif part == "snippet" and field in {"tags", "description"}:
                recovered[field] = [] if field == "tags" else ""
                resets.append(f"{field}: {'empty list' if field == 'tags' else 'empty text'} (original absent/null)")
            else:
                unavailable.append(f"{video_id}.{field}: original value missing/null; reset unverified, current value preserved")
        if recovered:
            restored_kind = "snippet" if kind == "chapters" else "privacy" if kind == "schedule" else kind
            edit = MetadataEdit(video_id=video_id, kind=restored_kind, patch=recovered)
            operations.append(_operation(client, edit, resets=resets))
    return _save_new(store, _batch(client, operations, {str(source): source_hash},
        restored_from=original_id, unavailable=unavailable))
