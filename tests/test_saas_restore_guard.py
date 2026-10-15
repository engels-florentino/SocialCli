"""Restored copies fail closed until current deletion authority and Auth cleanup."""
import sqlite3
import pytest
from tests.test_saas_lifecycle import connected
from tests.test_saas_auth import USER
from socialctl.saas.revocation import export_current_deletion_manifest
from socialctl.saas.store import SaaSStore
from socialctl.connection_service.store import Store


def fixture(tmp_path):
    client,now,_,_=connected(tmp_path/'authority')
    metadata=client.app.state.saas
    from socialctl.saas.backup import snapshot
    source=tmp_path/'saved'/'snapshot.sqlite3'
    snapshot(metadata.vault.path,source,metadata.eligibility_keys)
    with metadata.vault.transaction() as db:client.app.state.saas_lifecycle.disconnect(db,USER,'youtube')
    from socialctl.saas.revocation import run_revocations
    run_revocations(metadata,client.app.state.saas_connections.providers,lambda:now[0])
    manifest=export_current_deletion_manifest(metadata,lambda:now[0])
    return client,now,source,manifest


def test_restore_applies_tombstones_wipes_sessions_and_blocks_service_until_auth_cleanup(tmp_path):
    from socialctl.saas.restore_guard import prepare_restore,release_restore
    client,now,source,manifest=fixture(tmp_path)
    destination=tmp_path/'restore'/'state.sqlite3';keys=client.app.state.saas.eligibility_keys
    report=prepare_restore(source,destination,keys,manifest,now=now[0],snapshot_created=now[0]-100)
    assert report['status']=='identity_cleanup_required'
    vault=Store(destination,keys)
    with vault.read_transaction() as db:
        assert db.execute("SELECT count(*) FROM records WHERE kind IN ('saas-grant','saas-session','saas-installation','saas-capability')").fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM saas_slots').fetchone()[0]==0
    with pytest.raises(ValueError,match='restored database'):
        SaaSStore(vault,keys)
    assert release_restore(destination,keys,manifest,now=now[0],identity_cleanup_confirmed=True)['status']=='ready'
    SaaSStore(vault,keys)
    with sqlite3.connect(source) as db:
        assert db.execute("SELECT count(*) FROM records WHERE kind='saas-grant'").fetchone()[0]==1


def test_stale_backup_source_or_expired_snapshot_manifest_cannot_release(tmp_path):
    from socialctl.saas.restore_guard import prepare_restore,RestoreError
    client,now,source,manifest=fixture(tmp_path);keys=client.app.state.saas.eligibility_keys
    for bad in ({**manifest,'generated_at':now[0]-301},{**manifest,'source':'backup'},
                {**manifest,'generated_at':now[0]+61}):
        destination=tmp_path/'restore'/'bad.sqlite3'
        with pytest.raises(RestoreError):prepare_restore(source,destination,keys,bad,now=now[0],snapshot_created=now[0]-100)
        assert not destination.exists()
    with pytest.raises(RestoreError):prepare_restore(source,tmp_path/'restore'/'old.sqlite3',keys,manifest,now=now[0],snapshot_created=now[0]-7*86400)


def test_unavailable_authority_produces_only_quarantined_copy_with_no_access_credentials(tmp_path):
    from socialctl.saas.restore_guard import prepare_restore,release_restore,RestoreError
    client,now,source,manifest=fixture(tmp_path);keys=client.app.state.saas.eligibility_keys
    destination=tmp_path/'restore'/'quarantine.sqlite3'
    assert prepare_restore(source,destination,keys,None,now=now[0],snapshot_created=now[0]-100)['status']=='authority_unavailable'
    with sqlite3.connect(destination) as db:
        assert db.execute("SELECT count(*) FROM records WHERE kind IN ('saas-grant','saas-session','saas-installation','saas-capability')").fetchone()[0]==0
    with pytest.raises(RestoreError):release_restore(destination,keys,None,now=now[0],identity_cleanup_confirmed=True)
    with pytest.raises(ValueError):SaaSStore(Store(destination,keys),keys)


def test_user_tombstone_removes_owned_records_metadata_and_produces_auth_cleanup_sql(tmp_path):
    from socialctl.saas.restore_guard import prepare_restore,auth_cleanup_sql
    client,now,source,_=fixture(tmp_path);keys=client.app.state.saas.eligibility_keys
    client.app.state.saas_lifecycle.request_delete(USER)
    manifest=export_current_deletion_manifest(client.app.state.saas,lambda:now[0])
    destination=tmp_path/'restore'/'state.sqlite3'
    prepare_restore(source,destination,keys,manifest,now=now[0],snapshot_created=now[0]-100)
    with sqlite3.connect(destination) as db:
        assert db.execute('SELECT count(*) FROM saas_users WHERE id=?',(USER,)).fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM saas_entitlements WHERE user_id=?',(USER,)).fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM saas_record_owners WHERE user_id=?',(USER,)).fetchone()[0]==0
    sql=auth_cleanup_sql(manifest,now=now[0])
    assert 'BEGIN;' in sql and 'COMMIT;' in sql
    assert 'DELETE FROM auth.users' in sql and USER in sql
    assert 'DELETE FROM auth.sessions;' in sql and 'DELETE FROM auth.refresh_tokens;' in sql


def test_backup_cannot_export_current_authority_or_start_directly(tmp_path):
    from socialctl.saas.restore_guard import export_manifest,RestoreError
    client,now,source,manifest=fixture(tmp_path)
    with pytest.raises(RestoreError):export_manifest(source,now=now[0])
    with pytest.raises(ValueError,match='restored database'):
        SaaSStore(Store(source,client.app.state.saas.eligibility_keys),client.app.state.saas.eligibility_keys)


def test_current_pending_revocation_missing_from_old_backup_prevents_release(tmp_path):
    from socialctl.saas.restore_guard import prepare_restore,release_restore,RestoreError
    client,now,source,manifest=fixture(tmp_path);keys=client.app.state.saas.eligibility_keys
    manifest['pending_revocation_ids']=['new-current-job']
    destination=tmp_path/'restore'/'state.sqlite3'
    prepare_restore(source,destination,keys,manifest,now=now[0],snapshot_created=now[0]-100)
    with pytest.raises(RestoreError,match='revocation jobs are missing'):
        release_restore(destination,keys,manifest,now=now[0],identity_cleanup_confirmed=True)
    with pytest.raises(ValueError):SaaSStore(Store(destination,keys),keys)


def test_existing_destination_is_never_modified_and_auth_confirmation_is_required(tmp_path):
    from socialctl.saas.restore_guard import prepare_restore,release_restore,RestoreError
    client,now,source,manifest=fixture(tmp_path);keys=client.app.state.saas.eligibility_keys
    destination=tmp_path/'existing.sqlite3';destination.write_bytes(b'preserve')
    with pytest.raises(RestoreError):prepare_restore(source,destination,keys,manifest,now=now[0],snapshot_created=now[0]-100)
    assert destination.read_bytes()==b'preserve'
    destination=tmp_path/'restore'/'state.sqlite3'
    prepare_restore(source,destination,keys,manifest,now=now[0],snapshot_created=now[0]-100)
    with pytest.raises(RestoreError):release_restore(destination,keys,manifest,now=now[0])


def test_restore_cli_exports_from_authority_and_prepares_new_quarantined_output(tmp_path):
    import json,os,subprocess,sys
    client,now,source,manifest=fixture(tmp_path);keys=client.app.state.saas.eligibility_keys
    # CLI runs on the real wall clock, independent of fixture service time.
    import time
    manifest_path=tmp_path/'current.json'
    args=[sys.executable,'-m','socialctl.saas.restore_guard']
    result=subprocess.run([*args,'export',str(client.app.state.saas.vault.path),str(manifest_path),'--current-authority-confirmed'],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['status']=='manifest_exported'
    destination=tmp_path/'cli-restore'/'state.sqlite3'
    environment={**os.environ,'SOCIALCLI_ENCRYPTION_KEYS':','.join(key.decode() for key in keys)}
    result=subprocess.run([*args,'prepare',str(source),str(destination),'--manifest',str(manifest_path),'--snapshot-created',str(time.time())],env=environment,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['status']=='identity_cleanup_required'
    assert 'social-refresh' not in result.stdout+result.stderr
    sql=tmp_path/'cleanup.sql'
    result=subprocess.run([*args,'auth-sql',str(manifest_path),str(sql)],capture_output=True,text=True)
    assert result.returncode==0 and 'COMMIT;' in sql.read_text()


def test_release_rejects_live_database_before_opening_mutating_store(tmp_path,monkeypatch):
    from socialctl.saas import restore_guard
    client,now,source,manifest=fixture(tmp_path);metadata=client.app.state.saas
    def forbidden(*args,**kwargs):raise AssertionError('must not open a mutating store')
    monkeypatch.setattr(restore_guard,'Store',forbidden)
    with pytest.raises(restore_guard.RestoreError,match='live databases'):
        restore_guard.release_restore(metadata.vault.path,metadata.eligibility_keys,manifest,now=now[0],identity_cleanup_confirmed=True)


def test_raw_service_mode_cannot_bypass_snapshot_quarantine(tmp_path):
    from socialctl.connection_service.app import create_app
    from socialctl.connection_service.settings import Settings
    client,now,source,manifest=fixture(tmp_path)
    settings=Settings(public_url='https://social.example',database=source,
                      encryption_keys=client.app.state.saas.eligibility_keys,providers={})
    with pytest.raises(ValueError,match='restored database'):
        create_app(settings,clock=lambda:now[0])
