"""Brand-bound supplied-byte bundles, independent of historical queue migrations."""
from __future__ import annotations

import hashlib
import fcntl
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from contextlib import contextmanager

import yaml

from socialctl.hosted_media import MAX_BYTES, MIMES, file_digest
from socialctl.migration_files import durable_write, rewrite_media, write_json
from socialctl.models import CampaignType
from socialctl.rutas import validar_ruta_relativa
from socialctl.scheduler import ScheduleStore

MAX_ITEMS = 64
MAX_TOTAL = 4 * MAX_BYTES
MAX_POST = 1024 * 1024
MAX_HEADER = 256 * 1024


class MediaRegistryError(ValueError):
    pass


@contextmanager
def publication_gate(brand, slug: str):
    """Lock order: executor (if any), this gate, queue mutation, Meta gate."""
    component(slug)
    key = digest(canonical(dict(brand=brand.nombre, root=str(brand.raiz.resolve()), slug=slug)))
    path = relative(brand.raiz, f".socialctl/media-post-gates/{key}.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise MediaRegistryError("post is running concurrently; retry after it finishes") from exc
        yield
    finally:
        os.close(fd)


def record_publication_start(brand, post, platforms) -> None:
    if post.brand != brand.nombre:
        raise MediaRegistryError("post belongs to another brand")
    path = relative(brand.raiz, f".socialctl/media-publication-started/{component(post.slug)}.json")
    # Evidence only for ingress draft eligibility, never a publication/retry gate.
    write_json(path, dict(version=1, brand=brand.nombre, root=str(brand.raiz.resolve()),
                         slug=post.slug, platforms=[p.value for p in platforms]))


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def parse_json(raw: bytes, *, limit=MAX_HEADER):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise MediaRegistryError("duplicate JSON keys")
            result[key] = value
        return result
    if len(raw) > limit:
        raise MediaRegistryError("JSON exceeds the limit")
    return json.loads(raw, object_pairs_hook=unique)


def safe(path: Path) -> Path:
    """Reject links even when they point within the allowed root."""
    if any(part.is_symlink() for part in [path, *path.parents]):
        raise MediaRegistryError("paths containing symbolic links are not supported")
    if path.exists() and not (path.is_file() or path.is_dir()):
        raise MediaRegistryError("unsupported file type")
    return path


def relative(root: Path, value: str) -> Path:
    try:
        return safe(validar_ruta_relativa(root, value, MediaRegistryError, "media path"))
    except ValueError as exc:
        raise MediaRegistryError("invalid media path or symbolic link") from exc


def component(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[\w][\w .-]{0,127}", value) or value in {".", ".."}:
        raise MediaRegistryError("invalid identifier")
    return value


def check_mime(path: Path, suffix: str) -> str:
    mime = MIMES.get(suffix)
    with safe(path).open("rb") as handle:
        head = handle.read(32)
    recognized = (
        suffix in {".mp4", ".mov"} and len(head) >= 12 and head[4:8] == b"ftyp"
        or suffix in {".jpg", ".jpeg"} and head.startswith(b"\xff\xd8\xff")
        or suffix == ".png" and head.startswith(b"\x89PNG\r\n\x1a\n")
        or suffix == ".webp" and head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    )
    if not mime or not recognized:
        raise MediaRegistryError("unsupported supplied MIME type or incompatible signature")
    return mime


def check_file(path: Path, item: dict) -> None:
    safe(path)
    if (not path.is_file() or path.stat().st_size != item["size"]
            or file_digest(path) != item["sha256"]
            or check_mime(path, Path(item["relative_path"]).suffix) != item["mime"]):
        raise MediaRegistryError("existing bytes do not match SHA-256, size or MIME type")


def space(path: Path, size: int) -> None:
    safe(path)
    while not path.exists():
        path = path.parent
    if shutil.disk_usage(path).free < size + 1024 * 1024:
        raise MediaRegistryError("insufficient destination space")


def copy_exact(incoming, out, size: int) -> None:
    remaining = size
    while remaining:
        chunk = incoming.read(min(remaining, 1024 * 1024))
        if not chunk or len(chunk) > remaining:
            raise MediaRegistryError("supplied size changed during copying")
        out.write(chunk)
        remaining -= len(chunk)
    if incoming.read(1):
        raise MediaRegistryError("supplied size changed during copying")


def promote(source: Path, target: Path, item: dict, *, public: bool = False) -> None:
    """Copy into same-directory temporary inode, fsync then atomic no-clobber link."""
    safe(target)
    if target.exists():
        check_file(target, item)
        if public and target.stat().st_mode & 0o444 != 0o444:
            raise MediaRegistryError("existing public copy is unreadable")
        return
    space(target.parent, item["size"])
    target.parent.mkdir(parents=True, exist_ok=True)
    if public:
        # Only framework-owned content-address directories are made searchable.
        for directory in (target.parent, target.parent.parent):
            directory.chmod(directory.stat().st_mode | 0o555)
    fd, temporary = tempfile.mkstemp(prefix=".ingress-", dir=target.parent)
    temp = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as out, safe(source).open("rb") as incoming:
            copy_exact(incoming, out, item["size"])
            out.flush()
            os.fchmod(out.fileno(), 0o644 if public else 0o600)
            os.fsync(out.fileno())
        check_file(temp, item)
        try:
            os.link(temp, target)
        except FileExistsError:
            check_file(target, item)
        ScheduleStore._sync_directory(target.parent)
    finally:
        temp.unlink(missing_ok=True)


def validate_manifest(brand, manifest: dict) -> dict:
    try:
        if set(manifest) != {"version", "brand", "slug", "post_sha256", "post_size", "assets", "digest"}:
            raise ValueError()
        unsigned = {k: v for k, v in manifest.items() if k != "digest"}
        if (type(manifest["version"]) is not int or manifest["version"] != 1
                or manifest["brand"] != brand.nombre or manifest["digest"] != digest(canonical(unsigned))):
            raise ValueError()
        component(manifest["slug"])
        if not re.fullmatch(r"[a-f0-9]{64}", manifest["post_sha256"]):
            raise ValueError()
        if type(manifest["post_size"]) is not int or not 0 < manifest["post_size"] <= MAX_POST:
            raise ValueError()
        assets = manifest["assets"]
        if not isinstance(assets, list) or not 0 < len(assets) <= MAX_ITEMS:
            raise ValueError()
        seen = set()
        for item in assets:
            if set(item) != {"sha256", "size", "mime", "source", "relative_path", "uses"}:
                raise ValueError()
            sha = item["sha256"]
            if not re.fullmatch(r"[a-f0-9]{64}", sha):
                raise ValueError()
            suffix = Path(item["relative_path"]).suffix
            if (item["relative_path"] != f"approved/sha256/{sha}{suffix}"
                    or item["mime"] != MIMES.get(suffix) or suffix not in MIMES
                    or type(item["size"]) is not int or not 0 < item["size"] <= MAX_BYTES
                    or item["relative_path"] in seen):
                raise ValueError()
            seen.add(item["relative_path"])
            # Source is provenance only; ingestion never reads this client path.
            if not item["source"].startswith("media/"):
                raise ValueError()
            validar_ruta_relativa(Path("/supplied"), item["source"], MediaRegistryError, "origen")
            if (not isinstance(item["uses"], list) or not item["uses"] or len(item["uses"]) > 4
                    or len(set(item["uses"])) != len(item["uses"])
                    or any(use not in {manifest["slug"] + "/" + p for p in ("youtube", "facebook", "instagram", "tiktok")} for use in item["uses"])):
                raise ValueError()
        if sum(item["size"] for item in assets) > MAX_TOTAL:
            raise ValueError()
    except (KeyError, TypeError, ValueError) as exc:
        raise MediaRegistryError("invalid or modified manifest, or manifest belongs to another brand") from exc
    return manifest


def bundle_path(brand, ident: str, suffix=".json") -> Path:
    if not re.fullmatch(r"[a-f0-9]{64}", ident):
        raise MediaRegistryError("invalid bundle identifier")
    return relative(brand.raiz, f".socialctl/media-bundles/{ident}{suffix}")


def load_bundle(brand, ident: str) -> tuple[dict, bytes]:
    manifest = validate_manifest(brand, parse_json(bundle_path(brand, ident).read_bytes()))
    post = bundle_path(brand, ident, ".yml").read_bytes()
    if manifest["digest"] != ident or len(post) != manifest["post_size"] or digest(post) != manifest["post_sha256"]:
        raise MediaRegistryError("bundle was modified")
    return manifest, post


def post_media(raw: bytes) -> dict[str, list[str]]:
    try:
        if len(raw) > MAX_POST:
            raise ValueError()
        data = yaml.safe_load(raw)
        platforms = data["platforms"]
        if not isinstance(platforms, dict) or not platforms or data.get("campaign") not in {v.value for v in CampaignType}:
            raise ValueError()
        result = {}
        for name, block in platforms.items():
            if name not in {"youtube", "facebook", "instagram", "tiktok"} or not isinstance(block["body"], str):
                raise ValueError()
            media = block.get("media", [])
            if not isinstance(media, list) or any(not isinstance(value, str) for value in media):
                raise ValueError()
            result[name] = media
        # Also rejects aliases, anchors and duplicate mapping keys.
        rewrite_media(raw, {p: {v: v for v in values} for p, values in result.items() if values})
        return result
    except (KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise MediaRegistryError("invalid supplied post") from exc


def stage(brand, slug: str) -> dict:
    component(slug)
    safe(brand.raiz)
    path = relative(brand.raiz, f"posts/{slug}/post.yml")
    if path.stat().st_size > MAX_POST:
        raise MediaRegistryError("supplied post exceeds the limit")
    raw = path.read_bytes()
    uses = post_media(raw)
    assets, replacements = {}, {}
    for platform, values in uses.items():
        replacements[platform] = {}
        for name in values:
            source = relative(brand.raiz / "media", name)
            if not source.is_file():
                raise MediaRegistryError("supplied file is missing")
            size = source.stat().st_size
            if not 0 < size <= MAX_BYTES:
                raise MediaRegistryError("supplied size exceeds the limit")
            suffix = source.suffix.lower()
            mime = check_mime(source, suffix)
            sha = file_digest(source)
            relative_path = f"approved/sha256/{sha}{suffix}"
            item = assets.setdefault(relative_path, dict(sha256=sha, size=size, mime=mime,
                source="media/" + name, relative_path=relative_path, uses=[]))
            use = f"{slug}/{platform}"
            if use not in item["uses"]:
                item["uses"].append(use)
            replacements[platform][name] = relative_path
    rewritten = rewrite_media(raw, {p: values for p, values in replacements.items() if values})
    manifest = dict(version=1, brand=brand.nombre, slug=slug, post_sha256=digest(rewritten),
                    post_size=len(rewritten), assets=list(assets.values()))
    manifest["digest"] = digest(canonical(manifest))
    validate_manifest(brand, manifest)
    space(brand.raiz, sum(item["size"] for item in assets.values()) + len(rewritten))
    for item in assets.values():
        promote(relative(brand.raiz, item["source"]), relative(brand.raiz / "media", item["relative_path"]), item)
    target = bundle_path(brand, manifest["digest"])
    if target.exists():
        load_bundle(brand, manifest["digest"])
    else:
        durable_write(bundle_path(brand, manifest["digest"], ".yml"), rewritten)
        write_json(target, manifest)
    return manifest
