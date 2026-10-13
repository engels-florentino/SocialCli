"""Private online SQLite snapshots; never restore over a running service."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from contextlib import closing

from cryptography.fernet import Fernet, MultiFernet, InvalidToken


class BackupError(Exception):
    """Sanitized operator error: no SQL, identities or credentials in diagnostics."""


def _read(path, timeout=5):
    return sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=timeout)


def verify_snapshot(path: Path, keys: list[bytes]) -> dict:
    """Check an isolated snapshot read-only; no migrations, expiry or network I/O."""
    try:
        cipher=MultiFernet([Fernet(key) for key in keys])
        with closing(_read(path)) as db:
            db.row_factory=sqlite3.Row
            db.execute('BEGIN')
            if db.execute('PRAGMA integrity_check').fetchall()[0][0]!='ok':
                raise ValueError()
            if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise ValueError()
            records=0
            for row in db.execute('SELECT * FROM records'):
                envelope=json.loads(cipher.decrypt(row['payload']))
                if (envelope['kind']!=row['kind'] or envelope['id']!=row['id']
                        or envelope['expires']!=row['expires']
                        or envelope['credential_hash']!=row['credential_hash']
                        or not isinstance(envelope['payload'],dict)):
                    raise ValueError()
                records+=1
            names={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            version=db.execute('SELECT coalesce(max(version),0) FROM saas_schema').fetchone()[0] if 'saas_schema' in names else 0
            cutoff=bool(db.execute('SELECT 1 FROM legacy_cutoff WHERE singleton=1').fetchone()) if 'legacy_cutoff' in names else False
            tables=len(names)
        with Path(path).open('rb') as stream:
            digest=hashlib.file_digest(stream,'sha256').hexdigest()
        return {'integrity':'ok','records':records,'tables':tables,'schema_version':version,
                'legacy_cutoff':cutoff,'sha256':digest}
    except (OSError,sqlite3.Error,ValueError,TypeError,KeyError,InvalidToken):
        raise BackupError('Snapshot validation failed; check the database and its separate key set.') from None


def snapshot(source: Path, destination: Path, keys: list[bytes], *, timeout=60) -> dict:
    """Publish a verified 0600 snapshot atomically, refusing any existing target."""
    source,destination=Path(source),Path(destination)
    if not math.isfinite(timeout) or timeout<=0:
        raise BackupError('Backup timeout must be positive and finite.')
    temporary=None
    try:
        if destination.exists() or destination.is_symlink() or not source.is_file():
            raise BackupError('Source must exist and destination must be new.')
        destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        # Do not change permissions of a pre-existing operator directory.
        if destination.parent.stat().st_mode & 0o077:
            raise BackupError('Use a private destination directory with mode 0700.')
        fd,name=tempfile.mkstemp(prefix='.socialcli-snapshot-',dir=destination.parent)
        temporary=Path(name);os.close(fd)
        deadline=time.monotonic()+timeout
        def progress(status,remaining,total):
            if time.monotonic()>=deadline:
                raise BackupError('Snapshot timed out; no completed artifact was published.')
        with closing(_read(source,min(timeout,1))) as live, closing(sqlite3.connect(temporary)) as target:
            live.backup(target,pages=128,progress=progress,sleep=min(timeout,.05))
            # Produce a self-contained single file even when the source uses WAL.
            target.execute('PRAGMA journal_mode=DELETE')
        report=verify_snapshot(temporary,keys)
        with temporary.open('rb') as stream:os.fsync(stream.fileno())
        # Same-directory hard link is atomic and cannot replace an existing path.
        os.link(temporary,destination)
        directory_fd=os.open(destination.parent,os.O_RDONLY)
        try:os.fsync(directory_fd)
        finally:os.close(directory_fd)
        return report
    except (OSError,sqlite3.Error):
        raise BackupError('Snapshot failed; source preserved. Inspect storage permissions and locks.') from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
            for suffix in ('-journal','-wal','-shm'):
                Path(str(temporary)+suffix).unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['snapshot','verify'])
    parser.add_argument('database',type=Path)
    parser.add_argument('destination',type=Path,nargs='?')
    parser.add_argument('--timeout',type=float,default=60)
    args=parser.parse_args()
    if (args.operation=='snapshot') != (args.destination is not None):
        parser.error('snapshot requires a destination; verify accepts only a database')
    keys=[value.strip().encode() for value in os.environ.get('SOCIALCLI_ENCRYPTION_KEYS','').split(',') if value.strip()]
    try:
        report=(snapshot(args.database,args.destination,keys,timeout=args.timeout)
                if args.operation=='snapshot' else verify_snapshot(args.database,keys))
    except BackupError as error:
        parser.exit(1,str(error)+'\n')
    print(json.dumps(report,sort_keys=True))


if __name__=='__main__':
    main()
