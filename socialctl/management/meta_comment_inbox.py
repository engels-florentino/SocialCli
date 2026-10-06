"""Incremental Meta comment inbox backed by explicit, approved reply ChangeSets."""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from socialctl.management.changes import now_utc
from socialctl.management.meta_comments import (
    CommentCursorRejected,
    CommentError,
    CommentStore,
    MetaCommentsClient,
    apply_comment,
    prepare_comment,
    reconcile_comment,
)


CommentState = Literal["observado", "draft", "aprobado", "respondido", "incierto", "bloqueado"]
CommentKind = Literal["pregunta", "elogio", "correccion", "spam", "otro"]
CURSOR_FALLBACK_WINDOW = timedelta(days=7)


class InboxComment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: Literal["facebook", "instagram"]
    account_id: str
    media_id: str
    comment_id: str
    author_id: str
    author_name: str | None = None
    text: str
    created_time: str | None = None
    observed_at: str
    updated_at: str
    language: Literal["es", "en", "und"]
    kind: CommentKind
    status: CommentState = "observado"
    draft_text: str | None = None
    change_id: str | None = None
    draft_change_ids: list[str] = Field(default_factory=list)
    fingerprint: str | None = None
    idempotency_key: str | None = None
    response_id: str | None = None
    last_error: str | None = None
    journal: list[dict] = Field(default_factory=list)


class InboxCursor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: Literal["facebook", "instagram"]
    account_id: str
    media_id: str
    since: str | None = None
    provider_cursor: str | None = None
    last_sync_at: str
    complete: bool


class InboxData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    comments: list[InboxComment] = Field(default_factory=list)
    cursors: list[InboxCursor] = Field(default_factory=list)


def _parse_time(value: str, label: str = "fecha") -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError):
        raise CommentError(f"{label} debe ser RFC3339") from None
    if parsed.tzinfo is None:
        raise CommentError(f"{label} debe incluir zona horaria")
    return parsed.astimezone(timezone.utc)


def _canonical_time(value: str) -> str:
    return _parse_time(value).isoformat().replace("+00:00", "Z")


def classify_comment(text: str) -> tuple[Literal["es", "en", "und"], CommentKind]:
    """Small deterministic triage; it never produces reply copy."""
    lowered = f" {text.casefold()} "
    spanish = (" que ", " qué ", " por qué ", " gracias", "historia", "incorrecto", "error", " fue ")
    english = (" what ", " why ", " thanks", " follow ", "history", "wrong", "actually", " was ")
    es_score = sum(token in lowered for token in spanish) + sum(ch in lowered for ch in "¿¡ñáéíóú")
    en_score = sum(token in lowered for token in english)
    language: Literal["es", "en", "und"] = "es" if es_score > en_score else "en" if en_score else "und"

    urls = len(re.findall(r"(?:https?://|www\.)", lowered))
    if urls >= 2 or any(token in lowered for token in ("sígueme", "follow me", "crypto", "promo code", "gana dinero")):
        kind: CommentKind = "spam"
    elif any(token in lowered for token in (" incorrect", " equivocado", " error", " actually", " wrong", " no fue", " no era")):
        kind = "correccion"
    elif "?" in text or "¿" in text or any(lowered.lstrip().startswith(token) for token in
            ("qué ", "que ", "cómo ", "como ", "cuándo ", "cuando ", "why ", "what ", "how ")):
        kind = "pregunta"
    elif any(token in lowered for token in (" gracias", " excelente", " buen video", " buen vídeo", " me encanta", " thanks", " great", " love this")):
        kind = "elogio"
    else:
        kind = "otro"
    return language, kind


def response_key(account_id: str, comment_id: str, text: str) -> str:
    payload = json.dumps([account_id, comment_id, text], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class CommentInboxStore:
    def __init__(self, brand_root: Path):
        self.root = brand_root / ".socialctl" / "comment-inbox"
        self.path = self.root / "state.json"

    def load(self) -> InboxData:
        if not self.path.exists():
            return InboxData()
        try:
            return InboxData.model_validate_json(self.path.read_text(encoding="utf-8"))
        except (OSError, ValidationError):
            raise CommentError("no se pudo leer el estado durable de comentarios") from None

    def save(self, data: InboxData) -> None:
        temporary = None
        try:
            for directory in (self.root.parent, self.root):
                directory.mkdir(parents=True, exist_ok=True)
                parent = os.open(directory.parent, os.O_RDONLY)
                try:
                    os.fsync(parent)
                finally:
                    os.close(parent)
            fd, temporary = tempfile.mkstemp(prefix="state.", suffix=".tmp", dir=self.root)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data.model_dump(mode="json"), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            descriptor = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
            raise CommentError("no se pudo persistir el estado durable de comentarios") from None

    @contextmanager
    def lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.root / "state.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _cursor(data: InboxData, client: MetaCommentsClient, media_id: str) -> InboxCursor | None:
    rows = [row for row in data.cursors if (row.platform, row.account_id, row.media_id) ==
            (client.platform.value, client.account_id, media_id)]
    if len(rows) > 1:
        raise CommentError("estado durable ambiguo para el cursor del medio")
    return rows[0] if rows else None


def _record(data: InboxData, client: MetaCommentsClient, media_id: str, comment_id: str) -> InboxComment:
    rows = [row for row in data.comments if (row.platform, row.account_id, row.media_id, row.comment_id) ==
            (client.platform.value, client.account_id, media_id, comment_id)]
    if len(rows) != 1:
        raise CommentError("comentario no observado de forma inequívoca en la bandeja")
    return rows[0]


def grouped_inbox(data: InboxData, *, platform: str | None = None, media_id: str | None = None) -> list[dict]:
    groups: dict[tuple[str, str, str, str, str], list[InboxComment]] = {}
    for row in data.comments:
        if platform and row.platform != platform:
            continue
        if media_id and row.media_id != media_id:
            continue
        key = (row.platform, row.media_id, row.author_id, row.language, row.kind)
        groups.setdefault(key, []).append(row)
    result = []
    for key in sorted(groups):
        rows = sorted(groups[key], key=lambda row: (row.created_time or "", row.comment_id))
        result.append({"platform": key[0], "media_id": key[1], "author_id": key[2],
            "language": key[3], "kind": key[4], "comments": [row.model_dump(mode="json") for row in rows]})
    return result


def sync_inbox(client: MetaCommentsClient, store: CommentInboxStore, *, media_id: str,
               since: str | None = None, max_pages: int = 100) -> dict:
    if not 1 <= max_pages <= 100:
        raise CommentError("max_pages debe estar entre 1 y 100")
    media_id = str(media_id)
    current = store.load()
    prior_cursor = _cursor(current, client, media_id)
    explicit_since = _canonical_time(since) if since is not None else None
    effective_since = explicit_since or (prior_cursor.since if prior_cursor else None)
    # An explicit cut-off starts a fresh traversal. A cursor belongs to the
    # older traversal and must never override the caller's requested boundary.
    resume_cursor = (prior_cursor.provider_cursor if since is None and prior_cursor
                     and not prior_cursor.complete else None)
    cursor_recovered = False
    try:
        listing = client.list_comments(media_id, max_pages=max_pages, after=resume_cursor)
    except CommentCursorRejected:
        if resume_cursor is None:
            raise
        # Graph cursors are opaque and may expire. Restart the bounded traversal
        # without `after`, retaining the completed cut-off or a conservative
        # overlap window anchored to the sync that saved the rejected cursor.
        if effective_since is None:
            anchor = _parse_time(prior_cursor.last_sync_at, "fecha del cursor")
            effective_since = (anchor - CURSOR_FALLBACK_WINDOW).isoformat().replace(
                "+00:00", "Z")
        listing = client.list_comments(media_id, max_pages=max_pages, after=None)
        cursor_recovered = True
    now = now_utc().isoformat()
    remote: dict[str, dict] = {}
    for row in listing["data"]:
        ident = row.get("id")
        if not isinstance(ident, str):
            raise CommentError("Meta devolvió un comentario sin ID verificable")
        if ident in remote and remote[ident] != row:
            raise CommentError("Meta devolvió el mismo comentario con datos incompatibles")
        remote[ident] = row

    added = updated = unchanged = skipped_own = skipped_before_since = 0
    with store.lock():
        data = store.load()
        existing = {(row.platform, row.account_id, row.comment_id): row for row in data.comments}
        observed_times = []
        for ident, raw in remote.items():
            author = raw.get("from") or {}
            author_id = author.get("id") if isinstance(author, dict) else None
            if author_id == client.account_id:
                skipped_own += 1
                continue
            field = "message" if client.platform.value == "facebook" else "text"
            text = raw.get(field)
            if not isinstance(text, str) or not isinstance(author_id, str) or not author_id:
                raise CommentError("Meta devolvió texto o autor no verificable")
            key = (client.platform.value, client.account_id, ident)
            previous = existing.get(key)
            created = raw.get("created_time") if client.platform.value == "facebook" else raw.get("timestamp")
            if created is not None:
                created = _canonical_time(created)
                if (effective_since and previous is None and
                        _parse_time(created) < _parse_time(effective_since)):
                    skipped_before_since += 1
                    continue
                observed_times.append(created)
            language, kind = classify_comment(text)
            if previous:
                if previous.media_id != media_id:
                    raise CommentError("un ID remoto ya pertenece a otra pieza en la bandeja")
                if previous.text != text:
                    previous.text, previous.language, previous.kind = text, language, kind
                    previous.updated_at = now
                    previous.journal.append({"at": now, "event": "remote_text_changed"})
                    updated += 1
                else:
                    unchanged += 1
                continue
            name = author.get("name") or author.get("username")
            record = InboxComment(platform=client.platform.value, account_id=client.account_id,
                media_id=media_id, comment_id=ident, author_id=author_id,
                author_name=name if isinstance(name, str) else None, text=text,
                created_time=created, observed_at=now, updated_at=now,
                language=language, kind=kind,
                journal=[{"at": now, "event": "observed_by_incremental_get"}])
            data.comments.append(record)
            existing[key] = record
            added += 1
        cursor = _cursor(data, client, media_id)
        all_times = [row.created_time for row in data.comments
            if (row.platform, row.account_id, row.media_id) ==
               (client.platform.value, client.account_id, media_id) and row.created_time]
        completed_cutoffs = [*all_times, *observed_times]
        if effective_since is not None:
            completed_cutoffs.append(effective_since)
        next_since = max(completed_cutoffs) if listing["complete"] and completed_cutoffs else effective_since
        new_cursor = InboxCursor(platform=client.platform.value, account_id=client.account_id,
            media_id=media_id, since=next_since, provider_cursor=listing.get("next_cursor"),
            last_sync_at=now, complete=listing["complete"])
        if cursor:
            data.cursors[data.cursors.index(cursor)] = new_cursor
        else:
            data.cursors.append(new_cursor)
        store.save(data)
    return {"platform": client.platform.value, "account_id": client.account_id,
        "media_id": media_id, "since_used": effective_since, "next_since": next_since,
        "provider_cursor": listing.get("next_cursor"), "complete": listing["complete"],
        "cursor_recovered": cursor_recovered,
        "new": added, "updated": updated,
        "deduplicated": unchanged + len(listing["data"]) - len(remote),
        "skipped_own": skipped_own, "skipped_before_since": skipped_before_since,
        "groups": grouped_inbox(data, platform=client.platform.value, media_id=media_id)}


def prepare_inbox_draft(client: MetaCommentsClient, comment_store: CommentStore,
                        inbox_store: CommentInboxStore, *, media_id: str,
                        comment_id: str, text: str):
    if not isinstance(text, str) or not text.strip():
        raise CommentError("el borrador debe contener texto explícito no vacío")
    with inbox_store.lock():
        data = inbox_store.load()
        record = _record(data, client, media_id, comment_id)
        key = response_key(client.account_id, comment_id, text)
        if record.status in {"aprobado", "respondido", "incierto"}:
            raise CommentError("el comentario ya tiene una respuesta aprobada o incierta; no se crea otro POST")
        if record.idempotency_key == key and record.change_id:
            return comment_store.load(record.change_id), record
        if any(row is not record and row.idempotency_key == key and
               row.status in {"draft", "aprobado", "respondido", "incierto"} for row in data.comments):
            raise CommentError("respuesta duplicada para la misma cuenta, comentario y texto")
        change = prepare_comment(client, comment_store, media_id=media_id, action="reply",
            parent_id=comment_id, text=text, inbox_binding={
                "platform": client.platform.value, "account_id": client.account_id,
                "media_id": media_id, "comment_id": comment_id,
                "idempotency_key": key})
        now = now_utc().isoformat()
        record.status = "draft"
        record.draft_text = text
        record.change_id = change.id
        record.draft_change_ids.append(change.id)
        record.fingerprint = change.fingerprint
        record.idempotency_key = key
        record.response_id = None
        record.last_error = None
        record.updated_at = now
        record.journal.append({"at": now, "event": "explicit_draft_prepared", "change_id": change.id})
        inbox_store.save(data)
        return change, record


def _recent_attempts(data: InboxData, *, account_id: str, window_seconds: int, at: datetime) -> int:
    cutoff = at - timedelta(seconds=window_seconds)
    count = 0
    for row in data.comments:
        if row.account_id != account_id:
            continue
        for event in row.journal:
            if event.get("event") != "reply_write_intent":
                continue
            try:
                if _parse_time(event["at"], "fecha del diario") >= cutoff:
                    count += 1
            except (CommentError, KeyError):
                raise CommentError("diario de frecuencia inválido") from None
    return count


def apply_inbox_draft(client: MetaCommentsClient, comment_store: CommentStore,
                      inbox_store: CommentInboxStore, *, media_id: str, comment_id: str,
                      approval_digest: str, max_replies: int = 10, window_seconds: int = 3600):
    if max_replies < 1 or window_seconds < 1:
        raise CommentError("los límites de frecuencia deben ser enteros positivos")
    with inbox_store.lock():
        data = inbox_store.load()
        record = _record(data, client, media_id, comment_id)
        if not record.fingerprint or not hmac.compare_digest(approval_digest, record.fingerprint):
            raise CommentError("aprobación no coincide con la huella exacta")
        if record.status == "respondido":
            return record
        if record.status == "incierto":
            raise CommentError("resultado incierto bloqueado; ejecuta reconcile-draft antes de otro intento")
        if record.status not in {"draft", "aprobado", "bloqueado"} or not record.change_id:
            raise CommentError("el comentario no tiene un borrador aplicable")
        now = now_utc()
        attempts = _recent_attempts(data, account_id=client.account_id,
                                    window_seconds=window_seconds, at=now)
        if record.status != "aprobado" and attempts >= max_replies:
            record.status = "bloqueado"
            record.last_error = "frequency_limit"
            record.updated_at = now.isoformat()
            record.journal.append({"at": now.isoformat(), "event": "frequency_limit_blocked",
                "max_replies": max_replies, "window_seconds": window_seconds, "observed_attempts": attempts})
            inbox_store.save(data)
            raise CommentError("límite de frecuencia alcanzado; no se envió ningún POST")
        if record.status != "aprobado":
            record.status = "aprobado"
            record.last_error = None
            record.updated_at = now.isoformat()
            record.journal.append({"at": now.isoformat(), "event": "reply_write_intent",
                "max_replies": max_replies, "window_seconds": window_seconds,
                "observed_attempts": attempts})
            inbox_store.save(data)

    try:
        change = apply_comment(client, comment_store, record.change_id, approval_digest)
    except CommentError as exc:
        # Once the underlying durable intent left `proposed`, a persistence or
        # transport failure may have happened after Meta accepted the POST.
        # Fail closed even when the ChangeSet can no longer be read.
        uncertain = True
        try:
            uncertain = comment_store.load(record.change_id).status != "proposed"
        except CommentError:
            pass
        with inbox_store.lock():
            data = inbox_store.load()
            current = _record(data, client, media_id, comment_id)
            current.status = "incierto" if uncertain else "bloqueado"
            current.last_error = str(exc)
            current.updated_at = now_utc().isoformat()
            current.journal.append({"at": current.updated_at, "event": "sanitized_apply_error"})
            inbox_store.save(data)
        raise
    with inbox_store.lock():
        data = inbox_store.load()
        current = _record(data, client, media_id, comment_id)
        current.response_id = change.remote_id
        current.status = "respondido" if change.status == "verified" else (
            "incierto" if change.status in {"applying", "uncertain", "applied_unverified"} else "bloqueado")
        current.last_error = None if current.status == "respondido" else f"changeset:{change.status}"
        current.updated_at = now_utc().isoformat()
        current.journal.append({"at": current.updated_at, "event": "reply_result",
            "changeset_status": change.status, "response_id": change.remote_id})
        inbox_store.save(data)
        return current


def reconcile_inbox_draft(client: MetaCommentsClient, comment_store: CommentStore,
                          inbox_store: CommentInboxStore, *, media_id: str, comment_id: str):
    with inbox_store.lock():
        data = inbox_store.load()
        record = _record(data, client, media_id, comment_id)
        if not record.change_id:
            raise CommentError("el comentario no tiene un ChangeSet que reconciliar")
        change_id = record.change_id
    change = reconcile_comment(client, comment_store, change_id)
    with inbox_store.lock():
        data = inbox_store.load()
        current = _record(data, client, media_id, comment_id)
        current.response_id = change.remote_id
        current.status = "respondido" if change.status == "verified" else (
            "incierto" if change.status in {"applying", "uncertain", "applied_unverified"} else "bloqueado")
        current.last_error = None if current.status == "respondido" else f"changeset:{change.status}"
        current.updated_at = now_utc().isoformat()
        current.journal.append({"at": current.updated_at, "event": "read_only_reconciliation",
            "changeset_status": change.status})
        inbox_store.save(data)
        return current
