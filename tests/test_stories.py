from __future__ import annotations

from tests.terminal import plain

import shutil
import subprocess
from datetime import timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from socialctl.brands import crear_brand
from socialctl.cli import app
from socialctl.management.changes import now_utc
from socialctl.management.meta_schema import MetaError, MetaUncertain
from socialctl.management.stories import (
    MetaStoryClient,
    STORY_IMAGE_MAX_BYTES,
    STORY_VIDEO_MAX_BYTES,
    STORY_VIDEO_MAX_DURATION_S,
    STORY_VIDEO_MIN_DURATION_S,
    StoryError,
    StoryStore,
    apply_story,
    prepare_story,
    story_fingerprint,
    verify_story,
)
from socialctl.media import leer_media
from socialctl.models import MediaKind, Platform, StoryPost


runner = CliRunner()


@pytest.fixture(scope="session")
def story_samples(tmp_path_factory):
    root = tmp_path_factory.mktemp("story-media")
    image = root / "story.jpg"
    video = root / "story.mp4"
    mov = root / "story.mov"
    aac = root / "story.aac.mp4"
    pcm = root / "story.pcm.mov"
    short = root / "story.short.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=108x192",
            "-frames:v", "1", str(image),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=108x192:r=5:d=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video), "-c", "copy", str(mov)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=108x192:r=5:d=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(aac),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=108x192:r=5:d=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-shortest",
            str(pcm),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=108x192:r=5:d=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(short),
        ],
        check=True,
        capture_output=True,
    )
    return {path.name: path for path in (image, video, mov, aac, pcm, short)}


@pytest.fixture
def brand(tmp_path, story_samples):
    selected = crear_brand(tmp_path, "Example")
    selected.cuentas = {
        "facebook": {"page_id": "123"},
        "instagram": {"ig_user_id": "456"},
    }
    for name, source in story_samples.items():
        shutil.copyfile(source, selected.raiz / "media" / name)
    return selected


def story(brand, *, platform=Platform.INSTAGRAM, suffix=".jpg", expires=None):
    path = brand.raiz / "media" / f"story{suffix}"
    if not path.exists() and suffix == ".png":
        shutil.copyfile(brand.raiz / "media" / "story.jpg", path)
    asset = leer_media(path, ruta_relativa=path.name)
    return StoryPost(
        platform=platform,
        media=asset,
        public_url=f"https://cdn.example/{path.name}" if platform is Platform.INSTAGRAM else None,
        text="Consulta el documental completo",
        expires_at=expires or now_utc() + timedelta(hours=2),
        source_video_id="R4cUGeaKrfU",
    )


def test_story_model_is_separate_and_requires_meta_timezone(brand):
    value = story(brand)
    assert value.platform is Platform.INSTAGRAM
    assert value.media.kind is MediaKind.IMAGE
    with pytest.raises(ValidationError):
        value.model_copy(update={"platform": Platform.YOUTUBE}).model_validate(
            {**value.model_dump(), "platform": "youtube"}
        )


def test_prepare_binds_digest_media_and_rejects_duplicate(brand):
    store = StoryStore(brand.raiz)
    change = prepare_story(brand, store, story(brand))

    assert change.status == "prepared"
    assert change.media_sha256
    assert change.media_size_bytes == change.story.media.size_bytes
    assert change.fingerprint == story_fingerprint(change)
    assert change.capability == "instagram_public_api_write_grant_unverified"
    assert store.load(change.id) == change

    with pytest.raises(StoryError, match="duplicada"):
        prepare_story(brand, store, story(brand))


@pytest.mark.parametrize(
    ("suffix", "url", "message"),
    [
        (".png", "https://cdn.example/story.png", "formato de imagen"),
        (".jpg", "http://cdn.example/story.jpg", "URL pública insegura"),
    ],
)
def test_prepare_rejects_invalid_format_or_public_url(brand, suffix, url, message):
    value = story(brand, suffix=suffix)
    value.public_url = url
    with pytest.raises(StoryError, match=message):
        prepare_story(brand, StoryStore(brand.raiz), value)


def test_prepare_checks_real_formats_and_documented_story_limits(brand, monkeypatch):
    import socialctl.management.stories as module

    assert STORY_IMAGE_MAX_BYTES == 8_000_000
    assert STORY_VIDEO_MAX_BYTES == 100_000_000
    assert (STORY_VIDEO_MIN_DURATION_S, STORY_VIDEO_MAX_DURATION_S) == (3.0, 60.0)

    # Both documented ISO BMFF extensions are inspected and accepted.
    for suffix in (".mp4", ".mov"):
        change = prepare_story(brand, StoryStore(brand.raiz), story(brand, suffix=suffix))
        assert change.story.media.kind is MediaKind.VIDEO
        assert change.story.media.duration_s == pytest.approx(3.0)

    image = story(brand)
    monkeypatch.setattr(module, "STORY_IMAGE_MAX_BYTES", image.media.size_bytes - 1)
    with pytest.raises(StoryError, match="8 MB"):
        prepare_story(brand, StoryStore(brand.raiz), image)
    monkeypatch.setattr(module, "STORY_IMAGE_MAX_BYTES", STORY_IMAGE_MAX_BYTES)

    video = story(brand, suffix=".mp4")
    monkeypatch.setattr(module, "STORY_VIDEO_MAX_BYTES", video.media.size_bytes - 1)
    with pytest.raises(StoryError, match="100 MB"):
        prepare_story(brand, StoryStore(brand.raiz), video)
    monkeypatch.setattr(module, "STORY_VIDEO_MAX_BYTES", STORY_VIDEO_MAX_BYTES)

    invalid = story(brand)
    invalid.media.path.write_bytes(b"x" * invalid.media.size_bytes)
    with pytest.raises(StoryError, match="legible y real|bytes JPEG"):
        prepare_story(brand, StoryStore(brand.raiz), invalid)

    invalid_video = story(brand, suffix=".mp4")
    invalid_video.media.path.write_bytes(b"x" * invalid_video.media.size_bytes)
    with pytest.raises(StoryError, match="contenedor MP4 o MOV"):
        prepare_story(brand, StoryStore(brand.raiz), invalid_video)


def test_prepare_rejects_real_video_outside_duration_limit(brand):
    value = story(brand, suffix=".short.mp4")
    # Lying in the model cannot bypass the duration observed with ffprobe.
    value.media.duration_s = 30.0
    with pytest.raises(StoryError, match="entre 3 y 60"):
        prepare_story(brand, StoryStore(brand.raiz), value)


def test_prepare_accepts_aac_or_no_audio_and_rejects_other_audio_codec(brand):
    for suffix in (".mp4", ".aac.mp4"):
        change = prepare_story(brand, StoryStore(brand.raiz), story(brand, suffix=suffix))
        assert change.story.media.kind is MediaKind.VIDEO

    with pytest.raises(StoryError, match="audio.*AAC"):
        prepare_story(
            brand,
            StoryStore(brand.raiz),
            story(brand, suffix=".pcm.mov"),
        )


def test_apply_requires_exact_digest_before_any_client_call(brand):
    store = StoryStore(brand.raiz)
    change = prepare_story(brand, store, story(brand))

    with pytest.raises(StoryError, match="huella exacta"):
        apply_story(None, store, change.id, "0" * 64, brand=brand)
    assert store.load(change.id).status == "prepared"


def test_apply_expired_never_writes(brand, monkeypatch):
    import socialctl.management.stories as module

    store = StoryStore(brand.raiz)
    change = prepare_story(brand, store, story(brand))
    future = change.story.expires_at + timedelta(seconds=1)
    monkeypatch.setattr(module, "now_utc", lambda: future)

    result = apply_story(None, store, change.id, change.fingerprint, brand=brand)
    assert result.status == "expired"
    assert "expirado" in result.last_error


def test_facebook_apply_is_explicit_handoff_without_adapter(brand):
    store = StoryStore(brand.raiz)
    change = prepare_story(brand, store, story(brand, platform=Platform.FACEBOOK))

    result = apply_story(None, store, change.id, change.fingerprint, brand=brand)

    assert result.status == "handoff_required"
    assert "API pública" in result.last_error
    assert "Business Suite" in result.last_error
    assert result.remote_id is None


@pytest.mark.parametrize(
    ("suffix", "url_field", "content_type"),
    [(".jpg", "image_url", "image/jpeg"), (".mp4", "video_url", "video/mp4")],
)
def test_instagram_adapter_sends_stories_container_and_verifies_id(
    brand, suffix, url_field, content_type
):
    brand.guardar_secreto(
        Platform.INSTAGRAM,
        {"access_token": "TEST_TOKEN", "token_mode": "facebook_page"},
    )
    calls = []
    value = story(brand, suffix=suffix)
    approved_bytes = value.media.path.read_bytes()

    def graph(request: httpx.Request):
        calls.append((request.method, str(request.url), request.content))
        if request.method == "GET" and request.url.host == "cdn.example":
            return httpx.Response(
                200,
                headers={"content-type": content_type},
                content=approved_bytes,
            )
        if request.method == "GET" and request.url.path.endswith("/me"):
            return httpx.Response(
                200,
                json={"id": "123", "instagram_business_account": {"id": "456"}},
            )
        if request.method == "POST" and request.url.path.endswith("/456/media"):
            body = parse_qs(request.content.decode())
            assert body == {
                "media_type": ["STORIES"],
                url_field: [f"https://cdn.example/story{suffix}"],
            }
            return httpx.Response(200, json={"id": "700"})
        if request.method == "GET" and request.url.path.endswith("/700"):
            return httpx.Response(200, json={"id": "700", "status_code": "FINISHED"})
        if request.method == "POST" and request.url.path.endswith("/456/media_publish"):
            assert parse_qs(request.content.decode()) == {"creation_id": ["700"]}
            return httpx.Response(200, json={"id": "800"})
        if request.method == "GET" and request.url.path.endswith("/800"):
            return httpx.Response(
                200,
                json={
                    "id": "800",
                    "owner": {"id": "456"},
                    "media_product_type": "STORY",
                    "permalink": "https://www.instagram.com/stories/example/800/",
                    "timestamp": "2026-09-15T12:00:00+0000",
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    store = StoryStore(brand.raiz)
    change = prepare_story(brand, store, value)
    with httpx.Client(transport=httpx.MockTransport(graph)) as http:
        client = MetaStoryClient(brand, Platform.INSTAGRAM, http, poll_wait_s=0, poll_attempts=1)
        result = apply_story(client, store, change.id, change.fingerprint)

    assert result.status == "verified"
    assert result.container_id == "700"
    assert result.remote_id == "800"
    assert [method for method, _, _ in calls].count("POST") == 2
    assert [event["event"] for event in result.journal][-2:] == [
        "remote_id_persisted",
        "remote_story_verified",
    ]


def test_public_url_preflight_uses_persisted_bytes_across_local_get_race(brand):
    brand.guardar_secreto(
        Platform.INSTAGRAM,
        {"access_token": "TEST_TOKEN", "token_mode": "facebook_page"},
    )
    value = story(brand)
    original = value.media.path.read_bytes()
    changed = bytearray(original)
    changed[len(changed) // 2] ^= 1
    writes = []

    def graph(request: httpx.Request):
        if request.method == "GET" and request.url.path.endswith("/me"):
            return httpx.Response(
                200,
                json={"id": "123", "instagram_business_account": {"id": "456"}},
            )
        if request.method == "GET" and request.url.host == "cdn.example":
            # Simula el cambio después de _recheck_local y justo al comenzar el GET.
            value.media.path.write_bytes(changed)
            return httpx.Response(
                200,
                headers={"content-type": "image/jpeg"},
                content=bytes(changed),
            )
        if request.method == "POST":
            writes.append(request)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    store = StoryStore(brand.raiz)
    change = prepare_story(brand, store, value)
    with httpx.Client(transport=httpx.MockTransport(graph)) as http:
        result = apply_story(
            MetaStoryClient(brand, Platform.INSTAGRAM, http, poll_wait_s=0, poll_attempts=1),
            store,
            change.id,
            change.fingerprint,
        )

    assert result.status == "blocked"
    assert "bytes de la media pública" in result.last_error
    assert writes == []


def test_container_state_resumes_without_recreating_or_rechecking_url(brand):
    class ResumableClient:
        platform = Platform.INSTAGRAM
        account_id = "456"
        poll_attempts = 1
        poll_wait_s = 0

        def __init__(self):
            self.brand = brand
            self.preflight_calls = 0
            self.create_calls = 0
            self.status_calls = 0
            self.publish_calls = 0

        def identity(self):
            return {"account_id": "456"}

        def preflight_public_url(self, change):
            self.preflight_calls += 1

        def create_container(self, value):
            self.create_calls += 1
            return "700"

        def container_status(self, value):
            self.status_calls += 1
            if self.status_calls == 1:
                raise MetaError("lectura temporalmente no disponible")
            return "FINISHED"

        def publish_container(self, value):
            self.publish_calls += 1
            return "800"

        def verify_media(self, value):
            return {"id": value, "owner": {"id": "456"}, "media_product_type": "STORY"}

    store = StoryStore(brand.raiz)
    change = prepare_story(brand, store, story(brand))
    client = ResumableClient()

    first = apply_story(client, store, change.id, change.fingerprint)
    second = apply_story(client, store, change.id, change.fingerprint)

    assert first.status == "container_created"
    assert second.status == "verified"
    assert (client.preflight_calls, client.create_calls, client.publish_calls) == (1, 1, 1)


def test_expired_local_approval_with_container_requires_remote_expired_before_replacement(
    brand, monkeypatch
):
    import socialctl.management.stories as module

    class ContainerClient:
        platform = Platform.INSTAGRAM
        account_id = "456"
        poll_attempts = 1
        poll_wait_s = 0

        def __init__(self):
            self.brand = brand
            self.status = None
            self.status_calls = 0

        def identity(self):
            return {"account_id": "456"}

        def container_status(self, value):
            assert value == "700"
            self.status_calls += 1
            if self.status is None:
                raise MetaError("lectura temporalmente no disponible")
            return self.status

    store = StoryStore(brand.raiz)
    first = prepare_story(brand, store, story(brand))
    first.status = "container_created"
    first.container_id = "700"
    store.save(first)
    future = first.story.expires_at + timedelta(seconds=1)
    monkeypatch.setattr(module, "now_utc", lambda: future)

    client = ContainerClient()
    unresolved = apply_story(client, store, first.id, first.fingerprint)

    assert unresolved.status == "container_created"
    assert client.status_calls == 1
    equivalent = story(brand, expires=future + timedelta(hours=2))
    with pytest.raises(StoryError, match="duplicada.*container_created"):
        prepare_story(brand, store, equivalent)

    client.status = "EXPIRED"
    reconciled = verify_story(client, store, first.id)
    assert reconciled.status == "expired"
    replacement = prepare_story(brand, store, equivalent)
    assert replacement.status == "prepared"


def test_expired_uncertain_blocks_equivalent_effect_until_explicit_reconciliation(
    brand, monkeypatch
):
    import socialctl.management.stories as module

    store = StoryStore(brand.raiz)
    first = prepare_story(brand, store, story(brand))
    first.status = "uncertain"
    first.container_id = "700"
    store.save(first)
    future = first.story.expires_at + timedelta(seconds=1)
    monkeypatch.setattr(module, "now_utc", lambda: future)

    equivalent = story(brand, expires=future + timedelta(hours=2))
    equivalent.public_url = "https://other.example/same-approved-bytes.jpg"
    equivalent.text = "Otra referencia editorial"
    equivalent.source_video_id = "abcdefghijk"
    with pytest.raises(StoryError, match="duplicada.*uncertain"):
        prepare_story(brand, store, equivalent)

    class ReconcileExpired:
        platform = Platform.INSTAGRAM
        account_id = "456"

        def __init__(self):
            self.brand = brand

        def identity(self):
            return {"account_id": "456"}

        def container_status(self, value):
            assert value == "700"
            return "EXPIRED"

    reconciled = verify_story(ReconcileExpired(), store, first.id)
    assert reconciled.status == "expired"
    replacement = prepare_story(brand, store, equivalent)
    assert replacement.status == "prepared"


@pytest.mark.parametrize("status", ["sent", "verified"])
def test_expired_sent_or_verified_still_blocks_duplicate(brand, monkeypatch, status):
    import socialctl.management.stories as module

    store = StoryStore(brand.raiz)
    first = prepare_story(brand, store, story(brand))
    first.status = status
    first.remote_id = "800"
    store.save(first)
    future = first.story.expires_at + timedelta(seconds=1)
    monkeypatch.setattr(module, "now_utc", lambda: future)

    equivalent = story(brand, expires=future + timedelta(hours=2))
    equivalent.public_url = "https://other.example/same-approved-bytes.jpg"
    with pytest.raises(StoryError, match=f"duplicada.*{status}"):
        prepare_story(brand, store, equivalent)


def test_uncertain_publish_is_not_replayed(brand):
    class FakeClient:
        platform = Platform.INSTAGRAM
        account_id = "456"

        def __init__(self):
            self.brand = brand
            self.publish_calls = 0
            self.poll_attempts = 1
            self.poll_wait_s = 0

        def identity(self):
            return {"account_id": "456"}

        def preflight_public_url(self, change):
            return None

        def create_container(self, value):
            return "700"

        def container_status(self, value):
            return "FINISHED"

        def publish_container(self, value):
            self.publish_calls += 1
            raise MetaUncertain("resultado incierto; no repetir")

        def verify_media(self, value):
            raise AssertionError("no hay ID remoto que verificar")

    store = StoryStore(brand.raiz)
    change = prepare_story(brand, store, story(brand))
    client = FakeClient()

    first = apply_story(client, store, change.id, change.fingerprint)
    second = apply_story(client, store, change.id, change.fingerprint)

    assert first.status == second.status == "uncertain"
    assert client.publish_calls == 1
    assert "no repetir" in second.last_error


def test_missing_permission_is_actionable_blocked_state(brand):
    class DeniedClient:
        platform = Platform.INSTAGRAM
        account_id = "456"

        def __init__(self):
            self.brand = brand

        def identity(self):
            raise MetaError("Meta HTTP 403; comprueba permisos/elegibilidad")

    store = StoryStore(brand.raiz)
    change = prepare_story(brand, store, story(brand))
    result = apply_story(DeniedClient(), store, change.id, change.fingerprint)

    assert result.status == "blocked"
    assert "grant de escritura sigue sin verificar" in result.last_error
    assert result.container_id is None


def test_story_help_does_not_require_credentials():
    result = runner.invoke(app, ["story", "--help"])
    assert result.exit_code == 0
    assert {"prepare", "apply", "status", "verify"} <= set(plain(result.output).split())


def test_story_status_is_local_and_does_not_require_credentials(brand, tmp_path):
    store = StoryStore(brand.raiz)
    change = prepare_story(brand, store, story(brand))

    result = runner.invoke(
        app,
        ["story", "status", change.id, "--brand", "Example", "--root", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert change.id in result.output
    assert "prepared_local_no_remote_write" in result.output
