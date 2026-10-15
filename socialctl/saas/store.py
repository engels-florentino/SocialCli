"""Versioned metadata alongside the existing encrypted credential vault.

Only small local transactions here. HTTP requests belong outside these scopes.
Email and provider credentials live in encrypted records, never plain SQL rows.
"""
import hashlib
from socialctl.connection_service.store import Store
from socialctl.saas.access import AccessControl


class SaaSStore:
    def __init__(self, vault: Store, eligibility_keys: list[bytes]):
        self.vault = vault
        self.eligibility_keys = eligibility_keys
        with vault.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS saas_schema (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
            version = db.execute('SELECT coalesce(max(version),0) FROM saas_schema').fetchone()[0]
            if version > 8:
                raise ValueError('SaaS database is newer than this release; keep commercial access closed')
            if version < 1:
                db.execute('CREATE TABLE saas_users (id TEXT PRIMARY KEY, created REAL NOT NULL, suspended INTEGER NOT NULL DEFAULT 0, deleting INTEGER NOT NULL DEFAULT 0, session_version INTEGER NOT NULL DEFAULT 0)')
                db.execute('CREATE TABLE saas_rates (key TEXT PRIMARY KEY, bucket INTEGER NOT NULL, count INTEGER NOT NULL)')
                db.execute('INSERT INTO saas_schema(version) VALUES(1)')
            if version < 2:
                db.execute("CREATE TABLE saas_entitlements (user_id TEXT PRIMARY KEY,trial_start REAL,trial_end REAL,subscription_status TEXT NOT NULL DEFAULT '',paid_until REAL NOT NULL DEFAULT 0)")
                db.execute('CREATE TABLE saas_trial_markers (marker TEXT PRIMARY KEY,trial_start REAL NOT NULL,trial_end REAL NOT NULL,retain_until REAL NOT NULL)')
                db.execute('INSERT INTO saas_schema(version) VALUES(2)')
            if version < 3:
                db.execute('CREATE TABLE saas_brands (user_id TEXT PRIMARY KEY,id TEXT NOT NULL UNIQUE,label TEXT NOT NULL)')
                db.execute('CREATE TABLE saas_slots (user_id TEXT NOT NULL,platform TEXT NOT NULL,grant_id TEXT NOT NULL UNIQUE,PRIMARY KEY(user_id,platform))')
                db.execute('INSERT INTO saas_schema(version) VALUES(3)')
            if version < 4:
                db.execute("CREATE TABLE saas_billing (user_id TEXT PRIMARY KEY,customer_id TEXT UNIQUE,subscription_id TEXT UNIQUE,customer_key TEXT NOT NULL,customer_started REAL NOT NULL,checkout_id TEXT,attempt TEXT,attempt_started REAL,lease TEXT,lease_until REAL NOT NULL DEFAULT 0,sync_due REAL NOT NULL DEFAULT 0,cancel_at_period_end INTEGER NOT NULL DEFAULT 0,review TEXT NOT NULL DEFAULT '')")
                db.execute("CREATE TABLE saas_billing_events (account_id TEXT NOT NULL,mode TEXT NOT NULL,event_id TEXT NOT NULL,body_hash TEXT NOT NULL,customer_id TEXT,type TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',received REAL NOT NULL,next_attempt REAL NOT NULL DEFAULT 0,PRIMARY KEY(account_id,mode,event_id))")
                db.execute('INSERT INTO saas_schema(version) VALUES(4)')
            if version < 5:
                db.execute("CREATE TABLE saas_deletions (user_id TEXT PRIMARY KEY,requested REAL NOT NULL,state TEXT NOT NULL DEFAULT 'pending',billing_done INTEGER NOT NULL DEFAULT 0,next_attempt REAL NOT NULL DEFAULT 0,lease TEXT,lease_until REAL NOT NULL DEFAULT 0)")
                db.execute('INSERT INTO saas_schema(version) VALUES(5)')
            if version < 6:
                db.execute("CREATE TABLE saas_reminders (id TEXT PRIMARY KEY,user_id TEXT NOT NULL,trial_end REAL NOT NULL,milestone INTEGER NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,state TEXT NOT NULL DEFAULT 'pending',next_attempt REAL NOT NULL DEFAULT 0,lease TEXT,lease_until REAL NOT NULL DEFAULT 0,UNIQUE(user_id,trial_end,milestone))")
                db.execute('CREATE INDEX saas_reminders_due ON saas_reminders(state,next_attempt,trial_end)')
                db.execute('INSERT INTO saas_schema(version) VALUES(6)')
            if version < 7:
                from socialctl.saas.permissions import policy, PLATFORMS
                for row in db.execute("SELECT id FROM records WHERE kind='saas-installation'").fetchall():
                    record = self.vault.get(db, 'saas-installation', row['id'])
                    if 'platform_access' not in record['payload']:
                        record['payload']['platform_access'] = policy(PLATFORMS, source='legacy_approval')
                        self.vault.put(db, 'saas-installation', row['id'], record['payload'],
                                       expires=record['expires'], credential_hash=record['credential_hash'])
                db.execute('INSERT INTO saas_schema(version) VALUES(7)')

            if version < 8:
                db.execute('CREATE TABLE IF NOT EXISTS saas_media_allocations (id TEXT PRIMARY KEY,user_id TEXT NOT NULL,size INTEGER NOT NULL,expires REAL NOT NULL)')
                db.execute('CREATE INDEX IF NOT EXISTS saas_media_allocations_owner ON saas_media_allocations(user_id,expires)')
                for row in db.execute("SELECT kind,id FROM records WHERE kind LIKE 'saas-%' OR kind='authorization'").fetchall():
                    record = vault.get(db, row['kind'], row['id'])
                    vault.index_owner(db, row['kind'], row['id'], record['payload'])
                db.execute("ALTER TABLE saas_billing_events ADD COLUMN semantic_hash TEXT NOT NULL DEFAULT ''")
                from socialctl.saas.webhooks import semantic_hash
                for event in db.execute('SELECT account_id,mode,event_id FROM saas_billing_events').fetchall():
                    record=vault.get(db,'saas-stripe-event',':'.join(event))
                    if record:
                        db.execute('UPDATE saas_billing_events SET semantic_hash=? WHERE account_id=? AND mode=? AND event_id=?',(semantic_hash(record['payload']),*event))
                        state=db.execute('SELECT state,received FROM saas_billing_events WHERE account_id=? AND mode=? AND event_id=?',tuple(event)).fetchone()
                        if state['state'] in {'done','ignored'}:
                            vault.delete(db,'saas-stripe-event',':'.join(event))
                        else:
                            vault.put(db,'saas-stripe-event',':'.join(event),record['payload'],expires=min(record['expires'],state['received']+7*86400))
                db.execute('ALTER TABLE saas_rates ADD COLUMN expires REAL NOT NULL DEFAULT 0')
                db.execute('INSERT INTO saas_schema(version) VALUES(8)')

    def user(self, db, user_id):
        return db.execute('SELECT * FROM saas_users WHERE id=?',(user_id,)).fetchone()

    def ensure_user(self, db, user, now):
        db.execute('INSERT OR IGNORE INTO saas_users(id,created) VALUES(?,?)',(user.id,now))
        self.vault.put(db,'saas-user',user.id,{'email':user.email},expires=253402300799)
        AccessControl(self).restore_trial(db,user.id,user.email,now)

    def admit(self, db, key, now, *, limit, seconds=60):
        hashed = hashlib.sha256(key.encode()).hexdigest()
        bucket = int(now // seconds)
        row = db.execute('SELECT bucket,count FROM saas_rates WHERE key=?',(hashed,)).fetchone()
        count = row['count'] if row and row['bucket'] == bucket else 0
        if count >= limit:
            return False
        db.execute('INSERT OR REPLACE INTO saas_rates VALUES(?,?,?,?)',(hashed,bucket,count+1,(bucket+1)*seconds))
        return True
