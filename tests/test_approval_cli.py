"""Approval input must never silently block on an agent's open stdin pipe."""
import sys
import pytest
from socialctl import approval_cli

class OpenPipe:
    def isatty(self): return False
    def readline(self, *args): raise AssertionError('implicit stdin read')


def test_missing_noninteractive_approval_fails_without_reading(monkeypatch):
    monkeypatch.setattr(sys, 'stdin', OpenPipe())
    with pytest.raises(ValueError, match='--approval-fingerprint'):
        approval_cli.read_approval()


def test_explicit_fingerprint_does_not_read_stdin(monkeypatch):
    monkeypatch.setattr(sys, 'stdin', OpenPipe())
    assert approval_cli.read_approval('exact-digest') == 'exact-digest'


def test_explicit_stdin_reads_only_one_line(monkeypatch):
    import io
    stream = io.StringIO('exact-digest\nextra\n')
    monkeypatch.setattr(sys, 'stdin', stream)
    assert approval_cli.read_approval(approval_stdin=True) == 'exact-digest'
    assert stream.readline() == 'extra\n'


def test_conflicting_approval_sources_rejected(monkeypatch):
    monkeypatch.setattr(sys, 'stdin', OpenPipe())
    with pytest.raises(ValueError, match='Choose'):
        approval_cli.read_approval('exact-digest', approval_stdin=True)

@pytest.mark.parametrize('command', [
    ['publish'], ['schedule'], ['changes', 'apply'], ['meta', 'apply'],
    ['content', 'youtube-owned', 'apply'], ['content', 'youtube-assets', 'apply'],
    ['content', 'youtube-community', 'apply'], ['comments', 'apply'], ['comments', 'apply-draft'],
    ['content', 'youtube-analytics', 'apply'],
])
def test_commands_expose_explicit_approval_sources(command):
    from socialctl.cli import app
    from typer.testing import CliRunner
    from tests.terminal import plain
    result = CliRunner().invoke(app, [*command, '--help'], terminal_width=180)
    assert result.exit_code == 0, result.output
    text = plain(result.output)
    parsed = CliRunner().invoke(app, [*command, '--approval-fingerprint', 'wrong'])
    assert 'No such option' not in parsed.output
    assert '--approval-stdin' in text
