"""SQLite ordered occurrences; DELETE+EXTRA exceeds FULL durability."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def connect(path: Path):
    existed = path.exists()
    connection = sqlite3.connect(path, timeout=30)
    try:
        connection.execute("PRAGMA journal_mode=DELETE")
        # EXTRA also syncs the directory after deleting the rollback journal.
        connection.execute("PRAGMA synchronous=EXTRA")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1) or (version == 0 and existed):
            raise ValueError(f"unknown SQLite version: {version}")
        if version == 0:
            with connection:
                connection.execute("CREATE TABLE entries (position INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
                connection.execute("PRAGMA user_version=1")
        yield connection
    finally:
        connection.close()


def load(path: Path) -> list[dict]:
    with connect(path) as connection:
        return [json.loads(row[0]) for row in connection.execute("SELECT payload FROM entries ORDER BY position")]


def save(path: Path, entries: list[dict]) -> None:
    with connect(path) as connection, connection:
        connection.execute("DELETE FROM entries")
        connection.executemany("INSERT INTO entries(position, payload) VALUES (?, ?)",
                               [(index, json.dumps(entry, ensure_ascii=False)) for index, entry in enumerate(entries)])


def backup(path: Path, destination: Path) -> None:
    with connect(path) as connection, sqlite3.connect(destination) as target:
        connection.backup(target)
