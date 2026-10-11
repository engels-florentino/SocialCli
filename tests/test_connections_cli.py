"""Real browser pairing service plus fictional HTTP and an in-memory OS keyring boundary."""
import hashlib
import importlib.util
import json
from urllib.parse import parse_qs,urlsplit

import httpx
import pytest
from typer.testing import CliRunner

from socialctl.cli import app
from socialctl.brands import crear_brand,cargar_brand
from socialctl.models import Platform

runner=CliRunner()


class MemoryKeyring:
    def __init__(self): self.values={}
    def get_password(self,service,user): return self.values.get((service,user))
    def set_password(self,service,user,password): self.values[(service,user)]=password
    def delete_password(self,service,user): self.values.pop((service,user),None)


def wire(tmp_path,monkeypatch):
    assert importlib.util.find_spec('socialctl.connections.client') is not None, 'CLI browser connection is missing'
    from socialctl.connections import client as client_module,keychain,cli as cli_module
    monkeypatch.setattr(cli_module, 'interactive_terminal', lambda: True)
    from tests.test_connection_service import make_service, begin_browser
    server,store=make_service(tmp_path/'server')
    ring=MemoryKeyring()
    monkeypatch.setattr(keychain,'_backend',lambda:ring)
    def transport(r):
        response=server.request(r.method,str(r.url),headers=dict(r.headers),content=r.content,follow_redirects=False)
        return httpx.Response(response.status_code,headers=dict(response.headers),content=response.content)
    monkeypatch.setattr(cli_module,'http_client',lambda:httpx.Client(transport=httpx.MockTransport(transport)))
    def browser(url):
        response=begin_browser(server,url)
        state=parse_qs(urlsplit(response.headers['location']).query)['state'][0]
        assert server.get('/oauth/youtube/callback',params={'state':state,'code':'code'}).status_code==200
        return True
    monkeypatch.setattr(cli_module.webbrowser,'open',browser)
    return server,store,ring


def test_connect_binds_confirmed_channel_and_stores_only_metadata(tmp_path,monkeypatch):
    server,store,ring=wire(tmp_path,monkeypatch)
    brand=crear_brand(tmp_path,'Example')
    result=runner.invoke(app,['connect','youtube','--brand','Example','--root',str(tmp_path),'--service','https://social.example'],input='y\n')
    assert result.exit_code==0,result.output
    assert 'Creator one' in result.output and 'UC-one' in result.output
    assert 'connected' in result.output.lower()
    brand=cargar_brand(tmp_path,'Example')
    metadata=brand.leer_secreto(Platform.YOUTUBE)
    assert metadata['auth_mode']=='broker' and metadata['account_id']=='UC-one'
    assert brand.cuentas['youtube']['channel_id']=='UC-one'
    assert len(ring.values)==1
    disk=''.join(p.read_text() for p in brand.dir_secretos.glob('*.json'))
    for value in ['provider-access-one','provider-refresh-one','app-secret-one',next(iter(ring.values.values()))]:
        assert value not in disk+result.output
    status=runner.invoke(app,['connections','--brand','Example','--root',str(tmp_path),'--json'])
    assert status.exit_code==0,status.output
    assert json.loads(status.output)['connections'][0]['account']['id']=='UC-one'
    disconnected=runner.invoke(app,['disconnect','youtube','--brand','Example','--root',str(tmp_path)],input='y\n')
    assert disconnected.exit_code==0,disconnected.output
    assert not ring.values
    assert brand.leer_secreto(Platform.YOUTUBE)=={}
    with store.transaction() as db:
        assert db.execute("SELECT count(*) FROM records WHERE kind='connection'").fetchone()[0]==0


def test_declining_connection_preserves_existing_credentials(tmp_path,monkeypatch):
    server,store,ring=wire(tmp_path,monkeypatch)
    brand=crear_brand(tmp_path,'Example')
    brand.guardar_secreto(Platform.YOUTUBE,{'access_token':'existing-token'})
    before=(brand.raiz/'accounts.yml').read_bytes()
    result=runner.invoke(app,['connect','youtube','--brand','Example','--root',str(tmp_path),'--service','https://social.example'],input='n\n')
    assert result.exit_code==0,result.output
    assert brand.leer_secreto(Platform.YOUTUBE)=={'access_token':'existing-token'}
    assert (brand.raiz/'accounts.yml').read_bytes()==before
    assert not ring.values
    with store.transaction() as db:
        assert db.execute("SELECT count(*) FROM records").fetchone()[0]==0


def test_keyring_write_failure_cleans_up_new_connection(tmp_path,monkeypatch):
    server,store,ring=wire(tmp_path,monkeypatch)
    brand=crear_brand(tmp_path,'Example')
    brand.guardar_secreto(Platform.YOUTUBE,{'access_token':'existing-token'})
    def fail(*args): raise RuntimeError('fictional-secret-should-not-leak')
    monkeypatch.setattr(ring,'set_password',fail)
    result=runner.invoke(app,['connect','youtube','--brand','Example','--root',str(tmp_path),'--service','https://social.example'],input='y\n')
    assert result.exit_code==1
    assert 'fictional-secret' not in result.output
    assert brand.leer_secreto(Platform.YOUTUBE)=={'access_token':'existing-token'}
    with store.transaction() as db:
        assert db.execute("SELECT count(*) FROM records WHERE kind='connection'").fetchone()[0]==0


def test_connect_requires_brand_and_rejects_plaintext_keyring(tmp_path,monkeypatch):
    assert runner.invoke(app,['connect','youtube']).exit_code!=0
    assert importlib.util.find_spec('socialctl.connections.keychain') is not None, 'secure connection keychain is missing'
    import keyring
    from socialctl.connections import keychain
    class Plaintext:
        __module__='keyrings.alt.file'
    monkeypatch.setattr(keyring,'get_keyring',lambda:Plaintext())
    with pytest.raises(ValueError,match='secure OS keyring'):
        keychain._backend()


def test_activation_failure_restores_existing_credentials_and_configuration(tmp_path,monkeypatch):
    server,store,ring=wire(tmp_path,monkeypatch)
    from socialctl.connections import cli as cli_module
    brand=crear_brand(tmp_path,'Example')
    brand.guardar_secreto(Platform.YOUTUBE,{'access_token':'existing-token'})
    before=(brand.raiz/'accounts.yml').read_bytes()
    def transport(r):
        if r.url.path.endswith('/token'):
            return httpx.Response(503,json={'detail':'service unavailable'})
        response=server.request(r.method,str(r.url),headers=dict(r.headers),content=r.content,follow_redirects=False)
        return httpx.Response(response.status_code,headers=dict(response.headers),content=response.content)
    monkeypatch.setattr(cli_module,'http_client',lambda:httpx.Client(transport=httpx.MockTransport(transport)))
    result=runner.invoke(app,['connect','youtube','--brand','Example','--root',str(tmp_path),'--service','https://social.example'],input='y\n')
    assert result.exit_code==1
    assert brand.leer_secreto(Platform.YOUTUBE)=={'access_token':'existing-token'}
    assert (brand.raiz/'accounts.yml').read_bytes()==before
    assert not ring.values


def test_second_connection_attempt_cannot_overwrite_a_brand_lock(tmp_path,monkeypatch):
    import fcntl,os
    server,store,ring=wire(tmp_path,monkeypatch)
    brand=crear_brand(tmp_path,'Example')
    fd=os.open(brand.dir_secretos/'connection.lock',os.O_RDWR|os.O_CREAT,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        result=runner.invoke(app,['connect','youtube','--brand','Example','--root',str(tmp_path),'--service','https://social.example'],input='y\n')
        assert result.exit_code==1
        assert 'in progress' in result.output
        assert brand.leer_secreto(Platform.YOUTUBE)=={}
    finally:
        os.close(fd)


def test_legacy_auth_requires_disconnect_before_replacing_a_shared_connection(tmp_path):
    brand=crear_brand(tmp_path,'Example')
    brand.guardar_secreto(Platform.YOUTUBE,{'auth_mode':'broker','connection_id':'one'})
    result=runner.invoke(app,['auth','youtube','--brand','Example','--root',str(tmp_path)])
    assert result.exit_code==1
    assert 'disconnect' in result.output.lower()
    assert 'client_id' not in result.output


def test_ctrl_c_during_installation_restores_existing_account_and_credentials(tmp_path,monkeypatch):
    server,store,ring=wire(tmp_path,monkeypatch)
    from socialctl.connections import cli as cli_module
    brand=crear_brand(tmp_path,'Example')
    brand.guardar_secreto(Platform.YOUTUBE,{'access_token':'existing-token'})
    before=(brand.raiz/'accounts.yml').read_bytes()
    real=cli_module.atomic_bytes
    interrupted=[False]
    def write_then_interrupt(path,content,mode=0o600):
        real(path,content,mode)
        if path==brand.dir_secretos/'youtube.json' and not interrupted[0]:
            interrupted[0]=True
            raise KeyboardInterrupt()
    monkeypatch.setattr(cli_module,'atomic_bytes',write_then_interrupt)
    result=runner.invoke(app,['connect','youtube','--brand','Example','--root',str(tmp_path),'--service','https://social.example'],input='y\n')
    assert result.exit_code==1
    assert brand.leer_secreto(Platform.YOUTUBE)=={'access_token':'existing-token'}
    assert (brand.raiz/'accounts.yml').read_bytes()==before
    assert not ring.values


@pytest.mark.parametrize('platform',['instagram','facebook'])
def test_linked_meta_account_binding_cannot_break_an_existing_connection(tmp_path,platform):
    from socialctl.connections.cli import bind_account
    brand=crear_brand(tmp_path,'Example')
    data={'facebook':{'page_id':'123'},'instagram':{'ig_user_id':'456','media_url_base':''}}
    import yaml
    (brand.raiz/'accounts.yml').write_text(yaml.safe_dump(data))
    brand=cargar_brand(tmp_path,'Example')
    brand.guardar_secreto(Platform.FACEBOOK,{'auth_mode':'broker','account_id':'123'})
    brand.guardar_secreto(Platform.INSTAGRAM,{'auth_mode':'broker','account_id':'456'})
    before=(brand.raiz/'accounts.yml').read_bytes()
    with pytest.raises(ValueError,match='Facebook Page'):
        bind_account(brand,Platform(platform),{'id':'789' if platform=='facebook' else '999','page_id':'789','name':'Other'})
    assert (brand.raiz/'accounts.yml').read_bytes()==before


@pytest.mark.parametrize('command',['disconnect','auth'])
def test_all_credential_writers_share_the_brand_lock(tmp_path,monkeypatch,command):
    import fcntl,os
    wire(tmp_path,monkeypatch)
    brand=crear_brand(tmp_path,'Example')
    brand.guardar_secreto(Platform.YOUTUBE,{'auth_mode':'broker','version':1,'service_url':'https://social.example','connection_id':'one','account_id':'UC-one'})
    fd=os.open(brand.dir_secretos/'connection.lock',os.O_RDWR|os.O_CREAT,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        result=runner.invoke(app,[command,'youtube','--brand','Example','--root',str(tmp_path)],input='y\n')
        assert result.exit_code==1
        assert 'in progress' in result.output
        assert brand.leer_secreto(Platform.YOUTUBE)['connection_id']=='one'
    finally:
        os.close(fd)

@pytest.mark.parametrize('platform', ['facebook', 'instagram'])
def test_meta_analytics_flag_reaches_authorization_service(tmp_path, monkeypatch, platform):
    from socialctl.connections import cli as cli_module, keychain
    crear_brand(tmp_path, 'Example')
    ring = MemoryKeyring()
    monkeypatch.setattr(keychain, '_backend', lambda: ring)
    requests = []
    def transport(request):
        requests.append(json.loads(request.content))
        return httpx.Response(503, json={'detail':'unavailable'})
    monkeypatch.setattr(cli_module, 'http_client', lambda: httpx.Client(transport=httpx.MockTransport(transport)))
    result = runner.invoke(app, ['connect', platform, '--analytics', '--brand', 'Example', '--root', str(tmp_path), '--service', 'https://social.example', '--account-id', '123', '--yes'])
    assert len(requests) == 1, result.output
    assert requests[0]['platform'] == platform
    assert requests[0]['analytics'] is True
    assert requests[0]['management'] is False
    assert not ring.values


@pytest.mark.parametrize('platform', ['facebook', 'instagram'])
def test_meta_management_and_analytics_flags_reach_authorization_service(tmp_path, monkeypatch, platform):
    from socialctl.connections import cli as cli_module, keychain
    crear_brand(tmp_path, 'Example')
    ring = MemoryKeyring()
    monkeypatch.setattr(keychain, '_backend', lambda: ring)
    requests = []
    def transport(request):
        requests.append(json.loads(request.content))
        return httpx.Response(503, json={'detail':'unavailable'})
    monkeypatch.setattr(cli_module, 'http_client', lambda: httpx.Client(transport=httpx.MockTransport(transport)))
    result = runner.invoke(app, ['connect', platform, '--management', '--analytics', '--brand', 'Example', '--root', str(tmp_path), '--service', 'https://social.example', '--account-id', '123', '--yes'])
    assert len(requests) == 1, result.output
    assert requests[0]['management'] is True
    assert requests[0]['analytics'] is True
    assert not ring.values


def test_no_browser_hands_link_to_user_and_waits_for_authorization(tmp_path, monkeypatch):
    server, store, ring = wire(tmp_path, monkeypatch)
    from socialctl.connections import cli as cli_module
    crear_brand(tmp_path, 'Example')
    user_browser = cli_module.webbrowser.open
    def forbidden_browser(url):
        pytest.fail('Agent mode must never open the single-use link')
    monkeypatch.setattr(cli_module.webbrowser, 'open', forbidden_browser)
    handed_off = []
    def transport(request):
        if request.url.path.endswith('/poll') and not handed_off:
            auth_id = request.url.path.split('/')[-2]
            handed_off.append(auth_id)
            # The human opens the printed link while the agent's CLI is polling.
            user_browser('https://social.example/connect/' + auth_id)
        response = server.request(request.method, str(request.url), headers=dict(request.headers),
                                  content=request.content, follow_redirects=False)
        return httpx.Response(response.status_code, headers=dict(response.headers), content=response.content)
    monkeypatch.setattr(cli_module, 'http_client', lambda: httpx.Client(transport=httpx.MockTransport(transport)))
    result = runner.invoke(app, ['connect', 'youtube', '--brand', 'Example', '--root', str(tmp_path),
                                '--service', 'https://social.example', '--no-browser'], input='y\n')
    assert result.exit_code == 0, result.output
    assert 'https://social.example/connect/' + handed_off[0] in result.output
    assert 'single-use' in result.output
    assert 'Do not open, fetch or preview' in result.output
    assert 'Opening your browser' not in result.output
    assert cargar_brand(tmp_path, 'Example').cuentas['youtube']['channel_id'] == 'UC-one'
    assert len(ring.values) == 1


@pytest.mark.parametrize('flags', [[], ['--yes'], ['--account-id', 'UC-one']])
def test_noninteractive_requires_exact_account_before_pairing(tmp_path, monkeypatch, flags):
    from socialctl.connections import cli as cli_module
    crear_brand(tmp_path, 'Example')
    started = []
    monkeypatch.setattr(cli_module, 'http_client', lambda: started.append(True))
    result = runner.invoke(app, ['connect', 'youtube', '--brand', 'Example', '--root', str(tmp_path), *flags])
    assert result.exit_code != 0
    assert not started
    assert '--account-id' in result.output and '--yes' in result.output


def test_connect_yes_binds_only_requested_account(tmp_path, monkeypatch):
    server, store, ring = wire(tmp_path, monkeypatch)
    crear_brand(tmp_path, 'Example')
    result = runner.invoke(app, ['connect', 'youtube', '--brand', 'Example', '--root', str(tmp_path),
                                '--service', 'https://social.example', '--account-id', 'UC-one', '--yes'])
    assert result.exit_code == 0, result.output
    assert 'Connect Creator one' not in result.output
    assert cargar_brand(tmp_path, 'Example').cuentas['youtube']['channel_id'] == 'UC-one'
    assert len(ring.values) == 1


def test_connect_requested_account_mismatch_never_completes(tmp_path, monkeypatch):
    server, store, ring = wire(tmp_path, monkeypatch)
    crear_brand(tmp_path, 'Example')
    result = runner.invoke(app, ['connect', 'youtube', '--brand', 'Example', '--root', str(tmp_path),
                                '--service', 'https://social.example', '--account-id', 'UC-other', '--yes'])
    assert result.exit_code == 1
    assert 'requested account' in result.output
    assert not ring.values
    with store.transaction() as db:
        assert db.execute("SELECT count(*) FROM records WHERE kind='connection'").fetchone()[0] == 0


def test_noninteractive_replacement_requires_explicit_flag(tmp_path, monkeypatch):
    server, store, ring = wire(tmp_path, monkeypatch)
    brand = crear_brand(tmp_path, 'Example')
    brand.guardar_secreto(Platform.YOUTUBE, {'access_token': 'existing'})
    args = ['connect', 'youtube', '--brand', 'Example', '--root', str(tmp_path),
            '--service', 'https://social.example', '--account-id', 'UC-one', '--yes']
    result = runner.invoke(app, args)
    assert result.exit_code == 1 and '--replace-independent' in result.output
    assert brand.leer_secreto(Platform.YOUTUBE) == {'access_token': 'existing'}
    assert not ring.values
    result = runner.invoke(app, args + ['--replace-independent'])
    assert result.exit_code == 0, result.output


def test_account_id_selection_ignores_provider_order(tmp_path, monkeypatch):
    server, store, ring = wire(tmp_path, monkeypatch)
    from socialctl.connections import cli as cli_module
    crear_brand(tmp_path, 'Example')
    def transport(request):
        response = server.request(request.method, str(request.url), headers=dict(request.headers), content=request.content, follow_redirects=False)
        data = response.json()
        if request.url.path.endswith('/poll') and data.get('status') == 'ready':
            data['accounts'].insert(0, {'id': 'UC-other', 'name': 'Other creator'})
        return httpx.Response(response.status_code, headers={'content-type': 'application/json'}, json=data)
    monkeypatch.setattr(cli_module, 'http_client', lambda: httpx.Client(transport=httpx.MockTransport(transport)))
    result = runner.invoke(app, ['connect', 'youtube', '--brand', 'Example', '--root', str(tmp_path),
                                '--service', 'https://social.example', '--account-id', 'UC-one', '--yes'])
    assert result.exit_code == 0, result.output
    assert cargar_brand(tmp_path, 'Example').cuentas['youtube']['channel_id'] == 'UC-one'


def test_exact_binding_does_not_probe_closed_stdin(tmp_path, monkeypatch):
    server, store, ring = wire(tmp_path, monkeypatch)
    from socialctl.connections import cli as cli_module
    crear_brand(tmp_path, 'Example')
    def closed_stdin():
        raise ValueError('I/O operation on closed file')
    monkeypatch.setattr(cli_module, 'interactive_terminal', closed_stdin)
    result = runner.invoke(app, ['connect', 'youtube', '--brand', 'Example', '--root', str(tmp_path),
                                '--service', 'https://social.example', '--account-id', 'UC-one', '--yes'])
    assert result.exit_code == 0, result.output
    assert len(ring.values) == 1
