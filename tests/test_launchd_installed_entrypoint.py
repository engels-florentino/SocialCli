"""The installed queue agent runs from a creator workspace outside the project."""

import plistlib
import subprocess
import sys

import pytest

from typer.testing import CliRunner

from socialctl.brands import crear_brand
from socialctl.cli import app


@pytest.mark.parametrize("uv_available", [False, True])
def test_installed_agent_runs_from_external_workspace(tmp_path, monkeypatch, uv_available):
    workspace = tmp_path / "creator-workspace"
    workspace.mkdir()
    crear_brand(workspace, "ExampleBrand")
    home = tmp_path / "user-home"
    home.mkdir()
    monkeypatch.setattr("socialctl.cli.Path.home", lambda: home)
    # Avoid loading an actual launchd agent; inspect and execute its real command.
    monkeypatch.setattr("socialctl.cli.shutil.which", lambda name: "/unused/uv" if uv_available and name == "uv" else None)
    result = CliRunner().invoke(app, ["schedule-install", "--brand", "ExampleBrand", "--root", str(workspace)])
    assert result.exit_code == 0, result.output
    plist = home / "Library" / "LaunchAgents" / "com.socialctl.examplebrand.run-due.plist"
    payload = plistlib.loads(plist.read_bytes())
    assert payload["ProgramArguments"][:3] == [sys.executable, "-m", "socialctl"]
    completed = subprocess.run(payload["ProgramArguments"], cwd=workspace, capture_output=True, text=True, timeout=20)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (workspace / "ExampleBrand" / ".socialctl" / "executor-state.json").is_file()
