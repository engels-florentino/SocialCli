"""Private, read-only operations report. Never contacts providers or sends mail."""
from contextlib import closing
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import time

from cryptography.fernet import Fernet, MultiFernet, InvalidToken
from socialctl.saas.budget import WorkBudget
from socialctl.saas.renewal import state as renewal_state


def heartbeat_path(database):
    return Path(str(database)+'.maintenance.json')


def write_heartbeat(database,status,now):
    if status not in {'running','complete','attention','failed'} or not math.isfinite(now):
        raise ValueError('Invalid maintenance status.')
    target=heartbeat_path(database)
    fd,name=tempfile.mkstemp(prefix='.maintenance-',dir=target.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump({'status':status,'at':now},stream)
            stream.flush();os.fsync(stream.fileno())
        os.replace(name,target)
        directory=os.open(target.parent,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def inspect_status(database,keys,*,now=None):
    now=time.time() if now is None else now
    issues=[];counts={};budget=WorkBudget(5)
    try:
        with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
            db.row_factory=sqlite3.Row
            db.set_progress_handler(lambda:int(budget.expired()),1000)
            db.execute('BEGIN')
            if db.execute('SELECT max(version) FROM saas_schema').fetchone()[0]!=7:
                return {'status':'attention','issues':['schema_incompatible'],'counts':{}}
            queries={
                'billing_events_due':("SELECT count(*) FROM saas_billing_events WHERE state='pending' AND next_attempt<=?",(now,)),
                'billing_events_overdue':("SELECT count(*) FROM saas_billing_events WHERE state='pending' AND received<=?",(now-900,)),
                'billing_events_review':("SELECT count(*) FROM saas_billing_events WHERE state='review'",()),
                'billing_review':("SELECT count(*) FROM saas_billing WHERE review!=''",()),
                'billing_overdue':("SELECT count(*) FROM saas_billing b JOIN saas_users u ON u.id=b.user_id WHERE b.customer_id IS NOT NULL AND b.sync_due<=? AND u.deleting=0",(now-900,)),
                'deletions_pending':("SELECT count(*) FROM saas_deletions WHERE state='pending'",()),
                'deletions_overdue':("SELECT count(*) FROM saas_deletions WHERE state='pending' AND requested<=?",(now-900,)),
                'reminders_due':("SELECT count(*) FROM saas_reminders WHERE state='pending' AND next_attempt<=?",(now,)),
                'reminders_overdue':("SELECT count(*) FROM saas_reminders WHERE state='pending' AND next_attempt<=?",(now-900,)),
                'reminders_uncertain':("SELECT count(*) FROM saas_reminders WHERE state='uncertain' OR (state='sending' AND lease_until<=?)",(now,)),
                'reminders_rejected':("SELECT count(*) FROM saas_reminders WHERE state='rejected'",()),
            }
            for name,(sql,params) in queries.items():
                counts[name]=db.execute(sql,params).fetchone()[0]
            cipher=MultiFernet([Fernet(key) for key in keys])
            for row in db.execute("SELECT * FROM records WHERE kind='saas-grant' AND expires>?",(now,)):
                if budget.expired():raise ValueError()
                envelope=json.loads(cipher.decrypt(row['payload']))
                if any(envelope[key]!=row[key] for key in ('kind','id','expires','credential_hash')):
                    raise ValueError()
                current=renewal_state(envelope['payload'],now)
                key='renewal_'+current
                counts[key]=counts.get(key,0)+1
    except (OSError,sqlite3.Error,ValueError,TypeError,KeyError,AttributeError,InvalidToken):
        return {'status':'attention','issues':['database_unavailable'],'counts':{}}
    issues.extend(key for key,count in counts.items() if count and
        (key.endswith(('_overdue','_review','_uncertain','_rejected')) or key=='renewal_configuration_error'))
    try:
        with heartbeat_path(database).open('rb') as stream:
            heartbeat=json.loads(stream.read(4096))
        status,at=heartbeat['status'],heartbeat['at']
        if status not in {'running','complete','attention','failed'} or type(at) not in (int,float) or not math.isfinite(at) or at>now+60:
            raise ValueError()
        if now-at>900:issues.append('maintenance_stale')
        elif status=='running' and now-at>240:issues.append('maintenance_stalled')
        elif status in {'attention','failed'}:issues.append('maintenance_'+status)
    except (OSError,ValueError,TypeError,KeyError):
        issues.append('maintenance_unknown')
    return {'status':'attention' if issues else 'ok','issues':issues,'counts':counts}


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database',type=Path)
    args=parser.parse_args()
    keys=[key.strip().encode() for key in os.environ.get('SOCIALCLI_ENCRYPTION_KEYS','').split(',') if key.strip()]
    report=inspect_status(args.database,keys)
    print(json.dumps(report,sort_keys=True))
    raise SystemExit(0 if report['status']=='ok' else 1)


if __name__=='__main__':main()
