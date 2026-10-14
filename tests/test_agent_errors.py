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


@pytest.mark.parametrize('kind,read_only,action,retryable',[
    (httpx.ReadTimeout,True,'retry_later',True),
    (httpx.ConnectError,True,'retry_later',True),
    (httpx.ReadTimeout,False,'reconcile',False),
])
def test_typed_transport_context_controls_retry(kind,read_only,action,retryable):
    from socialctl.agent_errors import error_document
    error=kind('https://example.test/?access_token=never-print')
    result=error_document(error,read_only=read_only)
    assert (result['action'],result['retryable'])==(action,retryable)
    assert 'never-print' not in json.dumps(result)


@pytest.mark.parametrize('status,action,retryable',[(429,'retry_later',True),(503,'retry_later',True),
    (401,'user_action',False),(403,'user_action',False),(404,'correct_input',False)])
def test_http_status_is_typed_evidence_without_inventing_reconnection(status,action,retryable):
    from socialctl.agent_errors import error_document
    request=httpx.Request('GET','https://example.test/')
    error=httpx.HTTPStatusError('private provider message',request=request,response=httpx.Response(status,request=request))
    result=error_document(error,read_only=True)
    assert (result['action'],result['retryable'])==(action,retryable)
    assert 'private provider' not in json.dumps(result)


def test_post_transport_failure_inside_read_flow_is_not_blindly_retried():
    from socialctl.agent_errors import error_document
    error=httpx.ReadTimeout('refresh uncertain',request=httpx.Request('POST','https://example.test/token'))
    result=error_document(error,read_only=True)
    assert result['action']=='reconcile' and result['retryable'] is False


@pytest.mark.parametrize('kind,code', [('deadline','read_deadline'),('size','response_too_large')])
def test_read_limits_request_adjustment_not_same_request_replay(kind,code):
    from socialctl.agent_errors import error_document
    from socialctl.read_budget import ReadDeadlineExceeded,ReadResponseTooLarge
    exc=ReadDeadlineExceeded() if kind=='deadline' else ReadResponseTooLarge('private')
    result=error_document(exc,read_only=True)
    assert result['code']==code and result['action']=='adjust_read'
    assert result['retryable'] is False


def test_auth_failure_does_not_infer_expiry_from_its_message():
    from socialctl.agent_errors import error_document
    from socialctl.auth import AuthError
    result=error_document(AuthError('expired reconnect immediately'),read_only=True)
    assert result['code']=='authorization_unavailable'
    assert result['action']=='user_action' and result['retryable'] is False


def test_known_uncertain_provider_write_requires_reconciliation():
    from socialctl.agent_errors import error_document
    from socialctl.management.youtube_resources import ResourceUncertain
    result=error_document(ResourceUncertain('private'),read_only=False)
    assert result['action']=='reconcile' and result['retryable'] is False


@pytest.mark.parametrize('method',['GET','POST'])
@pytest.mark.parametrize('failure',['deadline','oversize'])
def test_dispatched_budget_limits_preserve_write_uncertainty(method,failure,monkeypatch):
    import asyncio
    import socialctl.read_budget as reads
    from socialctl.agent_errors import error_document
    from socialctl.read_reports import ReadReport
    monkeypatch.setattr(reads,'MAX_READ_BYTES',16)
    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self,request):
            if failure=='deadline':await asyncio.sleep(1)
            return httpx.Response(200,content=b'x'*17)
    budget=reads.ReadBudget(.05 if failure=='deadline' else 5)
    with httpx.Client(transport=reads.BudgetTransport(Transport(),budget)) as client:
        with pytest.raises((reads.ReadDeadlineExceeded,reads.ReadResponseTooLarge)) as caught:
            client.request(method,'https://example.test/token')
    error=error_document(caught.value,read_only=True)
    report=ReadReport(status='error',errors=[error])
    assert report.errors[0]['action']==('adjust_read' if method=='GET' else 'reconcile')
    assert report.errors[0]['retryable'] is False
