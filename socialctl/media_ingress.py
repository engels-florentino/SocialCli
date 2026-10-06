"""Bounded binary ingestion into server-controlled brand policy roots."""
from __future__ import annotations

import json
import os
import struct
import tempfile
from pathlib import Path

from socialctl.hosted_media import attest_public_media, public_url, validate_url
from socialctl.media_registry import (
    MAX_HEADER, MAX_POST, MediaRegistryError, bundle_path, canonical, check_file,
    copy_exact, digest, load_bundle, parse_json, post_media, promote, publication_gate, relative, safe, space, validate_manifest,
)
from socialctl.migration_files import durable_write, write_json
from socialctl.scheduler import ScheduleStore, now_utc


def policy_for(brand) -> dict:
    try:
        policy = json.loads(relative(brand.raiz, ".socialctl/media-policy.json").read_bytes())
        if (set(policy) != {"version", "brand", "private_root", "public_root", "public_url_base"}
                or type(policy["version"]) is not int or policy["version"] != 1
                or policy["brand"] != brand.nombre):
            raise ValueError()
        private, public = Path(policy["private_root"]), Path(policy["public_root"])
        if (not private.is_absolute() or not public.is_absolute() or ".." in public.parts
                or private != brand.raiz / "media" or public == Path("/")
                or private == public or private in public.parents or public in private.parents):
            raise ValueError()
        safe(private)
        safe(public)
        if not public.is_dir() or public.stat().st_mode & 0o005 != 0o005:
            raise ValueError()
        validate_url(policy["public_url_base"])
        if (brand.cuentas.get("instagram") or {}).get("media_url_base") != policy["public_url_base"]:
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise MediaRegistryError("política de media del servidor ausente o inválida") from exc
    return policy


def read_exact(stream, count: int) -> bytes:
    result = bytearray()
    while len(result) < count:
        chunk = stream.read(count - len(result))
        if not chunk:
            raise MediaRegistryError("transferencia truncada")
        result.extend(chunk)
    return bytes(result)


def write_transfer(brand, ident: str, stream) -> dict:
    manifest, post = load_bundle(brand, ident)
    header = canonical(manifest)
    if len(header) > MAX_HEADER:
        raise MediaRegistryError("manifest demasiado grande")
    # Revalidate all selected local assets before sending even the header.
    for item in manifest["assets"]:
        check_file(relative(brand.raiz / "media", item["relative_path"]), item)
    stream.write(struct.pack("!I", len(header)))
    stream.write(header)
    stream.write(post)
    for item in manifest["assets"]:
        with relative(brand.raiz / "media", item["relative_path"]).open("rb") as source:
            copy_exact(source, stream, item["size"])
    return manifest


def _post_matches(manifest: dict, raw: bytes) -> None:
    if len(raw) != manifest["post_size"] or digest(raw) != manifest["post_sha256"]:
        raise MediaRegistryError("post transferido alterado")
    referenced = {name: set() for name in (a["relative_path"] for a in manifest["assets"])}
    for platform, paths in post_media(raw).items():
        for path in paths:
            if path not in referenced:
                raise MediaRegistryError("post referencia media fuera del manifest")
            referenced[path].add(manifest["slug"] + "/" + platform)
    if any(referenced[item["relative_path"]] != set(item["uses"]) for item in manifest["assets"]):
        raise MediaRegistryError("usos del manifest no coinciden con el post")


def _inactive_draft(brand, slug: str, store: ScheduleStore) -> None:
    entries = [entry for entry in store.load() if entry.slug == slug]
    if any(entry.status == "published" or entry.platform_id for entry in entries):
        raise MediaRegistryError("post con evidencia de publicación en la cola; usa un slug nuevo")
    if any(e.status not in {"cancelled", "error"} for e in entries):
        raise MediaRegistryError("post protegido por cola activa; cancela/reconcilia y prepara de nuevo antes de editar")
    if relative(brand.raiz, f".socialctl/media-publication-started/{slug}.json").exists():
        raise MediaRegistryError("post con inicio de publicación registrado; usa un slug nuevo")
    # Published or uncertain media is not an inactive draft. Fail closed on broken journals.
    from socialctl.publication_steps import retry_media_blockers
    from socialctl.models import Platform
    if retry_media_blockers(brand, slug, list(Platform)):
        raise MediaRegistryError("post con publicación previa o incierta; requiere reconciliación")
    if relative(brand.raiz, f"posts/{slug}/resultado.json").exists():
        raise MediaRegistryError("post con resultado de publicación; no es un borrador inactivo")


def _update_preview(brand, manifest, existing, proposed, store):
    _inactive_draft(brand, manifest["slug"], store)
    fingerprint = digest(canonical(dict(brand=brand.nombre, bundle=manifest["digest"],
                                        old_sha256=digest(existing), new_sha256=digest(proposed))))
    # Complete before/after text, without context-line truncation.
    preview = "POST ACTUAL\n" + existing.decode("utf-8") + "\nPOST PROPUESTO\n" + proposed.decode("utf-8")
    return dict(protocol="socialctl.media-update.v1", bundle=manifest["digest"],
                preview=preview, update_digest=fingerprint)


def receive(brand, stream, *, preview_update=False, update_digest=None) -> dict:
    policy = policy_for(brand)
    if preview_update and update_digest:
        raise MediaRegistryError("preview no admite aprobación de actualización")
    length = struct.unpack("!I", read_exact(stream, 4))[0]
    if not 0 < length <= MAX_HEADER:
        raise MediaRegistryError("cabecera excede límite")
    manifest = validate_manifest(brand, parse_json(read_exact(stream, length)))
    post = read_exact(stream, manifest["post_size"])
    _post_matches(manifest, post)
    size = sum(item["size"] for item in manifest["assets"])
    # The staging area and final private copy may coexist during promotion.
    space(brand.raiz, size * 2 + len(post))
    space(Path(policy["public_root"]), size)
    inbox = relative(brand.raiz, ".socialctl/media-incoming")
    inbox.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="transfer-", dir=inbox) as temporary:
        pending = Path(temporary)
        for index, item in enumerate(manifest["assets"]):
            target = pending / str(index)
            with target.open("xb") as handle:
                remaining = item["size"]
                while remaining:
                    chunk = read_exact(stream, min(remaining, 1024 * 1024))
                    handle.write(chunk)
                    remaining -= len(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            check_file(target, item)
        if stream.read(1):
            raise MediaRegistryError("bytes o frames adicionales no admitidos")
        # No canonical asset path exists until every frame and EOF are verified.
        store = ScheduleStore(brand.raiz)
        for path in (store.executor_lock_path, store.mutation_lock_path, store.path, store.database_path):
            safe(path)
        with store.executor_lock(), publication_gate(brand, manifest["slug"]), store._mutation_lock():
            # Recheck server policy and existing bytes under authoritative locks.
            if policy_for(brand) != policy:
                raise MediaRegistryError("política modificada durante transferencia")
            if bundle_path(brand, manifest["digest"]).exists():
                load_bundle(brand, manifest["digest"])
            destination = relative(brand.raiz, f"posts/{manifest['slug']}/post.yml")
            existing = destination.read_bytes() if destination.exists() else None
            if existing != post:
                _inactive_draft(brand, manifest["slug"], store)
            conflict = existing is not None and existing != post
            if conflict:
                preview = _update_preview(brand, manifest, existing, post, store)
                if preview_update:
                    return preview
                if update_digest != preview["update_digest"]:
                    raise MediaRegistryError("post existente difiere; exige --preview-update y su --update-digest")
            elif preview_update or update_digest:
                raise MediaRegistryError("no existe conflicto de texto para actualizar")
            for item in manifest["assets"]:
                for root in (Path(policy["private_root"]), Path(policy["public_root"])):
                    target = relative(root, item["relative_path"])
                    if target.exists():
                        check_file(target, item)
            for index, item in enumerate(manifest["assets"]):
                promote(pending / str(index), relative(Path(policy["private_root"]), item["relative_path"]), item)
                promote(pending / str(index), relative(Path(policy["public_root"]), item["relative_path"]), item, public=True)
            if conflict:
                backup = relative(brand.raiz, f".socialctl/media-post-backups/{manifest['slug']}/{digest(existing)}.yml")
                if backup.exists() and backup.read_bytes() != existing:
                    raise MediaRegistryError("backup existente alterado")
                if not backup.exists():
                    durable_write(backup, existing)
            if existing != post:
                durable_write(destination, post)
            durable_write(bundle_path(brand, manifest["digest"], ".yml"), post)
            write_json(bundle_path(brand, manifest["digest"]), manifest)
            return verify(brand, manifest["digest"])


def verify(brand, ident: str, *, public=False, client=None) -> dict:
    manifest, post = load_bundle(brand, ident)
    _post_matches(manifest, post)
    result = dict(protocol="socialctl.media-readiness.v1", brand=brand.nombre, bundle=ident,
                  local_ready=True, private_ready=False, hosted_ready=False, public_ready=False,
                  checked_at=now_utc().isoformat(), attestations=[])
    for item in manifest["assets"]:
        check_file(relative(brand.raiz / "media", item["relative_path"]), item)
    policy_path = relative(brand.raiz, ".socialctl/media-policy.json")
    if policy_path.exists():
        policy = policy_for(brand)
        result["private_ready"] = True
        for item in manifest["assets"]:
            hosted = relative(Path(policy["public_root"]), item["relative_path"])
            check_file(hosted, item)
            if hosted.stat().st_mode & 0o444 != 0o444:
                raise MediaRegistryError("copia pública no es legible")
            if public:
                result["attestations"].append(attest_public_media(
                    relative(brand.raiz / "media", item["relative_path"]),
                    public_url(policy["public_url_base"], item["relative_path"]), client=client))
        result["hosted_ready"] = True
        result["public_ready"] = public
    elif public:
        raise MediaRegistryError("verificación pública exige política del servidor")
    write_json(relative(brand.raiz, f".socialctl/media-readiness/{ident}.json"), result)
    return result
