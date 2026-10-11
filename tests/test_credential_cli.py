import json
import pytest
from typer.testing import CliRunner
from tests.test_credential_store import setup_store
from socialctl.cli import app
from socialctl.models import Platform

runner=CliRunner()


def configure_args(brand,store):
    return ['credentials','configure','--backend','encrypted-file','--store-file',str(store.path),'--key-file',str(store.key_path),'--brand',brand.nombre,'--root',str(brand.raiz.parent),'--yes']


def test_configure_status_and_selected_backend(tmp_path):
    from socialctl.connections import keychain
    brand,store,meta=setup_store(tmp_path)
    result=runner.invoke(app,configure_args(brand,store))
    assert result.exit_code==0,result.output
    keychain.save(brand,Platform.YOUTUBE,meta,'fictional-capability')
    assert keychain.get(brand,Platform.YOUTUBE,meta)=='fictional-capability'
    result=runner.invoke(app,['credentials','status','--brand',brand.nombre,'--root',str(brand.raiz.parent)])
    assert json.loads(result.stdout)['backend']=='encrypted-file'
    assert 'fictional-capability' not in result.stdout
    keychain.delete(brand,Platform.YOUTUBE,meta)
    assert keychain.get(brand,Platform.YOUTUBE,meta) is None


def test_rotation_all_records_and_failed_configuration_preserves_old(tmp_path,monkeypatch):
    from cryptography.fernet import Fernet
    from socialctl.connections import keychain
    brand,store,meta=setup_store(tmp_path)
    assert runner.invoke(app,configure_args(brand,store)).exit_code==0
    store.save(brand,Platform.YOUTUBE,meta,'fictional-capability')
    new=store.key_path.with_name('new-key');new.write_bytes(Fernet.generate_key());new.chmod(0o600)
    args=['credentials','rotate','--new-key-file',str(new),'--brand',brand.nombre,'--root',str(brand.raiz.parent),'--yes']
    import socialctl.connections.credential_cli as cli
    original=cli.write_configuration
    monkeypatch.setattr(cli,'write_configuration',lambda *a:(_ for _ in ()).throw(OSError('disk')))
    failed=runner.invoke(app,args)
    assert failed.exit_code!=0 and store.get(brand,Platform.YOUTUBE,meta)=='fictional-capability'
    monkeypatch.setattr(cli,'write_configuration',original)
    result=runner.invoke(app,args)
    assert result.exit_code==0,result.output
    assert keychain.get(brand,Platform.YOUTUBE,meta)=='fictional-capability'
    with pytest.raises(ValueError): store.get(brand,Platform.YOUTUBE,meta)
    assert store.path.with_name(store.path.name+'.before-rotation').exists()


def test_migration_verifies_destination_before_source_removal(tmp_path,monkeypatch):
    from socialctl.connections import keychain
    from socialctl.connections.file_store import EncryptedFileStore
    brand,store,meta=setup_store(tmp_path)
    meta={**meta,'auth_mode':'broker'}
    brand.guardar_secreto(Platform.YOUTUBE,meta)
    class Backend:
        value='fictional-capability'
        def get_password(self,*args): return self.value
        def set_password(self,*args): self.value=args[-1]
        def delete_password(self,*args): self.value=None
    backend=Backend()
    monkeypatch.setattr(keychain,'_backend',lambda:backend)
    args=['credentials','migrate','--to','encrypted-file','--store-file',str(store.path),'--key-file',str(store.key_path),'--brand',brand.nombre,'--root',str(brand.raiz.parent),'--yes']
    original=EncryptedFileStore.get
    monkeypatch.setattr(EncryptedFileStore,'get',lambda *a:None)
    failed=runner.invoke(app,args)
    assert failed.exit_code==1 and backend.value=='fictional-capability'
    assert not (brand.dir_secretos/'credential-store.json').exists()
    monkeypatch.setattr(EncryptedFileStore,'get',original)
    result=runner.invoke(app,args)
    assert result.exit_code==0,result.output
    assert backend.value is None
    assert keychain.get(brand,Platform.YOUTUBE,meta)=='fictional-capability'
    assert 'fictional-capability' not in result.output


def test_rotation_failure_after_config_replace_restores_config_and_store(tmp_path,monkeypatch):
    from cryptography.fernet import Fernet
    from socialctl.connections import keychain
    import socialctl.connections.credential_cli as cli
    brand,store,meta=setup_store(tmp_path)
    assert runner.invoke(app,configure_args(brand,store)).exit_code==0
    keychain.save(brand,Platform.YOUTUBE,meta,'fictional-capability')
    new=store.key_path.with_name('next');new.write_bytes(Fernet.generate_key());new.chmod(0o600)
    original=cli.write_configuration
    def post_replace(*args):
        original(*args)
        raise OSError('synthetic directory fsync failure')
    monkeypatch.setattr(cli,'write_configuration',post_replace)
    result=runner.invoke(app,['credentials','rotate','--new-key-file',str(new),'--brand',brand.nombre,'--root',str(brand.raiz.parent),'--yes'])
    assert result.exit_code==1
    assert keychain.get(brand,Platform.YOUTUBE,meta)=='fictional-capability'


def test_one_encrypted_store_cannot_be_shared_by_different_brands(tmp_path):
    from socialctl.brands import crear_brand
    brand,store,meta=setup_store(tmp_path)
    assert runner.invoke(app,configure_args(brand,store)).exit_code==0
    other=crear_brand(brand.raiz.parent,'Other')
    result=runner.invoke(app,configure_args(other,store))
    assert result.exit_code!=0
    assert not (other.dir_secretos/'credential-store.json').exists()


def test_store_configuration_and_metadata_paths_are_rejected(tmp_path):
    brand,store,meta=setup_store(tmp_path)
    for name in ('credential-store.json','youtube.json','connection.lock'):
        bad=brand.dir_secretos/name
        args=configure_args(brand,store)
        args[args.index('--store-file')+1]=str(bad)
        result=runner.invoke(app,args)
        assert result.exit_code!=0
        assert not (brand.dir_secretos/'credential-store.json').exists()


def test_key_cannot_alias_store_lock_or_recovery_path(tmp_path):
    from socialctl.connections.credential_cli import external_paths
    brand,store,meta=setup_store(tmp_path)
    for suffix in ('.lock','.before-rotation'):
        with pytest.raises(ValueError): external_paths(store.path,store.path.with_name(store.path.name+suffix))
