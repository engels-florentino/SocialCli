import json
import subprocess
from pathlib import Path
import httpx
import pytest
import yaml
from typer.testing import CliRunner
from socialctl.cli import app
from socialctl.media import leer_media,MediaInvalida,validar_media
from socialctl.models import Platform,PlatformPost,MediaKind

runner=CliRunner()


def probe(*,codec='h264',width=1080,height=1920,fps='30/1',duration='30',format_name='mov,mp4,m4a,3gp,3g2,mj2'):
    return {'streams':[{'codec_type':'video','codec_name':codec,'width':width,'height':height,
        'avg_frame_rate':fps,'pix_fmt':'yuv420p','sample_aspect_ratio':'1:1','disposition':{'attached_pic':0}}],
        'format':{'duration':duration,'format_name':format_name,'tags':{'major_brand':'isom'}}}


def file(tmp_path):
    p=tmp_path/'clip.mp4';p.write_bytes(b'fictional unchanged file bytes');return p


def test_visual_track_is_selected_after_audio_and_cover_art(tmp_path,monkeypatch):
    data=probe();data['streams']=[{'codec_type':'audio','codec_name':'aac'},
        {'codec_type':'video','codec_name':'mjpeg','width':300,'height':300,'disposition':{'attached_pic':1}},*data['streams']]
    monkeypatch.setattr('socialctl.media._ffprobe',lambda p:data)
    asset=leer_media(file(tmp_path))
    assert (asset.width,asset.height)==(1080,1920)
    assert asset.probe['video_codec']=='h264'
    assert 'probe' not in asset.model_dump(mode='json')


@pytest.mark.parametrize('case',['audio_only','no_stream','nan','zero','negative','no_duration','no_width'])
def test_unusable_visual_metadata_fails_closed(tmp_path,monkeypatch,case):
    data=probe()
    if case=='audio_only':data['streams']=[{'codec_type':'audio','codec_name':'aac'}]
    if case=='no_stream':data['streams']=[]
    if case=='nan':data['format']['duration']='NaN'
    if case=='zero':data['format']['duration']='0'
    if case=='negative':data['format']['duration']='-1'
    if case=='no_duration':data['format'].pop('duration')
    if case=='no_width':data['streams'][0].pop('width')
    monkeypatch.setattr('socialctl.media._ffprobe',lambda p:data)
    with pytest.raises(MediaInvalida):leer_media(file(tmp_path))


def test_probe_is_bounded_and_local_only(tmp_path,monkeypatch):
    def run(argv,**kw):
        assert argv[0]=='ffprobe' and argv[argv.index('-protocol_whitelist')+1]=='file'
        assert 0<kw['timeout']<=30 and '-show_entries' in argv
        return subprocess.CompletedProcess(argv,0,json.dumps(probe()),'')
    monkeypatch.setattr(subprocess,'run',run)
    assert leer_media(file(tmp_path)).width==1080


@pytest.mark.parametrize('extension,codec,ratio,has_errors',[
    ('.jpg','mjpeg',(1080,1920),True),('.jpg','mjpeg',(1910,1000),False),
    ('.png','png',(1080,1080),True),('.mp4','h264',(1920,1080),False),
    ('.mp4','vp9',(1080,1920),True)])
def test_instagram_image_and_reel_requirements_differ(tmp_path,monkeypatch,extension,codec,ratio,has_errors):
    path=tmp_path/('supplied'+extension);path.write_bytes(b'original')
    data=probe(codec=codec,width=ratio[0],height=ratio[1],format_name='image2' if extension!='.mp4' else 'mov,mp4')
    monkeypatch.setattr('socialctl.media._ffprobe',lambda p:data)
    asset=leer_media(path)
    result=validar_media(PlatformPost(platform=Platform.INSTAGRAM,body='text',media=[asset]))
    assert bool(result)==has_errors,[e.motivo for e in result]


@pytest.mark.parametrize('platform',[Platform.YOUTUBE,Platform.TIKTOK])
def test_video_routes_reject_image(tmp_path,monkeypatch,platform):
    path=tmp_path/'photo.jpg';path.write_bytes(b'original')
    monkeypatch.setattr('socialctl.media._ffprobe',lambda p:probe(codec='mjpeg'))
    asset=leer_media(path)
    assert validar_media(PlatformPost(platform=platform,body='text',media=[asset]))


def setup_post(tmp_path,monkeypatch):
    from socialctl.brands import crear_brand
    brand=crear_brand(tmp_path,'Example')
    (brand.raiz/'accounts.yml').write_text(yaml.safe_dump({'facebook':{'page_id':'page-example'},'youtube':{'channel_id':'channel-example'}}))
    post=brand.dir_posts/'clip';post.mkdir(parents=True)
    data={'campaign':'clip-vertical','platforms':{
        'facebook':{'body':'Text','content_origin':'standalone','media':['good.mp4','missing.mp4']},
        'youtube':{'title':'Title','body':'Text','content_origin':'standalone','media':['good.mp4']}}}
    (post/'post.yml').write_text(yaml.safe_dump(data))
    good=brand.raiz/'media'/'good.mp4';good.parent.mkdir(exist_ok=True);good.write_bytes(b'original bytes')
    calls=[]
    def read(path):calls.append(path);return probe()
    monkeypatch.setattr('socialctl.media._ffprobe',read)
    monkeypatch.setattr(httpx.HTTPTransport,'handle_request',lambda *a:pytest.fail('no HTTP during local check'))
    return brand,good,calls


def test_preflight_keeps_other_platform_results_and_probes_shared_file_once(tmp_path,monkeypatch):
    brand,good,calls=setup_post(tmp_path,monkeypatch)
    result=runner.invoke(app,['media','check','clip','--brand','Example','--root',str(tmp_path),'--json'])
    assert result.exit_code==1,result.output
    value=json.loads(result.stdout)
    assert value['status']=='partial' and value['platforms']['youtube']['status']=='passed'
    assert value['platforms']['facebook']['status']=='blocked'
    assert any(e['code']=='media_missing' for e in value['platforms']['facebook']['issues'])
    assert len(calls)==1 and good.read_bytes()==b'original bytes'
    assert value['server_media_storage'] is False and value['provider_eligibility_verified'] is False
    assert not (brand.raiz/'.socialctl'/'media-registry').exists()


@pytest.mark.parametrize('update,code',[
    ({'fps':'22/1'},'frame_rate'),({'fps':'0/0'},'frame_rate'),
    ({'width':359},'dimensions'),({'height':4097},'dimensions'),
    ({'codec':'mpeg4'},'video_codec'),({'format_name':'avi'},'video_container')])
def test_tiktok_inspected_video_rejects_unsupported_specs(tmp_path,monkeypatch,update,code):
    from socialctl.media_requirements import technical_issues
    monkeypatch.setattr('socialctl.media._ffprobe',lambda p:probe(**update))
    asset=leer_media(file(tmp_path))
    assert code in {i['code'] for i in technical_issues(Platform.TIKTOK,asset)}


@pytest.mark.parametrize('failure,code,action',[(FileNotFoundError(),'probe_unavailable','install_probe'),
    (subprocess.TimeoutExpired('ffprobe',30),'probe_timeout','inspect_file')])
def test_probe_failure_outputs_fixed_action_and_no_raw_diagnostic(tmp_path,monkeypatch,failure,code,action):
    from socialctl.media import _ffprobe
    _,good,_=setup_post(tmp_path,monkeypatch)
    monkeypatch.setattr('socialctl.media._ffprobe',_ffprobe)
    monkeypatch.setattr(subprocess,'run',lambda *a,**kw:(_ for _ in ()).throw(failure))
    result=runner.invoke(app,['media','check','clip','--brand','Example','--root',str(tmp_path),'--only','youtube','--json'])
    value=json.loads(result.stdout)
    assert result.exit_code==1
    issue=value['platforms']['youtube']['issues'][0]
    assert issue['code']==code and issue['action']==action


def test_media_check_selection_and_approval_hash_are_unchanged(tmp_path,monkeypatch):
    from socialctl.brands import cargar_brand
    from socialctl.postfile import cargar_post
    from socialctl.scheduler import approval_hash
    from socialctl.publisher import validar_todo
    brand,good,calls=setup_post(tmp_path,monkeypatch)
    postpath=brand.dir_posts/'clip'/'post.yml'
    raw=yaml.safe_load(postpath.read_text());raw['platforms']['facebook']['media']=['good.mp4'];postpath.write_text(yaml.safe_dump(raw))
    selected=cargar_brand(tmp_path,'Example');post=cargar_post(selected,'clip')
    before=approval_hash(post,selected,Platform.YOUTUBE)
    post.platforms[Platform.YOUTUBE].media[0].probe=None
    assert approval_hash(post,selected,Platform.YOUTUBE)==before
    result=runner.invoke(app,['media','check','clip','--brand','Example','--root',str(tmp_path),'--only','youtube','--json'])
    assert result.exit_code==0,result.output
    value=json.loads(result.stdout)
    assert set(value['platforms'])=={'youtube'} and value['status']=='passed'
    assert value['platforms']['youtube']['coverage']['all_file_bytes_decoded'] is False


def test_local_nonregular_file_never_starts_probe(tmp_path,monkeypatch):
    monkeypatch.setattr('socialctl.media._ffprobe',lambda p:pytest.fail('not a regular file'))
    with pytest.raises(MediaInvalida):leer_media(tmp_path)


def test_unsupported_codec_blocks_normal_publication_validation(tmp_path,monkeypatch):
    from socialctl.brands import cargar_brand
    from socialctl.postfile import cargar_post
    from socialctl.publisher import validar_todo
    brand,good,calls=setup_post(tmp_path,monkeypatch)
    raw={'campaign':'clip-vertical','platforms':{'instagram':{'body':'Original','media':['good.mp4'],'content_origin':'standalone'}}}
    (brand.dir_posts/'clip'/'post.yml').write_text(yaml.safe_dump(raw))
    monkeypatch.setattr('socialctl.media._ffprobe',lambda p:probe(codec='vp9'))
    selected=cargar_brand(tmp_path,'Example')
    errors=validar_todo(cargar_post(selected,'clip'),selected)[Platform.INSTAGRAM]
    assert any('h264' in e.motivo for e in errors)
    assert good.read_bytes()==b'original bytes'


def test_instagram_hevc_420_ten_bit_is_not_rejected_as_wrong_chroma(tmp_path,monkeypatch):
    data=probe(codec='hevc');data['streams'][0]['pix_fmt']='yuv420p10le'
    monkeypatch.setattr('socialctl.media._ffprobe',lambda p:data)
    asset=leer_media(file(tmp_path))
    assert not validar_media(PlatformPost(platform=Platform.INSTAGRAM,body='Text',media=[asset]))


@pytest.mark.parametrize('platform,suffix,container,brand',[
    (Platform.TIKTOK,'.mkv','matroska,webm',None),
    (Platform.INSTAGRAM,'.3gp','mov,mp4,m4a,3gp,3g2,mj2','3gp6'),
    (Platform.INSTAGRAM,'.mp4','mov,mp4,m4a,3gp,3g2,mj2','3gp6'),
    (Platform.TIKTOK,'.webm','matroska,webm',None)])
def test_ambiguous_container_aliases_do_not_establish_identity(tmp_path,monkeypatch,platform,suffix,container,brand):
    from socialctl.media_requirements import technical_issues
    data=probe(format_name=container);data['format']['tags']={'major_brand':brand}
    monkeypatch.setattr('socialctl.media._ffprobe',lambda p:data)
    path=tmp_path/('clip'+suffix);path.write_bytes(b'fictional supplied file')
    assert 'video_container' in {i['code'] for i in technical_issues(platform,leer_media(path))}


@pytest.mark.parametrize('bad_entry',['../outside.mp4',123])
@pytest.mark.parametrize('bad_body',[False,True])
def test_bad_path_or_body_preserves_other_asset_diagnostics(tmp_path,monkeypatch,bad_entry,bad_body):
    from socialctl.media_preflight import check_post
    brand,good,calls=setup_post(tmp_path,monkeypatch)
    path=brand.dir_posts/'clip'/'post.yml';raw=yaml.safe_load(path.read_text())
    block=raw['platforms']['facebook'];block['media']=[bad_entry,'missing.mp4','good.mp4']
    if bad_body:block['body']=123
    path.write_text(yaml.safe_dump(raw))
    row=check_post(brand,'clip')['platforms']['facebook']
    assert len(row['assets'])==3
    assert row['assets'][0]['issue']['code']=='media_path_invalid'
    assert row['assets'][1]['issue']['code']=='media_missing'
    assert row['assets'][2]['status']=='inspected'
    assert len(calls)==1
