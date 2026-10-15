"""Owner-isolated reads and durable metadata migration."""
import sqlite3
from cryptography.fernet import Fernet
from socialctl.connection_service.store import Store
from socialctl.saas.store import SaaSStore
from socialctl.saas.lifecycle import AccountLifecycle


def test_wal_read_snapshot_does_not_block_writer(tmp_path):
    vault = Store(tmp_path / 'vault.db', Fernet.generate_key())
    with sqlite3.connect(vault.path) as db:
        assert db.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    with vault.read_transaction() as db:
        assert db.execute('SELECT count(*) FROM records').fetchone()[0] == 0
        with vault.transaction() as writer:
            vault.put(writer, 'sample', 'one', {}, expires=100)
        assert db.execute('SELECT count(*) FROM records').fetchone()[0] == 0


def test_ownership_index_backfills_and_never_decrypts_other_users(tmp_path):
    vault = Store(tmp_path / 'vault.db', Fernet.generate_key())
    with vault.transaction() as db:
        vault.put(db, 'authorization', 'old', {'saas': {'user_id': 'mine'}}, expires=100)
        vault.put(db, 'saas-session', 'other', {'user_id': 'other'}, expires=100)
    metadata = SaaSStore(vault, [b'k' * 32])
    with vault.transaction() as db:
        assert db.execute('SELECT max(version) FROM saas_schema').fetchone()[0] == 8
        # A corrupt unrelated ciphertext must never break this user's panel.
        db.execute("UPDATE records SET payload='invalid' WHERE id='other'")
        found = AccountLifecycle(metadata, lambda: 1).owned_records(db, 'mine')
        assert [r[1] for r in found] == ['old']
        vault.put(db, 'saas-session', 'mine', {'user_id': 'mine'}, expires=3)
        assert len(AccountLifecycle(metadata, lambda: 1).owned_records(db, 'mine')) == 2
        vault.delete(db, 'authorization', 'old')
        vault.purge(db, now=4)
        assert db.execute("SELECT count(*) FROM saas_record_owners WHERE user_id='mine'").fetchone()[0] == 0
