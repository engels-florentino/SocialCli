#!/usr/bin/env python3
"""Private host backup monitoring; encrypted remote completion and durable mail fencing."""
import argparse
from email.message import EmailMessage
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import time
from backup_host import private, inspect_status


LABELS={
    'retention_failed':'Backup retention failed; expired data may remain.',
    'retention_stalled':'Backup retention has stopped making progress.',
    'retention_unknown':'Backup retention has not been verified.',
    'retention_stale':'Backup retention has not completed in more than seven hours.',
    'retention_remote_unverified':'Google Drive backup removals have not been verified.',
    'backup_failed':'The local backup failed.',
    'backup_stalled':'The local backup has stopped making progress.',
    'backup_unverified':'No verified local backup is available.',
    'backup_stale':'The local backup is more than two hours old.',
    'backup_unknown':'The local backup status cannot be verified.',
    'remote_backup_unknown':'No verified Google Drive copy is recorded.',
    'remote_backup_stale':'The verified Google Drive backup is more than two hours old.',
    'backup_service_failed':'The backup service reported a failure.',
}


def read(path):
    with private(path).open('rb') as stream:raw=stream.read(65537)
    if len(raw)>65536:raise ValueError('Status is too large.')
    return json.loads(raw)


def write(root,name,value):
    fd,temp=tempfile.mkstemp(prefix='.alert-',dir=root)
    try:
        with os.fdopen(fd,'w') as out:
            json.dump(value,out);out.flush();os.fsync(out.fileno())
        os.replace(temp,root/name)
        fd=os.open(root,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:Path(temp).unlink(missing_ok=True)


def remote_complete(config,*,now=time.time):
    root=private(config['root'],directory=True)
    report=read(root/'status.json')
    if report.get('status')!='complete' or report.get('remote_verified') is not True or inspect_status(config,now=now)['status']!='ok':
        raise ValueError('A current local backup is required.')
    # The backup receipt explicitly records successful Drive sync/check for both
    # repositories. A local-only success can never certify an off-host copy.
    result={key:report[key] for key in ('last_success','state_snapshot','recovery_snapshot')}
    result['verified_at']=now()
    write(root,'remote-status.json',result)
    return {'status':'complete'}


def issues(config,*,service_result,now=time.time):
    result=inspect_status(config,now=now)['issues']
    if service_result!='success':result.append('backup_service_failed')
    try:
        root=private(config['root'],directory=True)
        report=read(root/'remote-status.json');current=now()
        for key in ('last_success','verified_at'):
            value=report[key]
            if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=current:
                raise ValueError
        if report['last_success']>report['verified_at']:raise ValueError
        for key in ('state_snapshot','recovery_snapshot'):
            if not re.fullmatch('[0-9a-f]{64}',report[key]):raise ValueError
        if current-report['last_success']>7200:result.append('remote_backup_stale')
    except Exception:result.append('remote_backup_unknown')
    return sorted(set(result))


def can_rearm(config,*,service_result,active_state,now=time.time):
    if service_result!='success' or active_state!='inactive':return False
    try:
        if issues(config,service_result=service_result,now=now):return False
        root=private(config['root'],directory=True)
        local=read(root/'status.json');remote=read(root/'remote-status.json')
        return local.get('status')=='complete' and all(local[key]==remote[key]
            for key in ('last_success','state_snapshot','recovery_snapshot'))
    except Exception:return False


def notify(config,problems,send,*,recovered=False,now=time.time):
    root=private(config['root'],directory=True)
    if any(key not in LABELS for key in problems):raise ValueError('Unknown issue.')
    fd=os.open(root/'alert.lock',os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        path=root/'alert-status.json'
        state=read(path) if path.exists() else {'active':False,'delivery':'none'}
        if not problems:
            if state.get('active') and not recovered:return state
            state={'active':False,'delivery':'none','at':now()}
            write(root,path.name,state);return state
        if state.get('active') and state.get('delivery') in ('sending','accepted','uncertain','rejected'):
            return state
        state={'active':True,'delivery':'pending','issues':problems,'at':now()}
        write(root,path.name,state)
        message=EmailMessage()
        message['From']='SocialCli <access@socialcli.app>'
        message['Subject']='SocialCli — Backup needs attention'
        message.set_content('SocialCli backup monitoring detected an issue.\n\n'+
            '\n'.join('- '+LABELS[key] for key in problems)+
            '\n\nCheck the private backup service and Google Drive copy on the server. '
            'This alert contains no customer data or recovery keys. '
            'One notification is sent per incident; monitoring continues automatically.\n')
        def fence():
            state['delivery']='sending';write(root,path.name,state);return True
        try:
            result=send(message,fence)
            state['delivery']=result if result in ('accepted','retry','rejected','uncertain') else 'uncertain'
        except Exception:
            state['delivery']='uncertain' if state['delivery']=='sending' else 'retry'
        write(root,path.name,state)
        return state


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config',type=Path)
    parser.add_argument('--remote-complete',action='store_true')
    parser.add_argument('--test',action='store_true',help='Send one English test using a separate durable receipt')
    args=parser.parse_args();os.umask(0o077)
    try:
        config=read(args.config)
        if args.remote_complete:result=remote_complete(config)
        else:
            # This is the existing, tested application transport, installed beside this script.
            from saas_mail import MailSettings,SMTPMailer,mailbox
            recipient=mailbox(config['alert_recipient'])
            values={}
            for line in private(config['secret_files']['auth.env']).read_text().splitlines():
                if not line.strip() or line.lstrip().startswith('#') or '=' not in line:continue
                key,value=line.split('=',1)
                if key.startswith('GOTRUE_SMTP_'):
                    values[key]=shlex.split(value)[0] if value.startswith(('"',"'")) else value
            mailer=SMTPMailer(MailSettings(values['GOTRUE_SMTP_HOST'],int(values['GOTRUE_SMTP_PORT']),
                values['GOTRUE_SMTP_USER'],values['GOTRUE_SMTP_PASS']))
            def send(message,fence):
                message['To']=recipient
                if args.test:
                    message.replace_header('Subject','SocialCli — Backup alert delivery test')
                    message.set_content('This is an authorized test of SocialCli backup notifications. '
                        'No backup failure is being reported. All future automated notifications are in English. '
                        'No customer data or recovery keys are included.\n')
                return mailer.send(recipient,message,fence)
            if args.test:
                root=private(config['root'],directory=True)/'alert-test-en'
                root.mkdir(mode=0o700,exist_ok=True)
                result=notify({**config,'root':str(root)},['remote_backup_unknown'],send)
            else:
                service=subprocess.run(['systemctl','show','socialcli-saas-candidate-backup.service',
                    '--property=Result','--property=ActiveState'],capture_output=True,text=True,timeout=10,check=True).stdout
                service=dict(line.split('=',1) for line in service.splitlines() if '=' in line)
                problems=issues(config,service_result=service.get('Result','unknown'))
                recovered=can_rearm(config,service_result=service.get('Result','unknown'),
                    active_state=service.get('ActiveState','unknown'))
                result=notify(config,problems,send,recovered=recovered)
                result={**result,'status':'attention' if problems else 'ok'}
        print(json.dumps(result,sort_keys=True))
        if result.get('delivery') in ('retry','rejected','uncertain','sending'):raise SystemExit(1)
    except Exception:
        print('{"status":"attention","error":"backup_monitor_failed"}')
        raise SystemExit(1)


if __name__=='__main__':main()
