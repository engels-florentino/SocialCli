"""Credential persistence must resist plaintext leaks and row substitution."""
import hashlib
import importlib.util
import os

import pytest
from cryptography.fernet import Fernet


def store_type():
    assert importlib.util.find_spec('socialctl.connection_service.store') is not None, 'encrypted connection store is missing'
    from socialctl.connection_service.store import Store
    return Store


def test_vault_encrypts_credentials_and_uses_private_permissions(tmp_path):
    Store = store_type()
    path = tmp_path / 'private' / 'connections.sqlite3'
    store = Store(path, Fernet.generate_key())
    with store.transaction() as db:
        store.put(db, 'connection', 'one', {'refresh_token': 'fictional-refresh-secret'}, expires=1000, credential_hash='hash-one')
    assert b'fictional-refresh-secret' not in path.read_bytes()
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert os.stat(path.parent).st_mode & 0o777 == 0o700
    with store.transaction() as db:
        record = store.get(db, 'connection', 'one')
    assert record['payload']['refresh_token'] == 'fictional-refresh-secret'
    assert record['credential_hash'] == 'hash-one'


def test_vault_rejects_encrypted_record_swaps(tmp_path):
    Store = store_type()
    store = Store(tmp_path / 'vault.sqlite3', Fernet.generate_key())
    with store.transaction() as db:
        store.put(db, 'connection', 'one', {'access_token': 'a'}, expires=1000)
        store.put(db, 'connection', 'two', {'access_token': 'b'}, expires=1000)
        db.execute("UPDATE records SET payload=(SELECT payload FROM records WHERE id='one') WHERE id='two'")
    with pytest.raises(ValueError, match='credential storage is invalid'):
        with store.transaction() as db:
            store.get(db, 'connection', 'two')


def test_transactions_rollback_and_expired_records_are_purged(tmp_path):
    Store = store_type()
    store = Store(tmp_path / 'vault.sqlite3', Fernet.generate_key())
    with pytest.raises(RuntimeError):
        with store.transaction() as db:
            store.put(db, 'authorization', 'failed', {}, expires=10)
            raise RuntimeError('rollback')
    with store.transaction() as db:
        assert store.get(db, 'authorization', 'failed') is None
        store.put(db, 'authorization', 'expired', {}, expires=10)
        store.put(db, 'connection', 'alive', {}, expires=100)
        store.purge(db, now=20)
        assert store.get(db, 'authorization', 'expired') is None
        assert store.get(db, 'connection', 'alive') is not None


def test_rate_limit_is_bounded_and_persistent(tmp_path):
    Store = store_type()
    store = Store(tmp_path / 'vault.sqlite3', Fernet.generate_key())
    with store.transaction() as db:
        assert all(store.admit(db, 'address', now=60, limit=5) for _ in range(5))
        assert not store.admit(db, 'address', now=60, limit=5)
        assert store.admit(db, 'other', now=60, limit=5)
        assert store.admit(db, 'address', now=120, limit=5)
    assert b'address' not in store.path.read_bytes()


@pytest.mark.parametrize('column,value',[('credential_hash','attacker-hash'),('expires',9999999999)])
def test_vault_rejects_capability_hash_and_expiry_tampering(tmp_path,column,value):
    Store=store_type()
    store=Store(tmp_path/'vault.sqlite3',Fernet.generate_key())
    with store.transaction() as db:
        store.put(db,'connection','victim',{'access_token':'victim-token'},expires=1000,credential_hash='owner-hash')
        db.execute(f'UPDATE records SET {column}=? WHERE id=?',(value,'victim'))
    with pytest.raises(ValueError,match='credential storage is invalid'):
        with store.transaction() as db:
            store.get(db,'connection','victim')
