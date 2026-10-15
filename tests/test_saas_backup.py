"""Snapshots preserve transactional metadata and encrypted grants without replay."""
import sqlite3
import stat
import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from socialctl.connection_service.store import Store
from socialctl.saas.store import SaaSStore
from socialctl.saas.models import AuthenticatedUser
from socialctl.saas.access import AccessControl


def fixture(tmp_path):
    keys=[Fernet.generate_key()]
    vault=Store(tmp_path/'live'/'state.sqlite3',keys)
    metadata=SaaSStore(vault,keys)
    with vault.transaction() as db:
        metadata.ensure_user(db,AuthenticatedUser('owner','owner@example.test'),1000)
        AccessControl(metadata).activate_trial(db,'owner',1000)
        vault.put(db,'saas-grant','fictional',{'refresh_token':'private-fictional-refresh'},expires=9000)
    return vault,keys


def api():
    from socialctl.saas.backup import snapshot,verify_snapshot,BackupError
    return snapshot,verify_snapshot,BackupError


def test_online_snapshot_contains_wal_commit_not_uncommitted_rows_and_private_files(tmp_path):
    snapshot,verify,_=api();vault,keys=fixture(tmp_path)
    writer=sqlite3.connect(vault.path)
    try:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute("INSERT INTO rates VALUES('committed',1,2)");writer.commit()
        writer.execute("INSERT INTO rates VALUES('uncommitted',1,99)")
        destination=tmp_path/'backups'/'state.sqlite3'
        report=snapshot(vault.path,destination,keys)
        assert report['records']==2 and report['schema_version']==8
        assert report==verify(destination,keys)
        assert stat.S_IMODE(destination.stat().st_mode)==0o600
        assert stat.S_IMODE(destination.parent.stat().st_mode)==0o700
        with sqlite3.connect(destination) as db:
            assert db.execute('SELECT identity,count FROM rates').fetchall()==[('committed',2)]
            assert db.execute('SELECT trial_start,trial_end FROM saas_entitlements').fetchone()==(1000,1210600)
        assert b'private-fictional-refresh' not in destination.read_bytes()
        assert b'owner@example.test' not in destination.read_bytes()
    finally:writer.rollback();writer.close()


def test_wrong_keys_or_tampered_record_fail_without_publishing_snapshot(tmp_path):
    snapshot,verify,Error=api();vault,keys=fixture(tmp_path)
    target=tmp_path/'backups'/'bad.sqlite3'
    with pytest.raises(Error):snapshot(vault.path,target,[Fernet.generate_key()])
    assert not target.exists()
    assert list(target.parent.iterdir())==[]
    with sqlite3.connect(vault.path) as db:db.execute("UPDATE records SET credential_hash='tampered' WHERE kind='saas-grant'")
    with pytest.raises(Error):verify(vault.path,keys)


def test_existing_target_and_missing_source_are_never_overwritten_or_created(tmp_path):
    snapshot,_,Error=api();vault,keys=fixture(tmp_path)
    target=tmp_path/'saved.sqlite3';target.write_bytes(b'keep me')
    with pytest.raises(Error):snapshot(vault.path,target,keys)
    assert target.read_bytes()==b'keep me'
    missing=tmp_path/'absent.sqlite3'
    with pytest.raises(Error):snapshot(missing,tmp_path/'copy.sqlite3',keys)
    assert not missing.exists() and not (tmp_path/'copy.sqlite3').exists()
    with pytest.raises(Error):snapshot(vault.path,vault.path,keys)


def test_busy_source_has_bounded_wait_and_no_completed_artifact(tmp_path):
    snapshot,_,Error=api();vault,keys=fixture(tmp_path)
    lock=sqlite3.connect(vault.path);lock.execute('PRAGMA journal_mode=DELETE');lock.execute('BEGIN EXCLUSIVE')
    start=time.monotonic()
    try:
        with pytest.raises(Error):snapshot(vault.path,tmp_path/'backups'/'locked.sqlite3',keys,timeout=.05)
    finally:lock.rollback();lock.close()
    assert time.monotonic()-start<2
    assert not (tmp_path/'backups'/'locked.sqlite3').exists()


def test_restored_service_preserves_trial_cutoff_and_owner_token_boundary(tmp_path):
    from dataclasses import replace
    import httpx
    from fastapi.testclient import TestClient
    from tests.test_saas_lifecycle import connected
    from tests.test_saas_connections import token,provider
    from socialctl.connection_service.app import create_app
    from socialctl.connection_service.legacy import LegacyPolicy
    snapshot,_,_=api();client,now,installation,connection=connected(tmp_path/'original')
    from socialctl.connection_service.settings import Settings,AppCredentials
    from socialctl.saas.settings import SaaSSettings
    vault=client.app.state.saas.vault
    settings=Settings(public_url="https://social.example",database=vault.path,
        encryption_keys=client.app.state.saas.eligibility_keys,saas=SaaSSettings(auth_url="https://auth.example"),
        providers={"youtube":AppCredentials("fictional-app","fictional-secret")})
    LegacyPolicy(vault,lambda:now[0],close=True)
    copy=tmp_path/'isolated'/'restore.sqlite3'
    report=snapshot(vault.path,copy,settings.encryption_keys)
    assert report['legacy_cutoff'] is True
    restored=TestClient(create_app(replace(settings,database=copy),clock=lambda:now[0],
        provider_client=httpx.Client(transport=httpx.MockTransport(provider))),base_url='https://social.example')
    assert token(restored,connection,refresh=False).status_code==200
    wrong={**connection,'connection_secret':'another-owner-secret'}
    assert token(restored,wrong,refresh=False).status_code==404
    creation={'platform':'youtube','poll_challenge':'a'*64}
    assert restored.post('/v1/authorizations',json=creation).status_code==410
    now[0]=1210600
    assert token(restored,connection,refresh=False).status_code==402


def test_rotated_keys_restore_all_records_but_current_key_alone_cannot(tmp_path):
    from cryptography.fernet import MultiFernet
    snapshot,verify,Error=api();vault,old=fixture(tmp_path)
    keys=[Fernet.generate_key(),*old]
    vault.cipher=MultiFernet([Fernet(k) for k in keys])
    with vault.transaction() as db:vault.put(db,'rotation','new',{'fixture':True},expires=9000)
    target=tmp_path/'backups'/'rotated.sqlite3'
    assert snapshot(vault.path,target,keys)['records']==3
    with pytest.raises(Error):verify(target,keys[:1])


def test_command_reports_counts_only_and_missing_keys_fail_closed(tmp_path):
    import json,os,subprocess,sys
    _,_,_=api();vault,keys=fixture(tmp_path)
    args=[sys.executable,'-m','socialctl.saas.backup','snapshot',str(vault.path),str(tmp_path/'backups'/'cli.sqlite3')]
    environment={**os.environ,'SOCIALCLI_ENCRYPTION_KEYS':','.join(k.decode() for k in keys)}
    result=subprocess.run(args,env=environment,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['records']==2
    assert 'owner@example.test' not in result.stdout+result.stderr
    assert 'private-fictional-refresh' not in result.stdout+result.stderr
    result=subprocess.run([sys.executable,'-m','socialctl.saas.backup','verify',args[-1]],
        env={**environment,'SOCIALCLI_ENCRYPTION_KEYS':''},capture_output=True,text=True)
    assert result.returncode==1 and result.stdout==''
    assert 'Traceback' not in result.stderr


def test_corrupt_file_is_rejected_and_not_published(tmp_path):
    snapshot,_,Error=api();source=tmp_path/'broken.sqlite3';source.write_bytes(b'not sqlite')
    target=tmp_path/'backups'/'copy.sqlite3'
    with pytest.raises(Error):snapshot(source,target,[Fernet.generate_key()])
    assert not target.exists()
