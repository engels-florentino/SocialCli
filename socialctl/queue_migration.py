"""Verified relocation of existing approvals; never publishes or approves copy."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid
from pathlib import Path

import httpx

from socialctl.brands import Brand, cargar_brand
from socialctl.hosted_media import MIMES, attest_public_media, file_digest, public_url
from socialctl.media import ruta_relativa_efectiva
from socialctl.migration_files import durable_write, rewrite_media, write_json
from socialctl.models import Platform
from socialctl.postfile import cargar_post, _ruta
from socialctl.publisher import render_preview, validar_todo
from socialctl.rutas import validar_ruta_relativa
from socialctl.scheduler import ScheduleEntry, ScheduleError, ScheduleStore, _canonical_json, approval_hash, legacy_approval_hash, now_utc

LIMITATION = "La huella legacy no probaba bytes ni identidad histórica de cuenta, link o privacy. Esta operación conserva los valores actuales verificados; no crea una aprobación social nueva."


def _assert_no_native_transfer(store: ScheduleStore) -> None:
    if (store.root / "legacy-tombstones.json").exists() or (store.root / "native-migrations").exists():
        raise ScheduleError("transferencia nativa existente; no restaurar legacy, conservar ledger y reconciliar")


def _digest(value: dict) -> str:
    return hashlib.sha256(_canonical_json({k: v for k, v in value.items() if k != "digest"})).hexdigest()


def _directory(brand: Brand, proposal_id: str) -> Path:
    try:
        if str(uuid.UUID(proposal_id)) != proposal_id:
            raise ValueError()
    except ValueError:
        raise ScheduleError("id de propuesta debe ser UUID") from None
    return _safe(brand, f".socialctl/migrations/{proposal_id}")


def _safe(brand: Brand, relative: str) -> Path:
    return validar_ruta_relativa(brand.raiz, relative, ScheduleError, "ruta de migración")


def load_proposal(brand: Brand, proposal_id: str) -> dict:
    data = json.loads((_directory(brand, proposal_id) / "proposal.json").read_bytes())
    if data["brand"] != brand.nombre or data["brand_root"] != str(brand.raiz.resolve()) or data["id"] != proposal_id:
        raise ScheduleError("la propuesta pertenece a otra marca o raíz")
    if not hmac.compare_digest(data["digest"], _digest(data)):
        raise ScheduleError("digest de propuesta no coincide")
    if any(item.get("approval_migration") != proposal_id for item in data["approvals"]):
        raise ScheduleError("propuesta sin vinculación de procedencia; vuelve a preparar la migración")
    return data


def _originals(brand: Brand, store: ScheduleStore, entries: list[ScheduleEntry]) -> dict:
    paths = [brand.raiz / "accounts.yml", store.database_path if store.database_path.exists() else store.path]
    paths += [_ruta(brand, slug) for slug in sorted({e.slug for e in entries if e.status == "approved"})]
    return {str(path.relative_to(brand.raiz)): file_digest(path) for path in paths}


def _verify(brand: Brand, proposal: dict) -> None:
    for relative, expected in proposal["originals"].items():
        if file_digest(_safe(brand, relative)) != expected:
            raise ScheduleError(f"entrada original modificada: {relative}")
    fresh = cargar_brand(brand.raiz.parent, brand.nombre)
    if fresh.cuentas != proposal["accounts"]:
        raise ScheduleError("identidad de cuenta modificada")
    store = ScheduleStore(brand.raiz)
    if [e.model_dump(mode="json") for e in store.load()] != proposal["entries"]:
        raise ScheduleError("cola modificada")
    for item in proposal["media"]:
        if file_digest(_safe(brand, item["source"])) != item["sha256"]:
            raise ScheduleError("bytes suministrados modificados")
    for item in proposal["approvals"]:
        post = cargar_post(fresh, item["slug"])
        if legacy_approval_hash(post, Platform(item["platform"])) != item["old_hash"]:
            raise ScheduleError("evidencia de aprobación legacy modificada")


def prepare(brand: Brand, *, backend: str = "json", dry_run: bool = False) -> dict:
    if backend not in ("json", "sqlite"):
        raise ScheduleError("backend inválido")
    store = ScheduleStore(brand.raiz)
    with store.executor_lock(), store._mutation_lock():
        _assert_no_native_transfer(store)
        brand = cargar_brand(brand.raiz.parent, brand.nombre)
        entries = store.load()
        if store.database_path.exists():
            backend = "sqlite"
        active_ids = set()
        approved = []
        for entry in entries:
            if entry.brand != brand.nombre:
                raise ScheduleError("cola contiene otra marca")
            if entry.status in {"published", "cancelled", "error"}:
                continue
            if entry.status != "approved" or entry.attempts or entry.platform_id or entry.id in active_ids:
                raise ScheduleError("estado/identidad activa ambigua o entrada ya intentada")
            active_ids.add(entry.id)
            if not entry.content_hash or not re.fullmatch(r"[0-9a-f]{64}", entry.content_hash):
                raise ScheduleError("se requiere aprobación legacy existente")
            approved.append(entry)
        if not approved:
            raise ScheduleError("no hay aprobaciones legacy migrables")
        proposal_id = str(uuid.uuid4())
        media, posts, approvals, previews = {}, {}, [], []
        for slug in dict.fromkeys(e.slug for e in approved):
            post = cargar_post(brand, slug)
            selected = [e for e in approved if e.slug == slug]
            replacements = {}
            previews.append(render_preview(post, validar_todo(post, brand), destinos=[Platform(e.platform) for e in selected]))
            for entry in selected:
                platform = Platform(entry.platform)
                if legacy_approval_hash(post, platform) != entry.content_hash:
                    raise ScheduleError("huella legacy no coincide")
                replacements[entry.platform] = {}
                if not post.platforms[platform].media:
                    raise ScheduleError("migración exige media suministrada")
                for asset in post.platforms[platform].media:
                    sha = file_digest(asset.path)
                    extension = asset.path.suffix.lower()
                    if extension not in MIMES:
                        raise ScheduleError("MIME de media no soportado")
                    relative = f"approved/sha256/{sha}{extension}"
                    source = str(asset.path.relative_to(brand.raiz))
                    media[source] = {"source": source, "relative_path": relative, "sha256": sha, "size": asset.path.stat().st_size, "mime": MIMES[extension],
                                     "public_url": public_url(brand.cuentas.get("instagram", {}).get("media_url_base", ""), relative)}
                    replacements[entry.platform][ruta_relativa_efectiva(asset)] = relative
                projected = post.model_copy(deep=True)
                for asset in projected.platforms[platform].media:
                    asset.ruta_relativa = replacements[entry.platform][ruta_relativa_efectiva(asset)]
                approvals.append({"id": entry.id, "slug": entry.slug, "platform": entry.platform, "created_at": entry.created_at.isoformat(), "old_hash": entry.content_hash,
                                  "new_hash": approval_hash(projected, brand, platform), "approval_migration": proposal_id})
            path = _ruta(brand, slug)
            posts[str(path.relative_to(brand.raiz))] = rewrite_media(path.read_bytes(), replacements).decode("utf-8")
        proposal = {"version": 1, "id": proposal_id, "brand": brand.nombre, "brand_root": str(brand.raiz.resolve()), "created_at": now_utc().isoformat(), "backend": backend,
                    "accounts": brand.cuentas, "originals": _originals(brand, store, entries), "entries": [e.model_dump(mode="json") for e in entries],
                    "approvals": approvals, "posts": posts, "media": list(media.values()), "preview": "\n\n".join(previews), "legacy_limitation": LIMITATION}
        proposal["digest"] = _digest(proposal)
        _verify(brand, proposal)
        if not dry_run:
            write_json(_directory(brand, proposal["id"]) / "proposal.json", proposal)
        return proposal


def stage(brand: Brand, proposal_id: str, output: Path) -> dict:
    store = ScheduleStore(brand.raiz)
    # Share the authority-handoff/executor lock and queue mutation lock through
    # the final manifest write; readiness checks inside both locks fail closed.
    with store.executor_lock(), store._mutation_lock():
        _assert_no_native_transfer(store)
        return _stage_locked(brand, proposal_id, output)


def _stage_locked(brand: Brand, proposal_id: str, output: Path) -> dict:
    proposal = load_proposal(brand, proposal_id)
    _verify(brand, proposal)
    files = {}
    for item in proposal["media"]:
        raw = _safe(brand, item["source"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise ScheduleError("bytes suministrados cambiaron durante staging")
        local = _safe(brand, "media/" + item["relative_path"])
        destination = validar_ruta_relativa(output.resolve(), item["relative_path"], ScheduleError, "staging")
        for path in (local, destination):
            if path.exists() and file_digest(path) != item["sha256"]:
                raise ScheduleError("destino existente contiene otros bytes")
            if not path.exists():
                durable_write(path, raw)
        files[item["relative_path"]] = {k: v for k, v in item.items() if k != "source"} | {"local_path": str(destination)}
    manifest = {"version": 1, "proposal": proposal_id, "digest": proposal["digest"], "files": list(files.values())}
    write_json(output / "transfer-manifest.json", manifest)
    return manifest


def apply(brand: Brand, proposal_id: str, digest: str, *, acknowledge_legacy_limitations: bool = False, client: httpx.Client | None = None) -> dict:
    proposal = load_proposal(brand, proposal_id)
    if not hmac.compare_digest(proposal["digest"], digest) or not acknowledge_legacy_limitations:
        raise ScheduleError("exige digest y reconocimiento explícito de limitaciones legacy")
    directory = _directory(brand, proposal_id)
    if (directory / "intent.json").exists():
        raise ScheduleError("migración ya intentada; usa rollback explícito")
    store = ScheduleStore(brand.raiz)
    with store.executor_lock(), store._mutation_lock():
        _assert_no_native_transfer(store)
        _verify(brand, proposal)
        attestations = []
        for item in {m["relative_path"]: m for m in proposal["media"]}.values():
            path = _safe(brand, "media/" + item["relative_path"])
            attestations.append(attest_public_media(path, item["public_url"], client=client))
        _verify(brand, proposal)
        # Back up every replaceable original before intent or mutation.
        backups = {}
        for relative, fingerprint in proposal["originals"].items():
            path = _safe(brand, relative)
            backup = directory / "backup" / relative
            if path == store.database_path:
                from socialctl import schedule_sqlite
                durable_write(backup, b"")
                schedule_sqlite.backup(path, backup)
                with backup.open("rb") as handle:
                    os.fsync(handle.fileno())
                store._sync_directory(backup.parent)
                fingerprint = file_digest(backup)
            else:
                durable_write(backup, path.read_bytes())
            if file_digest(backup) != fingerprint:
                raise ScheduleError("backup no coincide")
            backups[relative] = fingerprint
        intent = {"proposal": proposal_id, "digest": digest, "status": "applying", "backups": backups,
                  "database_existed": store.database_path.exists(), "attestations": attestations,
                  "legacy_limitation": LIMITATION, "acknowledged": True, "started_at": now_utc().isoformat()}
        write_json(directory / "intent.json", intent)
        write_json(store.root / "migration-active.json", {"proposal": proposal_id, "digest": digest})
        for relative, text in proposal["posts"].items():
            durable_write(_safe(brand, relative), text.encode("utf-8"))
        fresh = cargar_brand(brand.raiz.parent, brand.nombre)
        entries = [ScheduleEntry.model_validate(item) for item in proposal["entries"]]
        provenance = []
        for item in proposal["approvals"]:
            entry = next(e for e in entries if e.id == item["id"] and e.status == "approved")
            entry.content_hash = approval_hash(cargar_post(fresh, entry.slug), fresh, Platform(entry.platform))
            if entry.content_hash != item["new_hash"]:
                raise ScheduleError("payload, cuenta o bytes cambiaron durante aplicación")
            if item["approval_migration"] != proposal_id:
                raise ScheduleError("procedencia de migración no coincide")
            entry.approval_migration = proposal_id
            provenance.append(item | {"new_hash": entry.content_hash})
        if proposal["backend"] == "sqlite" and not store.database_path.exists():
            from socialctl import schedule_sqlite
            pending = directory / "queue.sqlite3"
            schedule_sqlite.save(pending, [e.model_dump(mode="json") for e in entries])
            os.replace(pending, store.database_path)
            store._sync_directory(store.root)
        else:
            store._save_unlocked(entries)
        intent.update(status="committed", completed_at=now_utc().isoformat(), approvals=provenance,
                      after={relative: file_digest(_safe(brand, relative)) for relative in proposal["posts"]},
                      queue_after=[e.model_dump(mode="json") for e in entries])
        write_json(directory / "intent.json", intent)
        write_json(store.root / "media-attestations.json", {"proposal": proposal_id, "attestations": attestations})
        (store.root / "migration-active.json").unlink()
        store._sync_directory(store.root)
        return intent


def rollback(brand: Brand, proposal_id: str) -> None:
    from socialctl.schedule_remote import is_remote, REMOTE_NOTICE
    if is_remote(brand.raiz):
        raise ScheduleError(REMOTE_NOTICE)
    proposal = load_proposal(brand, proposal_id)
    directory = _directory(brand, proposal_id)
    intent = json.loads((directory / "intent.json").read_bytes())
    store = ScheduleStore(brand.raiz)
    with store._file_lock(store.executor_lock_path, nonblocking=True), store._file_lock(store.mutation_lock_path, nonblocking=False):
        _assert_no_native_transfer(store)
        if is_remote(brand.raiz):
            raise ScheduleError(REMOTE_NOTICE)
        guard = store.root / "migration-active.json"
        if guard.exists() and json.loads(guard.read_bytes())["proposal"] != proposal_id:
            raise ScheduleError("hay otra migración pendiente")
        if intent["status"] == "rolled_back":
            if guard.exists():
                if [e.model_dump(mode="json") for e in store.load()] != proposal["entries"]:
                    raise ScheduleError("cola restaurada cambió antes de retirar guardia")
                for relative in proposal["posts"]:
                    if file_digest(_safe(brand, relative)) != intent["backups"][relative]:
                        raise ScheduleError("post restaurado cambió antes de retirar guardia")
                guard.unlink()
                store._sync_directory(store.root)
            return
        if intent["status"] == "committed" and not guard.exists():
            if [e.model_dump(mode="json") for e in store.load()] != intent["queue_after"]:
                raise ScheduleError("cola avanzó después de migración; rollback no seguro")
            for relative, digest in intent["after"].items():
                if file_digest(_safe(brand, relative)) != digest:
                    raise ScheduleError("post cambió después de migración")
        # Verify ALL backups before restoring anything, including interrupted retries.
        for relative, digest in intent["backups"].items():
            if file_digest(directory / "backup" / relative) != digest:
                raise ScheduleError("backup modificado; recuperación bloqueada")
        write_json(guard, {"proposal": proposal_id, "digest": proposal["digest"]})
        intent["status"] = "rolling_back"
        write_json(directory / "intent.json", intent)
        for relative in intent["backups"]:
            if relative == "accounts.yml":
                continue  # Accounts were never modified by relocation.
            durable_write(_safe(brand, relative), (directory / "backup" / relative).read_bytes())
        if not intent["database_existed"] and store.database_path.exists():
            os.replace(store.database_path, directory / "rolled-back.sqlite3")
            store._sync_directory(store.root)
            store._sync_directory(directory)
        intent["status"] = "rolled_back"
        intent["rolled_back_at"] = now_utc().isoformat()
        write_json(directory / "intent.json", intent)
        guard.unlink()
        store._sync_directory(store.root)
