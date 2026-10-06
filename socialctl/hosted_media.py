"""Read-only, bounded full-byte verification of content-addressed hosted media."""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

MAX_BYTES = 1024 * 1024 * 1024
TIMEOUT_SECONDS = 120.0
MIMES = {".mp4": "video/mp4", ".mov": "video/quicktime", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


class HostedMediaError(ValueError):
    pass


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def validate_url(url: str) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or any(ord(c) < 33 for c in url) or "\\" in url):
        raise HostedMediaError("unsafe public URL: requires HTTPS without credentials, query or fragment")
    return url


def public_url(base: str, relative: str) -> str:
    validate_url(base)
    return validate_url(base.rstrip("/") + "/" + "/".join(quote(part, safe="") for part in relative.split("/")))


def attest_public_media(path: Path, url: str, *, client: httpx.Client | None = None,
                        max_bytes: int = MAX_BYTES, timeout_s: float = TIMEOUT_SECONDS) -> dict:
    validate_url(url)
    if not re.fullmatch(r"[0-9a-f]{64}", path.stem):
        raise HostedMediaError("scheduled media requires a SHA-256 filename")
    size = path.stat().st_size
    mime = MIMES.get(path.suffix.lower())
    if not mime or size <= 0 or size > max_bytes or file_digest(path) != path.stem:
        raise HostedMediaError("invalid local media: MIME type, size or SHA-256")
    if client is None:
        with httpx.Client(timeout=httpx.Timeout(timeout_s), follow_redirects=False, trust_env=False) as owned:
            return attest_public_media(path, url, client=owned, max_bytes=max_bytes, timeout_s=timeout_s)
    started = time.monotonic()
    digest = hashlib.sha256()
    total = 0
    try:
        # Standalone request avoids forwarding a caller's cookies/default headers.
        request = httpx.Request("GET", url, headers={"Accept-Encoding": "identity"},
                                extensions={"timeout": {name: timeout_s for name in ("connect", "read", "write", "pool")}})
        response = client.send(request, stream=True, follow_redirects=False, auth=None)
        try:
            if response.status_code != 200:
                raise HostedMediaError("public media does not return HTTP 200 (redirects are not followed)")
            if response.headers.get("content-type", "").split(";")[0].strip().lower() != mime:
                raise HostedMediaError("public MIME type does not match")
            if response.headers.get("content-encoding", "identity") != "identity":
                raise HostedMediaError("public media must not be compressed")
            length = response.headers.get("content-length")
            if length is not None and (not length.isdigit() or int(length) != size):
                raise HostedMediaError("public size does not match")
            # Do not accumulate tiny transport chunks before checking budgets.
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > min(size, max_bytes) or time.monotonic() - started > timeout_s:
                    raise HostedMediaError("public media exceeds the byte or time limit")
                digest.update(chunk)
        finally:
            response.close()
    except httpx.HTTPError as exc:
        raise HostedMediaError(f"could not verify public media ({type(exc).__name__})") from exc
    if total != size or digest.hexdigest() != path.stem:
        raise HostedMediaError("public SHA-256 or size does not match the supplied bytes")
    return {"url": url, "sha256": path.stem, "size": size, "mime": mime}


def _migration_attestations(brand, entry) -> list[dict] | None:
    """A queue binding survives missing audit files; new approvals have none."""
    proposal_id = entry.approval_migration
    if proposal_id is None:
        return None
    from socialctl.rutas import validar_ruta_relativa
    from socialctl.queue_migration import _digest
    try:
        if str(uuid.UUID(proposal_id)) != proposal_id:
            raise ValueError("invalid UUID")
        directory = validar_ruta_relativa(brand.raiz, f".socialctl/migrations/{proposal_id}", HostedMediaError, "attestation")
        intent = json.loads((directory / "intent.json").read_bytes())
        proposal = json.loads((directory / "proposal.json").read_bytes())
        stored = json.loads((brand.raiz / ".socialctl/media-attestations.json").read_bytes())
        matching = [item for item in intent["approvals"]
                    if item["id"] == entry.id and item["slug"] == entry.slug
                    and item["platform"] == entry.platform
                    and item["created_at"] == entry.created_at.isoformat()
                    and item["new_hash"] == entry.content_hash
                    and item["approval_migration"] == proposal_id]
        if (intent["status"] != "committed" or intent["proposal"] != proposal_id
                or stored["proposal"] != proposal_id or len(matching) != 1
                or proposal["id"] != proposal_id or proposal["brand"] != brand.nombre
                or proposal["digest"] != _digest(proposal)
                or intent["digest"] != proposal["digest"]
                or matching[0] not in proposal["approvals"]
                or not isinstance(intent["attestations"], list)
                or stored["attestations"] != intent["attestations"]):
            raise ValueError("provenance or attestation does not match")
        return stored["attestations"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HostedMediaError("valid historical evidence of the migrated approval is missing") from exc


def verify_scheduled_instagram(post, brand, *, entry, client: httpx.Client | None = None) -> None:
    """Verify frozen migration attestation and fresh public bytes before writes."""
    from socialctl.media import ruta_relativa_efectiva
    from socialctl.models import Platform
    from socialctl.migration_files import write_json
    from socialctl.scheduler import now_utc
    base = brand.cuentas.get("instagram", {}).get("media_url_base", "")
    stored = _migration_attestations(brand, entry)
    results = []
    for asset in post.platforms[Platform.INSTAGRAM].media:
        url = public_url(base, ruta_relativa_efectiva(asset))
        if stored is not None:
            matching = [item for item in stored if item["url"] == url]
            if (len(matching) != 1 or matching[0]["sha256"] != asset.path.stem
                    or matching[0]["size"] != asset.path.stat().st_size
                    or matching[0]["mime"] != MIMES.get(asset.path.suffix.lower())):
                raise HostedMediaError("media does not match the persisted attestation")
        results.append(attest_public_media(asset.path, url, client=client))
    if not results:
        raise HostedMediaError("scheduled media is missing")
    write_json(brand.raiz / ".socialctl/last-media-preflight.json", {
        "slug": post.slug, "entry_id": entry.id, "created_at": entry.created_at.isoformat(),
        "content_hash": entry.content_hash, "approval_migration": entry.approval_migration,
        "checked_at": now_utc().isoformat(), "attestations": results})
