"""Cola durable para publicaciones manuales en Grupos de Facebook.

La Facebook Groups API y ``publish_to_groups`` ya no están disponibles. Este
módulo no contiene cliente HTTP: prepara un paquete, registra su aprobación y
acepta únicamente la confirmación que el usuario aporta después de publicar en
el navegador.
"""
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
    """Error legible de una propuesta manual para un Grupo."""


CONFIRMATION_PHRASE = "CONFIRMO PUBLICADO MANUALMENTE"
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
        raise GroupError(f"{field} debe incluir zona horaria")
    return value


def _plain_text(value: str, field: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise GroupError(f"{field} debe ser texto no vacío y sin espacios exteriores")
    if len(value) > maximum:
        raise GroupError(f"{field} supera el límite local de {maximum} caracteres")
    if any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise GroupError(f"{field} contiene caracteres de control")
    if any(pattern.search(value) for pattern in _SECRET_PATTERNS):
        raise GroupError(f"{field} parece contener un secreto o credencial; se rechazó")
    return value


def _safe_https_parts(value: str, field: str):
    value = _plain_text(value, field, maximum=2048)
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise GroupError(f"{field} no es una URL válida") from None
    if parsed.scheme != "https" or not parsed.hostname:
        raise GroupError(f"{field} debe ser una URL HTTPS pública")
    if parsed.username is not None or parsed.password is not None or port is not None:
        raise GroupError(f"{field} no admite credenciales ni puertos")
    if parsed.fragment:
        raise GroupError(f"{field} no admite fragmentos")
    return parsed


def validate_group_url(value: str) -> tuple[str, str]:
    """Devuelve URL canónica y slug/id exacto del Grupo."""
    parsed = _safe_https_parts(value, "group_url")
    host = (parsed.hostname or "").lower()
    if host not in {"facebook.com", "www.facebook.com", "m.facebook.com"}:
        raise GroupError("group_url debe apuntar a un Grupo público en facebook.com")
    if parsed.query:
        raise GroupError("group_url no admite query; elimina parámetros de seguimiento o credenciales")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2 or parts[0].lower() != "groups" or not _GROUP_SEGMENT.fullmatch(parts[1]):
        raise GroupError("group_url debe tener la forma https://www.facebook.com/groups/GRUPO")
    canonical = urlunsplit(("https", "www.facebook.com", f"/groups/{parts[1]}", "", ""))
    return canonical, parts[1]


def validate_youtube_url(value: str) -> tuple[str, str]:
    """Acepta enlaces canónicos a un vídeo largo, nunca endpoints de API."""
    parsed = _safe_https_parts(value, "youtube_url")
    host = (parsed.hostname or "").lower()
    video_id: str | None = None
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        if parsed.path != "/watch":
            raise GroupError("youtube_url debe apuntar a /watch?v=VIDEO_ID")
        query = parse_qs(parsed.query, keep_blank_values=True)
        if set(query) != {"v"} or len(query["v"]) != 1:
            raise GroupError("youtube_url solo admite el parámetro v; elimina tracking o credenciales")
        video_id = query["v"][0]
    elif host == "youtu.be":
        if parsed.query:
            raise GroupError("youtube_url corta no admite parámetros")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 1:
            raise GroupError("youtube_url corta debe tener la forma https://youtu.be/VIDEO_ID")
        video_id = parts[0]
    else:
        raise GroupError("youtube_url debe usar youtube.com o youtu.be; endpoints privados no se admiten")
    if not _YOUTUBE_ID.fullmatch(video_id or ""):
        raise GroupError("youtube_url no contiene un ID de vídeo de YouTube válido")
    return f"https://www.youtube.com/watch?v={video_id}", video_id


def validate_post_url(value: str, expected_group_key: str) -> str:
    parsed = _safe_https_parts(value, "post_url")
    if (parsed.hostname or "").lower() not in {"facebook.com", "www.facebook.com", "m.facebook.com"}:
        raise GroupError("post_url debe apuntar a facebook.com")
    if parsed.query:
        raise GroupError("post_url no admite query; elimina tracking o credenciales")
    parts = [part for part in parsed.path.split("/") if part]
    if (
        len(parts) != 4
        or parts[0].lower() != "groups"
        or parts[1] != expected_group_key
        or parts[2].lower() not in {"posts", "permalink"}
        or not _POST_SEGMENT.fullmatch(parts[3])
    ):
        raise GroupError("post_url debe ser una URL de post/permalink del mismo Grupo aprobado")
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
            raise ValueError("window_end debe ser posterior a window_start")
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
            raise ValueError("la media del Grupo requiere dimensiones visuales positivas")
        if self.kind == "image" and self.duration_s is not None:
            raise ValueError("una imagen del Grupo no admite duración")
        if self.kind == "video" and (
            self.duration_s is None or self.duration_s <= 0
        ):
            raise ValueError("un vídeo del Grupo requiere duración positiva")
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
            raise ValueError("confirmed_manual requiere confirmación del usuario")
        if self.status != "confirmed_manual" and self.confirmation is not None:
            raise ValueError("la evidencia solo puede existir tras confirmación manual")
        if self.status == "awaiting_manual_confirmation" and self.handoff_opened_at is None:
            raise ValueError("awaiting_manual_confirmation requiere un handoff abierto")
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
        raise GroupError("la marca configurada ya no coincide con el ChangeSet del Grupo")
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
        raise GroupError("el contrato canónico del ChangeSet no coincide")
    if change.media is not None:
        relative = change.media.relative_path
        if ".secrets" in Path(relative).parts:
            raise GroupError("la media persistida no puede apuntar a .secrets")
        validar_ruta_relativa(
            brand.raiz / "media", relative, GroupError, "media persistida del Grupo"
        )
    if change.confirmation is not None:
        if change.confirmation.confirmed_at.utcoffset() is None:
            raise GroupError("confirmed_at debe incluir zona horaria")
        if change.confirmation.post_url is not None:
            canonical_post = validate_post_url(change.confirmation.post_url, group_key)
            if canonical_post != change.confirmation.post_url:
                raise GroupError("la URL confirmada persistida no es canónica")
        if change.confirmation.evidence is not None:
            evidence_path = change.confirmation.evidence.relative_path
            if ".secrets" in Path(evidence_path).parts:
                raise GroupError("la evidencia persistida no puede apuntar a .secrets")
            validar_ruta_relativa(brand.raiz, evidence_path, GroupError, "evidencia persistida")
    expected = _dedupe_payload(
        change.brand, change.destination, change.copy_text, change.youtube_url,
        change.media, change.suggested_window,
    )
    if not hmac.compare_digest(change.dedupe_key, expected):
        raise GroupError("la clave de idempotencia del ChangeSet no coincide")
    return change


def load_group(brand, store: GroupStore, change_id: str) -> GroupChange:
    """Carga pública con binding de marca y clave de idempotencia verificados."""
    return _load_bound(brand, store, change_id)


def _media(brand, relative: str | None) -> GroupMedia | None:
    if relative is None:
        return None
    if ".secrets" in Path(relative).parts:
        raise GroupError("--media no puede apuntar a .secrets")
    path = validar_ruta_relativa(brand.raiz / "media", relative, GroupError, "media del Grupo")
    if path.is_symlink():
        raise GroupError("la media del Grupo debe ser un archivo regular, no un enlace")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise GroupError(f"no se pudo leer la media del Grupo: {exc}") from None
    if not resolved.is_file():
        raise GroupError("la media del Grupo debe ser un archivo regular existente")
    try:
        kind, width, height, duration = _probe_visual_file(resolved, evidence=False)
    except GroupError as exc:
        raise GroupError(f"media del Grupo inválida: {exc}") from None
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
        raise GroupError("la media cambió desde el preview; prepara un ChangeSet nuevo")


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
                    f"propuesta duplicada: ya existe el ChangeSet {other.id} en estado {other.status}"
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
    """Aprueba el paquete local; no abre navegador ni realiza red."""
    with store.apply_lock(change_id):
        change = _load_bound(brand, store, change_id)
        if not isinstance(approval_digest, str) or not hmac.compare_digest(
            approval_digest.encode(), change.fingerprint.encode()
        ):
            raise ApprovalMismatch("la aprobación no coincide con la huella exacta de la propuesta")
        if change.status == "handoff_ready":
            _verify_media_unchanged(brand, change.media)
            return change
        if change.status in {"awaiting_manual_confirmation", "confirmed_manual"}:
            return change
        if change.status not in {"prepared", "approved"}:
            raise GroupError(f"el ChangeSet está en estado {change.status}")
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
    """Registra que el navegador se abrió; no afirma que exista un post."""
    with store.apply_lock(change_id):
        change = _load_bound(brand, store, change_id)
        if change.status in {"awaiting_manual_confirmation", "confirmed_manual"}:
            return change
        if change.status != "handoff_ready":
            raise GroupError("el handoff debe aprobarse antes de abrir el navegador")
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
        change.last_error = _plain_text(message, "error de navegador", maximum=500)
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
    """Lexer mínimo de objetos PDF; strings y comentarios nunca producen claves."""

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
        raise GroupError("la evidencia PDF contiene un string literal truncado")

    def _hex_string(self) -> tuple[str, bytes]:
        self.cursor += 1
        while self.cursor < len(self.data):
            byte = self.data[self.cursor]
            self.cursor += 1
            if byte == ord(">"):
                return ("string", b"")
        raise GroupError("la evidencia PDF contiene un string hexadecimal truncado")

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
                raise GroupError("la evidencia PDF contiene un nombre escapado inválido")
            try:
                decoded.append(int(raw[index + 1:index + 3], 16))
            except ValueError:
                raise GroupError("la evidencia PDF contiene un nombre escapado inválido") from None
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
            raise GroupError("la evidencia PDF contiene un delimitador inesperado")
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
    """Lee el cierre por líneas de código, omitiendo comentarios intermedios."""
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
        raise GroupError("la evidencia PDF contiene objetos demasiado anidados")
    token = lexer.take()
    if token is None:
        raise GroupError("la evidencia PDF contiene un valor truncado")
    kind, raw = token
    if kind == "name":
        return _PdfName(raw)
    if kind == "string":
        return b""
    if kind == "array_start":
        values = []
        while lexer.peek() != ("array_end", b"]"):
            if lexer.peek() is None:
                raise GroupError("la evidencia PDF contiene un array truncado")
            values.append(_pdf_value(lexer, depth=depth + 1))
        lexer.take()
        return values
    if kind == "dict_start":
        dictionary = {}
        while lexer.peek() != ("dict_end", b">>"):
            key = lexer.take()
            if key is None:
                raise GroupError("la evidencia PDF contiene un diccionario truncado")
            if key[0] != "name":
                raise GroupError("la evidencia PDF contiene una clave de diccionario inválida")
            if key[1] in dictionary:
                raise GroupError("la evidencia PDF contiene una clave de diccionario duplicada")
            dictionary[key[1]] = _pdf_value(lexer, depth=depth + 1)
        lexer.take()
        return dictionary
    if kind != "word":
        raise GroupError("la evidencia PDF contiene un valor inesperado")
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
                raise GroupError("la evidencia PDF contiene una referencia negativa")
            return _PdfReference(integer, generation_number)
        return integer
    return raw


def _validate_jpeg_structure(path: Path) -> None:
    """Recorre los segmentos JPEG hasta SOS/EOI; un contenedor MJPEG no vale."""
    saw_frame = False
    saw_scan = False
    try:
        with path.open("rb") as handle:
            if handle.read(2) != b"\xff\xd8":
                raise GroupError("la imagen JPEG no tiene marcador SOI")
            while True:
                prefix = handle.read(1)
                if prefix != b"\xff":
                    raise GroupError("la imagen JPEG contiene segmentos inválidos")
                marker = handle.read(1)
                while marker == b"\xff":
                    marker = handle.read(1)
                if not marker:
                    raise GroupError("la imagen JPEG está truncada antes de EOI")
                code = marker[0]
                if code == 0xD9:
                    if not saw_frame or not saw_scan or handle.read().strip(b"\x00\t\r\n "):
                        raise GroupError("la imagen JPEG no tiene una estructura completa")
                    return
                if code in {0x00, 0xD8, 0x01} or 0xD0 <= code <= 0xD7:
                    raise GroupError("la imagen JPEG contiene un marcador fuera de lugar")
                raw_length = handle.read(2)
                if len(raw_length) != 2:
                    raise GroupError("la imagen JPEG contiene un segmento truncado")
                length = int.from_bytes(raw_length, "big")
                if length < 2:
                    raise GroupError("la imagen JPEG contiene un segmento con longitud inválida")
                if code in {
                    0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                    0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
                }:
                    saw_frame = True
                if code != 0xDA:
                    if len(handle.read(length - 2)) != length - 2:
                        raise GroupError("la imagen JPEG contiene un segmento truncado")
                    continue

                saw_scan = True
                if len(handle.read(length - 2)) != length - 2:
                    raise GroupError("la imagen JPEG contiene un scan truncado")
                while True:
                    byte = handle.read(1)
                    if not byte:
                        raise GroupError("la imagen JPEG está truncada antes de EOI")
                    if byte != b"\xff":
                        continue
                    next_byte = handle.read(1)
                    while next_byte == b"\xff":
                        next_byte = handle.read(1)
                    if not next_byte:
                        raise GroupError("la imagen JPEG está truncada antes de EOI")
                    next_code = next_byte[0]
                    if next_code == 0x00 or 0xD0 <= next_code <= 0xD7:
                        continue
                    handle.seek(-2, 1)
                    break
    except OSError as exc:
        raise GroupError(f"no se pudo analizar la imagen JPEG: {exc}") from None


def _decode_first_visual_frame(path: Path, field: str) -> None:
    """Exige que ffmpeg pueda decodificar un frame; nunca escribe ni transforma."""
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
        raise GroupError(f"{field} no contiene una imagen o pista de vídeo decodificable") from None


def _probe_visual_file(
    path: Path, *, evidence: bool,
) -> tuple[Literal["image", "video"], int, int, float | None]:
    """Valida extensión, contenedor y stream visual reales con ffprobe/ffmpeg."""
    extension = path.suffix.lower()
    allowed = set(_IMAGE_CODECS) if evidence else {*_IMAGE_CODECS, *_VIDEO_EXTENSIONS}
    if extension not in allowed:
        expected = "JPG, PNG o WebP" if evidence else "JPG, PNG, WebP, MP4 o MOV"
        raise GroupError(f"formato no admitido: se requiere {expected}")
    try:
        probe = _ffprobe(path)
    except (MediaInvalida, OSError, ValueError, json.JSONDecodeError) as exc:
        raise GroupError(f"no se pudo validar el formato real de {path.name}: {exc}") from None
    streams = probe.get("streams") or []
    visual = next(
        (
            stream for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "video"
        ),
        None,
    )
    if visual is None:
        raise GroupError("el archivo debe contener una imagen o pista de vídeo; audio solo no se admite")
    width = visual.get("width")
    height = visual.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise GroupError("el stream visual debe tener dimensiones reales positivas")
    if _rotacion_grados(visual) % 180 == 90:
        width, height = height, width

    codec = str(visual.get("codec_name") or "").lower()
    format_name = str((probe.get("format") or {}).get("format_name") or "").lower()
    if extension in _IMAGE_CODECS:
        formats = {item.strip() for item in format_name.split(",") if item.strip()}
        if codec != _IMAGE_CODECS[extension] or _IMAGE_DEMUXERS[extension] not in formats:
            raise GroupError("la extensión de imagen no coincide con su formato real")
        if extension in {".jpg", ".jpeg"}:
            _validate_jpeg_structure(path)
        duration = None
        kind: Literal["image", "video"] = "image"
    else:
        if "mp4" not in format_name and "mov" not in format_name:
            raise GroupError("la extensión de vídeo no coincide con un contenedor MP4 o MOV real")
        try:
            duration = float((probe.get("format") or {}).get("duration"))
        except (TypeError, ValueError):
            raise GroupError("el vídeo debe tener una duración real verificable") from None
        if duration <= 0:
            raise GroupError("el vídeo debe tener una duración real positiva")
        kind = "video"
    _decode_first_visual_frame(path, "el archivo")
    return kind, width, height, duration


def _pdf_dictionary(
    handle: BinaryIO,
    offset: int,
    expected: tuple[int, int],
) -> dict[bytes, object]:
    """Resuelve un objeto directo clásico y parsea su diccionario léxicamente."""
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
        raise GroupError("la evidencia PDF contiene una referencia a un objeto inexistente")
    dictionary = _pdf_value(lexer)
    if not isinstance(dictionary, dict):
        raise GroupError("la evidencia PDF contiene un objeto sin diccionario")
    if lexer.take() != ("word", b"endobj"):
        raise GroupError("la evidencia PDF contiene un objeto sin cierre endobj")
    return dictionary


def _pdf_classic_xref(
    handle: BinaryIO,
    offset: int,
) -> tuple[dict[tuple[int, int], int], dict[bytes, object]]:
    """Parsea subsecciones xref clásicas y el trailer correspondiente."""
    handle.seek(offset)
    if handle.readline().strip() != b"xref":
        raise GroupError("la evidencia PDF no usa una tabla xref clásica parseable")
    entries: dict[tuple[int, int], int] = {}
    while True:
        line = handle.readline()
        if not line:
            raise GroupError("la evidencia PDF contiene una tabla xref truncada")
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == b"trailer":
            break
        subsection = stripped.split()
        if len(subsection) != 2 or not all(part.isdigit() for part in subsection):
            raise GroupError("la evidencia PDF contiene una subsección xref inválida")
        first, count = map(int, subsection)
        if count > 1_000_000:
            raise GroupError("la evidencia PDF declara demasiados objetos")
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
                raise GroupError("la evidencia PDF contiene una entrada xref inválida")
            if parts[2] == b"n":
                entries[(object_number, int(parts[1]))] = int(parts[0])

    lexer = _PdfLexer(handle.read(1024 * 1024))
    trailer = _pdf_value(lexer)
    if not isinstance(trailer, dict):
        raise GroupError("la evidencia PDF contiene un trailer truncado")
    return entries, trailer


def _pdf_pages(
    handle: BinaryIO,
    xref: dict[tuple[int, int], int],
    reference: tuple[int, int],
    expected_parent: tuple[int, int] | None,
    visiting: set[tuple[int, int]],
    seen: set[tuple[int, int]],
) -> int:
    """Recorre el árbol /Pages y cuenta hojas /Page realmente resolubles."""
    if reference in visiting or len(visiting) >= 10_000:
        raise GroupError("la evidencia PDF contiene un árbol de páginas cíclico o excesivo")
    if reference in seen:
        raise GroupError("la evidencia PDF contiene un Kid duplicado o compartido")
    offset = xref.get(reference)
    if offset is None:
        raise GroupError("la evidencia PDF referencia un objeto de páginas inexistente")
    dictionary = _pdf_dictionary(handle, offset, reference)
    node_type = dictionary.get(b"Type")
    if expected_parent is None and node_type != _PdfName(b"Pages"):
        raise GroupError(
            "la evidencia PDF Catalog /Pages no apunta a un diccionario /Type /Pages"
        )
    parent = dictionary.get(b"Parent")
    if expected_parent is not None and parent != _PdfReference(*expected_parent):
        raise GroupError("la evidencia PDF contiene un Parent inexistente o incorrecto")
    if expected_parent is None and parent is not None:
        raise GroupError("la evidencia PDF contiene un Parent incorrecto en el Pages raíz")
    seen.add(reference)
    if node_type == _PdfName(b"Page"):
        return 1
    if node_type != _PdfName(b"Pages"):
        raise GroupError("la evidencia PDF referencia un objeto que no es Page ni Pages")
    declared = dictionary.get(b"Count")
    kids = dictionary.get(b"Kids")
    if not isinstance(declared, int) or not isinstance(kids, list):
        raise GroupError("la evidencia PDF contiene un nodo Pages incompleto")
    if declared <= 0 or not kids or not all(isinstance(child, _PdfReference) for child in kids):
        raise GroupError("la evidencia PDF no contiene páginas reales")
    visiting.add(reference)
    try:
        actual = sum(
            _pdf_pages(handle, xref, child, reference, visiting, seen)
            for child in kids
        )
    finally:
        visiting.remove(reference)
    if actual != declared:
        raise GroupError("la evidencia PDF declara un recuento de páginas incoherente")
    return actual


def _validate_pdf(path: Path, size: int) -> None:
    """Resuelve el xref, el catálogo y un árbol de páginas clásico real."""
    try:
        with path.open("rb") as handle:
            header = handle.read(16)
            if not _pdf_has_valid_header(header):
                raise GroupError("la evidencia PDF no tiene una firma PDF válida")
            tail_size = min(size, 128 * 1024)
            handle.seek(size - tail_size)
            tail = handle.read(tail_size)
            xref_offset = _pdf_startxref(tail)
            if xref_offset is None:
                raise GroupError("la evidencia PDF no tiene un cierre y startxref parseables")
            if xref_offset < 0 or xref_offset >= size:
                raise GroupError("la evidencia PDF contiene un startxref fuera del archivo")
            xref, trailer = _pdf_classic_xref(handle, xref_offset)
            declared_size = trailer.get(b"Size")
            if (
                not isinstance(declared_size, int)
                or declared_size <= 0
                or (xref and declared_size <= max(item[0] for item in xref))
            ):
                raise GroupError("la evidencia PDF contiene un Size incoherente")
            root = trailer.get(b"Root")
            if not isinstance(root, _PdfReference):
                raise GroupError("la evidencia PDF no contiene un catálogo Root resoluble")
            root_offset = xref.get(root)
            if root_offset is None:
                raise GroupError("la evidencia PDF referencia un Root inexistente")
            catalog = _pdf_dictionary(handle, root_offset, root)
            if catalog.get(b"Type") != _PdfName(b"Catalog"):
                raise GroupError("la evidencia PDF Root no es un catálogo")
            pages = catalog.get(b"Pages")
            if not isinstance(pages, _PdfReference):
                raise GroupError("la evidencia PDF no contiene un árbol Pages")
            if _pdf_pages(handle, xref, pages, None, set(), set()) <= 0:
                raise GroupError("la evidencia PDF no contiene páginas reales")
    except OSError as exc:
        raise GroupError(f"no se pudo analizar la evidencia PDF: {exc}") from None


def _evidence(brand, relative: str | None) -> GroupEvidence | None:
    if relative is None:
        return None
    if ".secrets" in Path(relative).parts:
        raise GroupError("la evidencia no puede estar dentro de .secrets")
    path = validar_ruta_relativa(brand.raiz, relative, GroupError, "evidencia")
    if path.is_symlink():
        raise GroupError("la evidencia debe ser un archivo regular, no un enlace")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise GroupError(f"no se pudo leer la evidencia: {exc}") from None
    if not resolved.is_file() or resolved.suffix.lower() not in _EVIDENCE_EXTENSIONS:
        raise GroupError("la evidencia debe ser un JPG, PNG, WebP o PDF existente")
    size = resolved.stat().st_size
    if size <= 0:
        raise GroupError("la evidencia está vacía")
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
    """Registra una afirmación explícita del usuario, sin comprobar la web."""
    if not isinstance(confirmation_phrase, str) or not hmac.compare_digest(
        confirmation_phrase.encode(), CONFIRMATION_PHRASE.encode()
    ):
        raise GroupError(f"la confirmación debe ser exactamente: {CONFIRMATION_PHRASE}")
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
            raise GroupError("el ChangeSet ya tiene una confirmación distinta; no se sobrescribe")
        supplied_evidence = _evidence(brand, evidence)
        if canonical_post is None and supplied_evidence is None:
            raise GroupError("confirma con --post-url, --evidence o ambos")
        if change.status not in {"handoff_ready", "awaiting_manual_confirmation"}:
            raise GroupError("el handoff debe aprobarse antes de confirmar la publicación manual")
        change.confirmation = GroupConfirmation(
            confirmed_at=now_utc(), post_url=canonical_post, evidence=supplied_evidence
        )
        change.status = "confirmed_manual"
        change.last_error = None
        _event(change, "user_confirmed_manual_publication")
        store.save(change)
        return change


def export_record(change: GroupChange) -> dict:
    """Vista exportable sin rutas absolutas, claves internas ni credenciales."""
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
        "reminder": "Publicación manual pendiente en navegador; socialctl no publica en Grupos por API.",
    }


def export_queue(brand, store: GroupStore, change_id: str | None = None) -> list[dict]:
    if change_id is not None:
        return [export_record(_load_bound(brand, store, change_id))]
    return [export_record(_load_bound(brand, store, item.id)) for item in _iter_changes(store)]
