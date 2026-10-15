import json
import httpx
import pytest
from typer.testing import CliRunner
from socialctl.cli import app
from socialctl.brands import crear_brand
from socialctl.agent_errors import error_document

@pytest.mark.parametrize('kind', [httpx.InvalidURL, httpx.LocalProtocolError, httpx.UnsupportedProtocol])
def test_local_protocol_failure_is_not_server_rejection(kind):
    result = error_document(kind('https://private:secret@example.test'), read_only=True)
    assert result['code'] == 'local_configuration'
    assert not result['retryable']
    assert 'secret' not in json.dumps(result)

def test_inbox_invalid_proxy_returns_sanitized_json(tmp_path, monkeypatch):
    crear_brand(tmp_path, 'Example')
    monkeypatch.setenv('HTTPS_PROXY', 'invalid://private:secret@example.test')
    result = CliRunner().invoke(app, ['inbox','--brand','Example','--root',str(tmp_path),'--since','2026-10-01','--timezone','UTC','--json'])
    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert report['errors'][0]['code'] == 'local_configuration'
    assert 'secret' not in result.output

def test_generate_key_is_private_usable_and_never_overwritten(tmp_path):
    from cryptography.fernet import Fernet
    directory = tmp_path/'protected'; directory.mkdir(mode=0o700)
    key = directory/'master.key'
    args = ['credentials','generate-key','--key-file',str(key),'--root',str(tmp_path/'workspace')]
    result = CliRunner().invoke(app,args)
    assert result.exit_code == 0, result.output
    original = key.read_bytes()
    cipher = Fernet(original)
    assert cipher.decrypt(cipher.encrypt(b'capability')) == b'capability'
    assert key.stat().st_mode & 0o777 == 0o600
    assert original.decode() not in result.output
    assert CliRunner().invoke(app,args).exit_code != 0
    assert key.read_bytes() == original

def test_progress_throttles_fast_requests_but_keeps_liveness(capsys):
    from socialctl.read_budget import ReadProgress
    now = [0.0]
    progress = ReadProgress('inbox', clock=lambda:now[0])
    for _ in range(500): progress()
    assert len(capsys.readouterr().err.splitlines()) == 1
    now[0] = 6
    progress()
    assert '501' in capsys.readouterr().err

@pytest.mark.parametrize('field,value',[('granted_scopes',['https://www.googleapis.com/auth/youtube.readonly']),('scope','https://www.googleapis.com/auth/youtube.readonly'),('granted_scopes','https://www.googleapis.com/auth/youtube.readonly')])
def test_inbox_known_missing_youtube_management_stops_before_requests(tmp_path, monkeypatch,field,value):
    from datetime import datetime, timezone
    from socialctl.models import Platform
    from socialctl.inbox import sync_brand_inbox
    from socialctl.read_budget import ReadBudget
    brand = crear_brand(tmp_path,'Example')
    brand.cuentas['youtube']={'channel_id':'fictional-channel'}
    brand.guardar_secreto(Platform.YOUTUBE, {field:value})
    import socialctl.inventory as inventory
    monkeypatch.setattr(inventory,'sync_content',lambda *a,**k:pytest.fail('Should not scan videos without the known required grant'))
    result=sync_brand_inbox(brand,since=datetime(2026,10,1,tzinfo=timezone.utc),budget=ReadBudget(30))
    error=next(e for e in result.errors if e.get('platform')=='youtube')
    assert error['code']=='permissions_required'
    assert '--management' in error['message']
    assert result.coverage[0]['complete'] is False

def test_inbox_only_selected_platform_avoids_unrelated_coverage(tmp_path):
    crear_brand(tmp_path,'Example')
    result=CliRunner().invoke(app,['inbox','--brand','Example','--root',str(tmp_path),'--since','2026-10-01','--timezone','UTC','--only','instagram','--json'])
    assert result.exit_code==1
    report=json.loads(result.stdout)
    assert [r['platform'] for r in report['coverage']]==['instagram']

def test_saas_transport_keeps_local_protocol_error_separate():
    from socialctl.connections.saas_client import SaaSClient
    def invalid(request):
        raise httpx.UnsupportedProtocol('private proxy detail',request=request)
    with httpx.Client(transport=httpx.MockTransport(invalid)) as transport:
        with pytest.raises(Exception) as caught:
            SaaSClient('https://example.test',transport).request('GET','/api/saas/v1/capabilities')
    assert error_document(caught.value,read_only=True)['code']=='local_configuration'

def test_account_transport_setup_classifies_bad_proxy(monkeypatch):
    from socialctl.connections.account_cli import http_client
    monkeypatch.setenv('HTTPS_PROXY','invalid://private:secret@example.test')
    with pytest.raises(Exception) as caught: http_client()
    assert error_document(caught.value,read_only=True)['code']=='local_configuration'

@pytest.mark.parametrize('failed',[True,False])
def test_inbox_distinguishes_all_failed_reads_from_verified_empty(tmp_path,monkeypatch,failed):
    from datetime import datetime,timezone
    from tests.test_meta_comments import service
    from socialctl import inbox as mod
    from socialctl.management.meta_client import MetaClient
    from socialctl.models import Platform
    from socialctl.read_budget import ReadBudget
    _,brand,graph,_,_=service(tmp_path)
    def provider(request):
        if request.url.path.endswith('/789/comments'):
            return httpx.Response(200,json={'data':[{'id':'800','message':'Malformed author','from':{}}]}) if failed else httpx.Response(200,json={'data':[]})
        return graph(request)
    monkeypatch.setattr(mod,'read_client',lambda *a,**k:httpx.Client(transport=httpx.MockTransport(provider)))
    monkeypatch.setattr(MetaClient,'content_list',lambda *a,**k:{'data':[{'id':'789'}],'complete':True})
    report=mod.sync_brand_inbox(brand,since=datetime(2026,10,1,tzinfo=timezone.utc),budget=ReadBudget(10),only=[Platform.FACEBOOK])
    assert report.data['items']==[]
    assert report.status==('error' if failed else 'ok')
    assert not graph.writes

@pytest.mark.parametrize('structured',[True,False])
def test_stats_interrupt_preserves_completed_data_with_partial_exit(tmp_path,monkeypatch,structured):
    from socialctl.metricas.modelos import LecturaRed,EstadoLectura,Cuenta
    from socialctl.models import Platform
    crear_brand(tmp_path,'Example')
    def read(platform,*args):
        if platform is Platform.FACEBOOK:raise KeyboardInterrupt()
        return LecturaRed(estado=EstadoLectura.OK,cuenta=Cuenta(seguidores=7))
    monkeypatch.setattr('socialctl.cli.leer_red',read)
    result=CliRunner().invoke(app,['stats','--brand','Example','--root',str(tmp_path),'--only','youtube','--only','facebook']+(['--json'] if structured else []))
    assert result.exit_code==3,result.output
    if structured:
        report=json.loads(result.stdout)
        assert report['status']=='partial'
        assert report['data']['snapshot']['redes']['youtube']['cuenta']['seguidores']==7
    else:assert 'youtube: ok' in result.stdout

@pytest.mark.parametrize('command',['stats','inbox'])
def test_malformed_proxy_port_never_exposes_its_value(tmp_path,monkeypatch,command):
    crear_brand(tmp_path,'Example')
    monkeypatch.setenv('HTTPS_PROXY','http://private:fictional-secret')
    args=[command,'--brand','Example','--root',str(tmp_path),'--json']
    if command=='inbox':args+=['--since','2026-10-01','--timezone','UTC']
    result=CliRunner().invoke(app,args)
    assert result.exit_code==1
    report=json.loads(result.stdout)
    assert report['errors'][0]['code']=='local_configuration'
    assert 'fictional-secret' not in result.output

@pytest.mark.parametrize('method',['GET','POST'])
@pytest.mark.parametrize('failure',['deadline','oversize','timeout'])
def test_saas_preserves_budget_reason_and_uncertain_token_exchange(method,failure):
    from socialctl.connections.saas_client import SaaSClient
    from socialctl.read_budget import ReadDeadlineExceeded,ReadResponseTooLarge
    def provider(request):
        if failure=='deadline':raise ReadDeadlineExceeded()
        if failure=='oversize':raise ReadResponseTooLarge('size limit')
        raise httpx.ReadTimeout('private detail',request=request)
    with httpx.Client(transport=httpx.MockTransport(provider)) as transport:
        with pytest.raises(Exception) as caught:
            SaaSClient('https://example.test',transport).request(method,'/api/saas/v1/connections/fixture/token')
    detail=error_document(caught.value,read_only=True)
    assert detail['code']==('operation_uncertain' if method=='POST' else {'deadline':'read_deadline','oversize':'response_too_large','timeout':'transport_failure'}[failure])
    assert detail['retryable'] is (method=='GET' and failure=='timeout')
