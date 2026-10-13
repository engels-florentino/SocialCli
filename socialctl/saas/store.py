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
            if version > 4:
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
        db.execute('INSERT OR REPLACE INTO saas_rates VALUES(?,?,?)',(hashed,bucket,count+1))
        return True
