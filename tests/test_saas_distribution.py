"""Agent bootstrap is discoverable without exposing any account handoff."""
from importlib.metadata import version
from typer.testing import CliRunner
from socialctl.cli import app
from tests.test_saas_auth import service


def test_installed_version_can_be_checked_without_a_workspace():
    result=CliRunner().invoke(app,['--version'])
    assert result.exit_code==0
    assert result.stdout.strip()=='SocialCli '+version('socialcli')


def test_public_agent_instructions_are_packaged_plain_text_without_identity_calls(tmp_path):
    client,now,calls=service(tmp_path)
    reply=client.get('/agents')
    assert reply.status_code==200
    assert reply.headers['content-type'].startswith('text/plain')
    for text in ('0.3.0rc16','Candidate','--no-browser','14 days','USD 10','Do not open','--dry-run'):
        assert text in reply.text
    assert calls==[]
    assert 'set-cookie' not in reply.headers
