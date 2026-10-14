"""Typed next actions must not turn uncertain writes into automatic retries."""
import json
import httpx
import pytest
from typer.testing import CliRunner

from socialctl.cli import app
from socialctl.brands import crear_brand
from socialctl.connections.saas_client import SaaSClient


@pytest.mark.parametrize('code,status,action,retryable', [
    ('provider_authorization_expired', 409, 'reconnect', False),
    ('provider_permissions_changed', 409, 'reconnect', False),
    ('renewal_uncertain', 409, 'reconcile', False),
    ('renewal_pending', 409, 'retry_later', True),
    ('subscription_required', 402, 'user_action', False),
    ('installation_revoked', 401, 'sign_in', False),
    ('activation_required', 409, 'recover', False),
    ('new_code_password=never-print', 409, 'inspect', False),
])
def test_service_error_exposes_safe_action_not_provider_text(code, status, action, retryable):
    transport = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(status, json={'detail': code})))
    client = SaaSClient('https://social.example', transport)
    with pytest.raises(ValueError) as caught:
        client.request('GET', '/api/saas/v1/capabilities')
    from socialctl.agent_errors import error_document
    result = error_document(caught.value, read_only=True)
    assert result['action'] == action
    assert result['retryable'] is retryable
    assert 'never-print' not in json.dumps(result)


def test_write_transport_failure_requires_reconciliation():
    from socialctl.agent_errors import error_document
    from socialctl.connections.saas_client import TransportFailure
    result = error_document(TransportFailure('private transport detail'), read_only=False)
    assert result['action'] == 'reconcile'
    assert result['retryable'] is False
    assert 'private transport detail' not in json.dumps(result)


def test_account_status_json_failure_is_parseable_and_preserves_stderr_boundary(tmp_path):
    crear_brand(tmp_path, 'Example')
    result = CliRunner().invoke(app, ['account', 'status', '--brand', 'Example', '--root', str(tmp_path), '--json'])
    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert report['status'] == 'error'
    assert report['error']['action'] == 'user_action'
    assert report['error']['retryable'] is False
