"""The installed queue agent runs from a creator workspace outside the project."""

import json
import os
import plistlib
import shutil
import subprocess
import sys

import pytest
from typer.testing import CliRunner

import socialctl.scheduler_launchd as service
from socialctl.brands import crear_brand
from socialctl.cli import app
from tests.test_scheduler_launchd import Launchd


@pytest.mark.parametrize('uv_available', [False, True])
def test_installed_agent_runs_from_external_workspace(tmp_path, monkeypatch, uv_available):
    real_run, real_which = subprocess.run, shutil.which
    manager = Launchd(monkeypatch, tmp_path)
    workspace = tmp_path / 'creator-workspace'
    brand = crear_brand(workspace, 'ExampleBrand')
    # The real child sees either no uv or a discoverable uv that fails if used.
    # It must execute the installed interpreter directly in both environments.
    bin_dir = tmp_path / 'bin'; bin_dir.mkdir()
    if uv_available:
        uv = bin_dir / 'uv'
        uv.write_text('#!/bin/sh\nexit 97\n')
        uv.chmod(0o700)
    monkeypatch.setenv('PATH', str(bin_dir))
    monkeypatch.setattr(service.shutil, 'which', lambda name:
        '/fictional/launchctl' if name == 'launchctl' else real_which(name))

    def run(argv, **kwargs):
        if argv[0] == '/fictional/launchctl':
            return manager.run(argv, **kwargs)
        return real_run(argv, **kwargs)

    monkeypatch.setattr(service.subprocess, 'run', run)
    result = CliRunner().invoke(app, ['schedule-install', '--brand', 'ExampleBrand', '--root', str(workspace)])
    assert result.exit_code == 0, result.output
    plists = list(manager.directory.glob('com.socialcli.*.run-due.plist'))
    assert len(plists) == 1
    payload = plistlib.loads(plists[0].read_bytes())
    assert payload['Label'] == plists[0].stem
    assert payload['Label'] in manager.jobs
    assert payload['ProgramArguments'] == [sys.executable, '-m', 'socialctl', 'run-due',
        '--brand', 'ExampleBrand', '--root', str(workspace.resolve())]
    child_env = dict(os.environ)
    child_env.pop('PYTHONPATH', None)
    completed = subprocess.run(payload['ProgramArguments'], cwd=workspace, env=child_env,
        capture_output=True, text=True, timeout=20)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    state = json.loads((brand.raiz / '.socialctl/executor-state.json').read_text())
    assert state['heartbeat']['state'] == 'finished'
