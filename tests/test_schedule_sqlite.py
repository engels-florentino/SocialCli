import sqlite3

import pytest

from socialctl.scheduler import ScheduleError, ScheduleStore
from tests.test_scheduler import _entry


def test_sqlite_preserves_order_history_and_is_authoritative(tmp_path):
    root = tmp_path / "One"
    legacy = ScheduleStore(root)
    old = _entry("same", "2020-01-01T00:00:00Z")
    old.status = "published"
    old.platform_id = "remote"
    current = _entry("same", "2021-01-01T00:00:00Z")
    legacy.save([current, old])
    store = ScheduleStore(root, backend="sqlite")
    assert store.load() == [current, old]
    current.last_error = "preserved"
    store.save([old, current])
    assert ScheduleStore(root).load() == [old, current]
    assert ScheduleStore(tmp_path / "Two").load() == []
    assert legacy.path.read_bytes()  # retained JSON backup
    backup = tmp_path / "consistent.sqlite3"
    store.backup(backup)
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM entries").fetchone()[0] == 2


def test_sqlite_failed_transaction_retains_complete_queue(tmp_path, monkeypatch):
    store = ScheduleStore(tmp_path, backend="sqlite")
    entry = _entry("one", "2020-01-01T00:00:00Z")
    store.save([entry])
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("CREATE TRIGGER refuse BEFORE INSERT ON entries BEGIN SELECT RAISE(ABORT, 'refuse'); END")
    with pytest.raises(ScheduleError):
        store.save([] + [entry, entry])
    assert store.load() == [entry]


def test_unknown_database_schema_never_falls_back_to_json(tmp_path):
    store = ScheduleStore(tmp_path)
    store.save([_entry("one", "2020-01-01T00:00:00Z")])
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("PRAGMA user_version=99")
    with pytest.raises(ScheduleError, match="versión"):
        ScheduleStore(tmp_path).load()


def test_sqlite_connection_uses_full_durability(tmp_path):
    from socialctl.schedule_sqlite import connect
    with connect(tmp_path / "new.sqlite3") as connection:
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 3
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
