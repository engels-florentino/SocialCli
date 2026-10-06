import json
import shlex
import subprocess
from pathlib import Path

import pytest

from socialctl.scheduler import ScheduleError, ScheduleStore
from tests.test_scheduler_cli import _make_social, _schedule, runner, app


def marker(brand, **changes):
    config = dict(host="florentino.pro", port=1384, user="blades3",
                  root="/home/blades3/projects/socialctl/current",
                  executable="/home/blades3/projects/socialctl/current/.venv/bin/socialctl")
    config.update(changes)
    path = brand.raiz / ".socialctl/remote-executor.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(config))
    return config


@pytest.mark.parametrize("command", [
    ["run-due"], ["schedule", "clip", "--at", "2030-01-01T00:00:00Z", "--yes"],
    ["schedule-cancel", "clip/facebook"],
    ["schedule-reschedule", "clip/facebook", "--at", "2030-01-01T00:00:00Z"],
    ["schedule-install"],
])
def test_marker_blocks_local_executor_and_mutations(tmp_path, monkeypatch, command):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "fake-home")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "", ""))
    brand, _ = _make_social(tmp_path)
    _schedule(tmp_path)
    original = ScheduleStore(brand.raiz).load()
    marker(brand)
    result = runner.invoke(app, command + ["--brand", "Histopast", "--root", str(tmp_path)])
    assert result.exit_code == 1, result.output
    assert 'remote' in result.output
    assert ScheduleStore(brand.raiz).load() == original


def test_marker_blocks_direct_store_writes_and_marks_readonly_stale(tmp_path):
    brand, _ = _make_social(tmp_path)
    _schedule(tmp_path)
    marker(brand)
    with pytest.raises(ScheduleError, match='remote'):
        ScheduleStore(brand.raiz).save([])
    for command in ("schedule-status", "schedule-health"):
        result = runner.invoke(app, [command, "--brand", "Histopast", "--root", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert 'remote' in result.output and 'stale' in result.output
        assert "clip/facebook" not in result.output


@pytest.mark.parametrize("key,value", [
    ("host", "-oProxyCommand=evil"), ("host", "host;touch bad"), ("user", "u@other"),
    ("port", 0), ("port", True), ("port", 65536), ("root", "relative"),
    ("root", "/srv/x;touch bad"), ("executable", "/bin/sh\ncommand"),
    ("root", "/srv/../etc"), ("user", ""),
    ("executable", "/bin/sh"),
])
def test_invalid_remote_configuration_never_starts_ssh(tmp_path, monkeypatch, key, value):
    brand, _ = _make_social(tmp_path)
    marker(brand, **{key: value})
    def forbidden(*args, **kwargs):
        pytest.fail("invalid config reached subprocess")
    monkeypatch.setattr(subprocess, "run", forbidden)
    result = runner.invoke(app, ["schedule-remote", "status", "--brand", "Histopast", "--root", str(tmp_path)])
    assert result.exit_code == 1, result.output
    assert value in (True, "") or str(value) not in result.output


@pytest.mark.parametrize("command,remote_args", [
    (["status"], ["schedule-status"]), (["health"], ["schedule-health"]),
    (["cancel", "clip/facebook"], ["schedule-cancel", "clip/facebook"]),
    (["reschedule", "clip/facebook", "--at", "2030-01-01T00:00:00Z"],
     ["schedule-reschedule", "clip/facebook", "--at", "2030-01-01T00:00:00Z"]),
    (["schedule", "clip", "--at", "2030-01-01T00:00:00Z", "--dry-run"],
     ["schedule", "clip", "--at", "2030-01-01T00:00:00Z", "--dry-run", "--preview-json"]),
])
def test_explicit_remote_actions_preserve_boundaries_and_do_not_recurse(tmp_path, monkeypatch, command, remote_args):
    brand, _ = _make_social(tmp_path)
    config = marker(brand)
    seen = []
    def transport(argv, **kwargs):
        seen.append((argv, kwargs))
        output = json.dumps(dict(protocol="socialctl.schedule-preview.v1", valid=True,
                                 preview="FULL PREVIEW\n", digest="a" * 64,
                                 scheduled_at="2030-01-01T00:00:00+00:00")) if command[0] == "schedule" else "FULL PREVIEW\n"
        return subprocess.CompletedProcess(argv, 0, output, "")
    monkeypatch.setattr(subprocess, "run", transport)
    result = runner.invoke(app, ["schedule-remote"] + command + ["--brand", "Histopast", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "FULL PREVIEW" in result.output
    argv, kwargs = seen[0]
    assert argv[:2] == ["ssh", "-T"]
    assert "1384" in argv and "blades3@florentino.pro" in argv
    assert shlex.split(argv[-1]) == [config["executable"]] + remote_args + ["--brand", "Histopast", "--root", config["root"]]
    assert kwargs.get("shell", False) is False
    assert kwargs["stdin"] is subprocess.DEVNULL


def test_remote_schedule_requires_preview_digest_before_approval(tmp_path, monkeypatch):
    brand, _ = _make_social(tmp_path)
    marker(brand)
    def forbidden(*args, **kwargs):
        pytest.fail("approval without digest reached SSH")
    monkeypatch.setattr(subprocess, "run", forbidden)
    result = runner.invoke(app, ["schedule-remote", "schedule", "clip", "--at", "2030-01-01T00:00:00Z", "--yes", "--brand", "Histopast", "--root", str(tmp_path)])
    assert result.exit_code == 1, result.output
    assert "digest" in result.output


def test_remote_failure_does_not_disclose_transport_errors(tmp_path, monkeypatch):
    brand, _ = _make_social(tmp_path)
    marker(brand)
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 255, "password=SECRET", "token=SECRET"))
    result = runner.invoke(app, ["schedule-remote", "status", "--brand", "Histopast", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "SECRET" not in result.output


def test_marker_blocks_migration_rollback_before_reading_backups(tmp_path):
    from socialctl.queue_migration import rollback
    brand, _ = _make_social(tmp_path)
    marker(brand)
    with pytest.raises(ScheduleError, match='remote'):
        rollback(brand, "not-a-proposal")


def test_status_omits_unsanitized_adapter_errors(tmp_path):
    brand, _ = _make_social(tmp_path)
    _schedule(tmp_path)
    store = ScheduleStore(brand.raiz)
    entry = store.load()[0]
    store.update(entry.model_copy(update={"last_error": "access_token=SECRET"}))
    result = runner.invoke(app, ["schedule-status", "--brand", "Histopast", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "SECRET" not in result.output


def test_remote_approval_transmits_bound_digest(tmp_path, monkeypatch):
    brand, _ = _make_social(tmp_path)
    config = marker(brand)
    seen = []
    def transport(argv, **kwargs):
        seen.extend(shlex.split(argv[-1]))
        return subprocess.CompletedProcess(argv, 0, "approved", "")
    monkeypatch.setattr(subprocess, "run", transport)
    result = runner.invoke(app, ["schedule-remote", "schedule", "clip", "--at", "2030-01-01T00:00:00Z", "--yes", "--approval-digest", "a" * 64, "--brand", "Histopast", "--root", str(tmp_path), "--only", "facebook"])
    assert result.exit_code == 0, result.output
    assert seen == [config["executable"], "schedule", "clip", "--at", "2030-01-01T00:00:00Z", "--approval-digest", "a" * 64, "--yes", "--only", "facebook", "--brand", "Histopast", "--root", config["root"]]


def test_remote_failed_dryrun_preserves_actionable_validation_preview(tmp_path, monkeypatch):
    brand, _ = _make_social(tmp_path)
    marker(brand)
    payload = dict(protocol="socialctl.schedule-preview.v1", valid=False,
                   preview="--- FACEBOOK ---\nTexto completo\nPROBLEMA [accounts]: falta page_id en accounts.yml\nProblemas detectados: 1",
                   digest="a" * 64, scheduled_at="2030-01-01T00:00:00+00:00")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, json.dumps(payload), "Traceback password=SECRET"))
    result = runner.invoke(app, ["schedule-remote", "schedule", "clip", "--at", "2030-01-01T00:00:00Z", "--dry-run", "--brand", "Histopast", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert payload["preview"] in result.output
    assert 'Nothing scheduled' in result.output
    assert "SECRET" not in result.output


@pytest.mark.parametrize("payload", [
    "Traceback access_token=SECRET",
    json.dumps(dict(protocol="other", valid=False, preview="SECRET")),
    json.dumps(dict(protocol="socialctl.schedule-preview.v1", valid=False, preview="password=SECRET", digest="a" * 64, scheduled_at="2030-01-01T00:00:00Z")),
    json.dumps(dict(protocol="socialctl.schedule-preview.v1", valid=False, preview="safe", digest="a" * 64, scheduled_at="2030-01-01T00:00:00Z", exception="SECRET")),
    json.dumps(dict(valid=False, preview="SECRET", digest="a" * 64, scheduled_at="2030-01-01T00:00:00Z")),
])
def test_remote_failed_dryrun_rejects_unstructured_or_secret_output(tmp_path, monkeypatch, payload):
    brand, _ = _make_social(tmp_path)
    marker(brand)
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, payload, "token=SECRET"))
    result = runner.invoke(app, ["schedule-remote", "schedule", "clip", "--at", "2030-01-01T00:00:00Z", "--dry-run", "--brand", "Histopast", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "SECRET" not in result.output


def test_remote_preview_relay_preserves_origin_validation_failure(tmp_path, monkeypatch):
    from tests.test_scheduler_cli import _make_video_social
    from tests.test_schedule_approval_digest import invocation
    server = tmp_path / 'server'
    server.mkdir()
    server_brand, _ = _make_video_social(server, monkeypatch, origin=None)
    response = invocation(server, '--dry-run', '--preview-json')
    assert response.exit_code == 1, response.output
    local = tmp_path / 'local'
    local.mkdir()
    brand, _ = _make_social(local)
    marker(brand)
    monkeypatch.setattr(subprocess, 'run', lambda argv, **kw: subprocess.CompletedProcess(
        argv, response.exit_code, response.output, ''))
    result = runner.invoke(app, ['schedule-remote', 'schedule', 'clip', '--at', '2030-01-01T00:00:00Z',
                                '--dry-run', '--brand', 'Histopast', '--root', str(local)])
    assert result.exit_code == 1, result.output
    assert 'declare standalone or youtube_long' in result.output
    assert not ScheduleStore(server_brand.raiz).load()
    assert not ScheduleStore(brand.raiz).load()
