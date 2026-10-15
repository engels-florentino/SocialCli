"""Durable customer Google revocation; credentials are never usable by the broker.

The encrypted queue keeps only the token required to finish revocation, until
Google confirms completion. All network requests occur outside SQLite locks.
"""
import secrets
from socialctl.saas.budget import using_budget

TOKEN_KIND = 'google-revocation'


def tombstone(db, kind, record_id, now):
    db.execute('INSERT OR REPLACE INTO saas_deletion_tombstones VALUES(?,?,?)',
               (kind, record_id, now))


def reserve(db, job_id, user_id, now):
    """Fence reconnect while an already-started provider request is unresolved."""
    tombstone(db, 'grant', job_id, now)
    db.execute('INSERT OR IGNORE INTO saas_google_revocations(id,user_id,requested) VALUES(?,?,?)',
               (job_id, user_id, now))


def enqueue(metadata, db, grant_id, payload, now):
    """Called in the same transaction that removes local access."""
    tombstone(db, 'grant', grant_id, now)
    if payload.get('platform') != 'youtube':
        return None
    grant = payload.get('grant') or {}
    token = grant.get('refresh_token') or grant.get('access_token')
    if not token:
        return None
    existing=db.execute('SELECT state FROM saas_google_revocations WHERE id=?', (grant_id,)).fetchone()
    if existing:
        if existing['state']=='pending' and metadata.vault.get(db,TOKEN_KIND,grant_id) is None:
            metadata.vault.put(db,TOKEN_KIND,grant_id,{'token':token},expires=253402300799)
            db.execute('UPDATE saas_google_revocations SET next_attempt=0 WHERE id=?',(grant_id,))
        return grant_id
    metadata.vault.put(db, TOKEN_KIND, grant_id, {'token': token}, expires=253402300799)
    db.execute('INSERT INTO saas_google_revocations(id,user_id,requested) VALUES(?,?,?)',
               (grant_id, payload['user_id'], now))
    return grant_id


def export_current_deletion_manifest(metadata, clock):
    """Export from the current authoritative service, NEVER from a restored backup."""
    with metadata.vault.read_transaction() as db:
        rows=[dict(row) for row in db.execute('SELECT kind,id,requested FROM saas_deletion_tombstones ORDER BY kind,id')]
        pending=[row[0] for row in db.execute("SELECT id FROM saas_google_revocations WHERE state='pending' ORDER BY id")]
    return {'schema': 1, 'source': 'current-authority', 'generated_at': clock(), 'tombstones': rows, 'pending_revocation_ids':pending}


def run_revocations(metadata, providers, clock, limit=100, *, user_id=None, job_id=None, budget=None):
    vault=metadata.vault
    where="state='pending' AND next_attempt<=? AND lease_until<=?"
    args=[clock(),clock()]
    for field,value in (('user_id',user_id),('id',job_id)):
        if value is not None:
            where+=' AND '+field+'=?';args.append(value)
    with vault.read_transaction() as db:
        jobs=[dict(row) for row in db.execute('SELECT * FROM saas_google_revocations WHERE '+where+' ORDER BY requested LIMIT ?',(*args,limit))]
    completed=0
    with using_budget(budget,providers):
        for job in jobs:
            if budget and budget.expired():break
            lease=secrets.token_hex(20)
            with vault.transaction() as db:
                claimed=db.execute("UPDATE saas_google_revocations SET lease=?,lease_until=? WHERE id=? AND state='pending' AND lease_until<=? AND next_attempt<=?",(lease,clock()+300,job['id'],clock(),clock())).rowcount
                if not claimed:continue
                record=vault.get(db,TOKEN_KIND,job['id'])
            try:
                if record is None or providers is None:
                    raise ValueError('google_revocation_pending')
                providers.revoke_google(record['payload']['token'])
            except Exception:
                with vault.transaction() as db:
                    db.execute('UPDATE saas_google_revocations SET lease=NULL,lease_until=0,attempts=attempts+1,next_attempt=? WHERE id=? AND lease=?',(clock()+300,job['id'],lease))
            else:
                with vault.transaction() as db:
                    updated=db.execute("UPDATE saas_google_revocations SET state='complete',lease=NULL,lease_until=0,attempts=attempts+1 WHERE id=? AND lease=?",(job['id'],lease)).rowcount
                    if updated:
                        vault.delete(db,TOKEN_KIND,job['id']);completed+=1
    with vault.read_transaction() as db:
        pending=db.execute("SELECT count(*) FROM saas_google_revocations WHERE state='pending'").fetchone()[0]
        due=db.execute("SELECT count(*) FROM saas_google_revocations WHERE state='pending' AND next_attempt<=?",(clock(),)).fetchone()[0]
        overdue=db.execute("SELECT count(*) FROM saas_google_revocations WHERE state='pending' AND requested<=?",(clock()-86400,)).fetchone()[0]
        awaiting=db.execute("SELECT count(*) FROM saas_google_revocations q WHERE state='pending' AND NOT EXISTS (SELECT 1 FROM records r WHERE r.kind='google-revocation' AND r.id=q.id)").fetchone()[0]
    return {'completed':completed,'pending':pending,'due':due,'overdue':overdue,
            'awaiting_credentials':awaiting,'operator_review_required':bool(awaiting),
            'budget_exhausted':bool(budget and budget.expired())}


def status(metadata, job_id):
    if job_id is None:return 'not_applicable'
    with metadata.vault.read_transaction() as db:
        row=db.execute('SELECT state FROM saas_google_revocations WHERE id=?',(job_id,)).fetchone()
    return row['state'] if row else 'pending'
