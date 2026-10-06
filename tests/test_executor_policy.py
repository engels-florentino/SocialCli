import json
from datetime import datetime

import pytest

import socialctl.cli as cli
from socialctl.scheduler import ScheduleError, ScheduleStore
from tests.test_scheduler_cli import _make_social, _schedule, runner, app


def policy(brand, **changes):
    data = dict(timezone="America/New_York", slots=["10:00", "14:00", "18:00"],
                window_minutes=60, max_groups_per_slot=1)
    data.update(changes)
    root = brand.raiz / ".socialctl"
    root.mkdir(exist_ok=True)
    (root / "executor-policy.json").write_text(json.dumps(data))


def test_slot_policy_bounds_catchup_and_preserves_original_times(tmp_path, monkeypatch):
    brand, _ = _make_social(tmp_path)
    _schedule(tmp_path)
    store = ScheduleStore(brand.raiz)
    first = store.load()[0]
    store.add(first.model_copy(update={"id": "later/facebook", "slug": "later",
                                      "scheduled_at": datetime.fromisoformat("2020-09-16T14:00:00-04:00")}))
    policy(brand)
    monkeypatch.setattr(cli, "now_utc", lambda: datetime.fromisoformat("2026-09-12T13:59:00+00:00"))
    result = runner.invoke(app, ["run-due", "--brand", "Histopast", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert [entry.status for entry in store.load()] == ["approved", "approved"]


def test_policy_admits_paired_platforms_once_across_restart_and_brands(tmp_path):
    from socialctl.executor import admitted_groups
    from socialctl.brands import crear_brand
    brand, _ = _make_social(tmp_path)
    _schedule(tmp_path)
    store = ScheduleStore(brand.raiz)
    first = store.load()[0]
    store.add(first.model_copy(update={"id": "clip/instagram", "platform": "instagram"}))
    store.add(first.model_copy(update={"id": "later/facebook", "slug": "later"}))
    policy(brand)
    now = datetime.fromisoformat("2026-09-12T14:10:00+00:00")
    with store.executor_lock():
        groups = list(admitted_groups(store, now))
    assert [[e.id for e in group] for group in groups] == [["clip/facebook", "clip/instagram"]]
    with ScheduleStore(brand.raiz).executor_lock():
        assert list(admitted_groups(ScheduleStore(brand.raiz), now)) == []
    other = crear_brand(tmp_path, "Other")
    policy(other)
    other_store = ScheduleStore(other.raiz)
    other_store.add(first.model_copy(update={"brand": "Other"}))
    with other_store.executor_lock():
        assert len(list(admitted_groups(other_store, now))) == 1
    with store.executor_lock():
        assert len(list(admitted_groups(store, datetime.fromisoformat("2026-09-12T18:00:00+00:00")))) == 1
    assert store.get(first.id).scheduled_at == first.scheduled_at


@pytest.mark.parametrize("when,want", [
    ("2026-03-08T07:30:00+00:00", None),
    ("2026-11-01T05:30:00+00:00", "2026-11-01/01:30"),
    ("2026-11-01T06:30:00+00:00", None),
])
def test_policy_validates_dst_instants(tmp_path, when, want):
    from socialctl.executor import ExecutionPolicy
    slot = "02:30" if "03-08" in when else "01:30"
    instance = ExecutionPolicy(timezone="America/New_York", slots=[slot], window_minutes=60, max_groups_per_slot=1)
    assert instance.active_slot(datetime.fromisoformat(when)) == want


def test_actual_overlapping_windows_have_latest_start_ownership():
    from socialctl.executor import ExecutionPolicy
    instance = ExecutionPolicy(timezone="America/New_York", slots=["01:30", "03:00"], window_minutes=120, max_groups_per_slot=1)
    assert instance.active_slot(datetime.fromisoformat("2026-03-08T07:05:00+00:00")) == "2026-03-08/03:00"


def test_slot_persistence_failure_blocks_admission(tmp_path, monkeypatch):
    from socialctl import executor
    brand, _ = _make_social(tmp_path)
    _schedule(tmp_path)
    policy(brand)
    def fail(*args):
        raise OSError("credential=do-not-display")
    monkeypatch.setattr(executor, "write_json", fail)
    with ScheduleStore(brand.raiz).executor_lock(), pytest.raises(ScheduleError, match='executor state'):
        list(executor.admitted_groups(ScheduleStore(brand.raiz), datetime.fromisoformat("2026-09-12T14:10:00+00:00")))


def test_heartbeat_survives_restart_and_health_sanitizes(tmp_path):
    brand, _ = _make_social(tmp_path)
    result = runner.invoke(app, ["run-due", "--brand", "Histopast", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    state = json.loads((brand.raiz / ".socialctl/executor-state.json").read_text())
    assert state["heartbeat"]["state"] == "finished"
    assert state["heartbeat"]["backend"] == "json"
    assert state["heartbeat"]["started_at"]
    state["heartbeat"]["error"] = "access_token=SECRET"
    (brand.raiz / ".socialctl/executor-state.json").write_text(json.dumps(state))
    result = runner.invoke(app, ["schedule-health", "--brand", "Histopast", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "finished" in result.output and "json" in result.output
    assert "SECRET" not in result.output


def test_policy_failure_persists_sanitized_error_heartbeat(tmp_path):
    brand, _ = _make_social(tmp_path)
    policy(brand, timezone="invalid-zone-secret")
    result = runner.invoke(app, ["run-due", "--brand", "Histopast", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "invalid-zone-secret" not in result.output
    state = json.loads((brand.raiz / ".socialctl/executor-state.json").read_text())
    assert state["heartbeat"]["state"] == "error"


@pytest.mark.parametrize("values", [dict(slots=[]), dict(slots=["25:00"]), dict(slots=["10:00", "10:00"]), dict(window_minutes=0), dict(window_minutes=1441), dict(max_groups_per_slot=0), dict(max_groups_per_slot=True)])
def test_invalid_policies_fail_closed(tmp_path, values):
    brand, _ = _make_social(tmp_path)
    policy(brand, **values)
    result = runner.invoke(app, ["run-due", "--brand", "Histopast", "--root", str(tmp_path)])
    assert result.exit_code == 1


def test_paired_fb_ig_publication_consumes_one_slot_and_restart_does_not_burst(tmp_path, monkeypatch):
    import yaml
    from socialctl.models import Platform, PostResult, PostStatus
    from socialctl import hosted_media
    brand, path = _make_social(tmp_path)
    data = yaml.safe_load(path.read_text())
    data["platforms"]["instagram"] = data["platforms"]["facebook"]
    path.write_text(yaml.safe_dump(data))
    # Real queue/hash/group/claim/persistence; only external publication/host verification are fake.
    monkeypatch.setattr(cli, "validar_todo", lambda *args, **kwargs: {})
    _schedule(tmp_path)
    store = ScheduleStore(brand.raiz)
    later = store.load()[0].model_copy(update={"id": "later/facebook", "slug": "later"})
    store.add(later)
    policy(brand)
    calls = []
    def publish(post, brand, solo, on_progreso, *, on_media_result, occurrence_ids, approval_provenance, legacy_approved_platforms):
        calls.append(solo[0])
        result = PostResult(platform=solo[0], status=PostStatus.PUBLICADO, platform_id="test-id")
        on_media_result(result)
        return [result]
    monkeypatch.setattr(cli, "publicar", publish)
    monkeypatch.setattr(hosted_media, "verify_scheduled_instagram", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "now_utc", lambda: datetime.fromisoformat("2026-09-12T14:01:00+00:00"))
    for _ in range(2):
        result = runner.invoke(app, ["run-due", "--brand", "Histopast", "--root", str(tmp_path)])
        assert result.exit_code == 0, result.output
    assert calls == [Platform.FACEBOOK, Platform.INSTAGRAM]
    assert [e.status for e in store.load()] == ["published", "published", "approved"]


def test_slot_persistence_failure_blocks_publication_and_records_error(tmp_path, monkeypatch):
    from socialctl import executor
    brand, _ = _make_social(tmp_path)
    _schedule(tmp_path)
    policy(brand)
    original = executor.write_json
    def fail_slot(path, state):
        if state["slots"]:
            raise OSError("token=SECRET")
        original(path, state)
    def forbidden(*args, **kwargs):
        pytest.fail("non-durable slot reached publishing")
    monkeypatch.setattr(executor, "write_json", fail_slot)
    monkeypatch.setattr(cli, "publicar", forbidden)
    monkeypatch.setattr(cli, "now_utc", lambda: datetime.fromisoformat("2026-09-12T14:01:00+00:00"))
    result = runner.invoke(app, ["run-due", "--brand", "Histopast", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "SECRET" not in result.output
    assert ScheduleStore(brand.raiz).get("clip/facebook").status == "approved"
    assert json.loads((brand.raiz / ".socialctl/executor-state.json").read_text())["heartbeat"]["state"] == "error"


def test_consumed_repeated_hour_survives_actual_process_restart(tmp_path):
    import subprocess
    import sys
    brand, _ = _make_social(tmp_path)
    _schedule(tmp_path)
    policy(brand, slots=["01:30"], window_minutes=120)
    code = """
import sys
from datetime import datetime
from pathlib import Path
from socialctl.scheduler import ScheduleStore
from socialctl.executor import admitted_groups
store = ScheduleStore(Path(sys.argv[1]))
with store.executor_lock():
    print(len(list(admitted_groups(store, datetime.fromisoformat(sys.argv[2])))))
"""
    for at, expected in [("2026-11-01T05:30:00+00:00", "1"), ("2026-11-01T06:30:00+00:00", "0")]:
        result = subprocess.run([sys.executable, "-c", code, str(brand.raiz), at], capture_output=True, text=True, check=True)
        assert result.stdout.strip() == expected


def test_run_due_does_not_print_raw_adapter_errors(tmp_path, monkeypatch):
    from socialctl.models import PostResult, PostStatus, Platform
    brand, _ = _make_social(tmp_path)
    _schedule(tmp_path)
    monkeypatch.setattr(cli, "publicar", lambda *args, **kwargs: [PostResult(platform=Platform.FACEBOOK, status=PostStatus.ERROR, error="access_token=SECRET")])
    result = runner.invoke(app, ["run-due", "--brand", "Histopast", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "SECRET" not in result.output


@pytest.mark.parametrize("change", ["reschedule", "replacement", "approval"])
def test_admitted_group_cannot_claim_a_concurrently_changed_occurrence(tmp_path, monkeypatch, change):
    import yaml
    from socialctl.models import Platform, PostResult, PostStatus
    from socialctl import hosted_media
    brand, path = _make_social(tmp_path)
    data = yaml.safe_load(path.read_text())
    data["platforms"]["instagram"] = data["platforms"]["facebook"]
    path.write_text(yaml.safe_dump(data))
    monkeypatch.setattr(cli, "validar_todo", lambda *args, **kwargs: {})
    _schedule(tmp_path)
    policy(brand)
    store = ScheduleStore(brand.raiz)
    moment = datetime.fromisoformat("2026-09-12T14:01:00+00:00")
    monkeypatch.setattr(cli, "now_utc", lambda: moment)
    monkeypatch.setattr(hosted_media, "verify_scheduled_instagram", lambda *a, **kw: None)
    calls = []
    def publish(post, brand, solo, on_progreso, *, on_media_result, occurrence_ids, approval_provenance, legacy_approved_platforms):
        calls.append(solo[0])
        if solo == [Platform.FACEBOOK]:
            original = store.get("clip/instagram")
            if change == "reschedule":
                store.reschedule(original.id, datetime.fromisoformat("2026-09-11T14:00:00+00:00"), moment)
            elif change == "replacement":
                store.cancel(original.id, moment)
                store.add(original.model_copy(update={"created_at": moment, "updated_at": moment}))
            else:
                store.update(original.model_copy(update={"approval_migration": "new-evidence", "updated_at": moment}))
        result = PostResult(platform=solo[0], status=PostStatus.PUBLICADO, platform_id="fake-id")
        on_media_result(result)
        return [result]
    monkeypatch.setattr(cli, "publicar", publish)
    for _ in range(2):
        result = runner.invoke(app, ["run-due", "--brand", "Histopast", "--root", str(tmp_path)])
        assert result.exit_code == 0, result.output
    assert calls == [Platform.FACEBOOK]
    assert store.get("clip/instagram").status == "approved"
    assert store.get("clip/instagram").attempts == 0
