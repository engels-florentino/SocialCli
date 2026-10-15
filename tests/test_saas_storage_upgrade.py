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
        assert db.execute('SELECT max(version) FROM saas_schema').fetchone()[0] == 10
        # A corrupt unrelated ciphertext must never break this user's panel.
        db.execute("UPDATE records SET payload='invalid' WHERE id='other'")
        found = AccountLifecycle(metadata, lambda: 1).owned_records(db, 'mine')
        assert [r[1] for r in found] == ['old']
        vault.put(db, 'saas-session', 'mine', {'user_id': 'mine'}, expires=3)
        assert len(AccountLifecycle(metadata, lambda: 1).owned_records(db, 'mine')) == 2
        vault.delete(db, 'authorization', 'old')
        vault.purge(db, now=4)
        assert db.execute("SELECT count(*) FROM saas_record_owners WHERE user_id='mine'").fetchone()[0] == 0


def test_schema7_webhook_migration_preserves_audit_and_bounds_every_state(tmp_path):
    from socialctl.saas.webhooks import semantic_hash
    vault = Store(tmp_path / 'vault.db', Fernet.generate_key())
    SaaSStore(vault, [b'k' * 32])
    received = 1000
    states = ('done', 'ignored', 'pending', 'review')
    with vault.transaction() as db:
        # Recreate the prior schema's event/rate columns and long-lived blobs.
        db.execute('DELETE FROM saas_schema WHERE version>=8')
        db.execute('ALTER TABLE saas_billing_events DROP COLUMN semantic_hash')
        db.execute('ALTER TABLE saas_rates DROP COLUMN expires')
        for state in states:
            event_id = 'evt_' + state
            payload = {'id': event_id, 'type': 'customer.subscription.updated'}
            db.execute('INSERT INTO saas_billing_events '
                       '(account_id,mode,event_id,body_hash,type,state,received) '
                       'VALUES(?,?,?,?,?,?,?)',
                       ('acct_fixture', 'test', event_id, 'original-' + state,
                        payload['type'], state, received))
            vault.put(db, 'saas-stripe-event', 'acct_fixture:test:' + event_id,
                      payload, expires=received + 365 * 86400)
    for _ in range(2):  # A later startup must not extend retention or lose hashes.
        SaaSStore(vault, [b'k' * 32])
        with vault.read_transaction() as db:
            assert db.execute('SELECT max(version) FROM saas_schema').fetchone()[0] == 10
            for state in states:
                event_id = 'evt_' + state
                row = db.execute('SELECT * FROM saas_billing_events WHERE event_id=?',
                                 (event_id,)).fetchone()
                assert row['state'] == state
                assert row['body_hash'] == 'original-' + state
                assert row['semantic_hash'] == semantic_hash(
                    {'id': event_id, 'type': 'customer.subscription.updated'})
                record = vault.get(db, 'saas-stripe-event', 'acct_fixture:test:' + event_id)
                if state in {'done', 'ignored'}:
                    assert record is None
                else:
                    assert record['expires'] == received + 7 * 86400


def test_schema8_backfills_private_email_and_legacy_pair_links_for_exact_deletion(tmp_path):
    from socialctl.saas.models import AuthenticatedUser
    from socialctl.saas.auth import digest
    key=Fernet.generate_key();vault=Store(tmp_path/'vault.db',key)
    metadata=SaaSStore(vault,[key]);user=AuthenticatedUser('fixture-owner','owner@example.test')
    with vault.transaction() as db:
        metadata.ensure_user(db,user,100)
        vault.put(db,'saas-installation','mine',{'user_id':user.id},expires=1000)
        vault.put(db,'saas-pair','legacy',{'installation_id':'mine','installation_secret':'fixture-secret'},expires=1000)
        vault.put(db,'saas-pair','pending',{'status':'pending'},expires=1000)
        vault.put(db,'saas-login','mine-login',{'email':user.email},expires=1000)
        vault.put(db,'saas-login','other-login',{'email':'other@example.test'},expires=1000)
        db.execute('DELETE FROM saas_record_links')
        db.execute('DELETE FROM saas_record_emails')
        db.execute('DELETE FROM saas_schema WHERE version>=9')
    # Upgrade while rotating keys: old and new keys must both match old attempts.
    vault=Store(vault.path,[Fernet.generate_key(),key]);metadata=SaaSStore(vault,[key])
    with vault.read_transaction() as db:
        markers=[r[0] for r in db.execute('SELECT email_hash FROM saas_record_emails')]
        assert user.email not in markers and digest(user.email) not in markers
        assert len(markers)==6
    AccountLifecycle(metadata,lambda:100).request_delete(user.id)
    with vault.read_transaction() as db:
        assert vault.get(db,'saas-pair','legacy') is None
        assert vault.get(db,'saas-login','mine-login') is None
        assert vault.get(db,'saas-pair','pending') is not None
        assert vault.get(db,'saas-login','other-login') is not None
        assert db.execute("SELECT count(*) FROM saas_record_links WHERE id='legacy'").fetchone()[0]==0
        assert db.execute("SELECT count(*) FROM saas_record_emails WHERE id IN ('mine-login','fixture-owner')").fetchone()[0]==0


def test_vault_string_key_supports_private_email_index(tmp_path):
    vault=Store(tmp_path/'vault.db',Fernet.generate_key().decode())
    with vault.transaction() as db:
        vault.put(db,'saas-user','fictional',{'email':'fictional@example.test'},expires=1000)
        assert vault.get(db,'saas-user','fictional')['payload']['email']=='fictional@example.test'
        assert db.execute('SELECT count(*) FROM saas_record_emails').fetchone()[0]==1


def test_reindex_during_key_rotation_preserves_login_deletion_after_old_key_retirement(tmp_path):
    from socialctl.saas.models import AuthenticatedUser
    old=Fernet.generate_key();new=Fernet.generate_key()
    vault=Store(tmp_path/'vault.db',old);metadata=SaaSStore(vault,[old])
    user=AuthenticatedUser('fictional-owner','owner@example.test')
    with vault.transaction() as db:
        metadata.ensure_user(db,user,100)
        vault.put(db,'saas-login','pending',{'email':user.email},expires=1000)
    vault=Store(vault.path,[new,old])
    with vault.transaction() as db:
        for row in db.execute('SELECT kind,id FROM records').fetchall():
            record=vault.get(db,row['kind'],row['id'])
            vault.put(db,row['kind'],row['id'],record['payload'],expires=record['expires'],credential_hash=record['credential_hash'])
    vault=Store(vault.path,new);metadata=SaaSStore(vault,[new])
    with vault.transaction() as db:metadata.ensure_user(db,user,100)
    AccountLifecycle(metadata,lambda:100).request_delete(user.id)
    with vault.read_transaction() as db:
        assert vault.get(db,'saas-login','pending') is None
