"""Missing remote copies and uncertain mail must never look healthy or resend."""
import importlib.util
import json
from pathlib import Path
import sys


def load():
    path=Path(__file__).resolve().parents[1]/'deploy/saas'
    spec=importlib.util.spec_from_file_location('backup_host',path/'backup_host.py')
    backup=importlib.util.module_from_spec(spec);spec.loader.exec_module(backup)
    sys.modules['backup_host']=backup
    spec=importlib.util.spec_from_file_location('backup_alert',path/'backup_alert.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return backup,module


def setup(tmp_path):
    tmp_path.chmod(0o700)
    report={'status':'complete','at':1000,'last_success':1000,
            'state_snapshot':'a'*64,'recovery_snapshot':'b'*64}
    (tmp_path/'status.json').write_text(json.dumps(report));(tmp_path/'status.json').chmod(0o600)
    return {'root':str(tmp_path)}


def test_local_success_does_not_hide_missing_or_stale_drive_copy(tmp_path):
    backup,alerts=load();config=setup(tmp_path)
    assert alerts.issues(config,service_result='success',now=lambda:1100)==['remote_backup_unknown']
    alerts.remote_complete(config,now=lambda:1100)
    assert alerts.issues(config,service_result='success',now=lambda:1200)==[]
    assert 'remote_backup_stale' in alerts.issues(config,service_result='success',now=lambda:9000)
    assert 'backup_service_failed' in alerts.issues(config,service_result='exit-code',now=lambda:1200)


def test_remote_completion_cannot_certify_failed_local_backup(tmp_path):
    _,alerts=load();config=setup(tmp_path)
    report=json.loads((tmp_path/'status.json').read_text());report['status']='failed'
    (tmp_path/'status.json').write_text(json.dumps(report))
    import pytest
    with pytest.raises(ValueError):alerts.remote_complete(config,now=lambda:1100)
    assert not (tmp_path/'remote-status.json').exists()


def test_one_english_notification_per_incident_and_rearm_after_recovery(tmp_path):
    _,alerts=load();config=setup(tmp_path);messages=[]
    def send(message,before_send):
        assert before_send()
        assert json.loads((tmp_path/'alert-status.json').read_text())['delivery']=='sending'
        messages.append(message)
        return 'accepted'
    assert alerts.notify(config,['remote_backup_unknown'],send)['delivery']=='accepted'
    alerts.notify(config,['remote_backup_stale'],send)
    assert len(messages)==1
    assert 'Backup needs attention' in str(messages[0])
    alerts.notify(config,[],send,recovered=True)
    alerts.notify(config,['backup_service_failed'],send)
    assert len(messages)==2


def test_uncertain_delivery_survives_restart_without_duplicate(tmp_path):
    _,alerts=load();config=setup(tmp_path)
    def interrupted(message,before_send):
        assert before_send()
        raise RuntimeError('private secret')
    assert alerts.notify(config,['remote_backup_unknown'],interrupted)['delivery']=='uncertain'
    _,alerts=load()
    def forbidden(*args):raise AssertionError('must not resend')
    assert alerts.notify(config,['remote_backup_stale'],forbidden)['delivery']=='uncertain'
    assert 'private secret' not in (tmp_path/'alert-status.json').read_text()


def test_retry_before_data_is_allowed(tmp_path):
    _,alerts=load();config=setup(tmp_path)
    assert alerts.notify(config,['remote_backup_unknown'],lambda *_:'retry')['delivery']=='retry'
    def accepted(message,before_send):
        assert before_send();return 'accepted'
    assert alerts.notify(config,['remote_backup_unknown'],accepted)['delivery']=='accepted'


def test_future_or_malformed_remote_receipt_never_looks_healthy(tmp_path):
    _,alerts=load();config=setup(tmp_path)
    alerts.remote_complete(config,now=lambda:1100)
    path=tmp_path/'remote-status.json';report=json.loads(path.read_text())
    report['verified_at']=999999;path.write_text(json.dumps(report))
    assert alerts.issues(config,service_result='success',now=lambda:1200)==['remote_backup_unknown']
    path.write_text('not json')
    assert alerts.issues(config,service_result='success',now=lambda:1200)==['remote_backup_unknown']


def test_crash_after_data_fence_is_not_replayed(tmp_path):
    _,alerts=load();config=setup(tmp_path)
    path=tmp_path/'alert-status.json'
    path.write_text(json.dumps({'active':True,'delivery':'sending','at':1000}));path.chmod(0o600)
    def forbidden(*args):raise AssertionError('must not resend after crash')
    assert alerts.notify(config,['remote_backup_unknown'],forbidden)['delivery']=='sending'


def test_running_retry_does_not_rearm_an_unrecovered_incident(tmp_path):
    _,alerts=load();config=setup(tmp_path);sent=[]
    alerts.remote_complete(config,now=lambda:1100)
    def send(message,fence):
        assert fence();sent.append(message);return 'accepted'
    alerts.notify(config,['backup_service_failed'],send)
    report=json.loads((tmp_path/'status.json').read_text());report.update(status='running',at=1150)
    (tmp_path/'status.json').write_text(json.dumps(report))
    assert not alerts.can_rearm(config,service_result='success',active_state='activating',now=lambda:1200)
    alerts.notify(config,[],send,recovered=False)
    alerts.notify(config,['backup_service_failed'],send)
    assert len(sent)==1
    report.update(status='complete',at=1250,last_success=1250,state_snapshot='c'*64)
    (tmp_path/'status.json').write_text(json.dumps(report))
    assert not alerts.can_rearm(config,service_result='success',active_state='inactive',now=lambda:1300)
    alerts.remote_complete(config,now=lambda:1300)
    assert alerts.can_rearm(config,service_result='success',active_state='inactive',now=lambda:1400)
    alerts.notify(config,[],send,recovered=True)
    alerts.notify(config,['backup_service_failed'],send)
    assert len(sent)==2
