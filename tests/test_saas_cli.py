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
    # The journal snapshots accounts.yml for the whole brand. No second
    # platform or sign-out may change its binding while recovery is pending.
    for args in (['connect','tiktok','--no-browser'],['account','attach','tiktok'],['account','logout']):
        blocked=runner.invoke(app,[*args,*common])
        assert blocked.exit_code==1 and 'recover youtube' in blocked.output,blocked.output
    result=runner.invoke(app,['account','recover','youtube',*common])
    assert result.exit_code==0 and 'recovered' in result.output.lower()
    assert not account_cli.recovery_path(brand,Platform.YOUTUBE).exists()
    with server.app.state.saas.vault.transaction() as db:
        assert db.execute("SELECT count(*) FROM records WHERE kind='authorization'").fetchone()[0]==before
    assert cargar_brand(tmp_path,'MyBrand').cuentas['youtube']['channel_id']=='UC-two'


def test_migrate_old_connection_with_browser_confirmation_and_lost_ack_recovery(tmp_path,monkeypatch):
    server,now,ring=wire(tmp_path,monkeypatch);crear_brand(tmp_path,'MyBrand')
    common=['--brand','MyBrand','--root',str(tmp_path)]
    assert runner.invoke(app,['account','login',*common,'--service','https://social.example','--no-browser']).exit_code==0
    from tests.test_saas_migration import old_connection
    from socialctl.connections import account_cli
    old=old_connection(server);brand=cargar_brand(tmp_path,'MyBrand')
    saved={'version':1,'auth_mode':'broker','service_url':'https://social.example',
           'connection_id':old['connection_id'],'account_id':'UC-two','account':{'id':'UC-two','name':'Creator two'}}
    keychain.save(brand,Platform.YOUTUBE,saved,old['connection_secret'])
    (brand.dir_secretos/'youtube.json').write_text(json.dumps(saved))
    legacy_cli.bind_account(brand,Platform.YOUTUBE,saved['account'])
    real_factory=account_cli.http_client;lost=[True]
    def transport(request):
        assert not request.url.path.endswith('/authorizations')
        if '/migrations/' in request.url.path and request.url.path.endswith('/poll'):
            page=server.get('/account/migrate/'+request.url.path.split('/')[-2])
            if page.status_code==200:
                assert server.post(page.url,data=fields(page),headers={'Origin':'https://social.example'}).status_code==200
        with real_factory() as client:
            response=client.send(request)
        if request.url.path.endswith('/activate') and lost[0]:
            lost[0]=False
            raise httpx.ReadTimeout('secret-in-error-never-print')
        return response
    monkeypatch.setattr(account_cli,'http_client',lambda:httpx.Client(transport=httpx.MockTransport(transport)))
    result=runner.invoke(app,['account','migrate','youtube',*common,'--no-browser'])
    assert result.exit_code==1 and 'recover' in result.output,result.output
    assert 'secret-in-error' not in result.output and old['connection_secret'] not in result.output
    result=runner.invoke(app,['account','recover','youtube',*common])
    assert result.exit_code==0,result.output
    brand=cargar_brand(tmp_path,'MyBrand');new=brand.leer_secreto(Platform.YOUTUBE)
    assert new['protocol']=='saas-v1'
    with legacy_cli.http_client() as client:
        from socialctl.connections.client import broker_access_token
        assert broker_access_token(brand,Platform.YOUTUBE,client,refresh=False)=='social-access'
    assert server.post('/v1/connections/'+old['connection_id']+'/token',headers={'Authorization':'Bearer '+old['connection_secret']}).status_code==404
    with server.app.state.saas.vault.transaction() as db:
        assert db.execute("SELECT count(*) FROM records WHERE kind='authorization'").fetchone()[0]==0


def test_migration_never_sends_old_secret_to_unrelated_service(tmp_path,monkeypatch):
    server,now,ring=wire(tmp_path,monkeypatch);crear_brand(tmp_path,'MyBrand')
    common=['--brand','MyBrand','--root',str(tmp_path)]
    assert runner.invoke(app,['account','login',*common,'--service','https://social.example','--no-browser']).exit_code==0
    brand=cargar_brand(tmp_path,'MyBrand')
    saved={'version':1,'auth_mode':'broker','service_url':'https://different-legacy.example','connection_id':'legacy-connection-123','account_id':'UC-two'}
    keychain.save(brand,Platform.YOUTUBE,saved,'legacy-secret-must-not-leak')
    (brand.dir_secretos/'youtube.json').write_text(json.dumps(saved))
    from socialctl.connections import account_cli
    requests=[]
    def capture(request):
        requests.append(request)
        return httpx.Response(404,json={'detail':'unavailable'})
    monkeypatch.setattr(account_cli,'http_client',lambda:httpx.Client(transport=httpx.MockTransport(capture)))
    result=runner.invoke(app,['account','migrate','youtube',*common,'--no-browser'])
    assert result.exit_code==1
    assert requests==[], 'Migration must reject an unrelated credential destination before network I/O'
    assert 'legacy-secret-must-not-leak' not in result.output


def test_official_one_way_migration_origin_is_allowed_and_reverse_is_rejected():
    from socialctl.connections import account_cli
    check=getattr(account_cli,'migration_origin_allowed',lambda old,new:False)
    assert check('https://social.florentino.pro/','https://socialcli.app')
    assert not check('https://socialcli.app','https://social.florentino.pro')
    assert not check('https://social.florentino.pro','https://socialcli.app.attacker.example')


import pytest


@pytest.mark.parametrize('rejected',[False,True])
def test_recovery_pointer_cleanup_failure_keeps_journal_recoverable(tmp_path,monkeypatch,rejected):
    from contextlib import contextmanager
    from pathlib import Path
    from socialctl.connections import account_cli
    server,_,_=wire(tmp_path,monkeypatch);brand=crear_brand(tmp_path,'MyBrand')
    common=['--brand','MyBrand','--root',str(tmp_path)]
    assert runner.invoke(app,['account','login',*common,'--service','https://social.example','--no-browser']).exit_code==0
    factory=account_cli.http_client
    class ActivationClient:
        def __enter__(self):self.client=factory();return self
        def __exit__(self,*args):self.client.close()
        @contextmanager
        def stream(self,method,url,**kwargs):
            if rejected and url.endswith('/activate'):
                yield httpx.Response(409,json={'detail':'connection_changed: fictional rejection'})
            else:
                with self.client.stream(method,url,**kwargs) as response:yield response
    monkeypatch.setattr(account_cli,'http_client',ActivationClient)
    original=Path.unlink;interrupt=[True]
    def unlink(path,*args,**kwargs):
        if path.name=='socialcli-recovery-youtube.json' and interrupt[0]:
            interrupt[0]=False
            raise OSError('fictional cleanup interruption')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'unlink',unlink)
    result=runner.invoke(app,['connect','youtube',*common,'--no-browser'])
    assert result.exit_code==1
    pointer=account_cli.recovery_path(brand,Platform.YOUTUBE)
    saved=json.loads(pointer.read_text())
    assert keychain.get(brand,account_cli.recovery_slot(Platform.YOUTUBE),saved) is not None
    result=runner.invoke(app,['account','recover','youtube',*common])
    assert result.exit_code==0,result.output
    assert not pointer.exists()
    assert keychain.get(brand,account_cli.recovery_slot(Platform.YOUTUBE),saved) is None
    if rejected:
        assert brand.leer_secreto(Platform.YOUTUBE)=={}
    else:
        assert runner.invoke(app,['connections',*common,'--json']).exit_code==0

@pytest.mark.parametrize('failure',['prepare_reply','commit_reply','local_write'])
def test_rotation_recovers_interruption_without_oauth_or_leaking_secrets(tmp_path,monkeypatch,failure):
    server,now,ring=wire(tmp_path,monkeypatch);crear_brand(tmp_path,'MyBrand')
    common=['--brand','MyBrand','--root',str(tmp_path)]
    assert runner.invoke(app,['account','login',*common,'--service','https://social.example','--no-browser']).exit_code==0
    assert runner.invoke(app,['connect','youtube',*common,'--no-browser']).exit_code==0
    from socialctl.connections import account_cli
    from socialctl.connections.client import broker_access_token
    brand=cargar_brand(tmp_path,'MyBrand');old=brand.leer_secreto(Platform.YOUTUBE)
    old_secret=keychain.get(brand,Platform.YOUTUBE,old)
    original=account_cli.http_client;failed=[False]
    def transport(request):
        assert not request.url.path.endswith('/authorizations')
        with original() as client:response=client.send(request)
        target=('/rotations/' in request.url.path and not request.url.path.endswith('/commit')) if failure=='prepare_reply' else request.url.path.endswith('/commit')
        if failure!='local_write' and target and not failed[0]:
            failed[0]=True;raise httpx.ReadTimeout('never-print-secret')
        return response
    monkeypatch.setattr(account_cli,'http_client',lambda:httpx.Client(transport=httpx.MockTransport(transport)))
    real_write=account_cli.atomic_bytes
    def write(path,*args,**kwargs):
        if failure=='local_write' and path.name=='youtube.json' and not failed[0]:
            failed[0]=True;raise OSError('fictional disk interruption')
        return real_write(path,*args,**kwargs)
    monkeypatch.setattr(account_cli,'atomic_bytes',write)
    result=runner.invoke(app,['account','rotate',*common])
    assert result.exit_code==1 and 'rotate' in result.output,result.output
    assert 'never-print-secret' not in result.output
    with legacy_cli.http_client() as client:
        with pytest.raises(ValueError,match='rotate'):broker_access_token(brand,Platform.YOUTUBE,client,refresh=False)
    assert runner.invoke(app,['account','logout',*common]).exit_code==1
    result=runner.invoke(app,['account','rotate',*common])
    assert result.exit_code==0,result.output
    brand=cargar_brand(tmp_path,'MyBrand');new=brand.leer_secreto(Platform.YOUTUBE)
    assert new['connection_id']!=old['connection_id']
    assert keychain.get(brand,Platform.YOUTUBE,new)!=old_secret
    with legacy_cli.http_client() as client:
        assert broker_access_token(brand,Platform.YOUTUBE,client,refresh=False)=='social-access'
    assert server.post('/api/saas/v1/connections/'+old['connection_id']+'/token',headers={'Authorization':'Bearer '+old_secret}).status_code==404
    assert not (brand.dir_secretos/'socialcli-rotation.json').exists()
    visible=''.join(p.read_text() for p in brand.dir_secretos.glob('*.json'))+result.output
    for secret in ring.values.values():assert secret not in visible


def test_rotating_revoked_installation_does_not_block_new_sign_in(tmp_path,monkeypatch):
    server,now,ring=wire(tmp_path,monkeypatch);crear_brand(tmp_path,'MyBrand')
    common=['--brand','MyBrand','--root',str(tmp_path)]
    assert runner.invoke(app,['account','login',*common,'--service','https://social.example','--no-browser']).exit_code==0
    from socialctl.connections import account_cli
    brand=cargar_brand(tmp_path,'MyBrand');saved=account_cli.metadata(brand)
    assert server.delete('/api/saas/v1/installations/'+saved['connection_id'],headers={'Authorization':'Bearer '+keychain.get(brand,account_cli.Slot.ACCOUNT,saved)}).status_code==200
    assert runner.invoke(app,['account','rotate',*common]).exit_code==1
    assert not (brand.dir_secretos/'socialcli-rotation.json').exists()
    assert runner.invoke(app,['account','login',*common,'--service','https://social.example','--no-browser']).exit_code==0


@pytest.mark.parametrize('stage',['prepared','committed','committed_active'])
def test_cancel_recovers_revocation_during_rotation_but_keeps_active_replacement(tmp_path,monkeypatch,stage):
    server,now,ring=wire(tmp_path,monkeypatch);crear_brand(tmp_path,'MyBrand')
    common=['--brand','MyBrand','--root',str(tmp_path)]
    assert runner.invoke(app,['account','login',*common,'--service','https://social.example','--no-browser']).exit_code==0
    assert runner.invoke(app,['connect','youtube',*common,'--no-browser']).exit_code==0
    from socialctl.connections import account_cli
    original=account_cli.http_client;lost=[False]
    def transport(request):
        with original() as client:response=client.send(request)
        target=('/rotations/' in request.url.path and not request.url.path.endswith('/commit')) if stage=='prepared' else request.url.path.endswith('/commit')
        if target and not lost[0]:
            lost[0]=True;raise httpx.ReadTimeout('uncertain rotation reply')
        return response
    monkeypatch.setattr(account_cli,'http_client',lambda:httpx.Client(transport=httpx.MockTransport(transport)))
    assert runner.invoke(app,['account','rotate',*common]).exit_code==1
    if stage!='committed_active':
        vault=server.app.state.saas.vault
        with vault.transaction() as db:db.execute("DELETE FROM records WHERE kind='saas-installation'")
    result=runner.invoke(app,['account','rotate',*common,'--cancel'])
    if stage=='committed_active':
        assert result.exit_code==1 and (cargar_brand(tmp_path,'MyBrand').dir_secretos/'socialcli-rotation.json').exists()
        assert runner.invoke(app,['account','rotate',*common]).exit_code==0
    else:
        assert result.exit_code==0,result.output
        assert not (cargar_brand(tmp_path,'MyBrand').dir_secretos/'socialcli-rotation.json').exists()
        assert runner.invoke(app,['account','login',*common,'--service','https://social.example','--no-browser']).exit_code==0
        assert runner.invoke(app,['account','attach','youtube',*common]).exit_code==0
