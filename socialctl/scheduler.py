"""Cola local, persistente e idempotente de publicaciones programadas.

La cola no guarda secretos ni copia media: solo conserva la referencia al
post, la red, el instante y el estado de ejecución. El contenido sigue
viviendo en ``post.yml`` y los resultados en ``resultado.json``.
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import tempfile
import uuid
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from socialctl.brands import Brand
from socialctl.media import ruta_relativa_efectiva
from socialctl.models import Platform, Post

APPROVAL_HASH_VERSION = "v2"
_HASH_CHUNK_SIZE = 1024 * 1024
_UNSET = object()
_REUSABLE_HISTORY_STATUSES = {"cancelled", "published", "error"}


class ScheduleError(ValueError):
    """Error legible de formato o consistencia de la cola."""


class ScheduleEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    brand: str
    slug: str
    platform: str
    scheduled_at: datetime
    status: str = "approved"
    attempts: int = 0
    last_error: str | None = None
    platform_id: str | None = None
    content_hash: str | None = None
    approval_migration: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("scheduled_at", "created_at", "updated_at")
    @classmethod
    def validate_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ScheduleError("la fecha debe incluir zona horaria, por ejemplo -04:00")
        return value


def occurrence_id(brand_root: Path, entry: ScheduleEntry) -> str:
    """Existing publisher identity, shared by audit and migration."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL,
        f"{brand_root.resolve()}:{entry.id}:{entry.created_at.isoformat()}:{entry.scheduled_at.isoformat()}"))


def tombstone_identity(entry: ScheduleEntry) -> tuple:
    # Date edits must never resurrect the same transferred occurrence.
    return (entry.id, entry.created_at.astimezone(timezone.utc), entry.brand, entry.slug, entry.platform)


class ScheduleStore:
    """Persistencia atómica de la cola de una marca."""

    def __init__(self, brand_root: Path, *, backend: str | None = None) -> None:
        self.root = brand_root / ".socialctl"
        self.path = self.root / "schedules.json"
        self.mutation_lock_path = self.root / "schedules.lock"
        self.executor_lock_path = self.root / "executor.lock"
        self.database_path = self.root / "schedules.sqlite3"
        if backend not in (None, "json", "sqlite"):
            raise ScheduleError("backend debe ser json o sqlite")
        if backend == "sqlite" and not self.database_path.exists():
            with self._mutation_lock():
                if not self.database_path.exists():
                    self._ensure_root()
                    entries = self._load_unlocked()
                    from socialctl import schedule_sqlite
                    temporary = self.root / "schedules.sqlite3.pending"
                    try:
                        schedule_sqlite.save(temporary, [e.model_dump(mode="json") for e in entries])
                        os.replace(temporary, self.database_path)
                        self._sync_directory(self.root)
                    finally:
                        temporary.unlink(missing_ok=True)

    def load(self) -> list[ScheduleEntry]:
        return self._load_unlocked()

    def _load_unlocked(self) -> list[ScheduleEntry]:
        if self.database_path.exists():
            from socialctl import schedule_sqlite
            try:
                return [ScheduleEntry.model_validate(item) for item in schedule_sqlite.load(self.database_path)]
            except (OSError, ValueError, sqlite3.Error) as exc:
                raise ScheduleError(f"no se pudo leer SQLite: {exc}") from exc
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            entries = data.get("entries", []) if isinstance(data, dict) else data
            if not isinstance(entries, list):
                raise ScheduleError("la cola no contiene una lista de entradas")
            return [ScheduleEntry.model_validate(item) for item in entries]
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise ScheduleError(f"no se pudo leer la cola {self.path}: {exc}") from exc

    def save(self, entries: list[ScheduleEntry]) -> None:
        with self._mutation_lock():
            self._save_unlocked(entries)

    def _save_unlocked(self, entries: list[ScheduleEntry]) -> None:
        if self.database_path.exists():
            from socialctl import schedule_sqlite
            try:
                schedule_sqlite.save(self.database_path, [e.model_dump(mode="json") for e in entries])
                self._sync_directory(self.root)
                return
            except (OSError, ValueError, sqlite3.Error) as exc:
                raise ScheduleError(f"no se pudo guardar SQLite: {exc}") from exc
        payload = {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "entries": [entry.model_dump(mode="json") for entry in entries],
        }
        tmp_name: str | None = None
        try:
            self._ensure_root()
            fd, tmp_name = tempfile.mkstemp(
                prefix="schedules.", suffix=".tmp", dir=self.root
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
            # El fsync del fichero cubre sus bytes, pero el rename solo es
            # durable al sincronizar también el directorio que contiene la
            # entrada. claim_due no puede devolver ``running`` (y por tanto
            # no puede abrir la puerta a publicar) antes de completar esto.
            self._sync_directory(self.root)
        except OSError as exc:
            if tmp_name is not None:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
            raise ScheduleError(f"no se pudo guardar la cola {self.path}: {exc}") from exc

    def _ensure_root(self) -> None:
        """Crea el directorio de cola y hace durable su entrada de padre.

        Se sincroniza el padre en cada guardado, incluso si ``.socialctl`` ya
        existe: un intento previo pudo crear el directorio y fallar justo en
        ese fsync, de modo que comprobar solo ``exists()`` perdería el retry.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        self._sync_directory(self.root.parent)

    def backup(self, destination: Path) -> None:
        """Consistent snapshot while holding the queue mutation lock."""
        with self._mutation_lock():
            if self.database_path.exists():
                from socialctl import schedule_sqlite
                schedule_sqlite.backup(self.database_path, destination)
            else:
                destination.write_bytes(self.path.read_bytes())
            with destination.open("rb") as handle:
                os.fsync(handle.fileno())
            self._sync_directory(destination.parent)

    @staticmethod
    def _sync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def add(self, entry: ScheduleEntry) -> None:
        with self._mutation_lock():
            entries = self._load_unlocked()
            for old in entries:
                if old.id == entry.id and old.status not in _REUSABLE_HISTORY_STATUSES:
                    raise ScheduleError(f"ya existe una programación pendiente con id {entry.id}")
            entries.append(entry)
            entries.sort(key=lambda item: item.scheduled_at)
            self._save_unlocked(entries)

    def add_many(self, new_entries: list[ScheduleEntry]) -> None:
        """Añade un lote de forma atómica: o entran todas o ninguna."""
        with self._mutation_lock():
            entries = self._load_unlocked()
            ids = {
                entry.id
                for entry in entries
                if entry.status not in _REUSABLE_HISTORY_STATUSES
            }
            nuevos = [entry.id for entry in new_entries]
            if len(set(nuevos)) != len(nuevos):
                raise ScheduleError("el lote contiene identificadores repetidos")
            repetidos = ids.intersection(nuevos)
            if repetidos:
                raise ScheduleError(f"ya existe una programación pendiente con id {sorted(repetidos)[0]}")
            entries.extend(new_entries)
            entries.sort(key=lambda item: item.scheduled_at)
            self._save_unlocked(entries)

    def update(self, entry: ScheduleEntry) -> None:
        with self._mutation_lock():
            entries = self._load_unlocked()
            index = self._current_index(entries, entry.id)
            if entries[index].created_at != entry.created_at:
                raise ScheduleError(
                    f"la programación {entry.id} es una ocurrencia histórica obsoleta"
                )
            entries[index] = entry
            self._save_unlocked(entries)

    def get(self, entry_id: str) -> ScheduleEntry:
        entries = self.load()
        return entries[self._current_index(entries, entry_id)]

    def due(self, now: datetime | None = None) -> list[ScheduleEntry]:
        self.assert_ready()
        tombstones = self.tombstones()
        moment = now or datetime.now(timezone.utc)
        return [
            entry for entry in self.load()
            if tombstone_identity(entry) not in tombstones
            and entry.status == "approved" and entry.scheduled_at.astimezone(timezone.utc) <= moment
        ]

    def claim_due(self, entry_id: str, moment: datetime, *,
                  expected: ScheduleEntry | None = None) -> ScheduleEntry | None:
        """Pasa una entrada vencida de approved a running de forma atómica."""
        with self._mutation_lock():
            entries = self._load_unlocked()
            entry = entries[self._current_index(entries, entry_id)]
            # Slot admission covers this snapshot, not any later occurrence with
            # the same reusable ID. Compare all evidence under the mutation lock.
            if expected is not None and entry.model_dump(mode="json") != expected.model_dump(mode="json"):
                return None
            if (
                tombstone_identity(entry) in self.tombstones()
                or entry.status != "approved"
                or entry.scheduled_at.astimezone(timezone.utc) > moment
            ):
                return None
            entry.status = "running"
            entry.attempts += 1
            entry.updated_at = moment
            self._save_unlocked(entries)
            return entry.model_copy(deep=True)

    def cancel(self, entry_id: str, moment: datetime) -> ScheduleEntry:
        """Cancela solo si la entrada sigue en un estado cancelable."""
        with self._mutation_lock():
            entries = self._load_unlocked()
            entry = entries[self._current_index(entries, entry_id)]
            if entry.status not in {"approved", "error"}:
                raise ScheduleError(
                    f"no se puede cancelar {entry_id}: estado actual {entry.status}"
                )
            entry.status = "cancelled"
            entry.updated_at = moment
            self._save_unlocked(entries)
            return entry.model_copy(deep=True)

    def reschedule(
        self, entry_id: str, scheduled_at: datetime, moment: datetime
    ) -> ScheduleEntry:
        """Reprograma solo si no fue reclamada por un executor concurrente."""
        with self._mutation_lock():
            entries = self._load_unlocked()
            entry = entries[self._current_index(entries, entry_id)]
            if entry.status not in {"approved", "error"}:
                raise ScheduleError(
                    f"no se puede reprogramar {entry_id}: estado actual {entry.status}"
                )
            entry.scheduled_at = scheduled_at
            entry.status = "approved"
            entry.last_error = None
            entry.updated_at = moment
            self._save_unlocked(entries)
            return entry.model_copy(deep=True)

    def transition(
        self,
        entry_id: str,
        expected_statuses: set[str],
        *,
        status: str,
        moment: datetime,
        last_error: str | None = None,
        platform_id: str | None | object = _UNSET,
    ) -> ScheduleEntry | None:
        """Actualiza campos de ejecución solo desde un estado todavía vigente."""
        with self._mutation_lock():
            entries = self._load_unlocked()
            entry = entries[self._current_index(entries, entry_id)]
            if entry.status not in expected_statuses:
                return None
            entry.status = status
            entry.last_error = last_error
            if platform_id is not _UNSET:
                entry.platform_id = platform_id  # type: ignore[assignment]
            entry.updated_at = moment
            self._save_unlocked(entries)
            return entry.model_copy(deep=True)

    @staticmethod
    def _current_index(entries: list[ScheduleEntry], entry_id: str) -> int:
        matches = [index for index, entry in enumerate(entries) if entry.id == entry_id]
        if not matches:
            raise ScheduleError(f"no existe la programación {entry_id}")
        active = [
            index
            for index in matches
            if entries[index].status not in _REUSABLE_HISTORY_STATUSES
        ]
        if len(active) > 1:
            raise ScheduleError(
                f"la cola contiene varias programaciones activas con id {entry_id}"
            )
        if active:
            return active[0]
        return max(matches, key=lambda index: (entries[index].created_at, index))

    def recover_stale(self, max_age_s: int = 900) -> int:
        """Aísla para revisión una ejecución cuyo resultado remoto es incierto."""
        now = now_utc()
        with self._mutation_lock():
            entries = self._load_unlocked()
            changed = 0
            for entry in entries:
                age = (now - entry.updated_at.astimezone(timezone.utc)).total_seconds()
                if entry.status == "running" and age > max_age_s:
                    entry.status = "manual_review"
                    entry.last_error = (
                        "ejecución interrumpida: confirma el resultado remoto antes de reintentar"
                    )
                    entry.updated_at = now
                    changed += 1
            if changed:
                self._save_unlocked(entries)
            return changed

    @contextmanager
    def _mutation_lock(self) -> Iterator[None]:
        with self._file_lock(self.mutation_lock_path, nonblocking=False):
            self.assert_ready()
            yield

    def tombstones(self) -> set[tuple]:
        path = self.root / "legacy-tombstones.json"
        if not path.exists():
            return set()
        try:
            data = json.loads(path.read_bytes())
            if set(data) != {"version", "entries"} or type(data["version"]) is not int or data["version"] != 1 or not isinstance(data["entries"], dict):
                raise ValueError()
            identities = set()
            for key, item in data["entries"].items():
                if set(item) != {"entry", "digest"} or not isinstance(item["digest"], str) or len(item["digest"]) != 64:
                    raise ValueError()
                int(item["digest"], 16)
                entry = ScheduleEntry.model_validate(item["entry"])
                if key != occurrence_id(self.root.parent, entry) or entry.brand != self.root.parent.name:
                    raise ValueError()
                identity = tombstone_identity(entry)
                if identity in identities:
                    raise ValueError()
                identities.add(identity)
            return identities
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise ScheduleError("ledger de tombstones inválido; ejecución bloqueada") from exc

    def assert_ready(self) -> None:
        from socialctl.schedule_remote import is_remote, REMOTE_NOTICE
        if is_remote(self.root.parent):
            raise ScheduleError(REMOTE_NOTICE)
        from socialctl.native_schedule.migration import validate_committed_guard
        self.tombstones()  # Corruption fails closed even without a guard.
        guard = self.root / "migration-active.json"
        if guard.exists():
            if not validate_committed_guard(self):
                raise ScheduleError("migración interrumpida o en curso; exige recuperación explícita antes de ejecutar")
        for path in (self.root / "native-migrations").glob("*/intent.json"):
            try:
                if json.loads(path.read_bytes())["status"] == "committed" and validate_committed_guard(self):
                    continue
            except (OSError, ValueError, KeyError):
                pass
            raise ScheduleError("transferencia nativa interrumpida; exige migration-recover")
        for path in (self.root / "migrations").glob("*/intent.json"):
            try:
                if json.loads(path.read_bytes())["status"] in {"committed", "rolled_back"}:
                    continue
            except (OSError, ValueError, KeyError):
                pass
            raise ScheduleError("migración interrumpida; exige rollback explícito antes de ejecutar")

    @contextmanager
    def executor_lock(self) -> Iterator[None]:
        """Impide que dos procesos ejecuten simultáneamente la misma marca."""
        self.root.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            self.executor_lock_path, os.O_RDWR | os.O_CREAT, 0o600
        )
        acquired = False
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError as exc:
                raise ScheduleError(
                    "ya hay otro ejecutor procesando la cola de esta marca"
                ) from exc
            self.assert_ready()
            yield
        finally:
            if acquired:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    @contextmanager
    def _file_lock(self, path: Path, *, nonblocking: bool) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        operation = fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0)
        acquired = False
        try:
            fcntl.flock(descriptor, operation)
            acquired = True
            yield
        finally:
            if acquired:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def parse_scheduled_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScheduleError(
            "fecha inválida; usa ISO 8601 con zona horaria, por ejemplo "
            "2026-09-15T14:00:00-04:00"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ScheduleError("la fecha debe incluir zona horaria, por ejemplo -04:00")
    return parsed


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def approval_hash(post: Post, brand: Brand, platform: Platform) -> str:
    """Huella v2 de payload, identidad de cuenta y bytes reales de media."""
    platform_post = post.platforms[platform]
    media = [
        {
            "kind": asset.kind.value,
            "path": ruta_relativa_efectiva(asset),
        }
        for asset in platform_post.media
    ]
    approved = {
        "account_identity": {
            "account": brand.cuentas.get(platform.value) or {},
            "brand": brand.nombre,
            "platform": platform.value,
        },
        "payload": {
            "body": platform_post.body,
            "first_comment": platform_post.first_comment,
            "hashtags": platform_post.hashtags,
            "link": platform_post.link,
            "media": media,
            "platform": platform.value,
            "privacy": platform_post.privacy,
            "title": platform_post.title,
        },
        "schema": "socialctl.schedule.approval.v2",
    }
    # Absent additive fields retain every old v2 approval byte-for-byte.
    # An explicit empty list is intent and must produce a different approval.
    for field in ("tags", "visible_hashtags", "content_origin", "source_video_id"):
        value = getattr(platform_post, field)
        if value is not None:
            approved["payload"][field] = value
    digest = hashlib.sha256(_canonical_json(approved))
    for asset in platform_post.media:
        digest.update(b"\0media-bytes\0")
        digest.update(_canonical_json(ruta_relativa_efectiva(asset)))
        try:
            with asset.path.open("rb") as handle:
                while chunk := handle.read(_HASH_CHUNK_SIZE):
                    digest.update(chunk)
        except OSError as exc:
            raise ScheduleError(
                f"no se pudo calcular la huella de aprobación de {asset.path}: {exc}"
            ) from exc
    return f"{APPROVAL_HASH_VERSION}:{digest.hexdigest()}"


def schedule_digest(slug: str, approvals: dict[str, str], at: datetime) -> str:
    """Binds an existing-post preview to payloads, identity and the exact instant."""
    return hashlib.sha256(_canonical_json({
        "schema": "socialctl.schedule.preview.v1", "slug": slug,
        "at": at.astimezone(timezone.utc).isoformat(),
        "approvals": approvals,
    })).hexdigest()


def legacy_approval_hash(post: Post, platform: Platform) -> str:
    """Reproduce exactamente la huella sin versión de las colas existentes."""
    platform_post = post.platforms[platform]
    digest = hashlib.sha256()
    digest.update(platform_post.body.encode("utf-8"))
    digest.update((platform_post.title or "").encode("utf-8"))
    digest.update((platform_post.first_comment or "").encode("utf-8"))
    digest.update("\0".join(platform_post.hashtags).encode("utf-8"))
    for asset in platform_post.media:
        digest.update(str(asset.path).encode("utf-8"))
        try:
            stat = asset.path.stat()
            digest.update(str(stat.st_size).encode("ascii"))
            digest.update(str(stat.st_mtime_ns).encode("ascii"))
        except OSError:
            digest.update(b"missing")
    return digest.hexdigest()


def approval_review_reason(
    stored_hash: str | None,
    post: Post,
    brand: Brand,
    platform: Platform,
) -> str | None:
    """Devuelve el motivo para retener una entrada; ``None`` autoriza v2."""
    if not stored_hash:
        return "entrada antigua sin huella de aprobación; requiere revisión manual"
    if stored_hash.startswith(f"{APPROVAL_HASH_VERSION}:"):
        current = approval_hash(post, brand, platform)
        if hmac.compare_digest(stored_hash, current):
            return None
        return (
            "la huella de aprobación no coincide: cambió el contenido, la media "
            "o la identidad de cuenta; requiere revisión manual"
        )
    if ":" in stored_hash:
        return "versión de huella de aprobación desconocida; requiere revisión manual"
    current_legacy = legacy_approval_hash(post, platform)
    if not hmac.compare_digest(stored_hash, current_legacy):
        return (
            "la huella de aprobación antigua no coincide con el contenido actual; "
            "requiere revisión manual"
        )
    return (
        "la huella de aprobación antigua coincide, pero no cubría los bytes de media "
        "ni la identidad de cuenta; requiere revisión manual"
    )
