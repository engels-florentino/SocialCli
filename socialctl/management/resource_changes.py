"""Separate durable schema for exact supplied YouTube assets and explicit deletion."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import tempfile
import uuid
import xml.etree.ElementTree as ET
from contextlib import ExitStack, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from socialctl.management.changes import ApprovalMismatch, ChangeError, ChangeStore, now_utc
from socialctl.management.youtube_resources import (
    MAX_CAPTION, ResourceError, ResourceRejected, ResourceConflict, ResourceUncertain, resource_id,
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def caption_format(path, data):
    extension = path.suffix.lower().lstrip(".")
    opaque = {"cap", "tds", "cin", "stl", "asc"}
    if extension in opaque:
        return "application/octet-stream", extension, "extension_only/provider_validation_required"
    allowed = {"srt", "vtt", "sbv", "sub", "mpsub", "lrc", "smi", "sami", "rt", "ttml", "dfxp", "scc"}
    if extension not in allowed:
        raise ResourceError("caption extension undocumented/unsupported")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ResourceError("text format requires supplied UTF-8; no conversion") from None
    if "\x00" in text or re.search(r"<!\s*(DOCTYPE|ENTITY)", text, re.I):
        raise ResourceError("captions: unsupported text or XML declarations")
    patterns = {
        "srt": r"(?m)^\d+\r?\n\d{2}:\d{2}:\d{2},\d{3}\s+-->\s+\d{2}:\d{2}:\d{2},\d{3}",
        "vtt": r"\AWEBVTT(?:\s|$)[\s\S]*\d{2}:\d{2}\.\d{3}\s+-->\s+\d{2}:\d{2}",
        "sbv": r"(?m)^\d+:\d{2}:\d{2}\.\d{2,3},\d+:\d{2}:\d{2}\.\d{2,3}",
        "sub": r"(?m)^\d+:\d{2}:\d{2}\.\d{2,3},\d+:\d{2}:\d{2}\.\d{2,3}",
        "mpsub": r"(?m)^FORMAT=(?:TIME|\d+(?:\.\d+)?)\s*$[\s\S]*^\d+(?:\.\d+)?\s+\d+(?:\.\d+)?\s*$",
        "lrc": r"\[\d{2}:\d{2}(?:\.\d{2,3})?\]",
        "smi": r"(?is)<sami\b[\s\S]*<sync\s+start\s*=\s*[\"']?\d+",
        "sami": r"(?is)<sami\b[\s\S]*<sync\s+start\s*=\s*[\"']?\d+",
        "rt": r"(?is)<window\b[\s\S]*<time\s+begin\s*=",
        "scc": r"\AScenarist_SCC V1\.0[\s\S]*\d{2}:\d{2}:\d{2}[:;]\d{2}\s+[0-9a-fA-F]{4}",
    }
    if extension in {"ttml", "dfxp"}:
        try:
            root = ET.fromstring(text)
            valid = root.tag.rsplit("}", 1)[-1] == "tt" and any(
                el.tag.rsplit("}", 1)[-1] == "p" and el.get("begin") is not None
                and (el.get("end") is not None or el.get("dur") is not None) for el in root.iter())
        except ET.ParseError:
            valid = False
    else:
        valid = re.search(patterns[extension], text) is not None
    if not valid:
        raise ResourceError("captions: unrecognized structure/timestamps; synchronization is not generated")
    mime = "text/xml" if extension in {"ttml", "dfxp"} else "application/octet-stream"
    return mime, extension, "basic_structure/provider_validation_required"


def inspect_asset(path: Path, *, thumbnail: bool):
    """Read a bounded regular supplied file, identify format, keep its exact bytes."""
    path = Path(path).absolute()
    maximum = 2 * 1024 * 1024 if thumbnail else MAX_CAPTION
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum:
                raise ResourceError(f"file is not regular, empty or exceeds {maximum} bytes")
            data = source.read(maximum + 1)
            after = os.fstat(source.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or len(data) != before.st_size:
                raise ResourceError("file changed during read")
    except OSError:
        raise ResourceError("failed to read regular file without symlinks") from None
    if thumbnail:
        if data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR" and data[-8:] == b"IEND\xaeB`\x82":
            mime, format_name = "image/png", "png"
        elif data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"):
            mime, format_name = "image/jpeg", "jpeg"
        else:
            raise ResourceError("thumbnail: PNG or JPEG bytes required; file is not converted")
        validation = "container_signature/provider_validation_required"
    else:
        mime, format_name, validation = caption_format(path, data)
    return {"path": str(path), "size": len(data), "sha256": digest(data), "mime": mime,
            "format": format_name, "local_format_validation": validation}, data


def save_download(path, data):
    """Atomic private no-clobber write of exact downloaded response bytes."""
    path = Path(path).absolute()
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix="caption-", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
        ChangeStore._sync_directory(path.parent)
    except OSError:
        raise ResourceError("failed to save private download; existing files are not replaced") from None
    finally:
        if temporary is not None:
            os.unlink(temporary)


class ResourceChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    version: Literal[1] = 1
    kind: Literal["youtube-supplied-resource"] = "youtube-supplied-resource"
    action: Literal["thumbnail-set", "caption-insert", "caption-update", "caption-delete"]
    target_brand: str
    target_account: str
    video_id: str
    track_id: str | None = None
    language: str | None = None
    name: str | None = None
    draft: bool | None = None
    draft_changed: bool = False
    asset: dict | None = None
    before: dict
    backup: dict
    fingerprint: str = ""
    created_at: datetime
    updated_at: datetime
    status: Literal["proposed", "applying", "uncertain", "failed", "conflict", "verified", "accepted_unverifiable"] = "proposed"
    verified: bool = False
    result_id: str | None = None
    receipt: dict | None = None
    journal: list[dict] = Field(default_factory=list)


def fingerprint(change):
    raw = change.model_dump(mode="json")
    immutable = {k: v for k, v in raw.items() if k not in {
        "fingerprint", "updated_at", "status", "verified", "result_id", "receipt", "journal"}}
    return digest(json.dumps(immutable, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())


class ResourceStore(ChangeStore):
    model_type = ResourceChange
    fingerprint_for = staticmethod(fingerprint)

    def __init__(self, brand_root):
        self.root = Path(brand_root) / ".socialctl" / "youtube-resources"

    def _ensure_root(self):
        for directory in (self.root.parent, self.root):
            if directory.is_symlink():
                raise ResourceError("private storage does not accept symlinks")
            directory.mkdir(mode=0o700, exist_ok=True)
            self._sync_directory(directory.parent)

    @contextmanager
    def apply_lock(self, change_id):
        self._ensure_root()
        if (self.root / f"{self._validate_id(change_id)}.lock").is_symlink():
            raise ResourceError("private lock does not accept symlinks")
        with super().apply_lock(change_id):
            yield

    def backup_path(self, change_id):
        return self.root / f"{self._validate_id(change_id)}.caption-original"

    def save_backup(self, change_id, data):
        self._ensure_root()
        save_download(self.backup_path(change_id), data)


def _event(change, name, **details):
    change.updated_at = now_utc()
    change.journal.append({"at": change.updated_at.isoformat(), "event": name, **details})


def _identity(client, change):
    if client.brand.nombre != change.target_brand or client.configured_channel_id != change.target_account:
        raise ResourceError("brand/account does not match proposal")
    return client.inspect(change.video_id)


def _operation_key(change):
    target = (["new-caption", change.language, change.name] if change.action == "caption-insert"
              else ["caption", change.track_id] if change.track_id else ["thumbnail"])
    return json.dumps([change.target_account, change.video_id, *target], ensure_ascii=False)


def prepare_resource(client, store, *, action, video_id, file=None, track_id=None,
                     language=None, name=None, draft=None):
    if action not in {"thumbnail-set", "caption-insert", "caption-update", "caption-delete"}:
        raise ResourceError("unsupported resource action")
    resource_id(video_id)
    if draft is not None and type(draft) is not bool:
        raise ResourceError("draft must be explicit boolean")
    if action == "caption-insert":
        if track_id is not None or file is None or draft is None or not isinstance(language, str) or not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", language) or not isinstance(name, str) or len(name) > 150:
            raise ResourceError("insert requires file, language, name (maximum 150) and explicit draft; no track ID")
    elif action == "caption-update":
        if track_id is None or language is not None or name is not None or (file is None and draft is None):
            raise ResourceError("update requires track ID and file and/or draft; language/name cannot be edited")
    elif action == "caption-delete":
        if track_id is None or any(v is not None for v in (file, language, name, draft)):
            raise ResourceError("delete requires only explicit track ID")
    elif file is None or any(v is not None for v in (track_id, language, name, draft)):
        raise ResourceError("thumbnail-set requires only a supplied file")
    if track_id is not None:
        resource_id(track_id)
    asset = inspect_asset(file, thumbnail=action == "thumbnail-set")[0] if file is not None else None
    inspected = client.inspect(video_id)
    cid = str(uuid.uuid4())
    draft_changed = draft is not None
    backup = {"fidelity": "not_applicable"}
    if action == "thumbnail-set":
        before = {"etag": inspected.etag, "thumbnails": inspected.snippet.get("thumbnails", {})}
        backup = {"fidelity": "metadata_only_no_original_bytes",
                  "limitation": "API does not expose original thumbnail; public URLs do not guarantee original bytes or faithful restoration."}
    elif action == "caption-insert":
        tracks = client.list_captions(video_id)
        if any(t["snippet"]["language"] == language and t["snippet"]["name"] == name for t in tracks):
            raise ResourceError("conflict: caption with this language and name already exists; no implicit deletion or replacement")
        before = {"etag": inspected.etag, "track_ids": [t["id"] for t in tracks]}
    else:
        before = client.find_caption(video_id, track_id)
        language, name = before["snippet"]["language"], before["snippet"]["name"]
        draft = before["snippet"]["isDraft"] if draft is None else draft
        try:
            original = client.download_caption(video_id, track_id)
        except ResourceError:
            backup = {"fidelity": "unavailable",
                      "limitation": "Failed to download original; permissions/format may prevent recovery. Restoration is not guaranteed."}
        else:
            store.save_backup(cid, original)
            backup = {"fidelity": "api_original_response_bytes", "size": len(original),
                "sha256": digest(original), "track_id": track_id,
                "limitation": "Bytes received without tfmt/tlang. YouTube may normalize them; equality with originally uploaded file is unproven."}
    moment = now_utc()
    change = ResourceChange(id=cid, action=action, target_brand=client.brand.nombre,
        target_account=inspected.channel_id, video_id=video_id, track_id=track_id,
        language=language, name=name, draft=draft, draft_changed=draft_changed,
        asset=asset, before=before, backup=backup, created_at=moment, updated_at=moment)
    _event(change, "prepared")
    change.fingerprint = fingerprint(change)
    store.save(change)
    return change


def _preflight(client, change):
    video = _identity(client, change)
    if change.action == "thumbnail-set":
        if {"etag": video.etag, "thumbnails": video.snippet.get("thumbnails", {})} != change.before:
            raise ResourceConflict("conflict: thumbnail or video changed since preview")
    elif change.action == "caption-insert":
        rows = client.list_captions(change.video_id)
        if any(t["snippet"]["language"] == change.language and t["snippet"]["name"] == change.name for t in rows):
            raise ResourceConflict("conflict: caption with this language/name already exists")
    else:
        if client.find_caption(change.video_id, change.track_id) != change.before:
            raise ResourceConflict("conflict: caption changed since preview")
        if change.backup["fidelity"] == "api_original_response_bytes":
            if digest(client.download_caption(change.video_id, change.track_id)) != change.backup["sha256"]:
                raise ResourceConflict("conflict: remote caption bytes changed")


def _readback(client, store, change):
    """Read-only; never infer insert identity or deletion from missing permissions."""
    try:
        video = _identity(client, change)
        if change.action == "thumbnail-set":
            _event(change, "thumbnail_readback", thumbnails=video.snippet.get("thumbnails", {}),
                fidelity="metadata_only_not_visual_or_byte_verification")
            if change.receipt is not None:
                change.status = "accepted_unverifiable"
        elif change.action == "caption-delete":
            rows = client.list_captions(change.video_id)
            absent = not any(t["id"] == change.track_id for t in rows)
            _event(change, "delete_readback", absent_in_list=absent,
                confirmed_delete=bool(change.receipt and change.receipt.get("http_status") == 204))
            if absent and change.receipt and change.receipt.get("http_status") == 204:
                change.status, change.verified = "verified", True
        elif change.result_id and change.receipt and change.receipt.get("identity_valid"):
            row = client.find_caption(change.video_id, change.result_id)
            snip = row["snippet"]
            matches = all(snip.get(k) == v for k, v in {"language": change.language, "name": change.name, "isDraft": change.draft}.items())
            if snip.get("status") == "failed":
                _event(change, "caption_processing_failed")
                change.status = "failed"
            else:
                byte_match = (digest(client.download_caption(change.video_id, change.result_id)) == change.asset["sha256"]
                    if change.asset else True)
                _event(change, "caption_readback", metadata_match=matches,
                    original_format_bytes_match=byte_match, processing_status=snip.get("status"))
                if matches and byte_match and snip.get("status") == "serving":
                    change.status, change.verified = "verified", True
        else:
            rows = client.list_captions(change.video_id)
            candidates = [t["id"] for t in rows if t["snippet"]["language"] == change.language and t["snippet"]["name"] == change.name]
            _event(change, "identity_unresolved", candidate_ids=candidates,
                limitation="Candidates do not prove which insertion produced resource; it will not be reinserted.")
    except ResourceError as exc:
        _event(change, "readback_unavailable", error=str(exc))
    store.save(change)
    return change


def reconcile_resource(client, store, change_id):
    with store.apply_lock(change_id):
        change = store.load(change_id)
        _identity(client, change)
        if change.status in {"applying", "uncertain", "accepted_unverifiable"}:
            if change.status == "applying":
                change.status = "uncertain"
            return _readback(client, store, change)
        return change


def apply_resource(client, store, change_id, approval_digest):
    with ExitStack() as locks:
        locks.enter_context(store.apply_lock(change_id))
        change = store.load(change_id)
        if not hmac.compare_digest(approval_digest, change.fingerprint):
            raise ApprovalMismatch("approval does not match exact fingerprint")
        # Serialize separate proposals for the same resource, not just retries of
        # one UUID. A new UUID is not an escape hatch from an uncertain insertion.
        key = _operation_key(change)
        locks.enter_context(store.apply_lock(str(uuid.uuid5(uuid.NAMESPACE_URL, key))))
        _identity(client, change)
        if change.status in {"applying", "uncertain", "accepted_unverifiable"}:
            if change.status == "applying":
                change.status = "uncertain"
            return _readback(client, store, change)
        if change.status != "proposed":
            return change
        for path in store.root.glob("*.json"):
            other = store.load(path.stem)
            if other.id != change.id and _operation_key(other) == key and other.status in {"applying", "uncertain"}:
                raise ResourceError(f"uncertain outcome pending in {other.id}; write will not be repeated with another proposal")
        try:
            _preflight(client, change)
        except ResourceConflict:
            change.status = "conflict"
            _event(change, "pre_write_conflict")
            store.save(change)
            raise
        if change.backup["fidelity"] == "api_original_response_bytes":
            backup_path = store.backup_path(change.id)
            if backup_path.is_symlink() or not backup_path.is_file() or digest(backup_path.read_bytes()) != change.backup["sha256"]:
                raise ResourceError("local caption backup changed or is missing; new proposal required")
        data = None
        if change.asset:
            asset, data = inspect_asset(Path(change.asset["path"]), thumbnail=change.action == "thumbnail-set")
            if asset != change.asset:
                raise ResourceError("file or bytes changed since preview; new fingerprint required")
        # The in-memory bytes checked after ownership reads are the bytes sent,
        # even if the source pathname changes afterward. No reopen at upload time.
        change.status = "applying"
        _event(change, "write_intent", approval_digest=approval_digest)
        store.save(change)
        try:
            response = client.mutate(change, data)
        except ResourceRejected as exc:
            change.status = "conflict" if isinstance(exc, ResourceConflict) else "failed"
            _event(change, "write_rejected", error=str(exc))
        except ResourceUncertain as exc:
            change.status = "uncertain"
            _event(change, "write_uncertain", error=str(exc))
        else:
            change.status = "uncertain"
            # Persist any valid response ID before subsequent validation/read-back.
            if change.action in {"caption-insert", "caption-update"}:
                try:
                    change.result_id = resource_id(response.get("id"))
                except ResourceError:
                    change.result_id = None
                snippet = response.get("snippet", {})
                identity_valid = bool(change.result_id) and (change.action == "caption-insert" or change.result_id == change.track_id)
                if change.action == "caption-insert":
                    identity_valid = identity_valid and isinstance(snippet, dict) and all(
                        snippet.get(k) == v for k, v in {"videoId": change.video_id, "language": change.language,
                        "name": change.name, "isDraft": change.draft}.items())
                change.receipt = {"id": change.result_id, "identity_valid": identity_valid}
            elif change.action == "caption-delete":
                change.receipt = response
            elif isinstance(response.get("items"), list) and response["items"] and isinstance(response.get("etag"), str):
                change.receipt = {"etag": response["etag"], "accepted": True}
            _event(change, "write_response", result_id=change.result_id, receipt=change.receipt)
        store.save(change)
        if change.status == "uncertain":
            return _readback(client, store, change)
        return change
