"""ChangeSets locales, inmutables y verificables para metadatos de YouTube."""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from socialctl.management.youtube import (
    YouTubeManagementClient,
    YouTubeManagementError,
    YouTubeUpdateConflict,
    YouTubeUpdateRejected,
    YouTubeUpdateUncertain,
)

SCHEMA_VERSION = 1
PATCH_FIELDS = frozenset(
    {"title", "description", "tags", "categoryId", "defaultLanguage"}
)
IMMUTABLE_FIELDS = (
    "id",
    "version",
    "platform",
    "video_id",
    "patch",
    "target_account",
    "observed_etag",
    "before",
    "after",
    "created_at",
)


def _validate_patch_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("patch debe ser un mapping")
    if not value:
        raise ValueError("patch está vacío")
    unknown = set(value) - PATCH_FIELDS
    if unknown:
        raise ValueError(
            f"campo de patch desconocido: {', '.join(sorted(str(x) for x in unknown))}"
        )
    nulls = [key for key, item in value.items() if item is None]
    if nulls:
        raise ValueError(
            f"patch no admite null ({', '.join(sorted(nulls))}); "
            "omitir conserva el valor"
        )
    _validate_patch_types(value)
    return dict(value)


class ChangeError(ValueError):
    """Error legible de formato, persistencia o estado de un ChangeSet."""


class ApprovalMismatch(ChangeError):
    """La aprobación no cubre la huella exacta de esta propuesta."""


class ChangeConflict(ChangeError):
    """El contenido remoto cambió desde el preview o durante el PUT."""


class ChangeLocked(ChangeError):
    """Otro proceso está aplicando el mismo ChangeSet."""


class EditFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    platform: Literal["youtube"]
    video_id: str = Field(min_length=1)
    patch: dict[str, Any]

    @field_validator("patch", mode="before")
    @classmethod
    def validate_patch(cls, value: Any) -> dict[str, Any]:
        return _validate_patch_mapping(value)


class ChangeSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    version: Literal[1] = 1
    platform: Literal["youtube"] = "youtube"
    video_id: str
    patch: dict[str, Any]
    target_account: str
    observed_etag: str
    before: dict[str, Any]
    after: dict[str, Any]
    fingerprint: str
    status: str = "proposed"
    verified: bool = False
    created_at: datetime
    updated_at: datetime
    journal: list[dict[str, Any]] = []


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def load_edit_file(path: Path) -> EditFile:
    """Carga el esquema inicial de una propuesta y rechaza omisiones ambiguas."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ChangeError(f"no se pudo leer el archivo de cambios {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ChangeError("el archivo de cambios debe contener un mapping")
    video_id = raw.get("video_id")
    if (
        not isinstance(video_id, str)
        or not video_id.strip()
        or video_id != video_id.strip()
    ):
        raise ChangeError("video_id debe ser texto no vacío y sin espacios exteriores")
    try:
        return EditFile.model_validate(raw)
    except ValidationError as exc:
        raise ChangeError(f"archivo de cambios inválido (campo extra o tipo): {exc}") from exc


def _validate_patch_types(patch: dict[str, Any]) -> None:
    for field in PATCH_FIELDS - {"tags"}:
        if field in patch and not isinstance(patch[field], str):
            raise ChangeError(f"patch.{field} debe ser texto")
    if "tags" in patch:
        tags = patch["tags"]
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise ChangeError("patch.tags debe ser una lista de textos")


def _validate_snippet(snippet: dict[str, Any]) -> None:
    title = snippet.get("title")
    category = snippet.get("categoryId")
    if not isinstance(title, str) or not title.strip():
        raise ChangeError("YouTube exige un título no vacío")
    if len(title) > 100:
        raise ChangeError(f"el título tiene {len(title)} caracteres y el máximo son 100")
    if not isinstance(category, str) or not category.strip():
        raise ChangeError("YouTube exige una categoría (categoryId) no vacía")

    description = snippet.get("description", "")
    if not isinstance(description, str):
        raise ChangeError("description debe ser texto")
    size = len(description.encode("utf-8"))
    if size > 5000:
        raise ChangeError(f"la descripción ocupa {size} bytes y el máximo son 5000 bytes")

    tags = snippet.get("tags", [])
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise ChangeError("tags debe ser una lista de textos")
    aggregate = len(",".join(f'"{tag}"' if " " in tag else tag for tag in tags))
    if aggregate > 500:
        raise ChangeError(
            f"las etiquetas ocupan {aggregate} caracteres agregados y el máximo son 500"
        )

    for field in ("defaultLanguage", "defaultAudioLanguage"):
        if field in snippet and not isinstance(snippet[field], str):
            raise ChangeError(f"{field} debe ser texto")


def _immutable_payload(change: ChangeSet | dict[str, Any]) -> dict[str, Any]:
    raw = change.model_dump(mode="json") if isinstance(change, ChangeSet) else change
    return {field: raw[field] for field in IMMUTABLE_FIELDS}


def _fingerprint(change: ChangeSet | dict[str, Any]) -> str:
    canonical = json.dumps(
        _immutable_payload(change),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class ChangeStore:
    """Persistencia JSON atómica y lock interproceso por ChangeSet."""

    model_type = ChangeSet
    fingerprint_for = staticmethod(_fingerprint)

    def __init__(self, brand_root: Path) -> None:
        self.root = brand_root / ".socialctl" / "changes"

    def _validate_id(self, change_id: str) -> str:
        try:
            parsed = uuid.UUID(change_id)
        except (ValueError, TypeError, AttributeError):
            raise ChangeError(f"id de ChangeSet inválido: debe ser un UUID, no {change_id!r}") from None
        canonical = str(parsed)
        if canonical != change_id:
            raise ChangeError("id de ChangeSet inválido: el UUID debe estar en formato canónico")
        return canonical

    def path_for(self, change_id: str) -> Path:
        return self.root / f"{self._validate_id(change_id)}.json"

    def load(self, change_id: str) -> ChangeSet:
        path = self.path_for(change_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            change = self.model_type.model_validate(raw)
        except FileNotFoundError:
            raise ChangeError(f"no existe el ChangeSet {change_id}") from None
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise ChangeError(f"no se pudo leer el ChangeSet {change_id}: {exc}") from exc
        self._validate_id(change.id)
        if change.id != change_id:
            raise ChangeError(
                f"el id interno del ChangeSet no coincide con el nombre {change_id}"
            )
        expected = self.fingerprint_for(change)
        if not hmac.compare_digest(change.fingerprint, expected):
            raise ChangeError(
                f"la huella del ChangeSet {change_id} no coincide; la propuesta fue alterada"
            )
        return change

    def save(self, change: ChangeSet) -> None:
        self._validate_id(change.id)
        if not hmac.compare_digest(change.fingerprint, self.fingerprint_for(change)):
            raise ChangeError("no se guardó el ChangeSet: su huella inmutable no coincide")
        temporary: str | None = None
        try:
            self._ensure_root()
            fd, temporary = tempfile.mkstemp(
                prefix=f"{change.id}.", suffix=".tmp", dir=self.root
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    change.model_dump(mode="json"),
                    handle,
                    indent=2,
                    ensure_ascii=False,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path_for(change.id))
            # El fsync del fichero temporal no hace durable el cambio de nombre:
            # para que un intent persistido no pueda volver a `proposed` tras un
            # corte de energía, también hay que sincronizar el directorio que
            # contiene la entrada reemplazada antes de permitir el PUT remoto.
            self._sync_directory(self.root)
        except OSError as exc:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
            raise ChangeError(f"no se pudo guardar el ChangeSet {change.id}: {exc}") from exc

    def _ensure_root(self) -> None:
        """Crea y sincroniza la cadena de almacenamiento en cada intento.

        No basta con sincronizar solo al crear: si ese fsync falla después del
        mkdir, el directorio queda visible y el siguiente intento debe repetir
        el fsync del padre aunque ya exista.
        """
        for directory in (self.root.parent, self.root):
            directory.mkdir(exist_ok=True)
            self._sync_directory(directory.parent)

    @staticmethod
    def _sync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @contextmanager
    def apply_lock(self, change_id: str) -> Iterator[None]:
        validated = self._validate_id(change_id)
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{validated}.lock"
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ChangeLocked(
                    f"otro proceso está aplicando el ChangeSet {change_id}"
                ) from None
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def prepare_youtube_change(
    youtube: YouTubeManagementClient, store: ChangeStore, edit: EditFile
) -> ChangeSet:
    # Un modelo Pydantic puede mutarse en memoria después de su construcción
    # (incluido su dict `patch`). Revalidar una copia cruda impide que un
    # llamador de servicio eluda el mismo límite que aplica `load_edit_file`.
    try:
        validated = EditFile.model_validate(edit.model_dump(mode="python"))
    except ValidationError as exc:
        raise ChangeError(f"patch de la propuesta inválido: {exc}") from exc

    inspected = youtube.inspect(validated.video_id)
    before = youtube.editable_snippet(inspected)
    after = {**before, **validated.patch}
    _validate_snippet(before)
    _validate_snippet(after)
    moment = now_utc()
    raw: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "version": SCHEMA_VERSION,
        "platform": "youtube",
        "video_id": validated.video_id,
        "patch": dict(validated.patch),
        "target_account": inspected.channel_id,
        "observed_etag": inspected.etag,
        "before": before,
        "after": after,
        "fingerprint": "",
        "status": "proposed",
        "verified": False,
        "created_at": moment,
        "updated_at": moment,
        "journal": [{"at": moment.isoformat(), "event": "prepared"}],
    }
    change = ChangeSet.model_validate(raw)
    change.fingerprint = _fingerprint(change)
    store.save(change)
    return change


def _append(change: ChangeSet, event: str, **details: Any) -> None:
    moment = now_utc()
    change.updated_at = moment
    change.journal.append({"at": moment.isoformat(), "event": event, **details})


def _same_snippet(current: dict[str, Any], expected: dict[str, Any]) -> bool:
    return current == expected


def apply_youtube_change(
    youtube: YouTubeManagementClient,
    store: ChangeStore,
    change_id: str,
    approval_digest: str,
) -> ChangeSet:
    """Aplica o reconcilia un ChangeSet; nunca repite un PUT incierto."""
    with store.apply_lock(change_id):
        change = store.load(change_id)
        if not hmac.compare_digest(approval_digest, change.fingerprint):
            raise ApprovalMismatch(
                "la aprobación no coincide exactamente con la huella de la propuesta"
            )
        if youtube.configured_channel_id != change.target_account:
            raise ChangeError("la cuenta configurada ya no coincide con el ChangeSet")

        if change.status in {"applying", "uncertain"}:
            return _reconcile(youtube, store, change)
        if change.status != "proposed":
            raise ChangeError(
                f"el ChangeSet está en estado {change.status}; no se repetirá el PUT"
            )

        current = youtube.inspect(change.video_id)
        current_snippet = youtube.editable_snippet(current)
        if not _same_snippet(current_snippet, change.before):
            change.status = "conflict"
            _append(change, "pre_write_conflict")
            store.save(change)
            raise ChangeConflict(
                "conflict: el snippet remoto cambió desde el preview; no se envió ningún PUT"
            )

        change.status = "applying"
        _append(
            change,
            "write_intent",
            approval_digest=approval_digest,
            if_match=current.etag,
        )
        store.save(change)

        try:
            youtube.update_snippet(change.video_id, change.after, etag=current.etag)
        except YouTubeUpdateConflict as exc:
            change.status = "conflict"
            _append(change, "etag_conflict", error=str(exc))
            store.save(change)
            raise ChangeConflict(str(exc)) from None
        except YouTubeUpdateRejected as exc:
            change.status = "failed"
            _append(change, "write_rejected", error=str(exc))
            store.save(change)
            raise ChangeError(str(exc)) from None
        except YouTubeUpdateUncertain as exc:
            change.status = "uncertain"
            _append(change, "write_uncertain", error=str(exc))
            store.save(change)
            raise ChangeError(str(exc)) from None
        except Exception as exc:
            change.status = "uncertain"
            safe = f"resultado incierto: fallo inesperado ({type(exc).__name__})"
            _append(change, "write_uncertain", error=safe)
            store.save(change)
            raise ChangeError(safe) from None

        try:
            verified = youtube.inspect(change.video_id)
        except YouTubeManagementError as exc:
            change.status = "uncertain"
            _append(change, "verification_uncertain", error=str(exc))
            store.save(change)
            raise ChangeError(
                "resultado incierto: YouTube aceptó el PUT pero no se pudo verificar"
            ) from None
        if not _same_snippet(youtube.editable_snippet(verified), change.after):
            change.status = "uncertain"
            _append(change, "verification_mismatch")
            store.save(change)
            raise ChangeError(
                "resultado incierto: el snippet releído no coincide con la propuesta"
            )

        change.status = "applied"
        change.verified = True
        _append(change, "verified_applied", etag=verified.etag)
        store.save(change)
        return change


def _reconcile(
    youtube: YouTubeManagementClient, store: ChangeStore, change: ChangeSet
) -> ChangeSet:
    """Solo relee: nunca llama a update al retomar un intento incierto."""
    try:
        current = youtube.inspect(change.video_id)
    except YouTubeManagementError as exc:
        _append(change, "reconciliation_failed", error=str(exc))
        store.save(change)
        raise ChangeError(
            "el resultado sigue incierto: no se pudo reconciliar por lectura"
        ) from None

    snippet = youtube.editable_snippet(current)
    if _same_snippet(snippet, change.after):
        change.status = "applied"
        change.verified = True
        _append(change, "reconciled_applied", etag=current.etag)
        store.save(change)
        return change

    change.status = "manual_review"
    change.verified = False
    event = "reconciled_still_before" if _same_snippet(snippet, change.before) else "reconciled_diverged"
    _append(change, event)
    store.save(change)
    raise ChangeError(
        "el PUT incierto requiere revisión manual; la reconciliación fue solo lectura "
        "y no repitió la escritura"
    )
