import pytest

from socialctl.models import MediaAsset, MediaKind, Platform, PlatformPost
from socialctl.publication_policy import expected_long_comment, validate_derivation


@pytest.mark.parametrize('platform', [Platform.FACEBOOK, Platform.INSTAGRAM])
def test_corte_exige_comentario_exactamente_del_origen(platform):
    pp = PlatformPost(platform=platform, body='Documental',
                      content_origin='youtube_long', source_video_id='R4cUGeaKrfU')
    assert validate_derivation(pp, require_origin=True)
    for wrong in ['🎥 Video completo en https://www.youtube.com/watch?v=Vhb3l5-KmEg',
                  expected_long_comment('R4cUGeaKrfU') + ' ',
                  'https://www.youtube.com/watch?v=R4cUGeaKrfU']:
        pp.first_comment = wrong
        assert validate_derivation(pp, require_origin=True)
    pp.first_comment = expected_long_comment('R4cUGeaKrfU')
    assert validate_derivation(pp, require_origin=True) == []


@pytest.mark.parametrize('platform', list(Platform))
@pytest.mark.parametrize('kind', [MediaKind.VIDEO, MediaKind.IMAGE, None])
@pytest.mark.parametrize('require_origin', [True, False])
def test_missing_origin_only_required_for_new_meta_video(platform, kind, require_origin):
    pp = PlatformPost(platform=platform, body='x', media=[] if kind is None else [
        MediaAsset(path='asset', kind=kind)])
    errors = validate_derivation(pp, require_origin=require_origin)
    assert bool(errors) == (require_origin and kind is MediaKind.VIDEO and
                            platform in {Platform.FACEBOOK, Platform.INSTAGRAM})


@pytest.mark.parametrize('require_origin', [True, False])
@pytest.mark.parametrize('origin,source,field', [
    (None, 'R4cUGeaKrfU', 'content_origin'),
    ('standalone', 'R4cUGeaKrfU', 'source_video_id'),
    ('youtube_long', None, 'source_video_id'),
    ('youtube_long', 'R4cUGeaKrfU', 'first_comment'),
])
def test_explicit_contradictions_always_fail(require_origin, origin, source, field):
    pp = PlatformPost(platform=Platform.FACEBOOK, body='x', content_origin=origin,
                      source_video_id=source)
    assert [e.campo for e in validate_derivation(pp, require_origin=require_origin)] == [field]


@pytest.mark.parametrize('platform', list(Platform))
def test_standalone_needs_no_comment(platform):
    pp = PlatformPost(platform=platform, body='x', content_origin='standalone')
    assert validate_derivation(pp, require_origin=True) == []


@pytest.mark.parametrize('platform', [Platform.YOUTUBE, Platform.TIKTOK])
def test_other_network_derivation_needs_source_but_not_meta_comment(platform):
    pp = PlatformPost(platform=platform, body='x', content_origin='youtube_long',
                      source_video_id='R4cUGeaKrfU')
    assert validate_derivation(pp, require_origin=True) == []


def test_preview_shows_declared_origin_and_source():
    from socialctl.models import CampaignType, Post
    from socialctl.publisher import render_preview
    pp = PlatformPost(platform=Platform.FACEBOOK, body='x', content_origin='youtube_long',
                      source_video_id='R4cUGeaKrfU', first_comment=expected_long_comment('R4cUGeaKrfU'))
    post = Post(slug='clip', brand='Histopast', campaign=CampaignType.CLIP_VERTICAL,
                platforms={pp.platform: pp})
    preview = render_preview(post, {})
    assert 'Origen: youtube_long' in preview
    assert 'ID del largo de origen: R4cUGeaKrfU' in preview
