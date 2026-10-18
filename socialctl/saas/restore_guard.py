"""Prepare an isolated restore; never write over a running or existing database.

Manifest provenance is an explicit operator trust boundary. The operator must
export from the still-current authoritative service, never regenerate timestamps
from a backup. JSON labels alone cannot establish that provenance. No command
contacts a provider or the identity service. Identity cleanup is separately applied
in the isolated PostgreSQL restore and explicitly attested before release.
"""
from __future__ import annotations
import argparse
from contextlib import closing
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import time

from socialctl.saas.backup import snapshot, verify_snapshot, BackupError, _read
from socialctl.connection_service.store import Store
from socialctl.saas.store import SaaSStore


class RestoreError(ValueError):
    """Fixed operator diagnostics without credential or identity data."""


def checked_manifest(manifest, now):
    try:
        if not isinstance(manifest,dict) or manifest['schema']!=1 or manifest['source']!='current-authority':
            raise ValueError()
        generated=manifest['generated_at']
        if type(generated) not in (int,float) or not math.isfinite(generated) or not now-300<=generated<=now+60:
            raise ValueError()
        tombstones=manifest['tombstones'];pending=manifest['pending_revocation_ids']
        if not isinstance(tombstones,list) or not isinstance(pending,list):raise ValueError()
        if len(tombstones)>1000000 or len(pending)>1000000:raise ValueError()
        for row in tombstones:
            if (not isinstance(row,dict) or row['kind'] not in {'user','grant'}
                    or not isinstance(row['id'],str) or not 1<=len(row['id'])<=512
                    or any(ord(c)<32 for c in row['id'])
                    or type(row['requested']) not in (int,float) or not math.isfinite(row['requested'])
                    or row['requested']>generated+60):raise ValueError()
        if any(not isinstance(value,str) or not 1<=len(value)<=512 for value in pending):raise ValueError()
        return manifest
    except (KeyError,TypeError,ValueError):
        raise RestoreError('A fresh manifest from the current authority is required; backup-derived or unavailable authority cannot release a restore.') from None


def export_manifest(database, *, now):
    """Read-only; caller attests this path is the current authority, not a backup."""
    try:
        with closing(_read(database)) as db:
            db.row_factory=sqlite3.Row
            db.execute('BEGIN')
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='saas_restore_guard'").fetchone():
                guard=db.execute('SELECT state FROM saas_restore_guard WHERE singleton=1').fetchone()
                if not guard or guard['state']!='ready':raise RestoreError('A quarantined restore is not an authority.')
            tombstones=[dict(row) for row in db.execute('SELECT kind,id,requested FROM saas_deletion_tombstones ORDER BY kind,id')]
            pending=[row[0] for row in db.execute("SELECT id FROM saas_google_revocations WHERE state='pending' ORDER BY id")]
        return {'schema':1,'source':'current-authority','generated_at':now,'tombstones':tombstones,'pending_revocation_ids':pending}
    except sqlite3.Error:
        raise RestoreError('Current deletion authority is unavailable; keep the restore quarantined.') from None


def _wipe_access(db, *, quarantine):
    kinds=['saas-session','saas-login','saas-pair','saas-installation','saas-capability',
           'saas-candidate','authorization','connection',
           'saas-meta-webhook-binding','saas-meta-webhook-event']
    if quarantine:kinds+=['saas-grant','google-revocation']
    db.execute('DELETE FROM records WHERE kind IN ('+','.join('?' for _ in kinds)+')',kinds)
    db.execute('UPDATE saas_users SET session_version=session_version+1')
    if quarantine:
        db.execute('DELETE FROM saas_slots')
        db.execute('DELETE FROM saas_google_revocations')
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='legacy_allowance'").fetchone():
        db.execute('DELETE FROM legacy_allowance')


def _apply(vault,db,manifest):
    _wipe_access(db,quarantine=manifest is None)
    if manifest is None:return
    pending=set(manifest['pending_revocation_ids'])
    for row in db.execute('SELECT id FROM saas_google_revocations').fetchall():
        if row['id'] not in pending:
            vault.delete(db,'google-revocation',row['id'])
            db.execute('DELETE FROM saas_google_revocations WHERE id=?',(row['id'],))
        else:
            db.execute("UPDATE saas_google_revocations SET state='pending',lease=NULL,lease_until=0,next_attempt=0 WHERE id=?",(row['id'],))
    tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for item in manifest['tombstones']:
        kind,record_id=item['kind'],item['id']
        if kind=='grant':
            vault.delete(db,'saas-grant',record_id)
            db.execute('DELETE FROM saas_slots WHERE grant_id=?',(record_id,))
        else:
            billing=db.execute('SELECT customer_id FROM saas_billing WHERE user_id=?',(record_id,)).fetchone()
            if billing and billing['customer_id']:
                for row in db.execute('SELECT account_id,mode,event_id FROM saas_billing_events WHERE customer_id=?',(billing['customer_id'],)).fetchall():
                    vault.delete(db,'saas-stripe-event',':'.join(row))
                db.execute('DELETE FROM saas_billing_events WHERE customer_id=?',(billing['customer_id'],))
            db.execute('DELETE FROM records WHERE (kind,id) IN (SELECT kind,id FROM saas_record_owners WHERE user_id=?)',(record_id,))
            vault.delete(db,'saas-user',record_id)
            for table in ('saas_users','saas_entitlements','saas_billing','saas_slots','saas_brands','saas_reminders','saas_deletions','saas_media_allocations'):
                if table in tables:
                    db.execute('DELETE FROM '+table+' WHERE '+('id' if table=='saas_users' else 'user_id')+'=?',(record_id,))
        db.execute('INSERT OR REPLACE INTO saas_deletion_tombstones VALUES(?,?,?)',(kind,record_id,item['requested']))
    db.execute('DELETE FROM saas_brands WHERE NOT EXISTS (SELECT 1 FROM saas_slots WHERE saas_slots.user_id=saas_brands.user_id)')


def prepare_restore(source,destination,keys,manifest,*,now,snapshot_created):
    if type(snapshot_created) not in (int,float) or not math.isfinite(snapshot_created) or not now-6*86400<=snapshot_created<=now+60:
        raise RestoreError('Only an independently dated snapshot no older than six days can enter this restore workflow.')
    if manifest is not None:checked_manifest(manifest,now)
    source,destination=Path(source),Path(destination)
    if destination.exists() or destination.is_symlink() or source.resolve()==destination.resolve():
        raise RestoreError('Restore destination must be new and isolated; existing databases are never overwritten.')
    temporary=None
    try:
        destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if destination.parent.stat().st_mode & 0o077:raise RestoreError('Use a private restore directory with mode 0700.')
        fd,name=tempfile.mkstemp(prefix='.restore-',dir=destination.parent);os.close(fd)
        temporary=Path(name);temporary.unlink()
        snapshot(source,temporary,keys)
        # Remove the unprepared marker only inside the unpublished offline copy;
        # re-establish a blocked marker atomically with deletion suppression.
        with sqlite3.connect(temporary) as db:db.execute('DROP TABLE IF EXISTS saas_restore_guard')
        vault=Store(temporary,keys);SaaSStore(vault,keys)
        state='identity_cleanup_required' if manifest is not None else 'authority_unavailable'
        with vault.transaction() as db:
            _apply(vault,db,manifest)
            db.execute('CREATE TABLE saas_restore_guard (singleton INTEGER PRIMARY KEY CHECK(singleton=1),state TEXT NOT NULL,prepared REAL NOT NULL,snapshot_created REAL NOT NULL)')
            db.execute('INSERT INTO saas_restore_guard VALUES(1,?,?,?)',(state,now,snapshot_created))
        with sqlite3.connect(temporary) as db:
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            db.execute('PRAGMA journal_mode=DELETE')
        verify_snapshot(temporary,keys)
        with temporary.open('rb') as stream:os.fsync(stream.fileno())
        os.link(temporary,destination)
        return {'status':state,'identity_cleanup_verified':False,'access_credentials_removed':True}
    except (OSError,sqlite3.Error,BackupError):
        raise RestoreError('Restore preparation failed; source preserved and no usable restore released.') from None
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
            for suffix in ('-wal','-shm','-journal'):Path(str(temporary)+suffix).unlink(missing_ok=True)


def release_restore(database,keys,manifest,*,now,identity_cleanup_confirmed=False):
    checked_manifest(manifest,now)
    if not identity_cleanup_confirmed:
        raise RestoreError('Apply the generated cleanup to the isolated Auth restore, then explicitly attest completion.')
    database=Path(database)
    if not database.is_file():raise RestoreError('A prepared isolated restore is required.')
    # Reject a live/unprepared database read-only, before Store can migrate,
    # change journal mode, or alter its filesystem permissions.
    try:
        with closing(_read(database)) as db:
            row=db.execute('SELECT state FROM saas_restore_guard WHERE singleton=1').fetchone()
            if row is None or row[0] not in {'identity_cleanup_required','authority_unavailable'}:
                raise RestoreError('Only a quarantined offline restore can be released.')
    except sqlite3.Error:
        raise RestoreError('A prepared isolated restore is required; live databases cannot be released here.') from None
    verify_snapshot(database,keys)
    vault=Store(database,keys)
    try:
        with vault.transaction() as db:
            row=db.execute('SELECT * FROM saas_restore_guard WHERE singleton=1').fetchone()
            if row is None or row['state'] not in {'identity_cleanup_required','authority_unavailable'}:
                raise RestoreError('Only a quarantined offline restore can be released.')
            if row['snapshot_created']<now-6*86400:
                raise RestoreError('The staged snapshot is too old; obtain a current recovery point.')
            _apply(vault,db,manifest)
            for record_id in manifest['pending_revocation_ids']:
                if vault.get(db,'google-revocation',record_id) is None or not db.execute('SELECT id FROM saas_google_revocations WHERE id=?',(record_id,)).fetchone():
                    raise RestoreError('Current revocation jobs are missing. Recover the authoritative encrypted queue before release.')
            db.execute("UPDATE saas_restore_guard SET state='ready' WHERE singleton=1")
    except sqlite3.Error:
        raise RestoreError('A prepared isolated restore is required; live databases cannot be released here.') from None
    return {'status':'ready','identity_cleanup_verified':False,'identity_cleanup_operator_attested':True}


def auth_cleanup_sql(manifest,*,now):
    checked_manifest(manifest,now)
    # All old Auth sessions are invalidated even for owners not deleted. This
    # script runs only in the isolated restored Auth DB with ON_ERROR_STOP.
    lines=['BEGIN;','DELETE FROM auth.refresh_tokens;','DELETE FROM auth.sessions;']
    users=sorted({item['id'] for item in manifest['tombstones'] if item['kind']=='user'})
    if users:
        values=','.join("'"+value.replace("'","''")+"'" for value in users)
        lines.append('DELETE FROM auth.users WHERE id IN ('+values+');')
    lines.append('COMMIT;')
    return '\n'.join(lines)+'\n'


def _write_private(path,value):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:
        stream.write(value);stream.flush();os.fsync(stream.fileno())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='operation',required=True)
    export=sub.add_parser('export');export.add_argument('database',type=Path);export.add_argument('destination',type=Path)
    export.add_argument('--current-authority-confirmed',action='store_true',required=True)
    prepare=sub.add_parser('prepare');prepare.add_argument('database',type=Path);prepare.add_argument('destination',type=Path)
    prepare.add_argument('--manifest',type=Path);prepare.add_argument('--snapshot-created',type=float,required=True)
    auth=sub.add_parser('auth-sql');auth.add_argument('manifest',type=Path);auth.add_argument('destination',type=Path)
    release=sub.add_parser('release');release.add_argument('database',type=Path);release.add_argument('--manifest',type=Path,required=True)
    release.add_argument('--identity-cleanup-confirmed',action='store_true',required=True)
    args=parser.parse_args();now=time.time()
    keys=[v.strip().encode() for v in os.environ.get('SOCIALCLI_ENCRYPTION_KEYS','').split(',') if v.strip()]
    try:
        manifest=json.loads(args.manifest.read_text()) if getattr(args,'manifest',None) else None
        if args.operation=='export':
            _write_private(args.destination,json.dumps(export_manifest(args.database,now=now)))
            result={'status':'manifest_exported','provenance':'operator_attested_current_authority'}
        elif args.operation=='prepare':result=prepare_restore(args.database,args.destination,keys,manifest,now=now,snapshot_created=args.snapshot_created)
        elif args.operation=='auth-sql':
            _write_private(args.destination,auth_cleanup_sql(manifest,now=now));result={'status':'auth_sql_written','identity_cleanup_verified':False}
        else:result=release_restore(args.database,keys,manifest,now=now,identity_cleanup_confirmed=args.identity_cleanup_confirmed)
    except (RestoreError,BackupError,OSError,ValueError,TypeError):
        parser.exit(1,'Restore guard failed; keep all restored services offline and review authority, freshness, keys and Auth cleanup.\n')
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':main()
