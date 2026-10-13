"""Private diagnostics must be read-only, sanitized and detect stalled workers."""
import json
import subprocess
import sys
import time


def test_missing_database_is_not_created_or_reported_ready(tmp_path):
    from socialctl.saas.operations import inspect_status
    path=tmp_path/'missing.sqlite3'
    report=inspect_status(path,[],now=1000)
    assert report['status']=='attention' and 'database_unavailable' in report['issues']
    assert list(tmp_path.iterdir())==[]


def test_heartbeat_stale_failure_and_private_permissions(tmp_path):
    from tests.test_saas_reminders import prepared
    from socialctl.saas.operations import inspect_status,write_heartbeat,heartbeat_path
    _,metadata,now,_=prepared(tmp_path);path=metadata.vault.path
    write_heartbeat(path,'complete',now[0])
    report=inspect_status(path,metadata.eligibility_keys,now=now[0])
    assert report['status']=='ok'
    assert heartbeat_path(path).stat().st_mode & 0o777==0o600
    assert 'maintenance_stale' in inspect_status(path,metadata.eligibility_keys,now=now[0]+901)['issues']
    write_heartbeat(path,'running',now[0])
    assert 'maintenance_stalled' in inspect_status(path,metadata.eligibility_keys,now=now[0]+241)['issues']
    write_heartbeat(path,'failed',now[0])
    assert 'maintenance_failed' in inspect_status(path,metadata.eligibility_keys,now=now[0])['issues']


def test_read_only_status_counts_review_and_preserves_private_details(tmp_path):
    from tests.test_saas_reminders import prepared
    from socialctl.saas.operations import inspect_status,write_heartbeat
    reminders,metadata,now,end=prepared(tmp_path);reminders.prepare()
    with metadata.vault.transaction() as db:
        db.execute("UPDATE saas_reminders SET state='uncertain'")
    write_heartbeat(metadata.vault.path,'complete',now[0])
    before=metadata.vault.path.read_bytes()
    report=inspect_status(metadata.vault.path,metadata.eligibility_keys,now=now[0])
    assert report['counts']['reminders_uncertain']==2
    assert 'reminders_uncertain' in report['issues']
    assert '@' not in json.dumps(report) and 'user_id' not in json.dumps(report)
    assert metadata.vault.path.read_bytes()==before


def test_database_lock_or_corruption_has_sanitized_diagnostic(tmp_path):
    from socialctl.saas.operations import inspect_status
    path=tmp_path/'secret-private-name';path.write_text('private password and recipient@example.test')
    result=inspect_status(path,[],now=1000)
    assert result['issues']==['database_unavailable']
    assert 'private' not in json.dumps(result)


def test_hard_deadline_terminates_actual_process():
    code="from socialctl.saas.budget import hard_deadline; import time\nwith hard_deadline(.05): time.sleep(10)"
    started=time.monotonic()
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,timeout=5)
    assert result.returncode==124 and time.monotonic()-started<5
    assert not result.stdout and not result.stderr


def test_maintenance_failure_is_private_and_persists_failure(tmp_path,monkeypatch,capsys):
    import pytest
    from tests.test_saas_reminders import prepared
    from socialctl.saas import maintenance
    from socialctl.saas.operations import inspect_status
    from socialctl.saas.settings import SaaSSettings
    from socialctl.connection_service.settings import Settings
    _,metadata,now,_=prepared(tmp_path)
    settings=Settings(public_url='https://social.example',database=metadata.vault.path,
        encryption_keys=metadata.eligibility_keys,providers={},saas=SaaSSettings(auth_url='https://auth.example'))
    monkeypatch.setattr(maintenance.Settings,'from_environment',lambda:settings)
    monkeypatch.setattr(maintenance.time,'time',lambda:now[0])
    monkeypatch.setattr(maintenance.sys,'argv',['maintenance'])
    def fail(settings):raise RuntimeError('secret token recipient@example.test')
    monkeypatch.setattr(maintenance,'run',fail)
    with pytest.raises(SystemExit) as error:maintenance.main()
    assert error.value.code==1
    assert json.loads(capsys.readouterr().out)=={'status':'failed','error':'maintenance_failed'}
    assert 'maintenance_failed' in inspect_status(metadata.vault.path,metadata.eligibility_keys,now=now[0])['issues']
