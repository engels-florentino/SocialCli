"""Headless capabilities use fictional secrets and isolated protected directories."""
import json
import os
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from socialctl.brands import crear_brand
from socialctl.models import Platform


def setup_store(tmp_path):
    from socialctl.connections.file_store import EncryptedFileStore
    brand = crear_brand(tmp_path/'workspace', 'Example')
    folder = tmp_path/'private'
    folder.mkdir(mode=0o700)
    key = folder/'key'
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    store = EncryptedFileStore(folder/'capabilities', key)
    metadata = {'service_url':'https://social.example','connection_id':'connection-a','account_id':'account-a'}
    return brand, store, metadata


def test_encrypted_roundtrip_and_account_binding(tmp_path):
    brand, store, meta = setup_store(tmp_path)
    store.ensure_available()
    store.save(brand, Platform.YOUTUBE, meta, 'fictional-capability')
    assert store.get(brand, Platform.YOUTUBE, meta) == 'fictional-capability'
    assert b'fictional-capability' not in store.path.read_bytes()
    assert store.get(brand, Platform.YOUTUBE, {**meta,'account_id':'another'}) is None
    store.delete(brand, Platform.YOUTUBE, meta)
    assert store.get(brand, Platform.YOUTUBE, meta) is None


@pytest.mark.parametrize('damage', ['wrong-key','corrupt','version','key-mode','store-mode','symlink','parent-symlink','missing-key'])
def test_encrypted_store_fails_closed(tmp_path, damage):
    brand, store, meta = setup_store(tmp_path)
    store.save(brand, Platform.YOUTUBE, meta, 'fictional-capability')
    if damage == 'wrong-key': store.key_path.write_bytes(Fernet.generate_key())
    if damage == 'corrupt': store.path.write_bytes(b'broken')
    if damage == 'version': store.path.write_text(json.dumps({'version':99,'ciphertext':'invalid'}))
    if damage == 'key-mode': store.key_path.chmod(0o644)
    if damage == 'store-mode': store.path.chmod(0o644)
    if damage == 'missing-key': store.key_path.unlink()
    if damage == 'symlink':
        copy = store.path.with_name('other'); store.path.rename(copy); store.path.symlink_to(copy)
    if damage == 'parent-symlink':
        parent = store.path.parent; moved=parent.with_name('moved'); parent.rename(moved); parent.symlink_to(moved)
    with pytest.raises(ValueError):
        store.get(brand, Platform.YOUTUBE, meta)


def test_atomic_failure_and_multiple_records_preserve_existing(tmp_path, monkeypatch):
    brand, store, meta = setup_store(tmp_path)
    store.save(brand, Platform.YOUTUBE, meta, 'first')
    store.save(brand, Platform.FACEBOOK, {**meta,'connection_id':'connection-b'}, 'second')
    original = store.path.read_bytes()
    def fail(*args, **kwargs): raise OSError('synthetic interrupted replacement')
    monkeypatch.setattr(os, 'replace', fail)
    with pytest.raises(ValueError): store.save(brand, Platform.YOUTUBE, meta, 'replacement')
    assert store.path.read_bytes() == original
    assert store.get(brand, Platform.YOUTUBE, meta) == 'first'
    assert store.get(brand, Platform.FACEBOOK, {**meta,'connection_id':'connection-b'}) == 'second'


def test_default_backend_never_falls_back(tmp_path, monkeypatch):
    from socialctl.connections import keychain
    from socialctl.connections.credential_store import store_for
    brand = crear_brand(tmp_path,'Example')
    monkeypatch.setattr(keychain, '_backend', lambda: (_ for _ in ()).throw(ValueError('locked')))
    with pytest.raises(ValueError): store_for(brand).ensure_available()
    assert not (brand.dir_secretos/'credential-store.json').exists()


def test_authenticated_record_binding_cannot_be_substituted(tmp_path):
    brand,store,meta=setup_store(tmp_path)
    store.save(brand,Platform.YOUTUBE,meta,'fictional-capability')
    cipher=store.cipher()
    envelope=json.loads(store.path.read_bytes())
    raw=json.loads(cipher.decrypt(envelope['ciphertext'].encode()))
    next(iter(raw['records'].values()))['binding']['account_id']='wrong'
    envelope['ciphertext']=cipher.encrypt(json.dumps(raw).encode()).decode()
    store.path.write_text(json.dumps(envelope))
    with pytest.raises(ValueError):store.get(brand,Platform.YOUTUBE,meta)


def _write_capability_process(brand_root, store_path, key_path, platform, metadata):
    from socialctl.brands import cargar_brand
    from socialctl.connections.file_store import EncryptedFileStore
    brand=cargar_brand(Path(brand_root).parent,Path(brand_root).name)
    EncryptedFileStore(store_path,key_path).save(brand,Platform(platform),metadata,platform)


def test_two_process_writers_preserve_records(tmp_path):
    import multiprocessing
    brand,store,meta=setup_store(tmp_path)
    ctx=multiprocessing.get_context('spawn')
    processes=[ctx.Process(target=_write_capability_process,args=(str(brand.raiz),str(store.path),str(store.key_path),p.value,{**meta,'connection_id':p.value})) for p in (Platform.YOUTUBE,Platform.FACEBOOK)]
    for process in processes:process.start()
    for process in processes:
        process.join(5)
    assert [process.exitcode for process in processes] == [0, 0]
    for p in (Platform.YOUTUBE,Platform.FACEBOOK):
        assert store.get(brand,p,{**meta,'connection_id':p.value})==p.value
