"""Durable manual Facebook Groups queue. The retired Groups API is not used; approval prepares a package, and only user confirmation after browser publication completes it."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Literal, NamedTuple
from urllib.parse import parse_qs, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from socialctl.hosted_media import file_digest
from socialctl.management.changes import ApprovalMismatch, ChangeError, ChangeStore, now_utc
from socialctl.media import MediaInvalida, _ffprobe, _rotacion_grados
from socialctl.rutas import validar_ruta_relativa


class GroupError(ChangeError):
    """Readable error for a manual Group proposal."""


CONFIRMATION_PHRASE = "I CONFIRM MANUAL PUBLICATION"
LEGACY_CONFIRMATION_PHRASE = "CONFIRMO PUBLICADO MANUALMENTE"
CAPABILITY = "facebook_groups_api_removed_manual_browser_handoff"
_GROUP_SEGMENT = re.compile(r"^[A-Za-z0-9._-]{1,200}$")
_POST_SEGMENT = re.compile(r"^[A-Za-z0-9._-]{1,300}$")
_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_SECRET_PATTERNS = (
    re.compile(
        r"(?i)\b(?:access[_ -]?token|client[_ -]?secret|api[_ -]?key|authorization)"
        r"\s*[:=]\s*[^\s,;]{6,}"
    ),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{8,}"),
    re.compile(r"\b(?:sk-|gh[pousr]_|xox[baprs]-)[A-Za-z0-9_-]{12,}"),
    re.compile(r"\bEA[A-Za-z0-9]{20,}"),
)


def _aware(value: datetime, field: str) -> datetime:
    if value.utcoffset() is None:
        raise GroupError(f"{field} must include a timezone")
    return value


def _plain_text(value: str, field: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise GroupError(f"{field} must be nonempty text without surrounding whitespace")
    if len(value) > maximum:
        raise GroupError(f"{field} exceeds local limit of {maximum} characters")
    if any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise GroupError(f"{field} contains control characters")
    if any(pattern.search(value) for pattern in _SECRET_PATTERNS):
        raise GroupError(f"{field} may contain a secret or credential; rejected")
    return value


def _safe_https_parts(value: str, field: str):
    value = _plain_text(value, field, maximum=2048)
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise GroupError(f"{field} is not a valid URL") from None
    if parsed.scheme != "https" or not parsed.hostname:
        raise GroupError(f"{field} must be a public HTTPS URL")
    if parsed.username is not None or parsed.password is not None or port is not None:
        raise GroupError(f"{field} does not accept credentials or ports")
    if parsed.fragment:
        raise GroupError(f"{field} does not accept fragments")
    return parsed


def validate_group_url(value: str) -> tuple[str, str]:
    """Return canonical Group URL and exact slug/ID."""
    parsed = _safe_https_parts(value, "group_url")
    host = (parsed.hostname or "").lower()
    if host not in {"facebook.com", "www.facebook.com", "m.facebook.com"}:
        raise GroupError("group_url must point to a public Group on facebook.com")
    if parsed.query:
        raise GroupError("group_url does not accept query parameters; remove tracking or credentials")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2 or parts[0].lower() != "groups" or not _GROUP_SEGMENT.fullmatch(parts[1]):
        raise GroupError("group_url must have the form https://www.facebook.com/groups/GROUP")
    canonical = urlunsplit(("https", "www.facebook.com", f"/groups/{parts[1]}", "", ""))
    return canonical, parts[1]


def validate_youtube_url(value: str) -> tuple[str, str]:
    """Accept canonical long-form video links, never API endpoints."""
    parsed = _safe_https_parts(value, "youtube_url")
    host = (parsed.hostname or "").lower()
    video_id: str | None = None
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        if parsed.path != "/watch":
            raise GroupError("youtube_url must point to /watch?v=VIDEO_ID")
        query = parse_qs(parsed.query, keep_blank_values=True)
        if set(query) != {"v"} or len(query["v"]) != 1:
            raise GroupError("youtube_url accepts only the v parameter; remove tracking or credentials")
        video_id = query["v"][0]
    elif host == "youtu.be":
        if parsed.query:
            raise GroupError("short youtube_url does not accept parameters")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 1:
            raise GroupError("short youtube_url must have the form https://youtu.be/VIDEO_ID")
        video_id = parts[0]
    else:
        raise GroupError("youtube_url must use youtube.com or youtu.be; private endpoints are not accepted")
    if not _YOUTUBE_ID.fullmatch(video_id or ""):
        raise GroupError("youtube_url does not contain a valid YouTube video ID")
    return f"https://www.youtube.com/watch?v={video_id}", video_id


def validate_post_url(value: str, expected_group_key: str) -> str:
    parsed = _safe_https_parts(value, "post_url")
    if (parsed.hostname or "").lower() not in {"facebook.com", "www.facebook.com", "m.facebook.com"}:
        raise GroupError("post_url must point to facebook.com")
    if parsed.query:
        raise GroupError("post_url does not accept query parameters; remove tracking or credentials")
    parts = [part for part in parsed.path.split("/") if part]
    if (
        len(parts) != 4
        or parts[0].lower() != "groups"
        or parts[1] != expected_group_key
        or parts[2].lower() not in {"posts", "permalink"}
        or not _POST_SEGMENT.fullmatch(parts[3])
    ):
        raise GroupError("post_url must be a post/permalink URL from the same approved Group")
    return urlunsplit(("https", "www.facebook.com", "/" + "/".join(parts), "", ""))


class GroupDestination(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    url: str
    key: str


class SuggestedWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: datetime
    end: datetime

    @model_validator(mode="after")
    def validate_window(self):
        _aware(self.start, "window_start")
        _aware(self.end, "window_end")
        if self.end <= self.start:
            raise ValueError("window_end must be after window_start")
        return self


class GroupMedia(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relative_path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(gt=0)
    kind: Literal["image", "video"]
    width: int | None = None
    height: int | None = None
    duration_s: float | None = None

    @model_validator(mode="after")
    def validate_visual_metadata(self):
        if (
            not isinstance(self.width, int)
            or not isinstance(self.height, int)
            or self.width <= 0
            or self.height <= 0
        ):
            raise ValueError("Group media requires positive visual dimensions")
        if self.kind == "image" and self.duration_s is not None:
            raise ValueError("Group image does not accept duration")
        if self.kind == "video" and (
            self.duration_s is None or self.duration_s <= 0
        ):
            raise ValueError("Group video requires positive duration")
        return self


class GroupEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relative_path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(gt=0)


class GroupConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authority: Literal["user_supplied_manual_confirmation"] = "user_supplied_manual_confirmation"
    confirmed_at: datetime
    post_url: str | None = None
    evidence: GroupEvidence | None = None


class GroupChange(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)

    id: str
    version: Literal[1] = 1
    kind: Literal["facebook-group-manual"] = "facebook-group-manual"
    brand: str
    brand_root: str
    destination: GroupDestination
    copy_text: str = Field(alias="copy", serialization_alias="copy")
    youtube_url: str
    youtube_video_id: str
    media: GroupMedia | None = None
    suggested_window: SuggestedWindow
    capability: Literal["facebook_groups_api_removed_manual_browser_handoff"] = CAPABILITY
    dedupe_key: str
    created_at: datetime
    updated_at: datetime
    fingerprint: str = ""
    status: Literal[
        "prepared", "approved", "handoff_ready", "awaiting_manual_confirmation", "confirmed_manual"
    ] = "prepared"
    approved_at: datetime | None = None
    handoff_opened_at: datetime | None = None
    confirmation: GroupConfirmation | None = None
    last_error: str | None = None
    journal: list[dict] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_state(self):
        if self.status == "confirmed_manual" and self.confirmation is None:
            raise ValueError("confirmed_manual requires user confirmation")
        if self.status != "confirmed_manual" and self.confirmation is not None:
            raise ValueError("evidence can only exist after manual confirmation")
        if self.status == "awaiting_manual_confirmation" and self.handoff_opened_at is None:
            raise ValueError("awaiting_manual_confirmation requires an open handoff")
        return self


_MUTABLE = {
    "fingerprint", "updated_at", "status", "approved_at", "handoff_opened_at",
    "confirmation", "last_error", "journal",
}


def group_fingerprint(change: GroupChange) -> str:
    raw = change.model_dump(mode="json", by_alias=True, exclude=_MUTABLE)
    encoded = json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class GroupStore(ChangeStore):
    model_type = GroupChange
    fingerprint_for = staticmethod(group_fingerprint)

    def __init__(self, brand_root: Path):
        self.root = Path(brand_root) / ".socialctl" / "facebook-groups"


def _event(change: GroupChange, name: str, **details) -> None:
    change.updated_at = now_utc()
    change.journal.append({"at": change.updated_at.isoformat(), "event": name, **details})


def _dedupe_payload(
    brand: str, destination: GroupDestination, copy: str, youtube_url: str,
    media: GroupMedia | None, window: SuggestedWindow,
) -> str:
    raw = {
        "brand": brand,
        "group_url": destination.url,
        "copy": copy,
        "youtube_url": youtube_url,
        "media_sha256": media.sha256 if media else None,
        "window": window.model_dump(mode="json"),
    }
    return hashlib.sha256(
        json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _load_bound(brand, store: GroupStore, change_id: str) -> GroupChange:
    change = store.load(change_id)
    if change.brand != brand.nombre or Path(change.brand_root) != brand.raiz.resolve():
        raise GroupError("configured brand no longer matches Group ChangeSet")
    name = _plain_text(change.destination.name, "group_name", maximum=200)
    group_url, group_key = validate_group_url(change.destination.url)
    copy = _plain_text(change.copy_text, "copy", maximum=10_000)
    youtube_url, video_id = validate_youtube_url(change.youtube_url)
    if (
        name != change.destination.name
        or group_url != change.destination.url
        or group_key != change.destination.key
        or copy != change.copy_text
        or youtube_url != change.youtube_url
        or video_id != change.youtube_video_id
    ):
        raise GroupError("canonical ChangeSet contract mismatch")
    if change.media is not None:
        relative = change.media.relative_path
        if ".secrets" in Path(relative).parts:
            raise GroupError("persisted media cannot point to .secrets")
        validar_ruta_relativa(
            brand.raiz / "media", relative, GroupError, "persisted Group media"
        )
    if change.confirmation is not None:
        if change.confirmation.confirmed_at.utcoffset() is None:
            raise GroupError("confirmed_at must include a timezone")
        if change.confirmation.post_url is not None:
            canonical_post = validate_post_url(change.confirmation.post_url, group_key)
            if canonical_post != change.confirmation.post_url:
                raise GroupError("persisted confirmed URL is not canonical")
        if change.confirmation.evidence is not None:
            evidence_path = change.confirmation.evidence.relative_path
            if ".secrets" in Path(evidence_path).parts:
                raise GroupError("persisted evidence cannot point to .secrets")
            validar_ruta_relativa(brand.raiz, evidence_path, GroupError, "persisted evidence")
    expected = _dedupe_payload(
        change.brand, change.destination, change.copy_text, change.youtube_url,
        change.media, change.suggested_window,
    )
    if not hmac.compare_digest(change.dedupe_key, expected):
        raise GroupError("ChangeSet idempotency key mismatch")
    return change


def load_group(brand, store: GroupStore, change_id: str) -> GroupChange:
    """Public load with verified brand binding and idempotency key."""
    return _load_bound(brand, store, change_id)


def _media(brand, relative: str | None) -> GroupMedia | None:
    if relative is None:
        return None
    if ".secrets" in Path(relative).parts:
        raise GroupError("--media cannot point to .secrets")
    path = validar_ruta_relativa(brand.raiz / "media", relative, GroupError, "Group media")
    if path.is_symlink():
        raise GroupError("Group media must be a regular file, not a symlink")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise GroupError(f"failed to read Group media: {exc}") from None
    if not resolved.is_file():
        raise GroupError("Group media must be an existing regular file")
    try:
        kind, width, height, duration = _probe_visual_file(resolved, evidence=False)
    except GroupError as exc:
        raise GroupError(f"invalid Group media: {exc}") from None
    return GroupMedia(
        relative_path=relative,
        sha256=file_digest(resolved),
        size_bytes=resolved.stat().st_size,
        kind=kind,
        width=width,
        height=height,
        duration_s=duration,
    )


def _verify_media_unchanged(brand, media: GroupMedia | None) -> None:
    if media is None:
        return
    current = _media(brand, media.relative_path)
    if current is None or current.model_dump() != media.model_dump():
        raise GroupError("media changed since preview; prepare a new ChangeSet")


def _iter_changes(store: GroupStore):
    if not store.root.exists():
        return
    for path in sorted(store.root.glob("*.json")):
        yield store.load(path.stem)


def prepare_group(
    brand,
    store: GroupStore,
    *,
    group_name: str,
    group_url: str,
    copy: str,
    youtube_url: str,
    window_start: datetime,
    window_end: datetime,
    media: str | None = None,
) -> GroupChange:
    name = _plain_text(group_name, "group_name", maximum=200)
    text = _plain_text(copy, "copy", maximum=10_000)
    canonical_group, group_key = validate_group_url(group_url)
    canonical_youtube, video_id = validate_youtube_url(youtube_url)
    try:
        window = SuggestedWindow(start=window_start, end=window_end)
    except ValueError as exc:
        raise GroupError(str(exc)) from None
    destination = GroupDestination(name=name, url=canonical_group, key=group_key)
    supplied_media = _media(brand, media)
    key = _dedupe_payload(brand.nombre, destination, text, canonical_youtube, supplied_media, window)
    lock_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"facebook-group:{brand.raiz.resolve()}:{key}"))
    with store.apply_lock(lock_id):
        for other in _iter_changes(store):
            if hmac.compare_digest(other.dedupe_key, key):
                raise GroupError(
                    f"duplicate proposal: ChangeSet {other.id} already exists with status {other.status}"
                )
        moment = now_utc()
        change = GroupChange(
            id=str(uuid.uuid4()),
            brand=brand.nombre,
            brand_root=str(brand.raiz.resolve()),
            destination=destination,
            copy_text=text,
            youtube_url=canonical_youtube,
            youtube_video_id=video_id,
            media=supplied_media,
            suggested_window=window,
            dedupe_key=key,
            created_at=moment,
            updated_at=moment,
        )
        change.fingerprint = group_fingerprint(change)
        _event(change, "prepared_local_no_api_write")
        store.save(change)
        return change


def approve_handoff(brand, store: GroupStore, change_id: str, approval_digest: str) -> GroupChange:
    """Approve local package without browser opening or network access."""
    with store.apply_lock(change_id):
        change = _load_bound(brand, store, change_id)
        if not isinstance(approval_digest, str) or not hmac.compare_digest(
            approval_digest.encode(), change.fingerprint.encode()
        ):
            raise ApprovalMismatch("approval does not match exact proposal fingerprint")
        if change.status == "handoff_ready":
            _verify_media_unchanged(brand, change.media)
            return change
        if change.status in {"awaiting_manual_confirmation", "confirmed_manual"}:
            return change
        if change.status not in {"prepared", "approved"}:
            raise GroupError(f"ChangeSet status is {change.status}")
        _verify_media_unchanged(brand, change.media)
        if change.status == "prepared":
            change.status = "approved"
            change.approved_at = now_utc()
            _event(change, "approved_exact_digest")
            store.save(change)
        change.status = "handoff_ready"
        change.last_error = None
        _event(change, "browser_handoff_ready_no_publication")
        store.save(change)
        return change


def mark_handoff_opened(brand, store: GroupStore, change_id: str) -> GroupChange:
    """Record browser opening without claiming a post exists."""
    with store.apply_lock(change_id):
        change = _load_bound(brand, store, change_id)
        if change.status in {"awaiting_manual_confirmation", "confirmed_manual"}:
            return change
        if change.status != "handoff_ready":
            raise GroupError("handoff must be approved before opening browser")
        _verify_media_unchanged(brand, change.media)
        change.handoff_opened_at = now_utc()
        change.status = "awaiting_manual_confirmation"
        change.last_error = None
        _event(change, "browser_opened_no_publication_claim")
        store.save(change)
        return change


def record_handoff_error(brand, store: GroupStore, change_id: str, message: str) -> GroupChange:
    with store.apply_lock(change_id):
        change = _load_bound(brand, store, change_id)
        if change.status != "handoff_ready":
            return change
        change.last_error = _plain_text(message, "browser error", maximum=500)
        _event(change, "browser_open_failed")
        store.save(change)
        return change


_IMAGE_CODECS = {
    ".jpg": "mjpeg",
    ".jpeg": "mjpeg",
    ".png": "png",
    ".webp": "webp",
}
_IMAGE_DEMUXERS = {
    ".jpg": "jpeg_pipe",
    ".jpeg": "jpeg_pipe",
    ".png": "png_pipe",
    ".webp": "webp_pipe",
}
_VIDEO_EXTENSIONS = {".mp4", ".mov"}
_EVIDENCE_EXTENSIONS = {*_IMAGE_CODECS, ".pdf"}
_PDF_WHITESPACE = b"\x00\t\n\x0c\r "
_PDF_DELIMITERS = b"()<>[]{}/%"
_PDF_VERSIONS = {f"%PDF-1.{minor}".encode() for minor in range(8)} | {b"%PDF-2.0"}


class _PdfReference(NamedTuple):
    object_number: int
    generation: int


class _PdfName(bytes):
    pass


class _PdfLexer:
    """Minimal PDF object lexer; strings and comments never produce keys."""

    def __init__(self, data: bytes):
        self.data = data
        self.cursor = 0
        self.buffer: list[tuple[str, bytes]] = []

    def _skip_ignored(self) -> None:
        while self.cursor < len(self.data):
            if self.data[self.cursor] in _PDF_WHITESPACE:
                self.cursor += 1
                continue
            if self.data[self.cursor] != ord("%"):
                return
            self.cursor += 1
            while self.cursor < len(self.data) and self.data[self.cursor] not in b"\r\n":
                self.cursor += 1

    def _literal_string(self) -> tuple[str, bytes]:
        self.cursor += 1
        depth = 1
        while self.cursor < len(self.data):
            byte = self.data[self.cursor]
            self.cursor += 1
            if byte == ord("\\"):
                if self.cursor < len(self.data):
                    if self.data[self.cursor] == ord("\r"):
                        self.cursor += 1
                        if self.cursor < len(self.data) and self.data[self.cursor] == ord("\n"):
                            self.cursor += 1
                    else:
                        self.cursor += 1
                continue
            if byte == ord("("):
                depth += 1
            elif byte == ord(")"):
                depth -= 1
                if depth == 0:
                    return ("string", b"")
        raise GroupError("PDF evidence contains a truncated literal string")

    def _hex_string(self) -> tuple[str, bytes]:
        self.cursor += 1
        while self.cursor < len(self.data):
            byte = self.data[self.cursor]
            self.cursor += 1
            if byte == ord(">"):
                return ("string", b"")
        raise GroupError("PDF evidence contains a truncated hexadecimal string")

    def _name(self) -> tuple[str, bytes]:
        self.cursor += 1
        start = self.cursor
        while (
            self.cursor < len(self.data)
            and self.data[self.cursor] not in _PDF_WHITESPACE
            and self.data[self.cursor] not in _PDF_DELIMITERS
        ):
            self.cursor += 1
        raw = self.data[start:self.cursor]
        decoded = bytearray()
        index = 0
        while index < len(raw):
            if raw[index] != ord("#"):
                decoded.append(raw[index])
                index += 1
                continue
            if index + 2 >= len(raw):
                raise GroupError("PDF evidence contains an invalid escaped name")
            try:
                decoded.append(int(raw[index + 1:index + 3], 16))
            except ValueError:
                raise GroupError("PDF evidence contains an invalid escaped name") from None
            index += 3
        return ("name", bytes(decoded))

    def _read(self) -> tuple[str, bytes] | None:
        self._skip_ignored()
        if self.cursor >= len(self.data):
            return None
        byte = self.data[self.cursor]
        if byte == ord("("):
            return self._literal_string()
        if byte == ord("<"):
            if self.data[self.cursor:self.cursor + 2] == b"<<":
                self.cursor += 2
                return ("dict_start", b"<<")
            return self._hex_string()
        if self.data[self.cursor:self.cursor + 2] == b">>":
            self.cursor += 2
            return ("dict_end", b">>")
        if byte == ord("["):
            self.cursor += 1
            return ("array_start", b"[")
        if byte == ord("]"):
            self.cursor += 1
            return ("array_end", b"]")
        if byte == ord("/"):
            return self._name()
        if byte in _PDF_DELIMITERS:
            raise GroupError("PDF evidence contains an unexpected delimiter")
        start = self.cursor
        while (
            self.cursor < len(self.data)
            and self.data[self.cursor] not in _PDF_WHITESPACE
            and self.data[self.cursor] not in _PDF_DELIMITERS
        ):
            self.cursor += 1
        return ("word", self.data[start:self.cursor])

    def peek(self, index: int = 0) -> tuple[str, bytes] | None:
        while len(self.buffer) <= index:
            token = self._read()
            if token is None:
                return None
            self.buffer.append(token)
        return self.buffer[index]

    def take(self) -> tuple[str, bytes] | None:
        if self.buffer:
            return self.buffer.pop(0)
        return self._read()


def _pdf_integer(raw: bytes) -> int | None:
    digits = raw[1:] if raw[:1] in {b"+", b"-"} else raw
    if not digits or not digits.isdigit():
        return None
    return int(raw)


def _pdf_has_valid_header(header: bytes) -> bool:
    line_end = min(
        (index for index in (header.find(b"\r"), header.find(b"\n")) if index >= 0),
        default=-1,
    )
    return line_end >= 0 and header[:line_end] in _PDF_VERSIONS


def _pdf_startxref(tail: bytes) -> int | None:
    """Read closure from code lines, skipping intervening comments."""
    lines = tail.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n")
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines or lines.pop().strip() != b"%%EOF":
        return None
    code_lines = []
    while lines and len(code_lines) < 2:
        line = lines.pop().split(b"%", 1)[0].strip()
        if line:
            code_lines.append(line)
    if len(code_lines) != 2 or code_lines[1] != b"startxref":
        return None
    return int(code_lines[0]) if code_lines[0].isdigit() else None


def _pdf_value(lexer: _PdfLexer, *, depth: int = 0):
    if depth >= 256:
        raise GroupError("PDF evidence contains excessively nested objects")
    token = lexer.take()
    if token is None:
        raise GroupError("PDF evidence contains a truncated value")
    kind, raw = token
    if kind == "name":
        return _PdfName(raw)
    if kind == "string":
        return b""
    if kind == "array_start":
        values = []
        while lexer.peek() != ("array_end", b"]"):
            if lexer.peek() is None:
                raise GroupError("PDF evidence contains a truncated array")
            values.append(_pdf_value(lexer, depth=depth + 1))
        lexer.take()
        return values
    if kind == "dict_start":
        dictionary = {}
        while lexer.peek() != ("dict_end", b">>"):
            key = lexer.take()
            if key is None:
                raise GroupError("PDF evidence contains a truncated dictionary")
            if key[0] != "name":
                raise GroupError("PDF evidence contains an invalid dictionary key")
            if key[1] in dictionary:
                raise GroupError("PDF evidence contains a duplicate dictionary key")
            dictionary[key[1]] = _pdf_value(lexer, depth=depth + 1)
        lexer.take()
        return dictionary
    if kind != "word":
        raise GroupError("PDF evidence contains an unexpected value")
    integer = _pdf_integer(raw)
    if integer is not None:
        generation = lexer.peek()
        marker = lexer.peek(1)
        generation_number = (
            _pdf_integer(generation[1])
            if generation and generation[0] == "word"
            else None
        )
        if generation_number is not None and marker == ("word", b"R"):
            lexer.take()
            lexer.take()
            if integer < 0 or generation_number < 0:
                raise GroupError("PDF evidence contains a negative reference")
            return _PdfReference(integer, generation_number)
        return integer
    return raw


def _validate_jpeg_structure(path: Path) -> None:
    """Walk JPEG segments through SOS/EOI; MJPEG containers are not accepted."""
    saw_frame = False
    saw_scan = False
    try:
        with path.open("rb") as handle:
            if handle.read(2) != b"\xff\xd8":
                raise GroupError("JPEG image lacks SOI marker")
            while True:
                prefix = handle.read(1)
                if prefix != b"\xff":
                    raise GroupError("JPEG image contains invalid segments")
                marker = handle.read(1)
                while marker == b"\xff":
                    marker = handle.read(1)
                if not marker:
                    raise GroupError("JPEG image is truncated before EOI")
                code = marker[0]
                if code == 0xD9:
                    if not saw_frame or not saw_scan or handle.read().strip(b"\x00\t\r\n "):
                        raise GroupError("JPEG image lacks complete structure")
                    return
                if code in {0x00, 0xD8, 0x01} or 0xD0 <= code <= 0xD7:
                    raise GroupError("JPEG image contains a misplaced marker")
                raw_length = handle.read(2)
                if len(raw_length) != 2:
                    raise GroupError("JPEG image contains a truncated segment")
                length = int.from_bytes(raw_length, "big")
                if length < 2:
                    raise GroupError("JPEG image contains a segment with invalid length")
                if code in {
                    0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                    0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
                }:
                    saw_frame = True
                if code != 0xDA:
                    if len(handle.read(length - 2)) != length - 2:
                        raise GroupError("JPEG image contains a truncated segment")
                    continue

                saw_scan = True
                if len(handle.read(length - 2)) != length - 2:
                    raise GroupError("JPEG image contains a truncated scan")
                while True:
                    byte = handle.read(1)
                    if not byte:
                        raise GroupError("JPEG image is truncated before EOI")
                    if byte != b"\xff":
                        continue
                    next_byte = handle.read(1)
                    while next_byte == b"\xff":
                        next_byte = handle.read(1)
                    if not next_byte:
                        raise GroupError("JPEG image is truncated before EOI")
                    next_code = next_byte[0]
                    if next_code == 0x00 or 0xD0 <= next_code <= 0xD7:
                        continue
                    handle.seek(-2, 1)
                    break
    except OSError as exc:
        raise GroupError(f"failed to analyze JPEG image: {exc}") from None


def _decode_first_visual_frame(path: Path, field: str) -> None:
    """Require ffmpeg to decode one frame; never write or transform media."""
    try:
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-i", str(path),
                "-map", "0:v:0", "-frames:v", "1", "-f", "null", "-",
            ],
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, OSError):
        raise GroupError(f"{field} does not contain a decodable image or video track") from None


def _probe_visual_file(
    path: Path, *, evidence: bool,
) -> tuple[Literal["image", "video"], int, int, float | None]:
    """Validate actual extension, container and visual stream using ffprobe/ffmpeg."""
    extension = path.suffix.lower()
    allowed = set(_IMAGE_CODECS) if evidence else {*_IMAGE_CODECS, *_VIDEO_EXTENSIONS}
    if extension not in allowed:
        expected = "JPG, PNG or WebP" if evidence else "JPG, PNG, WebP, MP4 or MOV"
        raise GroupError(f"unsupported format: {expected} required")
    try:
        probe = _ffprobe(path)
    except (MediaInvalida, OSError, ValueError, json.JSONDecodeError) as exc:
        raise GroupError(f"failed to validate actual format of {path.name}: {exc}") from None
    streams = probe.get("streams") or []
    visual = next(
        (
            stream for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "video"
        ),
        None,
    )
    if visual is None:
        raise GroupError("file must contain an image or video track; audio-only media is not accepted")
    width = visual.get("width")
    height = visual.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise GroupError("visual stream must have positive actual dimensions")
    if _rotacion_grados(visual) % 180 == 90:
        width, height = height, width

    codec = str(visual.get("codec_name") or "").lower()
    format_name = str((probe.get("format") or {}).get("format_name") or "").lower()
    if extension in _IMAGE_CODECS:
        formats = {item.strip() for item in format_name.split(",") if item.strip()}
        if codec != _IMAGE_CODECS[extension] or _IMAGE_DEMUXERS[extension] not in formats:
            raise GroupError("image extension does not match its actual format")
        if extension in {".jpg", ".jpeg"}:
            _validate_jpeg_structure(path)
        duration = None
        kind: Literal["image", "video"] = "image"
    else:
        if "mp4" not in format_name and "mov" not in format_name:
            raise GroupError("video extension does not match an actual MP4 or MOV container")
        try:
            duration = float((probe.get("format") or {}).get("duration"))
        except (TypeError, ValueError):
            raise GroupError("video must have a verifiable actual duration") from None
        if duration <= 0:
            raise GroupError("video must have a positive actual duration")
        kind = "video"
    _decode_first_visual_frame(path, "the file")
    return kind, width, height, duration


def _pdf_dictionary(
    handle: BinaryIO,
    offset: int,
    expected: tuple[int, int],
) -> dict[bytes, object]:
    """Resolve a classic direct object and lexically parse its dictionary."""
    handle.seek(offset)
    chunk = handle.read(1024 * 1024)
    lexer = _PdfLexer(chunk)
    object_number = _pdf_value(lexer)
    generation = _pdf_value(lexer)
    if (
        not isinstance(object_number, int)
        or not isinstance(generation, int)
        or (object_number, generation) != expected
        or lexer.take() != ("word", b"obj")
    ):
        raise GroupError("PDF evidence references a nonexistent object")
    dictionary = _pdf_value(lexer)
    if not isinstance(dictionary, dict):
        raise GroupError("PDF evidence contains an object without a dictionary")
    if lexer.take() != ("word", b"endobj"):
        raise GroupError("PDF evidence contains an object without endobj closure")
    return dictionary


def _pdf_classic_xref(
    handle: BinaryIO,
    offset: int,
) -> tuple[dict[tuple[int, int], int], dict[bytes, object]]:
    """Parse classic xref subsections and corresponding trailer."""
    handle.seek(offset)
    if handle.readline().strip() != b"xref":
        raise GroupError("PDF evidence does not use a parseable classic xref table")
    entries: dict[tuple[int, int], int] = {}
    while True:
        line = handle.readline()
        if not line:
            raise GroupError("PDF evidence contains a truncated xref table")
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == b"trailer":
            break
        subsection = stripped.split()
        if len(subsection) != 2 or not all(part.isdigit() for part in subsection):
            raise GroupError("PDF evidence contains an invalid xref subsection")
        first, count = map(int, subsection)
        if count > 1_000_000:
            raise GroupError("PDF evidence declares too many objects")
        for object_number in range(first, first + count):
            entry = handle.readline().strip()
            parts = entry.split()
            if (
                len(parts) != 3
                or len(parts[0]) != 10
                or not parts[0].isdigit()
                or len(parts[1]) != 5
                or not parts[1].isdigit()
                or parts[2] not in {b"f", b"n"}
            ):
                raise GroupError("PDF evidence contains an invalid xref entry")
            if parts[2] == b"n":
                entries[(object_number, int(parts[1]))] = int(parts[0])

    lexer = _PdfLexer(handle.read(1024 * 1024))
    trailer = _pdf_value(lexer)
    if not isinstance(trailer, dict):
        raise GroupError("PDF evidence contains a truncated trailer")
    return entries, trailer


def _pdf_pages(
    handle: BinaryIO,
    xref: dict[tuple[int, int], int],
    reference: tuple[int, int],
    expected_parent: tuple[int, int] | None,
    visiting: set[tuple[int, int]],
    seen: set[tuple[int, int]],
) -> int:
    """Walk /Pages tree and count resolvable /Page leaves."""
    if reference in visiting or len(visiting) >= 10_000:
        raise GroupError("PDF evidence contains a cyclic or excessive page tree")
    if reference in seen:
        raise GroupError("PDF evidence contains a duplicate or shared Kid")
    offset = xref.get(reference)
    if offset is None:
        raise GroupError("PDF evidence references a nonexistent page object")
    dictionary = _pdf_dictionary(handle, offset, reference)
    node_type = dictionary.get(b"Type")
    if expected_parent is None and node_type != _PdfName(b"Pages"):
        raise GroupError(
            "PDF evidence Catalog /Pages does not point to a /Type /Pages dictionary"
        )
    parent = dictionary.get(b"Parent")
    if expected_parent is not None and parent != _PdfReference(*expected_parent):
        raise GroupError("PDF evidence contains a nonexistent or incorrect Parent")
    if expected_parent is None and parent is not None:
        raise GroupError("PDF evidence contains an incorrect Parent in root Pages")
    seen.add(reference)
    if node_type == _PdfName(b"Page"):
        return 1
    if node_type != _PdfName(b"Pages"):
        raise GroupError("PDF evidence references an object that is neither Page nor Pages")
    declared = dictionary.get(b"Count")
    kids = dictionary.get(b"Kids")
    if not isinstance(declared, int) or not isinstance(kids, list):
        raise GroupError("PDF evidence contains an incomplete Pages node")
    if declared <= 0 or not kids or not all(isinstance(child, _PdfReference) for child in kids):
        raise GroupError("PDF evidence contains no actual pages")
    visiting.add(reference)
    try:
        actual = sum(
            _pdf_pages(handle, xref, child, reference, visiting, seen)
            for child in kids
        )
    finally:
        visiting.remove(reference)
    if actual != declared:
        raise GroupError("PDF evidence declares an inconsistent page count")
    return actual


def _validate_pdf(path: Path, size: int) -> None:
    """Resolve xref, catalog and an actual classic page tree."""
    try:
        with path.open("rb") as handle:
            header = handle.read(16)
            if not _pdf_has_valid_header(header):
                raise GroupError("PDF evidence lacks a valid PDF signature")
            tail_size = min(size, 128 * 1024)
            handle.seek(size - tail_size)
            tail = handle.read(tail_size)
            xref_offset = _pdf_startxref(tail)
            if xref_offset is None:
                raise GroupError("PDF evidence lacks parseable closure and startxref")
            if xref_offset < 0 or xref_offset >= size:
                raise GroupError("PDF evidence contains a startxref outside the file")
            xref, trailer = _pdf_classic_xref(handle, xref_offset)
            declared_size = trailer.get(b"Size")
            if (
                not isinstance(declared_size, int)
                or declared_size <= 0
                or (xref and declared_size <= max(item[0] for item in xref))
            ):
                raise GroupError("PDF evidence contains an inconsistent Size")
            root = trailer.get(b"Root")
            if not isinstance(root, _PdfReference):
                raise GroupError("PDF evidence contains no resolvable Root catalog")
            root_offset = xref.get(root)
            if root_offset is None:
                raise GroupError("PDF evidence references a nonexistent Root")
            catalog = _pdf_dictionary(handle, root_offset, root)
            if catalog.get(b"Type") != _PdfName(b"Catalog"):
                raise GroupError("PDF evidence Root is not a catalog")
            pages = catalog.get(b"Pages")
            if not isinstance(pages, _PdfReference):
                raise GroupError("PDF evidence contains no Pages tree")
            if _pdf_pages(handle, xref, pages, None, set(), set()) <= 0:
                raise GroupError("PDF evidence contains no actual pages")
    except OSError as exc:
        raise GroupError(f"failed to analyze PDF evidence: {exc}") from None


def _evidence(brand, relative: str | None) -> GroupEvidence | None:
    if relative is None:
        return None
    if ".secrets" in Path(relative).parts:
        raise GroupError("evidence cannot be inside .secrets")
    path = validar_ruta_relativa(brand.raiz, relative, GroupError, "evidence")
    if path.is_symlink():
        raise GroupError("evidence must be a regular file, not a symlink")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise GroupError(f"failed to read evidence: {exc}") from None
    if not resolved.is_file() or resolved.suffix.lower() not in _EVIDENCE_EXTENSIONS:
        raise GroupError("evidence must be an existing JPG, PNG, WebP or PDF")
    size = resolved.stat().st_size
    if size <= 0:
        raise GroupError("evidence is empty")
    if resolved.suffix.lower() == ".pdf":
        _validate_pdf(resolved, size)
    else:
        _probe_visual_file(resolved, evidence=True)
    return GroupEvidence(relative_path=relative, sha256=file_digest(resolved), size_bytes=size)


def confirm_group(
    brand,
    store: GroupStore,
    change_id: str,
    *,
    confirmation_phrase: str,
    post_url: str | None = None,
    evidence: str | None = None,
) -> GroupChange:
    """Record explicit user statement without checking the web."""
    if not isinstance(confirmation_phrase, str):
        raise GroupError(f"confirmation must be exactly: {CONFIRMATION_PHRASE}")
    supplied = confirmation_phrase.encode()
    accepted = hmac.compare_digest(supplied, CONFIRMATION_PHRASE.encode())
    accepted |= hmac.compare_digest(supplied, LEGACY_CONFIRMATION_PHRASE.encode())
    if not accepted:
        raise GroupError(f"confirmation must be exactly: {CONFIRMATION_PHRASE}")
    with store.apply_lock(change_id):
        change = _load_bound(brand, store, change_id)
        canonical_post = validate_post_url(post_url, change.destination.key) if post_url else None
        if change.status == "confirmed_manual":
            existing = change.confirmation
            existing_evidence = (
                existing.evidence.relative_path
                if existing is not None and existing.evidence is not None
                else None
            )
            if (
                existing is not None
                and existing.post_url == canonical_post
                and existing_evidence == evidence
            ):
                return change
            raise GroupError("ChangeSet already has a different confirmation; it will not be overwritten")
        supplied_evidence = _evidence(brand, evidence)
        if canonical_post is None and supplied_evidence is None:
            raise GroupError("confirm with --post-url, --evidence or both")
        if change.status not in {"handoff_ready", "awaiting_manual_confirmation"}:
            raise GroupError("handoff must be approved before confirming manual publication")
        change.confirmation = GroupConfirmation(
            confirmed_at=now_utc(), post_url=canonical_post, evidence=supplied_evidence
        )
        change.status = "confirmed_manual"
        change.last_error = None
        _event(change, "user_confirmed_manual_publication")
        store.save(change)
        return change


def export_record(change: GroupChange) -> dict:
    """Exportable view without absolute paths, internal keys or credentials."""
    return {
        "id": change.id,
        "brand": change.brand,
        "group": change.destination.model_dump(mode="json"),
        "copy": change.copy_text,
        "youtube_url": change.youtube_url,
        "media": change.media.model_dump(mode="json") if change.media else None,
        "suggested_window": change.suggested_window.model_dump(mode="json"),
        "status": change.status,
        "capability": change.capability,
        "fingerprint": change.fingerprint,
        "created_at": change.created_at.isoformat(),
        "updated_at": change.updated_at.isoformat(),
        "confirmation": change.confirmation.model_dump(mode="json") if change.confirmation else None,
        "reminder": "Manual publication pending in browser; socialcli does not publish to Groups via API.",
    }


def export_queue(brand, store: GroupStore, change_id: str | None = None) -> list[dict]:
    if change_id is not None:
        return [export_record(_load_bound(brand, store, change_id))]
    return [export_record(_load_bound(brand, store, item.id)) for item in _iter_changes(store)]
