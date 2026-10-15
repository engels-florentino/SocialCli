"""Host backup completion requires both databases and separate recovery secrets."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import pytest


def module():
    path=Path(__file__).resolve().parents[1]/'deploy/saas/backup_host.py'
    spec=importlib.util.spec_from_file_location('backup_host',path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


def fixture(tmp_path):
    root=tmp_path/'private';root.mkdir(mode=0o700)
    config={'root':str(root),'service_container':'socialcli-saas-candidate',
        'auth_container':'socialcli-auth-db','repository':str(root/'repo'),
        'password_file':str(root/'password'),'recovery_repository':str(root/'recovery'),
        'recovery_password_file':str(root/'recovery-password'),
        'secret_files':{'service.env':str(root/'service.env')}}
    for name in ('password','recovery-password','service.env'):
        (root/name).write_text(name+'-private-secret');(root/name).chmod(0o600)
    return config,root


class Commands:
    def __init__(self,fail=None):self.fail=fail;self.calls=[]
    def __call__(self,args,**kwargs):
        self.calls.append(args)
        if self.fail and self.fail(args):raise subprocess.CalledProcessError(3,args,stderr=b'private-secret')
        if 'snapshot' in args:return json.dumps({'integrity':'ok','schema_version':6,'sha256':__import__('hashlib').sha256(b'fixture-sqlite').hexdigest()}).encode()
        if args[:2]==['docker','cp']:Path(args[-1]).write_bytes(b'fixture-sqlite')
        if 'pg_dump' in args:kwargs['stdout'].write(b'PGDMP fixture')
        if 'pg_restore' in args:
            # A subprocess uses the OS file offset, not a Python buffered cursor.
            subprocess.run([sys.executable,'-c',
                "import sys; assert sys.stdin.buffer.read(5)==b'PGDMP'"],
                stdin=kwargs['stdin'],capture_output=True,check=True)
        if 'backup' in args:return json.dumps({'message_type':'summary','snapshot_id':('b' if 'recovery' in args else 'a')*64}).encode()
        if 'snapshots' in args:return b'[]'
        return b''


def test_success_requires_separate_state_and_recovery_snapshots(tmp_path):
    backup=module();config,root=fixture(tmp_path);commands=Commands()
    result=backup.perform(config,runner=commands,now=lambda:1000)
    assert result['status']=='complete' and result['last_success']==1000
    assert result['state_snapshot']!=result['recovery_snapshot']
    backups=[args for args in commands.calls if 'backup' in args]
    assert len(backups)==2
    assert not (root/'staging').exists()
    assert (root/'status.json').stat().st_mode & 0o777==0o600
    assert 'private-secret' not in json.dumps(result)


@pytest.mark.parametrize('failure',['dump','state','recovery'])
def test_failure_does_not_replace_last_good_receipt_or_leak_details(tmp_path,failure):
    backup=module();config,root=fixture(tmp_path)
    backup.perform(config,runner=Commands(),now=lambda:1000)
    def fails(args):
        if failure=='dump':return 'pg_dump' in args
        return 'backup' in args and (('recovery' in args)==(failure=='recovery'))
    with pytest.raises(backup.BackupFailure):backup.perform(config,runner=Commands(fails),now=lambda:2000)
    report=json.loads((root/'status.json').read_text())
    assert report['status']=='failed' and report['last_success']==1000
    assert 'private-secret' not in json.dumps(report)
    assert not (root/'staging').exists()


def test_public_password_file_is_rejected_before_any_command(tmp_path):
    backup=module();config,root=fixture(tmp_path);(root/'password').chmod(0o644);commands=Commands()
    with pytest.raises(backup.BackupFailure):backup.perform(config,runner=commands)
    assert commands.calls==[]


def test_identical_backup_passwords_are_not_independent_recovery(tmp_path):
    backup=module();config,root=fixture(tmp_path)
    (root/'recovery-password').write_bytes((root/'password').read_bytes())
    commands=Commands()
    with pytest.raises(backup.BackupFailure):backup.perform(config,runner=commands)
    assert commands.calls==[]


@pytest.mark.parametrize('status,at,last_success,issues',[
    ('complete',9900,9900,[]),
    ('complete',9900,9900.001,[]),
    ('failed',9900,9800,['backup_failed']),
    ('running',9950,9800,[]),
    ('running',9000,8900,['backup_stalled']),
    ('complete',1000,1000,['backup_stale']),
    ('running',9950,None,['backup_unverified']),
    ('complete',11000,11000,['backup_unknown']),
])
def test_backup_inspector_detects_failure_stall_and_stale_success(tmp_path,status,at,last_success,issues):
    backup=module();config,root=fixture(tmp_path)
    report={'status':status,'at':at}
    if last_success is not None:
        report.update(last_success=last_success,state_snapshot='a'*64,recovery_snapshot='b'*64)
    backup.receipt(root,report)
    backup.receipt(root,{'status':'complete','at':9900,'last_success':9900},name='retention-status.json')
    before=(root/'status.json').read_bytes()
    result=backup.inspect_status(config,now=lambda:10000)
    assert result['issues']==issues
    assert result['status']==('attention' if issues else 'ok')
    assert (root/'status.json').read_bytes()==before


@pytest.mark.parametrize('payload',[None,'{invalid private-secret','[]',
    '{"status":"complete","at":9900,"last_success":9900}',
    '{"status":"complete","at":NaN,"last_success":9900}',
])
def test_backup_inspector_fails_closed_on_unusable_receipts(tmp_path,payload):
    backup=module();config,root=fixture(tmp_path)
    if payload is not None:
        (root/'status.json').write_text(payload);(root/'status.json').chmod(0o600)
    result=backup.inspect_status(config,now=lambda:10000)
    assert result=={'status':'attention','issues':['backup_unknown']}
    assert 'private-secret' not in json.dumps(result)


def test_status_cli_never_starts_backup_and_returns_attention_for_missing_receipt(tmp_path):
    config,root=fixture(tmp_path)
    config_path=root/'config.json';config_path.write_text(json.dumps(config));config_path.chmod(0o600)
    script=Path(__file__).resolve().parents[1]/'deploy/saas/backup_host.py'
    result=subprocess.run([sys.executable,str(script),str(config_path),'--status'],capture_output=True,text=True)
    assert result.returncode==1
    assert json.loads(result.stdout)=={'status':'attention','issues':['backup_unknown']}
    assert not (root/'staging').exists() and not (root/'worker.lock').exists()


def test_absolute_retention_removes_expired_snapshots_even_when_no_recent_backup(tmp_path):
    backup=module();config,root=fixture(tmp_path)
    calls=[]
    def runner(args,**kwargs):
        calls.append(args)
        if 'snapshots' in args:
            return json.dumps([{'id':'a'*64,'time':'2026-01-01T00:00:00Z'},
                               {'id':'b'*64,'time':'2026-01-09T01:00:00Z'}]).encode()
        return b''
    from datetime import datetime,timezone
    now=datetime(2026,1,15,tzinfo=timezone.utc).timestamp()
    result=backup.prune_repositories(config,runner=runner,now=lambda:now)
    assert result['removed']==2
    forgotten=[c for c in calls if 'forget' in c]
    assert len(forgotten)==2 and all(c[-1]=='a'*64 and 'b'*64 not in c for c in forgotten)
    assert len([c for c in calls if 'prune' in c and c[-2:]==['--max-unused','0']])==2


def test_retention_validates_both_repository_inventories_before_deleting(tmp_path):
    backup=module();config,root=fixture(tmp_path);calls=[]
    def runner(args,**kwargs):
        calls.append(args)
        if 'snapshots' in args:
            return b'[]' if args[args.index('--repo')+1]!=config['recovery_repository'] else b'[{"id":"bad", "time":"yesterday"}]'
        return b''
    with pytest.raises(backup.BackupFailure):backup.prune_repositories(config,runner=runner)
    assert not any('forget' in c or 'prune' in c for c in calls)


def test_retention_preview_never_mutates_repository(tmp_path):
    backup=module();config,root=fixture(tmp_path);calls=[]
    def runner(args,**kwargs):
        calls.append(args)
        return b'[{"id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","time":"2000-01-01T00:00:00Z"}]' if 'snapshots' in args else b''
    assert backup.prune_repositories(config,runner=runner,preview=True)['removed']==0
    assert not any('forget' in c or 'prune' in c for c in calls)


def test_retention_mirrors_drive_hard_deletions_only_after_local_checks(tmp_path):
    backup=module();config,root=fixture(tmp_path)
    auth=root/'rclone.conf';auth.write_text('fixture');auth.chmod(0o600)
    config.update(drive_config=str(auth),drive_remote='socialcli_backup:SocialCli-Backups/candidate')
    calls=[]
    def runner(args,**kwargs):
        calls.append(args)
        return b'[]' if 'snapshots' in args else b''
    result=backup.prune_repositories(config,runner=runner)
    syncs=[args for args in calls if args[:2]==['rclone','sync']]
    assert len(syncs)==2 and result['remote_verified'] is True
    assert all('--drive-use-trash=false' in args and '--delete-after' in args for args in syncs)
    assert syncs[0][2:4]==[config['repository'],'socialcli_backup:SocialCli-Backups/candidate/data']
    assert calls.index(syncs[0])>max(i for i,c in enumerate(calls) if c[0]=='restic')


def test_unexpected_drive_destination_never_deletes_anything(tmp_path):
    backup=module();config,root=fixture(tmp_path)
    config.update(drive_remote='socialcli_backup:',drive_config=str(root/'password'))
    calls=[]
    def runner(args,**kwargs):
        calls.append(args)
        return b'[]' if 'snapshots' in args else b''
    with pytest.raises(backup.BackupFailure):backup.prune_repositories(config,runner=runner)
    assert not any('prune' in c or 'sync' in c for c in calls)


def test_independent_retention_failure_is_visible_without_new_backup_failure(tmp_path):
    backup=module();config,root=fixture(tmp_path)
    backup.perform(config,runner=Commands(),now=lambda:1000)
    with pytest.raises(backup.BackupFailure):
        backup.prune_repositories(config,runner=Commands(lambda args:'prune' in args),now=lambda:1100)
    result=backup.inspect_status(config,now=lambda:1200)
    assert 'retention_failed' in result['issues']
    assert 'backup_failed' not in result['issues']


def test_stopped_retention_timer_cannot_be_hidden_by_recent_local_backup_receipt(tmp_path):
    backup=module();config,root=fixture(tmp_path)
    backup.perform(config,runner=Commands(),now=lambda:1000)
    report=json.loads((root/'status.json').read_text())
    report.update(at=30000,last_success=30000)
    backup.receipt(root,report)
    assert 'retention_stale' in backup.inspect_status(config,now=lambda:30100)['issues']


def test_retention_preview_does_not_replace_failed_receipt(tmp_path):
    backup=module();config,root=fixture(tmp_path)
    backup.receipt(root,{'status':'failed','at':1000,'last_success':900},name='retention-status.json')
    before=(root/'retention-status.json').read_bytes()
    backup.prune_repositories(config,runner=Commands(),now=lambda:1100,preview=True)
    assert (root/'retention-status.json').read_bytes()==before


def test_retention_remote_failure_is_persisted_and_cannot_mark_backup_complete(tmp_path):
    backup=module();config,root=fixture(tmp_path)
    config.update(drive_remote='socialcli_backup:SocialCli-Backups/candidate',drive_config=str(root/'password'))
    with pytest.raises(backup.BackupFailure):
        backup.perform(config,runner=Commands(lambda args:args[:2]==['rclone','check']),now=lambda:1000)
    assert json.loads((root/'status.json').read_text())['status']=='failed'
    assert json.loads((root/'retention-status.json').read_text())['status']=='failed'
    assert 'retention_failed' in backup.inspect_status(config,now=lambda:1100)['issues']
