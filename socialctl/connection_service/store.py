"""Private, transactionally updated credential records with authenticated encryption."""
from __future__ import annotations

import hashlib
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
        self.cipher = MultiFernet([Fernet(key) for key in (keys if isinstance(keys, list) else [keys])])
        with self.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT NOT NULL, id TEXT NOT NULL, expires REAL NOT NULL, credential_hash TEXT NOT NULL, payload BLOB NOT NULL, PRIMARY KEY(kind,id))')
            db.execute('CREATE TABLE IF NOT EXISTS rates (identity TEXT PRIMARY KEY, bucket INTEGER NOT NULL, count INTEGER NOT NULL)')

    @contextmanager
    def transaction(self):
        # Each caller owns a connection. BEGIN IMMEDIATE serializes both refresh
        # token rotation and single-use state consumption across worker threads.
        db = sqlite3.connect(self.path, timeout=35)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA secure_delete=ON')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def put(self, db, kind: str, record_id: str, payload: dict, *, expires: float, credential_hash: str = ''):
        envelope = json.dumps({'kind': kind, 'id': record_id, 'payload': payload, 'expires': expires, 'credential_hash': credential_hash}, separators=(',', ':')).encode()
        encrypted = self.cipher.encrypt(envelope)
        db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?,?,?)', (kind, record_id, expires, credential_hash, encrypted))

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
        db.execute('DELETE FROM records WHERE expires<=?', (now,))
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
