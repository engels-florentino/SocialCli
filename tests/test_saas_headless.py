"""Actual encrypted backend for agent installation, pairing and rotation secrets."""
import json
import pytest
from cryptography.fernet import Fernet
from typer.testing import CliRunner
from socialctl.cli import app
from socialctl.brands import crear_brand,cargar_brand
from socialctl.connections import keychain,account_cli
from tests.test_saas_cli import wire

runner=CliRunner()


def setup(tmp_path,monkeypatch):
    workspace=tmp_path/'workspace';server,now,ring=wire(workspace,monkeypatch)
    brand=crear_brand(workspace,'MyBrand')
    private=tmp_path/'private';private.mkdir(mode=0o700)
    key=private/'key';key.write_bytes(Fernet.generate_key());key.chmod(0o600)
    common=['--brand','MyBrand','--root',str(workspace)]
    storage=['--store-file',str(private/'capabilities'),'--key-file',str(key)]
    return brand,common,storage,ring


def login(common):
    result=runner.invoke(app,['account','login',*common,'--service','https://social.example','--no-browser'])
    assert result.exit_code==0,result.output


def test_headless_login_connect_rotate_and_backend_migration(tmp_path,monkeypatch):
    brand,common,storage,ring=setup(tmp_path,monkeypatch)
    assert runner.invoke(app,['credentials','configure','--backend','encrypted-file',*storage,*common,'--yes']).exit_code==0
    login(common)
    for args in [['connect','youtube',*common,'--no-browser'],['account','rotate',*common]]:
        result=runner.invoke(app,args);assert result.exit_code==0,result.output
    assert ring.values=={}
    saved=account_cli.metadata(brand);secret=keychain.get(brand,account_cli.Slot.ACCOUNT,saved)
    assert secret and 'account_id' not in saved
    result=runner.invoke(app,['credentials','migrate','--to','keyring',*common,'--yes'])
    assert result.exit_code==0,result.output
    assert keychain.get(brand,account_cli.Slot.ACCOUNT,saved)==secret
    assert runner.invoke(app,['account','status',*common]).exit_code==0
    result=runner.invoke(app,['credentials','migrate','--to','encrypted-file',*storage,*common,'--yes'])
    assert result.exit_code==0,result.output
    assert keychain.get(brand,account_cli.Slot.ACCOUNT,saved)==secret
    assert runner.invoke(app,['account','status',*common]).exit_code==0


def test_configure_cannot_abandon_existing_installation_without_social_connections(tmp_path,monkeypatch):
    brand,common,storage,ring=setup(tmp_path,monkeypatch);login(common)
    result=runner.invoke(app,['credentials','configure','--backend','encrypted-file',*storage,*common,'--yes'])
    assert result.exit_code==1
    assert not (brand.dir_secretos/'credential-store.json').exists()
    assert runner.invoke(app,['account','status',*common]).exit_code==0


def test_migration_preserves_installation_before_first_social_connection(tmp_path,monkeypatch):
    brand,common,storage,ring=setup(tmp_path,monkeypatch);login(common)
    saved=account_cli.metadata(brand);secret=keychain.get(brand,account_cli.Slot.ACCOUNT,saved)
    result=runner.invoke(app,['credentials','migrate','--to','encrypted-file',*storage,*common,'--yes'])
    assert result.exit_code==0,result.output
    assert keychain.get(brand,account_cli.Slot.ACCOUNT,saved)==secret
    assert ring.values=={}


def test_backend_change_waits_for_pending_pairing_or_recovery(tmp_path,monkeypatch):
    brand,common,storage,ring=setup(tmp_path,monkeypatch)
    for name in ['socialcli-pairing.json','socialcli-recovery-youtube.json']:
        path=brand.dir_secretos/name;path.write_text('{}')
        result=runner.invoke(app,['credentials','migrate','--to','encrypted-file',*storage,*common,'--yes'])
        assert result.exit_code==1
        assert not (brand.dir_secretos/'credential-store.json').exists()
        path.unlink()


def test_lost_pair_ack_resumes_then_allows_backend_migration(tmp_path,monkeypatch):
    import httpx
    brand,common,storage,ring=setup(tmp_path,monkeypatch)
    original=account_cli.http_client;drop=[True]
    class LosingAck:
        def __enter__(self):self.inner=original();return self
        def __exit__(self,*args):self.inner.close()
        def stream(self,method,url,**kwargs):
            if url.endswith('/ack') and drop[0]:
                drop[0]=False
                with self.inner.stream(method,url,**kwargs) as reply:reply.read()
                raise httpx.ReadTimeout('fictional lost acknowledgement')
            return self.inner.stream(method,url,**kwargs)
    monkeypatch.setattr(account_cli,'http_client',LosingAck)
    first=runner.invoke(app,['account','login',*common,'--service','https://social.example','--no-browser'])
    assert first.exit_code==1
    pointer=brand.dir_secretos/'socialcli-pairing.json';assert pointer.exists()
    login(common)
    assert not pointer.exists()
    result=runner.invoke(app,['credentials','migrate','--to','encrypted-file',*storage,*common,'--yes'])
    assert result.exit_code==0,result.output
    assert runner.invoke(app,['account','status',*common]).exit_code==0


@pytest.mark.parametrize('encrypted',[False,True])
def test_logout_login_reattaches_existing_grant_without_oauth(tmp_path,monkeypatch,encrypted):
    from socialctl.models import Platform
    brand,common,storage,ring=setup(tmp_path,monkeypatch)
    if encrypted:
        assert runner.invoke(app,['credentials','configure','--backend','encrypted-file',*storage,*common,'--yes']).exit_code==0
    login(common)
    assert runner.invoke(app,['connect','youtube',*common,'--no-browser']).exit_code==0
    old=brand.leer_secreto(Platform.YOUTUBE)
    assert runner.invoke(app,['account','logout',*common]).exit_code==0
    login(common)
    result=runner.invoke(app,['account','attach','youtube',*common])
    assert result.exit_code==0,result.output
    current=brand.leer_secreto(Platform.YOUTUBE)
    assert current['account_id']==old['account_id']
    assert current['connection_id']!=old['connection_id']
    assert keychain.get(brand,Platform.YOUTUBE,old) is None
    assert runner.invoke(app,['connections',*common,'--json']).exit_code==0


def test_attach_preserves_active_local_connection(tmp_path,monkeypatch):
    from socialctl.models import Platform
    brand,common,_,_=setup(tmp_path,monkeypatch);login(common)
    assert runner.invoke(app,['connect','youtube',*common,'--no-browser']).exit_code==0
    old=brand.leer_secreto(Platform.YOUTUBE);secret=keychain.get(brand,Platform.YOUTUBE,old)
    result=runner.invoke(app,['account','attach','youtube',*common])
    assert result.exit_code==1
    assert brand.leer_secreto(Platform.YOUTUBE)==old
    assert keychain.get(brand,Platform.YOUTUBE,old)==secret


@pytest.mark.parametrize('failure',['timeout','unrecognized_401'])
def test_attach_preserves_connection_when_retirement_is_not_proven(tmp_path,monkeypatch,failure):
    from contextlib import contextmanager
    import httpx
    from socialctl.models import Platform
    brand,common,_,_=setup(tmp_path,monkeypatch);login(common)
    assert runner.invoke(app,['connect','youtube',*common,'--no-browser']).exit_code==0
    old=brand.leer_secreto(Platform.YOUTUBE);secret=keychain.get(brand,Platform.YOUTUBE,old)
    factory=account_cli.http_client
    class UncertainStatus:
        def __enter__(self):self.client=factory();return self
        def __exit__(self,*args):self.client.close()
        @contextmanager
        def stream(self,method,url,**kwargs):
            if method=='GET' and '/connections/' in url:
                if failure=='timeout':raise httpx.ReadTimeout('fictional connection uncertainty')
                yield httpx.Response(401,json={'detail':'unknown_identity_failure'})
            else:
                with self.client.stream(method,url,**kwargs) as response:yield response
    monkeypatch.setattr(account_cli,'http_client',UncertainStatus)
    assert runner.invoke(app,['account','attach','youtube',*common]).exit_code==1
    assert brand.leer_secreto(Platform.YOUTUBE)==old
    assert keychain.get(brand,Platform.YOUTUBE,old)==secret
    assert not account_cli.recovery_path(brand,Platform.YOUTUBE).exists()
