"""Never report an installed or healthy scheduler from a saved file alone."""
import json
import subprocess
from datetime import datetime, timezone

import pytest
from typer.testing import CliRunner

import socialctl.cli as cli
import socialctl.scheduler_launchd as launchd
from socialctl.brands import crear_brand
from socialctl.executor import heartbeat
from socialctl.scheduler import ScheduleStore

runner = CliRunner()


@pytest.mark.parametrize('launchctl,returncode', [(None, 0), ('/test/launchctl', 1)])
def test_install_does_not_claim_success_without_loaded_service(tmp_path, monkeypatch, launchctl, returncode):
    monkeypatch.setattr(cli.sys,'platform','darwin')
    crear_brand(tmp_path, 'Example')
    home = tmp_path / 'home'
    monkeypatch.setattr(cli.Path, 'home', lambda: home)
    monkeypatch.setattr(launchd.shutil, 'which', lambda _: launchctl)
    monkeypatch.setattr(launchd.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a[0], returncode))
    result = runner.invoke(cli.app, ['schedule-install', '--brand', 'Example', '--root', str(tmp_path)])
    assert result.exit_code == 1, result.output
    assert 'Agent installed:' not in result.stdout
    if launchctl is None:
        assert not home.exists()


def test_doctor_never_run_queue_exits_nonzero_with_action(tmp_path):
    crear_brand(tmp_path, 'Example')
    result = runner.invoke(cli.app, ['schedule-doctor', '--brand', 'Example', '--root', str(tmp_path), '--json'])
    assert result.exit_code == 1, result.output
    report = json.loads(result.stdout)
    assert report['status'] == 'attention_required'
    assert report['error']['action'] == 'user_action'
    assert report['heartbeat']['stale'] is True
    assert report['server_media_storage'] is False


def test_recent_single_run_does_not_prove_persistent_supervision(tmp_path):
    brand = crear_brand(tmp_path, 'Example')
    heartbeat(ScheduleStore(brand.raiz), 'finished', datetime.now(timezone.utc))
    result = runner.invoke(cli.app, ['schedule-doctor', '--brand', 'Example', '--root', str(tmp_path), '--json'])
    report = json.loads(result.stdout)
    assert report['heartbeat']['stale'] is False
    assert report['persistent_supervision'] == 'unverified'
    assert report['unattended_delivery_guaranteed'] is False


def test_recent_failed_executor_is_visible_in_human_diagnostic(tmp_path):
    brand = crear_brand(tmp_path, 'Example')
    heartbeat(ScheduleStore(brand.raiz), 'error', datetime.now(timezone.utc))
    result = runner.invoke(cli.app, ['schedule-doctor', '--brand', 'Example', '--root', str(tmp_path)])
    assert result.exit_code == 1
    assert 'executor reported an error' in result.stdout
