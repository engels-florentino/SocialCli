import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from socialctl.adapters.youtube import YouTubeAdapter
from socialctl.brands import Brand
from socialctl.formatter import texto_publicado, validar_texto
from socialctl.models import CampaignType, MediaAsset, MediaKind, Platform, PlatformPost, Post
from socialctl.postfile import cargar_post, guardar_post
from socialctl.publisher import render_preview
from socialctl.scheduler import approval_hash, approval_review_reason, legacy_approval_hash, ScheduleEntry
from tests.test_youtube_metadata_parts import brand_at, isolated


def post(**kwargs):
    return PlatformPost(**{"platform": Platform.YOUTUBE, "title": "Title", "body": "Body", "first_comment": "Keep comment", **kwargs})


def whole(pp):
    return Post(slug="demo", brand="MarcaA", campaign=CampaignType.CLIP_VERTICAL, platforms={Platform.YOUTUBE: pp})


def test_explicit_visible_hashtags_and_internal_tags_drive_preview_and_payload(tmp_path):
    brand = brand_at(tmp_path)
    asset = tmp_path / "synthetic.bin"
    asset.write_bytes(b"synthetic transport fixture")
    pp = post(tags=["internal key"], visible_hashtags=["Visible"],
              media=[MediaAsset(path=asset, kind=MediaKind.VIDEO, size_bytes=27)])
    assert texto_publicado(pp) == "Body\n\n#Visible"
    preview = render_preview(whole(pp), {})
    assert "#Visible" in preview and "internal key" in preview
    assert "Hashtags visibles" in preview
    sent = []
    def transport(request):
        if request.method == "POST":
            sent.append(json.loads(request.content))
            return httpx.Response(200, headers={"Location": "https://upload.invalid/session"})
        return httpx.Response(200, json={"id": "video-synthetic"})
    result = YouTubeAdapter().publish(pp, brand, httpx.Client(transport=httpx.MockTransport(transport)))
    assert result.platform_id == "video-synthetic", result.error
    assert sent[0]["snippet"] == {"title": "Title", "description": "Body\n\n#Visible", "tags": ["internal key"]}


def test_legacy_hashtags_remain_internal_and_new_fields_roundtrip(tmp_path):
    brand = brand_at(tmp_path)
    legacy = post(hashtags=["legacy"])
    assert texto_publicado(legacy) == "Body"
    pp = post(tags=[], visible_hashtags=[])
    file = guardar_post(brand, whole(pp))
    loaded = cargar_post(brand, "demo").platforms[Platform.YOUTUBE]
    assert "tags: []" in file.read_text()
    assert loaded.tags == [] and loaded.visible_hashtags == []
    assert loaded.first_comment == "Keep comment"


@pytest.mark.parametrize("kwargs", [{"hashtags": ["legacy"], "tags": ["explicit"]},
                                    {"visible_hashtags": ["not one hashtag"]}, {"tags": None}])
def test_ambiguous_invalid_or_null_explicit_fields_fail_closed(kwargs):
    with pytest.raises(ValidationError):
        post(**kwargs)


@pytest.mark.parametrize("platform", [Platform.FACEBOOK, Platform.INSTAGRAM, Platform.TIKTOK])
def test_other_network_hashtags_unchanged_and_reject_youtube_fields(platform):
    normal = PlatformPost(platform=platform, body="Body", hashtags=["Visible"])
    assert texto_publicado(normal) == "Body\n\n#Visible"
    with pytest.raises(ValidationError):
        PlatformPost(platform=platform, body="Body", tags=[])


def test_youtube_lengths_validate_exact_description_and_internal_tags():
    assert any(error.campo == "body" for error in validar_texto(post(body="x" * 4999, visible_hashtags=["Visible"])))
    assert any(error.campo == "tags" for error in validar_texto(post(tags=["x" * 501])))


def test_golden_legacy_approval_hashes_and_migrated_shape_stay_unchanged():
    brand = Brand(nombre="MarcaA", raiz=Path("/synthetic/MarcaA"), cuentas={"youtube": {"channel_id": "channel-a"}})
    data = whole(post(hashtags=["legacy"]))
    expected = "v2:669a9b8515b874235bf6f08cd5f5d111e3009b50dd99b2f4976f862081cf68b0"
    assert approval_hash(data, brand, Platform.YOUTUBE) == expected
    assert legacy_approval_hash(data, Platform.YOUTUBE) == "cf84960e40e71cb46faf0c0aa01c9348d55c33bd4a6f0c65d3f82fd991e6527c"
    assert approval_review_reason(expected, data, brand, Platform.YOUTUBE) is None
    migrated = ScheduleEntry.model_validate({"id": "demo/youtube", "brand": "MarcaA", "slug": "demo",
        "platform": "youtube", "scheduled_at": "2099-01-01T00:00:00Z", "status": "approved", "attempts": 0,
        "content_hash": expected, "approval_migration": "synthetic-approved-migration",
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z"})
    assert approval_review_reason(migrated.content_hash, data, brand, Platform.YOUTUBE) is None


@pytest.mark.parametrize("field", ["tags", "visible_hashtags"])
def test_new_metadata_and_absent_versus_empty_intent_invalidate_hash(tmp_path, field):
    brand = brand_at(tmp_path)
    baseline = approval_hash(whole(post()), brand, Platform.YOUTUBE)
    empty = approval_hash(whole(post(**{field: []})), brand, Platform.YOUTUBE)
    value = approval_hash(whole(post(**{field: ["new"]})), brand, Platform.YOUTUBE)
    assert len({baseline, empty, value}) == 3
    assert approval_review_reason(baseline, whole(post(**{field: []})), brand, Platform.YOUTUBE) is not None
