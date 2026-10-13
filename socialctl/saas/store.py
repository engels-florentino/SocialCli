"""Versioned metadata alongside the existing encrypted credential vault.

Only small local transactions here. HTTP requests belong outside these scopes.
Email and provider credentials live in encrypted records, never plain SQL rows.
"""
import hashlib
from socialctl.connection_service.store import Store


class SaaSStore:
    def __init__(self, vault: Store):
        self.vault = vault
        with vault.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS saas_schema (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
            version = db.execute('SELECT coalesce(max(version),0) FROM saas_schema').fetchone()[0]
            if version > 1:
                raise ValueError('SaaS database is newer than this release; keep commercial access closed')
            if version < 1:
                db.execute('CREATE TABLE saas_users (id TEXT PRIMARY KEY, created REAL NOT NULL, suspended INTEGER NOT NULL DEFAULT 0, deleting INTEGER NOT NULL DEFAULT 0, session_version INTEGER NOT NULL DEFAULT 0)')
                db.execute('CREATE TABLE saas_rates (key TEXT PRIMARY KEY, bucket INTEGER NOT NULL, count INTEGER NOT NULL)')
                db.execute('INSERT INTO saas_schema(version) VALUES(1)')

    def user(self, db, user_id):
        return db.execute('SELECT * FROM saas_users WHERE id=?',(user_id,)).fetchone()

    def ensure_user(self, db, user, now):
        db.execute('INSERT OR IGNORE INTO saas_users(id,created) VALUES(?,?)',(user.id,now))
        self.vault.put(db,'saas-user',user.id,{'email':user.email},expires=253402300799)

    def admit(self, db, key, now, *, limit, seconds=60):
        hashed = hashlib.sha256(key.encode()).hexdigest()
        bucket = int(now // seconds)
        row = db.execute('SELECT bucket,count FROM saas_rates WHERE key=?',(hashed,)).fetchone()
        count = row['count'] if row and row['bucket'] == bucket else 0
        if count >= limit:
            return False
        db.execute('INSERT OR REPLACE INTO saas_rates VALUES(?,?,?)',(hashed,bucket,count+1))
        return True
