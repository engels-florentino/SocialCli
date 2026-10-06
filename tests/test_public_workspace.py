"""Installed CLIs must write user data to a workspace, never site-packages."""
import os
from pathlib import Path
import subprocess
import sys


def _create(cwd, env, name):
    env = {**os.environ, **env, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    return subprocess.run(
        [sys.executable, "-c", "from socialctl.cli import app; app()", "brand", "new", name],
        cwd=cwd, env=env, text=True, capture_output=True, timeout=20,
    )


def test_new_brand_defaults_to_users_working_directory(tmp_path):
    result = _create(tmp_path, {"SOCIALCLI_ROOT": ""}, "WorkingExample")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "WorkingExample" / "accounts.yml").is_file()


def test_environment_workspace_overrides_working_directory(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = _create(tmp_path, {"SOCIALCLI_ROOT": str(workspace)}, "EnvironmentExample")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (workspace / "EnvironmentExample" / "accounts.yml").is_file()
    assert not (tmp_path / "EnvironmentExample").exists()
