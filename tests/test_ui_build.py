"""Generated web and wheel bundles must not silently diverge."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_build_detects_stale_assets_and_repairs_both_targets(tmp_path):
    import shutil
    shutil.copytree(ROOT / 'socialctl/ui/assets', tmp_path / 'socialctl/ui/assets')
    script = ROOT / 'scripts/build_ui_assets.py'
    run = lambda *args: subprocess.run([sys.executable, str(script), '--root', str(tmp_path), *args], capture_output=True)
    assert run().returncode == 0
    web = tmp_path / 'docs/style.css'
    wheel = tmp_path / 'socialctl/connection_service/assets/connection.css'
    old_web, old_wheel = web.read_text(), wheel.read_text()
    tokens = tmp_path / 'socialctl/ui/assets/tokens.css'
    tokens.write_text(tokens.read_text().replace('#1464ec', '#123abc'))
    assert run('--check').returncode == 1
    assert run().returncode == 0
    assert web.read_text() != old_web and wheel.read_text() != old_wheel
    assert '#123abc' in web.read_text() and '#123abc' in wheel.read_text()
    assert run('--check').returncode == 0


def test_committed_bundles_are_current():
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/build_ui_assets.py'), '--check'], capture_output=True)
    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
