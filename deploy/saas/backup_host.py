#!/usr/bin/env python3
"""Host-only encrypted backup orchestration; never modifies live database rows."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import time


class BackupFailure(Exception):
    pass


def run(args,**kwargs):
    result=subprocess.run(args,check=True,timeout=kwargs.pop('timeout',180),stderr=subprocess.PIPE,
        stdout=kwargs.pop('stdout',subprocess.PIPE),**kwargs)
    return result.stdout or b''


def private(path,*,directory=False):
    path=Path(path)
    if (path.is_symlink() or not (path.is_dir() if directory else path.is_file())
            or path.stat().st_mode & 0o077 or path.stat().st_uid!=os.geteuid()):
        raise BackupFailure('Private backup path is unavailable.')
    return path


def receipt(root,value,*,name="status.json"):
    filename=name
    fd,name=tempfile.mkstemp(prefix='.status-',dir=root)
    try:
        with os.fdopen(fd,'w') as out:
            json.dump(value,out);out.flush();os.fsync(out.fileno())
        os.replace(name,root/filename)
        descriptor=os.open(root,os.O_RDONLY)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)
    finally:Path(name).unlink(missing_ok=True)


def restic(config,recovery=False):
    prefix='recovery_' if recovery else ''
    return ['restic','--repo',config[prefix+'repository'],'--password-file',config[prefix+'password_file']]


def snapshot_id(output):
    summaries=[json.loads(line) for line in output.splitlines() if line]
    ids=[row.get('snapshot_id','') for row in summaries if row.get('message_type')=='summary']
    if len(ids)!=1 or not re.fullmatch(r'[0-9a-f]{64}',ids[0]):raise BackupFailure('Backup receipt unavailable.')
    return ids[0]


def _retention(config,*,runner,now,preview=False):
    """Absolute six-day cutoff, including repositories whose backups have stopped."""
    current=now()
    if not isinstance(current,(int,float)) or not math.isfinite(current) or current<=0:
        raise BackupFailure('Invalid retention clock.')
    cutoff=current-6*86400
    plans=[]
    try:
        remote=config.get('drive_remote')
        if remote is not None:
            if not isinstance(remote,str) or not re.fullmatch(r'[A-Za-z0-9_-]+:SocialCli-Backups/[A-Za-z0-9_-]+',remote):
                raise ValueError
            private(config['drive_config'])
        for recovery in (False,True):
            base=restic(config,recovery)
            runner(base+['check'])
            rows=json.loads(runner(base+['snapshots','--json']))
            if not isinstance(rows,list):raise ValueError
            expired=[]
            for row in rows:
                identifier=row['id']
                at=datetime.fromisoformat(row['time'].replace('Z','+00:00'))
                if (not re.fullmatch(r'[0-9a-f]{64}',identifier) or at.tzinfo is None
                        or at.timestamp()>current+300):raise ValueError
                if at.timestamp()<=cutoff:expired.append(identifier)
            plans.append((base,expired))
        # Inspect both repositories before making the first destructive change.
        if not preview:
            for base,expired in plans:
                for start in range(0,len(expired),100):
                    runner(base+['forget',*expired[start:start+100]],timeout=900)
                # A previous interrupted prune may leave packs even with no old snapshots.
                runner(base+['prune','--max-unused','0'],timeout=900)
                runner(base+['check'])
            if remote:
                for key,suffix in (('repository','data'),('recovery_repository','recovery')):
                    destination=remote+'/'+suffix
                    runner(['rclone','sync',config[key],destination,'--config',config['drive_config'],
                            '--checksum','--check-first','--delete-after','--drive-use-trash=false',
                            '--transfers','4','--checkers','4'],timeout=900)
                    runner(['rclone','check',config[key],destination,'--config',config['drive_config']],timeout=900)
        count=sum(len(expired) for _,expired in plans)
        return {'status':'preview' if preview else 'complete','retention_days':6,
                'eligible':count,'removed':0 if preview else count,'at':current,
                'remote_verified':bool(remote and not preview)}
    except (ValueError,KeyError,TypeError,OSError,subprocess.SubprocessError):
        raise BackupFailure('Backup retention failed; inspect private repositories.') from None


def _run_retention(config,*,runner,now,preview=False):
    if preview:return _retention(config,runner=runner,now=now,preview=True)
    root=private(config['root'],directory=True)
    previous={}
    try:previous=json.loads(private(root/'retention-status.json').read_text())
    except (OSError,ValueError,BackupFailure):pass
    last=previous.get('last_success') if isinstance(previous,dict) else None
    receipt(root,{'status':'running','at':now(),'last_success':last},name='retention-status.json')
    try:
        result=_retention(config,runner=runner,now=now)
    except Exception:
        receipt(root,{'status':'failed','at':now(),'last_success':last},name='retention-status.json')
        raise
    receipt(root,{**result,'at':now(),'last_success':now()},name='retention-status.json')
    return result


def retention_issues(config,*,now):
    try:
        root=private(config['root'],directory=True)
        with private(root/'retention-status.json').open('rb') as stream:raw=stream.read(65537)
        if len(raw)>65536:raise ValueError
        record=json.loads(raw)
        at,last,state=record['at'],record.get('last_success'),record['status']
        if state not in {'running','failed','complete'}:raise ValueError
        for value in (at,last):
            if value is not None and (type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=now):raise ValueError
        if at is None or (last is not None and last>at+1):raise ValueError
        problems=[]
        if state=='failed':problems.append('retention_failed')
        if state=='running' and now-at>3600:problems.append('retention_stalled')
        if last is None:problems.append('retention_unknown')
        elif now-last>7*3600:problems.append('retention_stale')
        if state=='complete' and config.get('drive_remote') and record.get('remote_verified') is not True:
            problems.append('retention_remote_unverified')
        return problems
    except Exception:return ['retention_unknown']


def prune_repositories(config,*,runner=run,now=time.time,preview=False):
    root=private(config['root'],directory=True)
    for key in ('password_file','recovery_password_file'):private(config[key])
    fd=os.open(root/'worker.lock',os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise BackupFailure('Backup already running.') from None
        return _run_retention(config,runner=runner,now=now,preview=preview)


def inspect_status(config,*,now=time.time):
    """Read local completion receipts only; never create a backup or contact Drive."""
    try:
        root=private(config['root'],directory=True)
        path=private(root/'status.json')
        with path.open('rb') as stream:raw=stream.read(65537)
        if len(raw)>65536:raise ValueError
        report=json.loads(raw)
        current=now()
        def timestamp(value):
            if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=current:
                raise ValueError
            return value
        at=timestamp(report['at'])
        status=report['status']
        if status not in ('complete','running','failed'):raise ValueError
        last=report.get('last_success')
        if last is not None:
            last=timestamp(last)
            # Existing receipts sample the two clocks separately at completion.
            if last>at+1:raise ValueError
            for key in ('state_snapshot','recovery_snapshot'):
                if not re.fullmatch(r'[0-9a-f]{64}',report[key]):raise ValueError
        elif status=='complete':raise ValueError
        issues=[]
        if status=='failed':issues.append('backup_failed')
        if status=='running' and current-at>600:issues.append('backup_stalled')
        if last is None:issues.append('backup_unverified')
        elif current-last>7200:issues.append('backup_stale')
        issues.extend(retention_issues(config,now=current))
        return {'status':'attention' if issues else 'ok','issues':issues}
    except Exception:
        return {'status':'attention','issues':['backup_unknown']}


def perform(config,*,runner=run,now=time.time):
    root=private(config['root'],directory=True)
    for name in ('password_file','recovery_password_file'):private(config[name])
    if (Path(config['password_file']).read_bytes()==Path(config['recovery_password_file']).read_bytes()
            or config['repository']==config['recovery_repository']):
        raise BackupFailure('State and recovery repositories must be separate.')
    for name,source in config['secret_files'].items():
        if not re.fullmatch(r'[a-zA-Z0-9_.-]+',name):raise BackupFailure('Invalid recovery filename.')
        private(source)
    service=config['service_container'];auth=config['auth_container']
    if not all(re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*',name) for name in (service,auth)):
        raise BackupFailure('Invalid backup service.')
    fd=os.open(root/'worker.lock',os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise BackupFailure('Backup already running.') from None
        previous={}
        try:previous=json.loads((root/'status.json').read_text())
        except (OSError,ValueError):pass
        report={key:previous[key] for key in ('last_success','state_snapshot','recovery_snapshot') if key in previous}
        stage=root/'staging';remote='/data/.socialcli-backup-'+secrets.token_hex(16)+'.sqlite3'
        remote_started=False
        try:
            receipt(root,{**report,'status':'running','at':now()})
            # Dedicated disposable staging only; a crashed run may leave plaintext.
            if stage.exists():
                private(stage,directory=True);shutil.rmtree(stage)
            stage.mkdir(mode=0o700)
            data=stage/'data';recovery=stage/'recovery'
            data.mkdir(mode=0o700);recovery.mkdir(mode=0o700)
            remote_started=True
            vault=json.loads(runner(['docker','exec',service,'python','-m','socialctl.saas.backup',
                'snapshot','/data/connections.sqlite3',remote,'--timeout','60']))
            if vault.get('integrity')!='ok':raise BackupFailure('Vault verification failed.')
            runner(['docker','cp',service+':'+remote,str(data/'vault.sqlite3')])
            (data/'vault.sqlite3').chmod(0o600)
            with (data/'auth.dump').open('wb') as out:
                (data/'auth.dump').chmod(0o600)
                runner(['docker','exec',auth,'pg_dump','-U','socialcli_auth','-d','socialcli_auth','-Fc'],stdout=out)
            # pg_restore inherits the OS offset; avoid a buffered seek after header inspection.
            with (data/'auth.dump').open('rb',buffering=0) as source:
                if source.read(5)!=b'PGDMP':raise BackupFailure('Auth dump unavailable.')
                source.seek(0)
                runner(['docker','exec','-i',auth,'pg_restore','--list'],stdin=source)
            # Copy configuration separately, encrypted with an independent password.
            for name,source in config['secret_files'].items():
                shutil.copyfile(source,recovery/name);(recovery/name).chmod(0o600)
            hashes={}
            for name in ('vault.sqlite3','auth.dump'):
                with (data/name).open('rb') as stream:hashes[name]=hashlib.file_digest(stream,'sha256').hexdigest()
            if hashes['vault.sqlite3']!=vault['sha256']:raise BackupFailure('Vault copy hash mismatch.')
            manifest={'created':now(),'vault':vault,'files':hashes,'atomic_across_stores':False}
            (data/'manifest.json').write_text(json.dumps(manifest));(data/'manifest.json').chmod(0o600)
            state_id=snapshot_id(runner(restic(config)+['backup','--json','--host',service,'--tag','data','.'],cwd=data))
            recovery_id=snapshot_id(runner(restic(config,True)+['backup','--json','--host',service,'--tag','recovery','.'],cwd=recovery))
            runner(restic(config)+['check'])
            runner(restic(config,True)+['check'])
            retention=_run_retention(config,runner=runner,now=now)
            report['retention_at']=retention['at']
            report['remote_verified']=retention['remote_verified']
            report.update(status='complete',at=now(),last_success=now(),state_snapshot=state_id,recovery_snapshot=recovery_id)
            receipt(root,report)
            return report
        except Exception:
            receipt(root,{**report,'status':'failed','at':now(),'error':'backup_failed'})
            raise BackupFailure('Backup failed; previous successful receipt preserved.') from None
        finally:
            if remote_started:
                try:runner(['docker','exec',service,'python','-c','from pathlib import Path; import sys; Path(sys.argv[1]).unlink(missing_ok=True)',remote])
                except Exception:pass
            if stage.exists() and not stage.is_symlink():shutil.rmtree(stage)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config',type=Path)
    parser.add_argument('--status',action='store_true',help='Inspect local backup freshness without starting a backup')
    parser.add_argument('--retention',action='store_true',help='Prune expired snapshots without making a new backup')
    parser.add_argument('--preview-retention',action='store_true',help='Inspect six-day retention without deleting anything')
    args=parser.parse_args()
    if sum((args.status,args.retention,args.preview_retention))>1:parser.error('Choose one operation')
    os.umask(0o077)
    try:
        config=json.loads(private(args.config).read_text())
        result=(inspect_status(config) if args.status else
                prune_repositories(config,preview=args.preview_retention) if args.retention or args.preview_retention
                else perform(config))
    except Exception:
        print(json.dumps({'status':'attention','issues':['backup_unknown']} if args.status
                         else {'status':'failed','error':'backup_failed'}))
        raise SystemExit(1)
    print(json.dumps(result,sort_keys=True))
    if args.status and result['status']!='ok':raise SystemExit(1)


if __name__=='__main__':main()
