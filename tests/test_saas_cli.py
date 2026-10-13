"""Run the real CLI against real SaaS routes with fictional human approvals."""
import json
import httpx
from typer.testing import CliRunner
from socialctl.cli import app
from socialctl.brands import crear_brand,cargar_brand
from socialctl.models import Platform
from socialctl.connections import keychain,cli as legacy_cli
from tests.test_connections_cli import MemoryKeyring
from tests.test_saas_auth import service,login,fields
from tests.test_saas_connections import provider,select
from tests.test_saas_pairing import approve

runner=CliRunner()


def wire(tmp_path,monkeypatch):
    server,now,_=service(tmp_path/'server',provider=provider);login(server)
    ring=MemoryKeyring();monkeypatch.setattr(keychain,'_backend',lambda:ring)
    def transport(request):
        path=request.url.path
        # A separate fictional human browser approves, never the client itself.
        if path.startswith('/api/saas/v1/pairings/') and path.endswith('/poll'):
            pair_id=path.split('/')[-2]
            page=server.get('/account/pair/'+pair_id)
            if page.status_code==200:
                server.post('/account/pair/'+pair_id,data=fields(page),headers={'Origin':'https://social.example'})
        if path.startswith('/api/saas/v1/authorizations/') and path.endswith('/poll'):
            aid=path.split('/')[-2]
            with server.app.state.saas.vault.transaction() as db:
                record=server.app.state.saas.vault.get(db,'authorization',aid)
            if record and record['payload']['status']=='pending':
                select(server,{'authorization_id':aid,'browser_url':'https://social.example/connect/'+aid})
        response=server.request(request.method,str(request.url),headers=dict(request.headers),content=request.content,follow_redirects=False)
        return httpx.Response(response.status_code,headers=dict(response.headers),content=response.content)
    factory=lambda:httpx.Client(transport=httpx.MockTransport(transport))
    monkeypatch.setattr(legacy_cli,'http_client',factory)
    from socialctl.connections import account_cli
    monkeypatch.setattr(account_cli,'http_client',factory)
    monkeypatch.setattr(account_cli.time,'sleep',lambda _:None)
    return server,now,ring


def test_noninteractive_login_and_browser_selected_connect_keep_json_clean(tmp_path,monkeypatch):
    server,now,ring=wire(tmp_path,monkeypatch);crear_brand(tmp_path,'MyBrand')
    common=['--brand','MyBrand','--root',str(tmp_path)]
    result=runner.invoke(app,['account','login',*common,'--service','https://social.example','--no-browser','--json'])
    assert result.exit_code==0,result.output
    assert json.loads(result.stdout)['status']=='signed_in'
    assert '/account/pair/' in result.stderr
    result=runner.invoke(app,['connect','youtube',*common,'--no-browser'])
    assert result.exit_code==0,result.output
    brand=cargar_brand(tmp_path,'MyBrand')
    metadata=brand.leer_secreto(Platform.YOUTUBE)
    assert metadata['protocol']=='saas-v1' and metadata['account_id']=='UC-two'
    assert brand.cuentas['youtube']['channel_id']=='UC-two'
    from socialctl.connections.client import broker_access_token
    with legacy_cli.http_client() as client:
        assert broker_access_token(brand,Platform.YOUTUBE,client,refresh=False)=='social-access'
    visible=''.join(p.read_text() for p in brand.dir_secretos.glob('*.json'))+result.output
    for secret in list(ring.values.values())+['social-access','social-refresh','identity-access-secret']:
        assert secret not in visible
    status=runner.invoke(app,['connections',*common,'--json'])
    assert status.exit_code==0 and json.loads(status.stdout)['connections'][0]['account']['id']=='UC-two'
    logout=runner.invoke(app,['account','logout',*common])
    assert logout.exit_code==0
    with legacy_cli.http_client() as client:
        import pytest
        with pytest.raises(ValueError,match='installation'):
            broker_access_token(brand,Platform.YOUTUBE,client,refresh=False)


def test_secure_store_failure_happens_before_pairing(tmp_path,monkeypatch):
    server,now,ring=wire(tmp_path,monkeypatch);crear_brand(tmp_path,'MyBrand')
    def unavailable(): raise ValueError('secure keyring unavailable')
    monkeypatch.setattr(keychain,'_backend',unavailable)
    result=runner.invoke(app,['account','login','--brand','MyBrand','--root',str(tmp_path),'--service','https://social.example','--no-browser'])
    assert result.exit_code==1 and 'keyring' in result.output.lower()
    with server.app.state.saas.vault.transaction() as db:
        assert db.execute("SELECT count(*) FROM records WHERE kind='saas-pair'").fetchone()[0]==0


def test_lost_activation_response_recovers_without_repeating_oauth(tmp_path,monkeypatch):
    server,now,ring=wire(tmp_path,monkeypatch);crear_brand(tmp_path,'MyBrand')
    common=['--brand','MyBrand','--root',str(tmp_path)]
    assert runner.invoke(app,['account','login',*common,'--service','https://social.example','--no-browser']).exit_code==0
    from socialctl.connections import account_cli
    real_factory=account_cli.http_client
    lost=[True]
    class LosingClient:
        def __init__(self):self.client=real_factory()
        def __enter__(self):return self
        def __exit__(self,*args):self.client.close()
        def stream(self,method,url,**kwargs):
            if url.endswith('/activate') and lost[0]:
                lost[0]=False
                with self.client.stream(method,url,**kwargs) as response:
                    response.read()
                raise httpx.ReadTimeout('Fictional lost response with secret=never-print')
            return self.client.stream(method,url,**kwargs)
    monkeypatch.setattr(account_cli,'http_client',LosingClient)
    result=runner.invoke(app,['connect','youtube',*common,'--no-browser'])
    assert result.exit_code==1 and 'recover' in result.output and 'never-print' not in result.output
    brand=cargar_brand(tmp_path,'MyBrand')
    assert account_cli.recovery_path(brand,Platform.YOUTUBE).exists()
    with server.app.state.saas.vault.transaction() as db:
        before=db.execute("SELECT count(*) FROM records WHERE kind='authorization'").fetchone()[0]
    result=runner.invoke(app,['account','recover','youtube',*common])
    assert result.exit_code==0 and 'recovered' in result.output.lower()
    assert not account_cli.recovery_path(brand,Platform.YOUTUBE).exists()
    with server.app.state.saas.vault.transaction() as db:
        assert db.execute("SELECT count(*) FROM records WHERE kind='authorization'").fetchone()[0]==before
    assert cargar_brand(tmp_path,'MyBrand').cuentas['youtube']['channel_id']=='UC-two'
