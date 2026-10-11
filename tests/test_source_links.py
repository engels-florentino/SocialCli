import pytest
from typer.testing import CliRunner
from socialctl.brands import crear_brand
from socialctl.models import Post,Platform,PlatformPost,CampaignType
from socialctl.cli import app


def setup(tmp_path,content='version: 1\nclips:\n  clip:\n    video_id: abcdefghijk\n    series: History\n'):
    brand=crear_brand(tmp_path,'Example')
    (brand.raiz/'source-videos.yml').write_text(content)
    post=Post(slug='clip',brand=brand.nombre,campaign=CampaignType.POST_IMAGEN,
        platforms={p:PlatformPost(platform=p,body='Exact caption') for p in Platform})
    return brand,post


def test_exact_map_generates_full_approved_placements(tmp_path):
    from socialctl.source_links import apply_source_mapping,resolve_source_link
    brand,post=setup(tmp_path)
    assert resolve_source_link(brand,'clip')=='https://www.youtube.com/watch?v=abcdefghijk'
    mapped=apply_source_mapping(brand,post)
    for p in (Platform.YOUTUBE,Platform.FACEBOOK,Platform.INSTAGRAM):
        assert mapped.platforms[p].first_comment=='Full video: https://www.youtube.com/watch?v=abcdefghijk'
        assert mapped.platforms[p].source_video_id=='abcdefghijk'
    assert mapped.platforms[Platform.TIKTOK].body=='Exact caption\n\nFull video: https://www.youtube.com/watch?v=abcdefghijk'
    assert mapped.platforms[Platform.TIKTOK].first_comment is None


@pytest.mark.parametrize('content',[
    'version: 1\nversion: 1\nclips: {}',
    'version: 1\nclips:\n  clip: {video_id: abcdefghijk}\n  clip: {video_id: 12345678901}',
    'version: 1\nclips:\n  clip: {video_id: bad}',
    'version: 1\nclips:\n  clip: {series: History}',
    'version: 1\nseries: {History: [abcdefghijk, 12345678901]}\nclips: {}',
])
def test_invalid_or_ambiguous_maps_fail_closed(tmp_path,content):
    from socialctl.source_links import resolve_source_link
    brand,_=setup(tmp_path,content)
    with pytest.raises(ValueError):resolve_source_link(brand,'clip')


def test_missing_map_is_unresolved_only_when_requested(tmp_path):
    from socialctl.source_links import apply_source_mapping,resolve_source_link
    brand,post=setup(tmp_path);(brand.raiz/'source-videos.yml').unlink()
    assert resolve_source_link(brand,'clip') is None
    assert apply_source_mapping(brand,post)==post
    with pytest.raises(ValueError,match='unresolved'):apply_source_mapping(brand,post,required=True)


@pytest.mark.parametrize('field,value',[
    ('source_video_id','12345678901'),('content_origin','standalone'),
    ('first_comment','Watch https://youtu.be/12345678901'),
    ('body','Watch https://www.youtube.com/watch?v=12345678901'),
    ('link','https://youtube.com/shorts/12345678901'),
])
def test_conflicting_explicit_intent_is_never_overwritten(tmp_path,field,value):
    from socialctl.source_links import apply_source_mapping
    brand,post=setup(tmp_path)
    setattr(post.platforms[Platform.FACEBOOK],field,value)
    with pytest.raises(ValueError,match='conflict'):apply_source_mapping(brand,post)


def test_mapping_change_invalidates_approval_before_publication(tmp_path,monkeypatch):
    import socialctl.cli as cli
    from socialctl.source_links import apply_source_mapping
    brand,post=setup(tmp_path)
    post=apply_source_mapping(brand,post)
    monkeypatch.setattr(cli,'validar_todo',lambda *a:{})
    monkeypatch.setattr(cli,'publicar',lambda *a,**k:pytest.fail('changed mapping must prevent publication'))
    def confirm(*args):
        (brand.raiz/'source-videos.yml').write_text('version: 1\nclips:\n  clip: {video_id: 12345678901}\n')
        return True
    monkeypatch.setattr(cli,'_confirmar',confirm)
    with pytest.raises(__import__('typer').Exit):
        cli._publicar_impl(post,brand,dry_run=False,yes=False,only=[Platform.FACEBOOK])


def test_mapping_is_shown_in_complete_dry_run(tmp_path,monkeypatch):
    import socialctl.cli as cli
    brand,post=setup(tmp_path)
    monkeypatch.setattr(cli,'cargar_post',lambda *a:post)
    monkeypatch.setattr(cli,'validar_todo',lambda *a:{})
    result=CliRunner().invoke(app,['publish','clip','--brand',brand.nombre,'--root',str(tmp_path),'--only','facebook','--dry-run','--source-link'])
    assert result.exit_code==0,result.stdout
    assert 'Full video: https://www.youtube.com/watch?v=abcdefghijk' in result.stdout
    assert 'First comment' in result.stdout


def test_generated_meta_comment_passes_existing_origin_policy(tmp_path):
    from socialctl.source_links import apply_source_mapping
    from socialctl.publication_policy import validate_derivation
    brand,post=setup(tmp_path)
    post=apply_source_mapping(brand,post)
    for platform in (Platform.FACEBOOK,Platform.INSTAGRAM):
        assert validate_derivation(post.platforms[platform],require_origin=True)==[]


def test_mapped_first_comment_uses_existing_durable_no_replay(tmp_path,monkeypatch):
    from tests.test_first_comment_durability import setup_publication
    from socialctl.source_links import apply_source_mapping
    from socialctl.publisher import publicar
    from socialctl.publication_steps import retry_first_comment
    brand,graph,client,post,uploads=setup_publication(tmp_path,monkeypatch)
    post.platforms[Platform.FACEBOOK].first_comment=None
    (brand.raiz/'source-videos.yml').write_text('version: 1\nclips:\n  clip: {video_id: abcdefghijk}\n')
    post=apply_source_mapping(brand,post)
    result=publicar(post,brand)[0]
    assert result.first_comment_status=='verified'
    assert len(graph.writes)==1
    retry_first_comment(brand,client.client,result.publication_id)
    assert len(graph.writes)==1 and len(uploads)==1


def test_changed_map_blocks_pending_follow_up_without_reupload(tmp_path,monkeypatch):
    from tests.test_first_comment_durability import setup_publication
    from socialctl.source_links import apply_source_mapping
    from socialctl.publisher import publicar
    from socialctl.publication_steps import retry_first_comment
    from socialctl.management.meta_comments import CommentError
    brand,graph,client,post,uploads=setup_publication(tmp_path,monkeypatch)
    post.platforms[Platform.FACEBOOK].first_comment=None
    path=brand.raiz/'source-videos.yml'
    path.write_text('version: 1\nclips:\n  clip: {video_id: abcdefghijk}\n')
    post=apply_source_mapping(brand,post)
    def stop(*args):raise OSError('fictional persistence interruption')
    result=publicar(post,brand,on_media_result=stop)[0]
    assert result.first_comment_status=='pending' and not graph.writes
    path.write_text('version: 1\nclips:\n  clip: {video_id: zyxwvutsrqp}\n')
    with pytest.raises(CommentError,match='mapping changed'):
        retry_first_comment(brand,client.client,result.publication_id)
    assert len(uploads)==1 and not graph.writes
