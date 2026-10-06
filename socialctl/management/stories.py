"""Durable Meta Stories with exact approval and no blind remote replay.

Instagram uses the public container flow (``media_type=STORIES``).  Facebook
Page Stories is a documented public capability, but its distinct photo/video
upload contract is deliberately left as an explicit handoff in this phase.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from socialctl.hosted_media import HostedMediaError, file_digest, validate_url
from socialctl.management.changes import ChangeError, ChangeStore, now_utc
from socialctl.management.meta_client import MetaClient
from socialctl.management.meta_schema import MetaError, MetaRejected, MetaUncertain, meta_id
from socialctl.media import MediaInvalida, _ffprobe, _rotacion_grados
from socialctl.models import MediaKind, Platform, StoryPost


class StoryError(ChangeError):
    """Sanitized validation, binding, persistence or state error."""


# Contrato conservador de Instagram Stories documentado por Meta. Meta expresa
# estos límites en MB decimales: JPEG hasta 8 MB; MP4/MOV H.264 o HEVC, audio
# AAC cuando exista, 3-60 s y hasta 100 MB.
# https://developers.facebook.com/documentation/instagram-platform/
# instagram-graph-api/reference/ig-user/media
STORY_IMAGE_MAX_BYTES = 8 * 1000**2
STORY_VIDEO_MAX_BYTES = 100 * 1000**2
STORY_VIDEO_MIN_DURATION_S = 3.0
STORY_VIDEO_MAX_DURATION_S = 60.0
STORY_VIDEO_CODECS = frozenset({"h264", "hevc"})
STORY_AUDIO_CODEC = "aac"


class StoryChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    version: Literal[1] = 1
    kind: Literal["meta-story"] = "meta-story"
    brand: str
    brand_root: str
    account_id: str
    story: StoryPost
    relative_media: str
    media_sha256: str
    media_size_bytes: int = Field(gt=0)
    dedupe_key: str
    capability: Literal[
        "instagram_public_api_write_grant_unverified",
        "facebook_page_stories_api_pending_implementation",
    ]
    created_at: datetime
    updated_at: datetime
    fingerprint: str = ""
    status: Literal[
        "prepared",
        "approved",
        "applying",
        "container_created",
        "publishing",
        "sent",
        "verified",
        "uncertain",
        "blocked",
        "expired",
        "handoff_required",
    ] = "prepared"
    container_id: str | None = None
    remote_id: str | None = None
    receipt: dict | None = None
    last_error: str | None = None
    journal: list[dict] = Field(default_factory=list)


_MUTABLE = {
    "fingerprint",
    "updated_at",
    "status",
    "container_id",
    "remote_id",
    "receipt",
    "last_error",
    "journal",
}


def story_fingerprint(change: StoryChange) -> str:
    raw = change.model_dump(mode="json", exclude=_MUTABLE)
    encoded = json.dumps(
        raw, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class StoryStore(ChangeStore):
    model_type = StoryChange
    fingerprint_for = staticmethod(story_fingerprint)

    def __init__(self, brand_root: Path):
        self.root = Path(brand_root) / ".socialctl" / "stories"


def _event(change: StoryChange, name: str, **details) -> None:
    change.updated_at = now_utc()
    change.journal.append({"at": change.updated_at.isoformat(), "event": name, **details})


def _read_edges(path: Path) -> tuple[bytes, bytes]:
    try:
        with path.open("rb") as handle:
            head = handle.read(64)
            handle.seek(-2, 2)
            tail = handle.read(2)
    except OSError as exc:
        raise StoryError(f"failed to inspect Story media: {exc}") from None
    return head, tail


def _probe_story(path: Path, kind: MediaKind, extension: str) -> tuple[int, int, float | None]:
    """Validate actual container/codec and return observed metadata."""
    head, tail = _read_edges(path)
    if kind is MediaKind.IMAGE and (
        not head.startswith(b"\xff\xd8\xff") or tail != b"\xff\xd9"
    ):
        raise StoryError("unsupported actual image format: JPEG bytes required")
    if kind is MediaKind.VIDEO and (
        len(head) < 12 or head[4:8] != b"ftyp"
    ):
        raise StoryError("unsupported actual video format: MP4 or MOV container required")
    try:
        probe = _ffprobe(path)
    except (MediaInvalida, OSError, ValueError, json.JSONDecodeError) as exc:
        raise StoryError(f"media does not contain an actual readable {extension.upper()}: {exc}") from None

    streams = probe.get("streams") or []
    video_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "video"),
        streams[0] if streams else None,
    )
    if not isinstance(video_stream, dict):
        raise StoryError("Story media does not contain a readable image or video track")
    width = video_stream.get("width")
    height = video_stream.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise StoryError("Story media must have positive actual dimensions")
    if _rotacion_grados(video_stream) % 180 == 90:
        width, height = height, width

    format_name = str((probe.get("format") or {}).get("format_name") or "")
    codec = str(video_stream.get("codec_name") or "").lower()
    if kind is MediaKind.IMAGE:
        if codec != "mjpeg" or "jpeg" not in format_name:
            raise StoryError("unsupported actual image format: readable JPEG required")
        return width, height, None

    if "mp4" not in format_name:
        raise StoryError("unsupported actual video format: MP4 or MOV container required")
    if codec not in STORY_VIDEO_CODECS:
        raise StoryError("unsupported Story codec: use H.264 or HEVC video")
    audio_streams = [
        stream for stream in streams if stream.get("codec_type") == "audio"
    ]
    if any(
        str(stream.get("codec_name") or "").lower() != STORY_AUDIO_CODEC
        for stream in audio_streams
    ):
        raise StoryError("unsupported Story audio codec: use AAC")
    raw_duration = (probe.get("format") or {}).get("duration")
    try:
        duration = float(raw_duration)
    except (TypeError, ValueError):
        raise StoryError("Story video must have a verifiable actual duration") from None
    return width, height, duration


def _media_contract(story: StoryPost, brand) -> tuple[StoryPost, str, str, int]:
    media_root = (brand.raiz / "media").resolve()
    if story.media.path.is_symlink():
        raise StoryError("Story media must be a regular file, not a symlink")
    try:
        path = story.media.path.resolve(strict=True)
        relative = path.relative_to(media_root).as_posix()
    except (OSError, ValueError):
        raise StoryError("Story media must exist inside the brand media folder") from None
    if not path.is_file():
        raise StoryError("Story media must be a regular file, not a symlink")

    extension = path.suffix.lower()
    image_extensions = {".jpg", ".jpeg"}
    video_extensions = {".mp4", ".mov"}
    if story.media.kind is MediaKind.IMAGE and extension not in image_extensions:
        raise StoryError("unsupported Story image format: use JPEG (.jpg or .jpeg)")
    if story.media.kind is MediaKind.VIDEO and extension not in video_extensions:
        raise StoryError("unsupported Story video format: use MP4 or MOV")
    size = path.stat().st_size
    if size <= 0 or story.media.size_bytes != size:
        raise StoryError("media size mismatch or file is empty")

    width, height, duration = _probe_story(path, story.media.kind, extension)
    if story.media.kind is MediaKind.IMAGE:
        if size > STORY_IMAGE_MAX_BYTES:
            raise StoryError("Story image exceeds documented 8 MB maximum")
    else:
        if size > STORY_VIDEO_MAX_BYTES:
            raise StoryError("Story video exceeds documented 100 MB maximum")
        if not STORY_VIDEO_MIN_DURATION_S <= duration <= STORY_VIDEO_MAX_DURATION_S:
            raise StoryError("Story video must last between 3 and 60 seconds")

    normalized_media = story.media.model_copy(
        update={
            "path": path,
            "ruta_relativa": relative,
            "width": width,
            "height": height,
            "duration_s": duration,
            "size_bytes": size,
        }
    )
    return story.model_copy(update={"media": normalized_media}), relative, file_digest(path), size


def _validate_story(story: StoryPost, brand, *, at: datetime | None = None) -> tuple[StoryPost, str, str, int, str]:
    moment = at or now_utc()
    if story.expires_at <= moment:
        raise StoryError("Story proposal expired; prepare a new one")
    if story.platform is Platform.INSTAGRAM:
        if story.public_url is None:
            raise StoryError("Instagram requires a public HTTPS URL for media")
        try:
            validate_url(story.public_url)
        except HostedMediaError as exc:
            raise StoryError(str(exc)) from None
        account = (brand.cuentas.get("instagram") or {}).get("ig_user_id")
    elif story.platform is Platform.FACEBOOK:
        if story.public_url is not None:
            try:
                validate_url(story.public_url)
            except HostedMediaError as exc:
                raise StoryError(str(exc)) from None
        account = (brand.cuentas.get("facebook") or {}).get("page_id")
    else:
        raise StoryError("Stories supported only on Facebook or Instagram")
    try:
        account_id = meta_id(account)
    except MetaError:
        raise StoryError("accounts.yml lacks a valid Meta account ID") from None
    normalized, relative, digest, size = _media_contract(story, brand)
    return normalized, relative, digest, size, account_id


def _dedupe_payload(story: StoryPost, account_id: str, media_sha256: str) -> str:
    # Instagram no recibe text/source_video_id y distintas URLs que sirven los
    # mismos bytes producen el mismo efecto remoto: una nueva Story idéntica.
    raw = {
        "platform": story.platform.value,
        "account_id": account_id,
        "media_sha256": media_sha256,
    }
    return hashlib.sha256(
        json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _iter_changes(store: StoryStore):
    if not store.root.exists():
        return
    for path in sorted(store.root.glob("*.json")):
        yield store.load(path.stem)


def _active_duplicate(store: StoryStore, key: str, *, excluding: str | None = None) -> StoryChange | None:
    now = now_utc()
    locally_active = {"prepared", "approved", "handoff_required"}
    remote_effect_possible = {
        "applying",
        "container_created",
        "publishing",
        "sent",
        "verified",
        "uncertain",
    }
    for other in _iter_changes(store):
        # Desde el primer write intent puede existir estado remoto. La caducidad
        # editorial local no demuestra que Meta ya no retenga o sirva la Story,
        # ni siquiera cuando la creación quedó sent/verified. Sin un estado
        # remoto explícito que pruebe expiración, esos ChangeSets bloquean la
        # misma operación de forma durable.
        if (
            other.id != excluding
            and other.dedupe_key == key
            and (
                other.status in remote_effect_possible
                or (
                    other.status in locally_active
                    and other.story.expires_at > now
                )
            )
        ):
            return other
    return None


def prepare_story(brand, store: StoryStore, story: StoryPost) -> StoryChange:
    normalized, relative, media_sha256, media_size_bytes, account_id = _validate_story(story, brand)
    key = _dedupe_payload(normalized, account_id, media_sha256)
    lock_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"story:{brand.raiz.resolve()}:{key}"))
    with store.apply_lock(lock_id):
        duplicate = _active_duplicate(store, key)
        if duplicate is not None:
            raise StoryError(
                f"Duplicate Story: active proposal {duplicate.id} already exists "
                f"with status {duplicate.status}"
            )
        moment = now_utc()
        capability = (
            "instagram_public_api_write_grant_unverified"
            if normalized.platform is Platform.INSTAGRAM
            else "facebook_page_stories_api_pending_implementation"
        )
        change = StoryChange(
            id=str(uuid.uuid4()),
            brand=brand.nombre,
            brand_root=str(brand.raiz.resolve()),
            account_id=account_id,
            story=normalized,
            relative_media=relative,
            media_sha256=media_sha256,
            media_size_bytes=media_size_bytes,
            dedupe_key=key,
            capability=capability,
            created_at=moment,
            updated_at=moment,
        )
        change.fingerprint = story_fingerprint(change)
        _event(change, "prepared_local_no_remote_write")
        store.save(change)
        return change


class MetaStoryClient:
    """Small Instagram Story adapter over the bounded Graph client."""

    def __init__(self, brand, platform: Platform, client: httpx.Client, *, poll_wait_s: float = 60.0, poll_attempts: int = 5):
        if platform is not Platform.INSTAGRAM:
            raise StoryError("remote Stories adapter is implemented only for Instagram")
        self.brand = brand
        self.platform = platform
        self.graph = MetaClient(brand, platform, client)
        self.http = client
        self.poll_wait_s = poll_wait_s
        self.poll_attempts = poll_attempts

    @property
    def account_id(self) -> str:
        return self.graph.account_id

    def identity(self) -> dict:
        return self.graph.identity()

    def preflight_public_url(self, change: StoryChange) -> None:
        story = change.story
        assert story.public_url is not None
        expected_size = change.media_size_bytes
        expected_digest = change.media_sha256
        remote_digest = hashlib.sha256()
        total = 0
        try:
            with self.http.stream(
                "GET",
                story.public_url,
                headers={"Accept-Encoding": "identity"},
                follow_redirects=False,
                timeout=30,
            ) as response:
                if response.status_code != 200:
                    raise StoryError(
                        "public Story URL must return HTTP 200; "
                        f"returned {response.status_code}"
                    )
                expected = "image/" if story.media.kind is MediaKind.IMAGE else "video/"
                content_type = response.headers.get("content-type", "").lower()
                if content_type and not content_type.startswith(expected):
                    raise StoryError(
                        "public Content-Type does not match Story format"
                    )
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise StoryError("public media must not be compressed")
                length = response.headers.get("content-length")
                if length is not None and (
                    not length.isdigit() or int(length) != expected_size
                ):
                    raise StoryError("public media size does not match approved file")
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > expected_size:
                        raise StoryError("public media exceeds approved file size")
                    remote_digest.update(chunk)
        except httpx.HTTPError:
            raise StoryError("failed to check public Story URL") from None
        if total != expected_size or remote_digest.hexdigest() != expected_digest:
            raise StoryError("public media bytes do not match approved file")

    def create_container(self, story: StoryPost) -> str:
        assert story.public_url is not None
        url_field = "image_url" if story.media.kind is MediaKind.IMAGE else "video_url"
        result = self.graph.request(
            "POST",
            f"{self.account_id}/media",
            data={"media_type": "STORIES", url_field: story.public_url},
        )
        try:
            return meta_id(result.get("id"))
        except MetaError:
            raise MetaUncertain("Meta accepted container creation but returned no valid ID; do not repeat") from None

    def container_status(self, container_id: str) -> str:
        result = self.graph.request("GET", meta_id(container_id), params={"fields": "status_code"})
        status = result.get("status_code")
        if status not in {"EXPIRED", "ERROR", "FINISHED", "IN_PROGRESS", "PUBLISHED"}:
            raise StoryError("Meta returned unverifiable container status")
        return status

    def publish_container(self, container_id: str) -> str:
        result = self.graph.request(
            "POST",
            f"{self.account_id}/media_publish",
            data={"creation_id": meta_id(container_id)},
        )
        try:
            return meta_id(result.get("id"))
        except MetaError:
            raise MetaUncertain("Meta accepted media_publish but returned no valid ID; do not repeat") from None

    def verify_media(self, remote_id: str) -> dict:
        result = self.graph.request(
            "GET",
            meta_id(remote_id),
            params={"fields": "id,owner,media_product_type,permalink,timestamp"},
        )
        if (
            result.get("id") != remote_id
            or (result.get("owner") or {}).get("id") != self.account_id
            or result.get("media_product_type") != "STORY"
        ):
            raise StoryError("remote ID was not verified as an account-owned Story")
        return result


def _bound(client, change: StoryChange) -> None:
    if (
        client.brand.nombre != change.brand
        or str(client.brand.raiz.resolve()) != change.brand_root
        or client.platform.value != change.story.platform.value
        or client.account_id != change.account_id
    ):
        raise StoryError("brand/account does not match Story proposal")
    identity = client.identity()
    if identity.get("account_id") != change.account_id:
        raise StoryError("authenticated identity does not match prepared Story")


def _recheck_local(change: StoryChange, brand) -> None:
    normalized, relative, digest, size, account_id = _validate_story(change.story, brand)
    if (
        normalized != change.story
        or relative != change.relative_media
        or digest != change.media_sha256
        or size != change.media_size_bytes
        or account_id != change.account_id
    ):
        raise StoryError("media, URL, expiry or account changed since preview")


def _save_failure(store: StoryStore, change: StoryChange, status: str, error: str, event: str) -> StoryChange:
    change.status = status
    change.last_error = error
    _event(change, event)
    store.save(change)
    return change


def _verify(client, store: StoryStore, change: StoryChange) -> StoryChange:
    if not change.remote_id:
        if change.status in {"applying", "publishing"}:
            change.status = "uncertain"
            change.last_error = "uncertain remote outcome without ID; POST will not be repeated"
            _event(change, "missing_remote_id_no_replay")
            store.save(change)
        return change
    try:
        receipt = client.verify_media(change.remote_id)
    except (MetaError, StoryError):
        change.status = "sent"
        change.last_error = "Meta returned an ID, but Story could not yet be verified by GET"
        _event(change, "verification_unavailable_no_replay")
    else:
        change.status = "verified"
        change.receipt = receipt
        change.last_error = None
        _event(change, "remote_story_verified")
    store.save(change)
    return change


def verify_story(client, store: StoryStore, change_id: str) -> StoryChange:
    with store.apply_lock(change_id):
        change = store.load(change_id)
        if change.story.platform is Platform.FACEBOOK:
            return change
        _bound(client, change)
        if change.status == "verified":
            return change
        if change.container_id and not change.remote_id:
            try:
                status = client.container_status(change.container_id)
            except (MetaError, StoryError):
                change.last_error = "failed to verify current container status"
                _event(change, "container_verification_unavailable")
            else:
                _event(change, "container_status_verified", status_code=status)
                if status == "EXPIRED":
                    change.status = "expired"
                    change.last_error = "Instagram container expired before publication"
                elif status == "ERROR":
                    change.status = "blocked"
                    change.last_error = "Meta could not process Story media"
                elif status == "PUBLISHED":
                    change.status = "uncertain"
                    change.last_error = (
                        "container shows PUBLISHED without a durable remote ID; "
                        "media_publish will not be repeated"
                    )
                else:
                    change.status = "container_created"
                    change.last_error = (
                        "container is ready" if status == "FINISHED"
                        else "container is still processing"
                    )
            store.save(change)
            return change
        return _verify(client, store, change)


def apply_story(
    client,
    store: StoryStore,
    change_id: str,
    approval_digest: str,
    *,
    brand=None,
) -> StoryChange:
    with ExitStack() as locks:
        locks.enter_context(store.apply_lock(change_id))
        change = store.load(change_id)
        if not hmac.compare_digest(change.fingerprint, approval_digest):
            raise StoryError("approval does not match exact Story fingerprint")
        dedupe_lock = str(uuid.uuid5(uuid.NAMESPACE_URL, f"story:{change.brand_root}:{change.dedupe_key}"))
        locks.enter_context(store.apply_lock(dedupe_lock))
        duplicate = _active_duplicate(store, change.dedupe_key, excluding=change.id)
        if duplicate is not None:
            raise StoryError(f"Duplicate Story blocked by active operation {duplicate.id}")

        if change.status == "verified":
            return change
        if change.status in {"blocked", "expired", "handoff_required"}:
            return change
        current_brand = client.brand if client is not None else brand
        if current_brand is None:
            raise StoryError("explicit brand required to apply Story")
        if (
            change.story.expires_at <= now_utc()
            and change.container_id is None
            and change.remote_id is None
        ):
            return _save_failure(
                store, change, "expired", "Story approval expired", "approval_expired_no_write"
            )

        if change.story.platform is Platform.FACEBOOK:
            try:
                _recheck_local(change, current_brand)
            except StoryError as exc:
                return _save_failure(store, change, "blocked", str(exc), "local_binding_changed_no_write")
            change.status = "handoff_required"
            change.last_error = (
                "Facebook Page Stories has a public API, but its photo_stories/video_stories flow "
                "is not yet implemented; publish through Business Suite and record evidence"
            )
            _event(change, "facebook_page_story_handoff_no_remote_write")
            store.save(change)
            return change

        if client is None:
            raise StoryError("Instagram adapter required to apply Story")
        try:
            _bound(client, change)
        except (MetaError, StoryError) as exc:
            return _save_failure(
                store, change, "blocked",
                f"identity or read permission unavailable: {exc}; write grant remains unverified",
                "identity_or_permission_blocked_no_write",
            )

        # Si el proceso cayó después de persistir un write intent, el POST
        # puede haber llegado a Meta. Solo se permite lectura/verificación.
        if change.status in {"applying", "uncertain", "publishing", "sent"}:
            return _verify(client, store, change)
        if (
            change.story.expires_at <= now_utc()
            and change.container_id is None
            and change.remote_id is None
        ):
            return _save_failure(
                store, change, "expired", "Story approval expired", "approval_expired_no_write"
            )
        if change.container_id is None:
            try:
                _recheck_local(change, current_brand)
            except StoryError as exc:
                return _save_failure(store, change, "blocked", str(exc), "local_binding_changed_no_write")

        if change.container_id is None:
            try:
                client.preflight_public_url(change)
            except StoryError as exc:
                return _save_failure(store, change, "blocked", str(exc), "public_url_preflight_failed_no_write")
            change.status = "approved"
            change.last_error = None
            _event(change, "approved_exact_digest")
            store.save(change)
            change.status = "applying"
            _event(change, "container_write_intent")
            store.save(change)
            try:
                change.container_id = client.create_container(change.story)
            except MetaRejected:
                return _save_failure(
                    store, change, "blocked",
                    "Meta rejected Story creation; check instagram_content_publish, eligibility and public URL. Write grant is unverified",
                    "container_write_rejected",
                )
            except (MetaUncertain, Exception) as exc:
                message = str(exc) if isinstance(exc, MetaUncertain) else "unexpected failure after starting POST"
                return _save_failure(store, change, "uncertain", message, "container_write_uncertain_no_replay")
            change.status = "container_created"
            _event(change, "container_id_persisted", container_id=change.container_id)
            store.save(change)

        status = None
        for attempt in range(client.poll_attempts):
            try:
                status = client.container_status(change.container_id)
            except (MetaError, StoryError):
                change.last_error = "failed to read container status; ID preserved to continue without recreating it"
                _event(change, "container_status_unavailable_safe_to_resume")
                store.save(change)
                return change
            _event(change, "container_status_observed", status_code=status)
            store.save(change)
            if status == "FINISHED":
                break
            if status == "ERROR":
                return _save_failure(store, change, "blocked", "Meta could not process Story media", "container_processing_error")
            if status == "EXPIRED":
                return _save_failure(store, change, "expired", "Instagram container expired before publication", "container_expired")
            if status == "PUBLISHED":
                return _save_failure(
                    store, change, "uncertain",
                    "container shows PUBLISHED without a durable remote ID; media_publish will not be repeated",
                    "container_already_published_no_remote_id",
                )
            if attempt < client.poll_attempts - 1:
                time.sleep(client.poll_wait_s)
        if status != "FINISHED":
            change.last_error = "container is still processing; run apply or verify again later"
            _event(change, "container_still_processing_safe_to_resume")
            store.save(change)
            return change

        change.status = "publishing"
        change.last_error = None
        _event(change, "media_publish_write_intent")
        store.save(change)
        try:
            change.remote_id = client.publish_container(change.container_id)
        except MetaRejected:
            return _save_failure(
                store, change, "blocked",
                "Meta rejected media_publish; check instagram_content_publish and eligibility. Write grant was unverified",
                "media_publish_rejected",
            )
        except (MetaUncertain, Exception) as exc:
            message = str(exc) if isinstance(exc, MetaUncertain) else "uncertain outcome after media_publish"
            return _save_failure(store, change, "uncertain", message, "media_publish_uncertain_no_replay")
        change.status = "sent"
        _event(change, "remote_id_persisted", remote_id=change.remote_id)
        store.save(change)
        return _verify(client, store, change)
