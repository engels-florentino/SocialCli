"""Real publication orchestration with fictional local adapters and workspaces."""
import json

import httpx
import pytest
import yaml
from typer.testing import CliRunner

from socialctl.adapters.base import Adapter, ADAPTADORES
from socialctl.brands import crear_brand
from socialctl.cli import app
from socialctl.models import Platform, PostResult, PostStatus

runner=CliRunner()


@pytest.fixture
def setup_post(tmp_path,monkeypatch):
    def blocked(*args,**kwargs):raise AssertionError('live HTTP forbidden')
    monkeypatch.setattr(httpx.HTTPTransport,'handle_request',blocked)
    brand=crear_brand(tmp_path,'Example')
    (brand.raiz/'accounts.yml').write_text("facebook:\n  page_id: '123'\ninstagram:\n  ig_user_id: '456'\n")
    folder=brand.dir_posts/'fixture';folder.mkdir()
    (folder/'post.yml').write_text(yaml.safe_dump({'slug':'fixture','campaign':'post-imagen',
        'platforms':{'facebook':{'body':'Exact supplied body'},'instagram':{'body':'Exact supplied body'}}}))
    calls=[]
    class Facebook(Adapter):
        platform=Platform.FACEBOOK
        def validate(self,*args):return []
        def publish(self,*args):
            calls.append('facebook')
            return PostResult(platform=self.platform,status=PostStatus.PUBLICADO,platform_id='fb-id',url='https://example.test/fb-id')
    class Instagram(Facebook):
        platform=Platform.INSTAGRAM
        def publish(self,*args):
            calls.append('instagram')
            return PostResult(platform=self.platform,status=PostStatus.ERROR,error='access_token=never-print',riesgo_duplicado=True)
    monkeypatch.setitem(ADAPTADORES,Platform.FACEBOOK,Facebook)
    monkeypatch.setitem(ADAPTADORES,Platform.INSTAGRAM,Instagram)
    return brand,folder,calls


def invoke(tmp_path,command='publish',*options,input=''):
    return runner.invoke(app,[command,'fixture','--brand','Example','--root',str(tmp_path),'--json',*options],input=input)


def test_json_dry_run_keeps_full_preview_and_never_calls_publish(setup_post,tmp_path):
    brand,folder,calls=setup_post
    result=invoke(tmp_path,'publish','--dry-run')
    assert result.exit_code==0,result.output
    payload=json.loads(result.stdout)
    assert payload['status']=='preview' and not calls
    assert 'Exact supplied body' in payload['preview'] and payload['preview'] in result.stderr
    assert not (folder/'resultado.json').exists()


@pytest.mark.parametrize('answer',['n\n',''])
def test_json_refusal_never_publishes(setup_post,tmp_path,answer,monkeypatch):
    monkeypatch.setattr("typer.testing._NamedTextIOWrapper.isatty", lambda self: True)
    brand,folder,calls=setup_post
    result=invoke(tmp_path,input=answer)
    assert result.exit_code==0,result.output
    assert json.loads(result.stdout)['status']=='cancelled' and not calls
    assert 'Exact supplied body' in result.stderr


def test_json_partial_publish_preserves_confirmed_id_and_uncertain_action(setup_post,tmp_path):
    brand,folder,calls=setup_post
    result=invoke(tmp_path,'publish','--yes')
    assert result.exit_code==1,result.output
    payload=json.loads(result.stdout)
    assert payload['status']=='partial' and calls==['facebook','instagram']
    rows={r['platform']:r for r in payload['resultados']}
    assert rows['facebook']['platform_id']=='fb-id'
    assert rows['instagram']['error_detail']['action']=='reconcile'
    assert rows['instagram']['error_detail']['retryable'] is False
    assert payload['persistence']['complete'] is True
    assert 'never-print' not in result.stdout and 'never-print' not in result.stderr
    assert json.loads((folder/'resultado.json').read_text())['resultados'][0]['platform_id']=='fb-id'


def test_json_persistence_failure_keeps_remote_success(setup_post,tmp_path,monkeypatch):
    from socialctl.publisher import PersistenciaError
    def failed(*args):raise PersistenciaError('access_token=never-print')
    monkeypatch.setattr('socialctl.cli.guardar_resultado',failed)
    result=invoke(tmp_path,'publish','--only','facebook','--yes')
    assert result.exit_code==1,result.output
    payload=json.loads(result.stdout)
    assert payload['resultados'][0]['platform_id']=='fb-id'
    assert payload['persistence']['complete'] is False
    assert payload['errors'][0]['code']=='persistence_failed'
    assert 'never-print' not in result.stdout and 'never-print' not in result.stderr


def test_json_interruption_keeps_prior_observation_and_requires_reconcile(setup_post,tmp_path,monkeypatch):
    class Interrupted(Adapter):
        platform=Platform.INSTAGRAM
        def validate(self,*args):return []
        def publish(self,*args):raise KeyboardInterrupt()
    monkeypatch.setitem(ADAPTADORES,Platform.INSTAGRAM,Interrupted)
    result=invoke(tmp_path,'publish','--yes')
    assert result.exit_code==130,result.output
    payload=json.loads(result.stdout)
    assert payload['resultados'][0]['platform_id']=='fb-id'
    assert payload['errors'][0]['action']=='reconcile'
    assert payload['status']=='partial'


def test_json_retry_excludes_uncertain_platform_but_keeps_safe_result(setup_post,tmp_path):
    brand,folder,calls=setup_post
    (folder/'resultado.json').write_text(json.dumps({'resultados':[
        {'platform':'facebook','status':'error','riesgo_duplicado':False},
        {'platform':'instagram','status':'error','riesgo_duplicado':True,'error':'access_token=never-print'}]}))
    result=invoke(tmp_path,'retry','--yes')
    assert result.exit_code==1,result.output
    payload=json.loads(result.stdout)
    assert calls==['facebook'] and payload['status']=='partial'
    assert payload['resultados'][0]['platform_id']=='fb-id'
    assert payload['retry']['blocked']==['instagram']
    assert payload['errors'][0]['action']=='reconcile'
    assert 'never-print' not in result.stdout and 'never-print' not in result.stderr


@pytest.mark.parametrize('command',['publish','retry'])
def test_json_missing_workspace_is_parseable(tmp_path,command):
    result=invoke(tmp_path,command,'--yes')
    assert result.exit_code==1,result.output
    assert json.loads(result.stdout)['errors'][0]['retryable'] is False


def test_json_pending_platform_confirmation_is_not_published(setup_post,tmp_path,monkeypatch):
    brand,folder,calls=setup_post
    data=yaml.safe_load((folder/'post.yml').read_text())
    data['platforms']['tiktok']={'body':'Exact supplied body'}
    (folder/'post.yml').write_text(yaml.safe_dump(data))
    class Pending(Adapter):
        platform=Platform.TIKTOK
        def validate(self,*args):return []
        def publish(self,*args):
            calls.append('tiktok')
            return PostResult(platform=self.platform,status=PostStatus.PENDIENTE_CONFIRMACION,platform_id='upload-id')
    monkeypatch.setitem(ADAPTADORES,Platform.TIKTOK,Pending)
    result=invoke(tmp_path,'publish','--only','tiktok','--yes')
    assert result.exit_code==0,result.output  # legacy pending-submission exit
    payload=json.loads(result.stdout)
    assert payload['status']=='partial' and calls==['tiktok']
    row=payload['resultados'][0]
    assert row['status']=='pendiente_confirmacion' and row['platform_id']=='upload-id'
    assert row['error_detail']['code']=='publication_pending' and not row['error_detail']['retryable']


def test_json_first_comment_failure_keeps_media_confirmed(setup_post,tmp_path,monkeypatch):
    brand,folder,calls=setup_post
    data=yaml.safe_load((folder/'post.yml').read_text())
    data['platforms']['facebook']['first_comment']='Exact supplied first comment'
    (folder/'post.yml').write_text(yaml.safe_dump(data))
    def failed(*args,**kwargs):raise OSError('private diagnostic')
    monkeypatch.setattr('socialctl.publication_steps._first_comment',failed)
    result=invoke(tmp_path,'publish','--only','facebook','--yes')
    assert result.exit_code==0,result.output
    payload=json.loads(result.stdout)
    assert payload['status']=='partial'
    row=payload['resultados'][0]
    assert row['status']=='publicado' and row['platform_id']=='fb-id'
    assert row['first_comment_error_detail']['code']=='comment_incomplete'
    assert row['first_comment_error_detail']['retryable'] is False
    assert row['publication_id']


def test_json_retry_noop_and_protected_outcome_differ(setup_post,tmp_path):
    brand,folder,calls=setup_post
    (folder/'resultado.json').write_text(json.dumps({'resultados':[{'platform':'facebook','status':'publicado','platform_id':'old-id'}]}))
    noop=invoke(tmp_path,'retry','--yes')
    assert noop.exit_code==0 and json.loads(noop.stdout)['status']=='noop'
    assert not calls
    # Real publication creates a durable uncertain journal for Instagram.
    invoke(tmp_path,'publish','--only','instagram','--yes')
    calls.clear()
    (folder/'resultado.json').unlink()
    blocked=invoke(tmp_path,'retry','--yes')
    assert blocked.exit_code==1 and not calls,blocked.output
    payload=json.loads(blocked.stdout)
    assert payload['status']=='error' and payload['retry']['blocked']==['instagram']
    assert payload['errors'][0]['action']=='reconcile'


def test_json_validation_error_retains_preview_without_sending(setup_post,tmp_path,monkeypatch):
    from socialctl.models import ValidationError
    class Invalid(Adapter):
        platform=Platform.FACEBOOK
        def validate(self,*args):return [ValidationError(platform=self.platform,campo='body',motivo='Fixture rejection')]
        def publish(self,*args):raise AssertionError('validation must block publish')
    monkeypatch.setitem(ADAPTADORES,Platform.FACEBOOK,Invalid)
    result=invoke(tmp_path,'publish','--only','facebook','--yes')
    assert result.exit_code==1,result.output
    payload=json.loads(result.stdout)
    assert 'Fixture rejection' in payload['preview']
    assert payload['errors'][0]['code']=='validation_failed'
    assert payload['resultados']==[] and payload['persistence']['complete'] is None


@pytest.mark.parametrize('failed_observation',[1,3])
def test_advisory_report_failure_cannot_block_comment_or_result_persistence(setup_post,tmp_path,monkeypatch,failed_observation):
    from socialctl.publish_output import PublishOutput
    from tests.test_meta_comments import Graph
    brand,folder,calls=setup_post
    brand.guardar_secreto(Platform.FACEBOOK,{'access_token':'SECRET'})
    data=yaml.safe_load((folder/'post.yml').read_text())
    data['platforms']['facebook']['first_comment']='Exact supplied first comment'
    (folder/'post.yml').write_text(yaml.safe_dump(data))
    class Uploaded(Adapter):
        platform=Platform.FACEBOOK
        def validate(self,*args):return []
        def publish(self,*args):
            return PostResult(platform=self.platform,status=PostStatus.PUBLICADO,platform_id='789')
    monkeypatch.setitem(ADAPTADORES,Platform.FACEBOOK,Uploaded)
    graph=Graph();factory=httpx.Client
    monkeypatch.setattr('socialctl.publisher.httpx.Client',lambda **kw:factory(transport=httpx.MockTransport(graph)))
    observe=PublishOutput.observe
    count=0
    def fail_once(self,result):
        nonlocal count
        count+=1
        if count==failed_observation:raise ValueError('access_token=never-print')
        return observe(self,result)
    monkeypatch.setattr(PublishOutput,'observe',fail_once)
    result=invoke(tmp_path,'publish','--only','facebook','--yes')
    payload=json.loads(result.stdout)
    assert len(graph.writes)==1,result.output
    assert result.exit_code==0 and payload['persistence']['complete'] is True,result.output
    saved=json.loads((folder/'resultado.json').read_text())['resultados'][0]
    assert saved['platform_id']=='789' and saved['first_comment_status']=='verified'
    assert payload['errors'][0]['code']=='reporting_incomplete'
    assert 'never-print' not in result.stdout and 'never-print' not in result.stderr


def test_interrupted_media_journal_save_keeps_confirmed_id_and_attempt(setup_post,tmp_path,monkeypatch):
    from socialctl.publication_steps import PublicationStore
    original=PublicationStore.write_json
    def interrupt_after_upload(self,identifier,data):
        if data.get('media') is not None:raise KeyboardInterrupt()
        return original(self,identifier,data)
    monkeypatch.setattr(PublicationStore,'write_json',interrupt_after_upload)
    result=invoke(tmp_path,'publish','--only','facebook','--yes')
    assert result.exit_code==130,result.output
    payload=json.loads(result.stdout)
    assert payload['resultados'][0]['platform_id']=='fb-id'
    assert payload['resultados'][0]['publication_id']
    assert payload['errors'][0]['action']=='reconcile'
    assert payload['persistence']['complete'] is False
