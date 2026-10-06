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
    from tests.test_connection_service import make_service
    server,store=make_service(tmp_path/'server')
    ring=MemoryKeyring()
    monkeypatch.setattr(keychain,'_backend',lambda:ring)
    def transport(r):
        response=server.request(r.method,str(r.url),headers=dict(r.headers),content=r.content,follow_redirects=False)
        return httpx.Response(response.status_code,headers=dict(response.headers),content=response.content)
    monkeypatch.setattr(cli_module,'http_client',lambda:httpx.Client(transport=httpx.MockTransport(transport)))
    def browser(url):
        response=server.get(url,follow_redirects=False)
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
