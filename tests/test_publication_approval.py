"""Exact preview approval binds the intended payload and cannot wait silently."""
import json
import re
import subprocess
import sys

import pytest
import yaml
from typer.testing import CliRunner
from socialctl.brands import crear_brand
from socialctl.cli import app
from socialctl.scheduler import ScheduleStore

runner = CliRunner()


def workspace(tmp_path):
    brand = crear_brand(tmp_path, 'Example')
    (brand.raiz / 'accounts.yml').write_text("facebook:\n  page_id: page-example\n")
    post = brand.dir_posts / 'example'
    post.mkdir()
    (post / 'post.yml').write_text(yaml.safe_dump({'slug':'example','campaign':'post-imagen','platforms':{'facebook':{'body':'Approved words','content_origin':'standalone'}}}))
    return brand


@pytest.mark.parametrize('command', ['publish', 'schedule'])
def test_noninteractive_open_stdin_fails_fast(tmp_path, command):
    brand = workspace(tmp_path)
    args = [command, 'example', '--brand', brand.nombre, '--root', str(tmp_path)]
    if command == 'schedule': args += ['--at', '2030-01-01T12:00:00Z']
    process = subprocess.Popen([sys.executable, '-m', 'socialctl', *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        # Keep the write end open: communicate() would close it and hide a hang.
        process.wait(timeout=5)
        out = process.stdout.read().decode()
        assert process.returncode == 1, out
        assert '--approval-fingerprint' in out
        assert not ScheduleStore(brand.raiz).load()
    finally:
        if process.poll() is None: process.kill(); process.wait()
        process.stdin.close(); process.stdout.close(); process.stderr.close()


def test_schedule_exact_digest_approves_without_yes_or_prompt(tmp_path):
    brand = workspace(tmp_path)
    args = ['schedule','example','--brand',brand.nombre,'--root',str(tmp_path),'--at','2030-01-01T12:00:00Z']
    preview = runner.invoke(app, [*args, '--dry-run'])
    digest = re.search(r'Approval digest: (\S+)', preview.output)[1]
    rejected = runner.invoke(app, [*args, '--approval-fingerprint', 'wrong'])
    assert rejected.exit_code == 1 and not ScheduleStore(brand.raiz).load()
    result = runner.invoke(app, [*args, '--approval-fingerprint', digest])
    assert result.exit_code == 0, result.output
    assert ScheduleStore(brand.raiz).get('example/facebook').status == 'approved'


def test_publish_preview_exposes_digest_and_rejects_stale_approval(tmp_path, monkeypatch):
    import socialctl.cli as cli
    brand = workspace(tmp_path)
    args = ['publish','example','--brand',brand.nombre,'--root',str(tmp_path)]
    preview = runner.invoke(app, [*args, '--dry-run'])
    match = re.search(r'Approval digest: (\S+)', preview.output)
    assert match, preview.output
    path = brand.dir_posts / 'example/post.yml'
    path.write_text(path.read_text().replace('Approved words','Changed words'))
    monkeypatch.setattr(cli, 'publicar', lambda *a, **k: pytest.fail('stale approval published'))
    result = runner.invoke(app, [*args, '--approval-fingerprint', match[1]])
    assert result.exit_code == 1 and 'does not match' in result.output
