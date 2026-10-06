import pytest

from pathlib import Path

from socialctl.models import (
    CampaignType,
    MediaAsset,
    MediaKind,
    Platform,
    PlatformPost,
    Post,
    PostResult,
    PostStatus,
)


def test_platform_tiene_las_cuatro_redes():
    assert {p.value for p in Platform} == {
        "youtube",
        "facebook",
        "instagram",
        "tiktok",
    }


def test_media_asset_guarda_metadatos():
    asset = MediaAsset(
        path=Path("/tmp/clip.mp4"),
        kind=MediaKind.VIDEO,
        width=1080,
        height=1920,
        duration_s=42.5,
        size_bytes=8_000_000,
    )
    assert asset.kind is MediaKind.VIDEO
    assert asset.duration_s == 42.5


def test_post_agrupa_una_version_por_red():
    clip = MediaAsset(
        path=Path("/tmp/clip.mp4"),
        kind=MediaKind.VIDEO,
        width=1080,
        height=1920,
        duration_s=42.5,
        size_bytes=8_000_000,
    )
    post = Post(
        slug="2026-09-07-santo-domingo",
        brand="Histopast",
        campaign=CampaignType.CLIP_VERTICAL,
        platforms={
            Platform.TIKTOK: PlatformPost(
                platform=Platform.TIKTOK,
                title=None,
                body="La primera ciudad de America",
                hashtags=["historia"],
                media=[clip],
                link=None,
            )
        },
    )
    assert post.platforms[Platform.TIKTOK].body.startswith("La primera")


def test_post_result_por_defecto_no_tiene_url():
    resultado = PostResult(platform=Platform.INSTAGRAM, status=PostStatus.ERROR, error="token caducado")
    assert resultado.url is None
    assert resultado.status is PostStatus.ERROR


@pytest.mark.parametrize('source', ['short', 'R4cUGeaKrfU!', 'https://youtu.be/R4cUGeaKrfU', 'R4cUGeaKrfU\n'])
def test_source_video_id_requires_exact_youtube_id(source):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        PlatformPost(platform=Platform.FACEBOOK, body='x', source_video_id=source)


def test_content_origin_rejects_unknown_classification():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        PlatformPost(platform=Platform.FACEBOOK, body='x', content_origin='inferred')
