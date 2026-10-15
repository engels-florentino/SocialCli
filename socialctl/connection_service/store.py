"""Private, transactionally updated credential records with authenticated encryption."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class Store:
    def __init__(self, path: Path, keys: bytes | list[bytes]):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        self.keys = tuple(key.encode() if isinstance(key,str) else key
                          for key in (keys if isinstance(keys,list) else [keys]))
        self.cipher = MultiFernet([Fernet(key) for key in self.keys])
        with sqlite3.connect(self.path, timeout=35) as db:
            db.execute('PRAGMA journal_mode=WAL')
        with self.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT NOT NULL, id TEXT NOT NULL, expires REAL NOT NULL, credential_hash TEXT NOT NULL, payload BLOB NOT NULL, PRIMARY KEY(kind,id))')
            db.execute('CREATE TABLE IF NOT EXISTS saas_record_owners (user_id TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL, PRIMARY KEY(kind,id))')
            db.execute('CREATE INDEX IF NOT EXISTS saas_record_owners_user ON saas_record_owners(user_id,kind,id)')
            db.execute('CREATE TRIGGER IF NOT EXISTS records_owner_delete AFTER DELETE ON records BEGIN DELETE FROM saas_record_owners WHERE kind=OLD.kind AND id=OLD.id; END')
            db.execute('CREATE TABLE IF NOT EXISTS saas_record_links (kind TEXT NOT NULL,id TEXT NOT NULL,installation_id TEXT,grant_id TEXT,PRIMARY KEY(kind,id))')
            db.execute('CREATE INDEX IF NOT EXISTS saas_record_links_installation ON saas_record_links(installation_id,kind,id)')
            db.execute('CREATE TABLE IF NOT EXISTS saas_record_emails (kind TEXT NOT NULL,id TEXT NOT NULL,email_hash TEXT NOT NULL,PRIMARY KEY(kind,id,email_hash))')
            db.execute('CREATE INDEX IF NOT EXISTS saas_record_emails_hash ON saas_record_emails(email_hash,kind,id)')
            db.execute('CREATE TRIGGER IF NOT EXISTS records_links_delete AFTER DELETE ON records BEGIN DELETE FROM saas_record_links WHERE kind=OLD.kind AND id=OLD.id; DELETE FROM saas_record_emails WHERE kind=OLD.kind AND id=OLD.id; END')
            db.execute('CREATE TABLE IF NOT EXISTS rates (identity TEXT PRIMARY KEY, bucket INTEGER NOT NULL, count INTEGER NOT NULL)')

    @contextmanager
    def transaction(self, *, readonly=False):
        # Each caller owns a connection. BEGIN IMMEDIATE serializes both refresh
        # token rotation and single-use state consumption across worker threads.
        db = sqlite3.connect(self.path, timeout=35)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA secure_delete=ON')
            # Credential rotations and billing intents must survive acknowledged commits.
            db.execute('PRAGMA synchronous=FULL')
            if readonly:
                db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN DEFERRED' if readonly else 'BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def read_transaction(self):
        return self.transaction(readonly=True)

    def index_owner(self, db, kind, record_id, payload):
        db.execute('DELETE FROM saas_record_owners WHERE kind=? AND id=?', (kind, record_id))
        nested = payload.get('saas')
        owner = payload.get('user_id') or (nested.get('user_id') if isinstance(nested, dict) else None)
        if isinstance(owner, str) and owner:
            db.execute('INSERT INTO saas_record_owners VALUES(?,?,?)', (owner, kind, record_id))
        db.execute('DELETE FROM saas_record_links WHERE kind=? AND id=?',(kind,record_id))
        if kind in {'saas-capability','saas-pair'}:
            db.execute('INSERT INTO saas_record_links VALUES(?,?,?,?)',
                       (kind,record_id,payload.get('installation_id'),payload.get('grant_id')))
        db.execute('DELETE FROM saas_record_emails WHERE kind=? AND id=?',(kind,record_id))
        if kind in {'saas-user','saas-login'} and isinstance(payload.get('email'),str):
            # Domain-separated keyed digests permit exact matching without exposing
            # email addresses or enabling offline dictionary attacks on the index.
            value=('socialcli-record-email:'+payload['email'].casefold()).encode()
            for key in set(self.keys):
                marker=hmac.new(key,value,hashlib.sha256).hexdigest()
                db.execute('INSERT INTO saas_record_emails VALUES(?,?,?)',(kind,record_id,marker))


    def put(self, db, kind: str, record_id: str, payload: dict, *, expires: float, credential_hash: str = ''):
        envelope = json.dumps({'kind': kind, 'id': record_id, 'payload': payload, 'expires': expires, 'credential_hash': credential_hash}, separators=(',', ':')).encode()
        encrypted = self.cipher.encrypt(envelope)
        db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?,?,?)', (kind, record_id, expires, credential_hash, encrypted))
        self.index_owner(db, kind, record_id, payload)

    def get(self, db, kind: str, record_id: str) -> dict | None:
        row = db.execute('SELECT * FROM records WHERE kind=? AND id=?', (kind, record_id)).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(self.cipher.decrypt(row['payload']))
            if value['expires'] != row['expires'] or value['credential_hash'] != row['credential_hash'] or value['kind'] != kind or value['id'] != record_id or not isinstance(value['payload'], dict):
                raise ValueError()
        except (InvalidToken, ValueError, TypeError, KeyError):
            raise ValueError('credential storage is invalid') from None
        return {'expires': row['expires'], 'credential_hash': row['credential_hash'], 'payload': value['payload']}

    def delete(self, db, kind: str, record_id: str):
        db.execute('DELETE FROM records WHERE kind=? AND id=?', (kind, record_id))

    def purge(self, db, *, now: float):
        # SaaS grants need a durable revocation intent before their only Google
        # token is discarded. AccountLifecycle.prune_expired owns that cleanup.
        db.execute("DELETE FROM records WHERE expires<=? AND kind!='saas-grant'", (now,))
        db.execute('DELETE FROM rates WHERE bucket<?', (int(now // 60) - 60,))

    def admit(self, db, identity: str, *, now: float, limit: int = 5) -> bool:
        identity_hash = hashlib.sha256(identity.encode()).hexdigest()
        bucket = int(now // 60)
        row = db.execute('SELECT bucket,count FROM rates WHERE identity=?', (identity_hash,)).fetchone()
        count = row['count'] if row and row['bucket'] == bucket else 0
        if count >= limit:
            return False
        db.execute('INSERT OR REPLACE INTO rates VALUES (?,?,?)', (identity_hash, bucket, count + 1))
        return True
