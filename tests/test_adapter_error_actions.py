"""Recovery actions survive real adapter boundaries over offline transports."""
import json

import httpx
import pytest
from typer.testing import CliRunner

from socialctl.agent_errors import error_document
from socialctl.brands import crear_brand
from socialctl.cli import app
from socialctl.models import Platform


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('live HTTP forbidden')
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', blocked)


def brand_at(tmp_path):
    brand = crear_brand(tmp_path, 'Example')
    brand.cuentas = {'youtube': {'channel_id': 'channel-a'}, 'facebook': {'page_id': '123'}}
    (brand.raiz / 'accounts.yml').write_text('youtube:\n  channel_id: channel-a\nfacebook:\n  page_id: "123"\n')
    for platform in (Platform.YOUTUBE, Platform.FACEBOOK):
        brand.guardar_secreto(platform, {'access_token': 'PRIVATE_TOKEN'})
    return brand


def request_through(kind, brand, http, method='GET'):
    if kind == 'youtube':
        from socialctl.management.youtube import YouTubeManagementClient
        return YouTubeManagementClient(brand, http).inspect('video-1')
    if kind == 'resources':
        from socialctl.management.youtube_resources import YouTubeResourcesClient
        return YouTubeResourcesClient(brand, http)._request(method, 'https://www.googleapis.com/youtube/v3/videos')
    if kind == 'comments':
        from socialctl.management.meta_comments import MetaCommentsClient
        return MetaCommentsClient(brand, Platform.FACEBOOK, http)._request(method, '123/comments')
    from socialctl.management.meta_client import MetaClient
    return MetaClient(brand, Platform.FACEBOOK, http).request(method, '123/comments')


@pytest.mark.parametrize('kind', ['youtube', 'resources', 'comments', 'meta'])
@pytest.mark.parametrize('failure,code,action,retryable', [
    ('timeout', 'transport_failure', 'retry_later', True),
    (429, 'rate_limited', 'retry_later', True),
    (503, 'provider_unavailable', 'retry_later', True),
    (401, 'authorization_unavailable', 'user_action', False),
    (403, 'permissions_required', 'user_action', False),
    (404, 'target_unavailable', 'correct_input', False),
])
def test_adapter_read_retains_typed_cause_without_provider_text(tmp_path, kind, failure, code, action, retryable):
    brand = brand_at(tmp_path)
    def transport(request):
        if failure == 'timeout':
            raise httpx.ReadTimeout('PRIVATE_TOKEN', request=request)
        return httpx.Response(failure, json={'error': {'message': 'expired reconnect PRIVATE_TOKEN'}})
    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        with pytest.raises(Exception) as caught:
            request_through(kind, brand, http)
    detail = error_document(caught.value, read_only=True)
    assert (detail['code'], detail['action'], detail['retryable']) == (code, action, retryable)
    assert 'PRIVATE_TOKEN' not in str(caught.value) + json.dumps(detail)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize('kind', ['resources', 'comments', 'meta'])
@pytest.mark.parametrize('failure', ['timeout', 503])
def test_wrapped_write_stays_uncertain_even_in_read_classifier(tmp_path, kind, failure):
    brand = brand_at(tmp_path)
    requests = []
    def transport(request):
        requests.append(request.method)
        if failure == 'timeout':
            raise httpx.ReadTimeout('PRIVATE_TOKEN', request=request)
        return httpx.Response(failure, json={})
    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        with pytest.raises(Exception) as caught:
            request_through(kind, brand, http, 'POST')
    detail = error_document(caught.value, read_only=True)
    assert detail['action'] == 'reconcile' and detail['retryable'] is False
    assert requests == ['POST']


@pytest.mark.parametrize('kind', ['youtube', 'resources'])
def test_token_refresh_timeout_remains_uncertain_through_nested_wrappers(tmp_path, kind):
    brand = brand_at(tmp_path)
    brand.guardar_secreto(Platform.YOUTUBE, {'access_token': 'PRIVATE_TOKEN', 'refresh_token': 'PRIVATE_REFRESH', 'expira_en': 1})
    methods = []
    def transport(request):
        methods.append(request.method)
        raise httpx.ReadTimeout('PRIVATE_REFRESH', request=request)
    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        with pytest.raises(Exception) as caught:
            request_through(kind, brand, http)
    detail = error_document(caught.value, read_only=True)
    assert detail['action'] == 'reconcile' and detail['retryable'] is False
    assert methods == ['POST']
    assert 'PRIVATE_REFRESH' not in str(caught.value) + json.dumps(detail)


@pytest.mark.parametrize('code,action', [('provider_authorization_expired', 'reconnect'), ('renewal_uncertain', 'reconcile')])
def test_broker_code_survives_auth_and_resource_wrapper(tmp_path, monkeypatch, code, action):
    from socialctl.connections.saas_client import ServiceRejected
    brand = brand_at(tmp_path)
    brand.guardar_secreto(Platform.YOUTUBE, {'auth_mode': 'broker'})
    def denied(*args, **kwargs):
        raise ServiceRejected(code, 'PRIVATE_TOKEN', 409)
    monkeypatch.setattr('socialctl.connections.client.broker_access_token', denied)
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail('provider must not be contacted'))) as http:
        with pytest.raises(Exception) as caught:
            request_through('resources', brand, http)
    detail = error_document(caught.value, read_only=True)
    assert detail['action'] == action and not detail['retryable']
    assert 'PRIVATE_TOKEN' not in str(caught.value) + json.dumps(detail)


@pytest.mark.parametrize('command', ['list', 'sync'])
def test_partial_comments_cli_keeps_rows_and_typed_next_action(tmp_path, monkeypatch, command):
    from tests.test_meta_comments import Graph
    brand_at(tmp_path)
    api = Graph()
    original = api.__call__
    def transport(request):
        if request.url.path.endswith('/comments'):
            if request.url.params.get('after'):
                raise httpx.ReadTimeout('PRIVATE_TOKEN', request=request)
            return httpx.Response(200, json={'data': [{**api.row('800', 'Verified observation', author='999'), 'created_time': '2026-10-10T10:00:00Z'}],
                'paging': {'next': 'https://graph.facebook.com/ignored', 'cursors': {'after': 'cursor-2'}}})
        return original(request)
    # Match the fictional media ownership expected by the existing list operation.
    monkeypatch.setattr('socialctl.management.comments_cli.make_http_client', lambda: httpx.Client(transport=httpx.MockTransport(transport)))
    result = CliRunner().invoke(app, ['comments', command, '789', '--platform', 'facebook', '--brand', 'Example', '--root', str(tmp_path), '--json'])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload['complete'] is False
    if command == 'list':
        assert payload['data'][0]['id'] == '800'
        assert payload['next_cursor'] == 'cursor-2'
    else:
        from socialctl.management.meta_comment_inbox import CommentInboxStore
        assert CommentInboxStore(tmp_path/'Example').load().comments[0].comment_id == '800'
        assert payload['provider_cursor'] == 'cursor-2'
    assert payload['error_detail']['code'] == 'transport_failure'
    assert payload['error_detail']['retryable'] is True
    assert 'PRIVATE_TOKEN' not in result.stdout


@pytest.mark.parametrize('kind', ['youtube', 'resources', 'comments', 'meta'])
@pytest.mark.parametrize('limit', ['deadline', 'size'])
def test_adapter_preserves_dispatched_read_limits(tmp_path, kind, limit):
    from socialctl.read_budget import ReadDeadlineExceeded, ReadResponseTooLarge
    brand = brand_at(tmp_path)
    def transport(request):
        error = ReadDeadlineExceeded() if limit == 'deadline' else ReadResponseTooLarge('PRIVATE_TOKEN')
        error.request = request
        raise error
    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        with pytest.raises(Exception) as caught:
            request_through(kind, brand, http)
    detail = error_document(caught.value, read_only=True)
    assert detail['code'] == ('read_deadline' if limit == 'deadline' else 'response_too_large')
    assert detail['action'] == 'adjust_read' and not detail['retryable']


@pytest.mark.parametrize('failure', [503, 'size'])
def test_refresh_response_failure_cannot_be_retried_as_read(tmp_path, failure):
    from socialctl.read_budget import ReadResponseTooLarge
    brand = brand_at(tmp_path)
    brand.guardar_secreto(Platform.YOUTUBE, {'access_token': 'PRIVATE_TOKEN', 'expira_en': 1})
    def transport(request):
        if failure == 'size':
            raise ReadResponseTooLarge('PRIVATE_TOKEN', request=request)
        return httpx.Response(503, json={})
    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        with pytest.raises(Exception) as caught:
            request_through('resources', brand, http)
    detail = error_document(caught.value, read_only=True)
    assert detail['action'] == 'reconcile' and not detail['retryable']


@pytest.mark.parametrize('kind', ['youtube', 'meta'])
def test_paginated_result_preserves_rows_and_http_failure_hint(tmp_path, kind):
    from socialctl.read_reports import annotate_partial_read
    brand = brand_at(tmp_path)
    def transport(request):
        if request.url.path.endswith('/channels'):
            return httpx.Response(200, json={'items': [{'id': 'channel-a'}], 'pageInfo': {'totalResults': 1, 'resultsPerPage': 5}})
        if request.url.params.get('pageToken') or request.url.params.get('after'):
            return httpx.Response(429, json={})
        if kind == 'youtube':
            return httpx.Response(200, json={'items': [{'id': 'retained'}], 'nextPageToken': 'next-2', 'pageInfo': {'totalResults': 2, 'resultsPerPage': 50}})
        return httpx.Response(200, json={'data': [{'id': '800'}], 'paging': {'next': 'https://ignored.test/', 'cursors': {'after': 'next-2'}}})
    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        if kind == 'youtube':
            from socialctl.management.youtube_community import YouTubeCommunityClient
            result = YouTubeCommunityClient(brand, http)._pages('comments', {}, lambda row: row['id'])
        else:
            from socialctl.management.meta_client import MetaClient
            result = MetaClient(brand, Platform.FACEBOOK, http).listing('123', 'comments', fields='id')
    result = annotate_partial_read(result)
    assert (result.get('items') or result.get('data'))[0]['id'] == ('retained' if kind == 'youtube' else '800')
    assert result['complete'] is False and result['absence_proven'] is False
    assert result['error_detail']['code'] == 'rate_limited' and result['error_detail']['retryable'] is True


def test_hint_does_not_override_uncertain_type_or_trust_unknown_exception():
    from socialctl.management.meta_schema import MetaUncertain, MetaError
    uncertain = MetaUncertain('PRIVATE_TOKEN')
    uncertain._socialcli_error_code = 'transport_failure'
    assert error_document(uncertain, read_only=True)['action'] == 'reconcile'
    unknown = RuntimeError('provider_authorization_expired')
    unknown._socialcli_error_code = 'provider_authorization_expired'
    assert error_document(unknown, read_only=True)['action'] == 'inspect'
    wrapped = MetaError('provider_authorization_expired')
    wrapped._socialcli_error_code = 'PRIVATE_TOKEN'
    detail = error_document(wrapped, read_only=True)
    assert detail['code'] == 'operation_failed' and not detail['retryable']
    assert 'PRIVATE_TOKEN' not in json.dumps(detail)


@pytest.mark.parametrize('kind', ['youtube', 'resources', 'comments', 'meta'])
def test_lost_saas_token_post_is_not_advertised_as_safe_read_retry(tmp_path, monkeypatch, kind):
    brand = brand_at(tmp_path)
    platform = Platform.YOUTUBE if kind in {'youtube', 'resources'} else Platform.FACEBOOK
    brand.guardar_secreto(platform, {'auth_mode': 'broker', 'version': 1, 'protocol': 'saas-v1',
        'connection_id': 'fictional-connection', 'account_id': 'channel-a' if platform is Platform.YOUTUBE else '123',
        'service_url': 'https://social.example'})
    monkeypatch.setattr('socialctl.connections.client.keychain.get', lambda *args: 'fictional-connection-secret')
    methods = []
    def transport(request):
        methods.append(request.method)
        raise httpx.ReadTimeout('PRIVATE_TOKEN', request=request)
    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        with pytest.raises(Exception) as caught:
            request_through(kind, brand, http)
    assert methods == ['POST']
    detail = error_document(caught.value, read_only=True)
    assert detail['action'] == 'reconcile' and detail['retryable'] is False
    assert 'PRIVATE_TOKEN' not in str(caught.value) + json.dumps(detail)


def test_metrics_snapshot_retains_refresh_uncertainty(tmp_path):
    from socialctl.metricas.base import leer_red
    import socialctl.metricas.youtube  # Register the actual reader.
    brand = brand_at(tmp_path)
    brand.guardar_secreto(Platform.YOUTUBE, {'access_token': 'PRIVATE_TOKEN', 'expira_en': 1})
    def transport(request):
        raise httpx.ReadTimeout('PRIVATE_TOKEN', request=request)
    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        reading = leer_red(Platform.YOUTUBE, brand, http, None)
    assert reading.failure_code == 'operation_uncertain'
    assert 'PRIVATE_TOKEN' not in reading.model_dump_json()


@pytest.mark.parametrize('stage', ['enumeration', 'comments', 'replies'])
def test_unified_inbox_retains_typed_partial_failure(tmp_path, monkeypatch, stage):
    from datetime import datetime, timezone
    from socialctl import inbox as mod
    from socialctl.read_budget import ReadBudget
    from tests.test_meta_comments import service
    from tests.test_meta_comment_inbox import remote_row
    _, brand, api, _, _ = service(tmp_path)
    comment = remote_row(api, '800', 'Verified observation', created='2026-10-10T10:00:00Z')
    def transport(request):
        path = request.url.path
        enumeration = path.endswith('/123/posts') or path.endswith('/123/feed')
        comments = path.endswith('/789/comments')
        replies = path.endswith('/800/comments')
        if (stage == 'enumeration' and enumeration or stage == 'comments' and comments) and request.url.params.get('after'):
            return httpx.Response(429, json={})
        if stage == 'replies' and replies:
            raise httpx.ReadTimeout('PRIVATE_TOKEN', request=request)
        if enumeration or comments or replies:
            body = {'data': [{'id': '789', 'from': {'id': '123'}}] if enumeration else [comment] if comments else []}
            if stage == 'enumeration' and enumeration or stage == 'comments' and comments:
                body['paging'] = {'next': 'https://ignored.test/', 'cursors': {'after': 'next-2'}}
            return httpx.Response(200, json=body)
        return api(request)
    monkeypatch.setattr(mod, 'read_client', lambda *a, **k: httpx.Client(transport=httpx.MockTransport(transport)))
    report = mod.sync_brand_inbox(brand, since=datetime(2026, 10, 10, tzinfo=timezone.utc), budget=ReadBudget(10))
    assert [item['comment_id'] for item in report.data['items']] == ['800']
    errors = [error for error in report.errors if error['platform'] == 'facebook']
    assert any(error['code'] == ('transport_failure' if stage == 'replies' else 'rate_limited') and error['retryable'] for error in errors)
    assert not next(row for row in report.coverage if row['platform'] == 'facebook')['complete']
    assert not api.writes


def test_youtube_inbox_keeps_partial_thread_code_and_observations(tmp_path):
    from tests.test_youtube_community import service, comment, thread
    from socialctl.inbox import _youtube_rows
    _, api, client, _ = service(tmp_path)
    top = comment(author='viewer')
    top['snippet']['publishedAt'] = '2026-10-10T10:00:00Z'
    first = thread(top=top)
    first['snippet']['totalReplyCount'] = 0
    def transport(request):
        if request.url.path.endswith('/commentThreads'):
            if request.url.params.get('pageToken'):
                return httpx.Response(429, json={})
            return httpx.Response(200, json={'items': [first], 'nextPageToken': 'next-2',
                'pageInfo': {'totalResults': 2, 'resultsPerPage': 50}})
    api.on_read = transport
    rows, complete, errors = _youtube_rows(client, 'video-1')
    assert rows[0]['comment_id'] == 'UgxTop' and not complete
    assert errors[0]['code'] == 'rate_limited'
    assert not api.writes
