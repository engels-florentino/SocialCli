"""User-only Instagram operations stay server-side and never replay writes."""
import hashlib
import json
import secrets
import uuid
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from socialctl.connection_service.settings import AppCredentials
from tests.test_connection_service import begin_browser
from tests.test_saas_auth import service, login, fields
from tests.test_saas_connections import new_installation, selected, activate, token


class MetaProvider:
    def __init__(self):
        self.scopes = ['pages_show_list', 'pages_read_engagement', 'instagram_basic',
                       'instagram_content_publish', 'instagram_manage_comments',
                       'instagram_manage_contents', 'pages_manage_metadata']
        self.writes = []
        self.timeout = False
        self.media_owner = '456'
        self.page_tasks = ['MANAGE']
        self.after_write = None

    def __call__(self, request):
        path = request.url.path.removeprefix('/v26.0/')
        if path == 'oauth/access_token':
            return httpx.Response(200, json={'access_token': 'USER-PRIVATE', 'expires_in': 5184000})
        assert request.headers['authorization'] == 'Bearer USER-PRIVATE'
        if request.method != 'GET':
            self.writes.append((request.method, path))
            if self.after_write:
                self.after_write()
            if self.timeout:
                raise httpx.ReadTimeout('USER-PRIVATE', request=request)
            return httpx.Response(200, json={'success': True, 'deleted_id': '789', 'id': '901'})
        if path == 'me/permissions':
            return httpx.Response(200, json={'data': [{'permission': s, 'status': 'granted'} for s in self.scopes]})
        if path == 'me/accounts':
            return httpx.Response(200, json={'data': [{'id': '123', 'name': 'Fictional Page',
                'tasks': self.page_tasks, 'access_token': 'PAGE-PRIVATE',
                'instagram_business_account': {'id': '456', 'username': 'fictional'}}]})
        if path == 'me':
            return httpx.Response(200, json={'id': '111'})
        if path == '789':
            return httpx.Response(200, json={'id': '789', 'owner': {'id': self.media_owner},
                'caption': 'Disposable fixture', 'media_type': 'IMAGE', 'media_product_type': 'FEED'})
        if path == '456':
            return httpx.Response(200, json={'id': '456', 'mentioned_comment': {
                'id': '800', 'text': 'Consenting test mention', 'media': {'id': '789'}}})
        return httpx.Response(404)


def connected(tmp_path, graph=None):
    graph = graph or MetaProvider()
    client, now, _ = service(tmp_path, provider=graph)
    client.app.state.saas_connections.providers.settings.providers['instagram'] = AppCredentials('fake-app', 'fake-secret')
    login(client)
    cap = new_installation(client)
    proof = secrets.token_urlsafe(32)
    start = client.post('/api/saas/v1/installations/' + cap['installation_id'] + '/authorizations',
        headers={'Authorization': 'Bearer ' + cap['installation_secret']}, json={
            'platform': 'instagram', 'brand': 'MyBrand', 'management': True,
            'poll_challenge': hashlib.sha256(proof.encode()).hexdigest()})
    assert start.status_code == 201, start.text
    value = start.json()
    browser = begin_browser(client, value['browser_url'])
    state = parse_qs(urlsplit(browser.headers['location']).query)['state'][0]
    page = client.get('/oauth/instagram/callback', params={'state': state, 'code': 'fake-code'})
    assert page.status_code == 200, page.text
    form = fields(page); form['account_id'] = '456'
    choice = client.post('/account/connect/' + value['authorization_id'], data=form,
                        headers={'Origin': 'https://social.example'})
    assert choice.status_code == 200, choice.text
    result = selected(client, value, {'Authorization': 'Bearer ' + proof})
    assert activate(client, result).status_code == 200
    return client, now, graph, result


EDIT = {'action': 'media-delete', 'target_id': '789'}


class GranularMetaProvider(MetaProvider):
    """Real-provider shape: accounts edge empty, explicit asset grants present."""
    def __init__(self):
        super().__init__()
        self.empty_accounts = False
        self.debug_calls = 0
        self.metadata_patch = {}
        self.bad_scope = None
        self.page_id = '123'
        self.linked_ig = '456'
        self.association = None
        self.accounts_response = None

    def __call__(self, request):
        path = request.url.path.removeprefix('/v26.0/')
        if request.method == 'GET' and path == 'debug_token':
            self.debug_calls += 1
            assert request.headers['authorization'] == 'Bearer fake-app|fake-secret'
            assert request.url.params['input_token'] == 'USER-PRIVATE'
            scopes = ['pages_show_list', 'pages_read_engagement', 'instagram_basic',
                      'instagram_manage_contents', 'instagram_manage_comments']
            metadata = {'is_valid': True, 'app_id': 'fake-app', 'type': 'USER',
                        'user_id': '111', 'scopes': scopes, 'granular_scopes': [
                            {'scope': s, 'target_ids': ['999' if s == self.bad_scope else
                             ('123' if s.startswith('pages_') else '456')]} for s in scopes]}
            return httpx.Response(200, json={'data': {**metadata, **self.metadata_patch}})
        if request.method == 'GET' and path == 'me/accounts' and self.empty_accounts:
            return httpx.Response(200, json=self.accounts_response or {'data': []})
        if request.method == 'GET' and path == '123':
            assert request.headers['authorization'] == 'Bearer USER-PRIVATE'
            assert request.url.params['fields'] == 'id,instagram_business_account'
            return httpx.Response(200, json={'id': self.page_id,
                'instagram_business_account': self.association if self.association is not None else {'id': self.linked_ig}})
        return super().__call__(request)


@pytest.mark.parametrize('mention', [False, True])
def test_empty_accounts_uses_exact_current_granular_asset_authorization(tmp_path, mention):
    graph = GranularMetaProvider()
    client, _, _, connection = connected(tmp_path, graph)
    graph.empty_accounts = True
    edit = {'action': 'mention-reply', 'target_id': '456', 'media_id': '789',
            'comment_id': '800', 'message': 'Approved test reply'} if mention else EDIT
    approved = proposal(client, connection, edit)
    response = call(client, connection, 'mutate', edit=edit, **approved)
    assert response.status_code == 200, response.text
    assert response.json()['identity']['token_mode'] == 'facebook_user'
    assert graph.writes == [('POST', '456/mentions')] if mention else graph.writes == [('DELETE', '789')]
    assert 'PRIVATE' not in response.text and 'fake-secret' not in response.text


@pytest.mark.parametrize('invalid', [
    {'is_valid': False}, {'app_id': 'other-app'}, {'user_id': '222'}, {'type': 'PAGE'},
    {'scopes': []}, {'granular_scopes': []}, {'granular_scopes': 'malformed'},
])
def test_empty_accounts_rejects_unbound_debug_metadata_before_delete(tmp_path, invalid):
    graph = GranularMetaProvider()
    client, _, _, connection = connected(tmp_path, graph)
    approved = proposal(client, connection)
    graph.empty_accounts = True
    graph.metadata_patch = invalid
    assert call(client, connection, 'mutate', **approved).status_code == 400
    assert not graph.writes


@pytest.mark.parametrize('scope', ['pages_show_list', 'pages_read_engagement',
                                  'instagram_basic', 'instagram_manage_contents'])
def test_granular_permission_for_another_asset_cannot_authorize_delete(tmp_path, scope):
    graph = GranularMetaProvider()
    client, _, _, connection = connected(tmp_path, graph)
    approved = proposal(client, connection)
    graph.empty_accounts = True
    graph.bad_scope = scope
    assert call(client, connection, 'mutate', **approved).status_code == 400
    assert not graph.writes


@pytest.mark.parametrize('attribute', ['page_id', 'linked_ig'])
def test_granular_grants_do_not_replace_current_page_instagram_link(tmp_path, attribute):
    graph = GranularMetaProvider()
    client, _, _, connection = connected(tmp_path, graph)
    approved = proposal(client, connection)
    graph.empty_accounts = True
    setattr(graph, attribute, '999')
    assert call(client, connection, 'mutate', **approved).status_code == 400
    assert not graph.writes


@pytest.mark.parametrize('accounts', [
    {'data': [], 'paging': {'next': 'https://graph.facebook.com/next'}},
    {'data': [{'id': '123', 'tasks': [], 'instagram_business_account': {'id': '456'}}]},
    {'data': [{'id': '999', 'tasks': ['MANAGE'], 'instagram_business_account': {'id': '456'}}]},
])
def test_granular_fallback_does_not_override_incomplete_or_conflicting_page_evidence(tmp_path, accounts):
    graph = GranularMetaProvider()
    client, _, _, connection = connected(tmp_path, graph)
    approved = proposal(client, connection)
    graph.empty_accounts = True
    graph.accounts_response = accounts
    assert call(client, connection, 'mutate', **approved).status_code == 400
    assert graph.debug_calls == 0 and not graph.writes


def test_granular_asset_revoked_after_preview_prevents_delete(tmp_path):
    graph = GranularMetaProvider()
    client, _, _, connection = connected(tmp_path, graph)
    graph.empty_accounts = True
    approved = proposal(client, connection)
    graph.bad_scope = 'instagram_manage_contents'
    assert call(client, connection, 'mutate', **approved).status_code == 400
    assert not graph.writes


@pytest.mark.parametrize('association', ['malformed', ['456']])
def test_malformed_granular_page_association_is_sanitized_rejection(tmp_path, association):
    graph = GranularMetaProvider()
    client, _, _, connection = connected(tmp_path, graph)
    approved = proposal(client, connection)
    graph.empty_accounts = True
    graph.association = association
    response = call(client, connection, 'mutate', **approved)
    assert response.status_code == 400
    assert not graph.writes and 'PRIVATE' not in response.text


@pytest.mark.parametrize('entries', [
    [{'scope': 'instagram_basic', 'target_ids': ['456']}] * 2,
    [{'scope': 'instagram_basic'}],
    [{'scope': 'instagram_basic', 'target_ids': '456'}],
    [{'scope': 'instagram_basic', 'target_ids': [{'id': '456'}]}],
])
def test_ambiguous_or_malformed_granular_targets_block_delete(tmp_path, entries):
    graph = GranularMetaProvider()
    client, _, _, connection = connected(tmp_path, graph)
    approved = proposal(client, connection)
    graph.empty_accounts = True
    graph.metadata_patch = {'granular_scopes': entries + [
        {'scope': s, 'target_ids': ['123' if s.startswith('pages_') else '456']}
        for s in ('pages_show_list', 'pages_read_engagement', 'instagram_manage_contents')]}
    assert call(client, connection, 'mutate', **approved).status_code == 400
    assert not graph.writes


def call(client, connection, phase='inspect', edit=None, **extra):
    return client.post('/api/saas/v1/connections/' + connection['connection_id'] + '/instagram-management',
        headers={'Authorization': 'Bearer ' + connection['connection_secret']},
        json={'phase': phase, 'edit': edit or EDIT, **extra})


def proposal(client, connection, edit=None):
    observed = call(client, connection, edit=edit)
    assert observed.status_code == 200, observed.text
    return {'operation_id': str(uuid.uuid4()), 'expected_identity': observed.json()['identity'],
            'expected_before': observed.json()['before']}


def test_selected_user_credential_is_retained_but_never_exported_by_token_or_inspection(tmp_path):
    client, now, graph, connection = connected(tmp_path)
    assert token(client, connection, False).json()['access_token'] == 'PAGE-PRIVATE'
    response = call(client, connection)
    assert response.status_code == 200, response.text
    assert response.json()['identity'] == {'actor_id': '111', 'token_mode': 'facebook_user',
                                           'page_id': '123', 'account_id': '456'}
    assert response.json()['before']['owner']['id'] == '456'
    assert 'PRIVATE' not in response.text and not graph.writes
    assert 'USER-PRIVATE' not in token(client, connection, False).text


def test_owned_delete_is_durable_and_same_operation_returns_receipt_without_replay(tmp_path):
    client, _, graph, connection = connected(tmp_path)
    approved = proposal(client, connection)
    response = call(client, connection, 'mutate', **approved)
    assert response.status_code == 200, response.text
    assert response.json()['receipt']['success'] is True
    assert call(client, connection, 'mutate', **approved).json() == response.json()
    assert graph.writes == [('DELETE', '789')]
    assert 'PRIVATE' not in response.text


def test_uncertain_delete_blocks_same_and_different_operation_ids(tmp_path):
    client, _, graph, connection = connected(tmp_path)
    approved = proposal(client, connection)
    graph.timeout = True
    response = call(client, connection, 'mutate', **approved)
    assert response.status_code == 409 and 'operation_uncertain' in response.text
    assert 'PRIVATE' not in response.text
    assert call(client, connection, 'mutate', **approved).status_code == 409
    approved['operation_id'] = str(uuid.uuid4())
    assert call(client, connection, 'mutate', **approved).status_code == 409
    assert graph.writes == [('DELETE', '789')]


@pytest.mark.parametrize('condition', ['scope', 'missing_user', 'expired_user', 'owner', 'tasks', 'wrong_secret', 'changed_baseline'])
def test_invalid_user_route_never_sends_delete(tmp_path, condition):
    client, now, graph, connection = connected(tmp_path)
    approved = proposal(client, connection)
    if condition in {'scope', 'missing_user', 'expired_user'}:
        vault = client.app.state.saas.vault
        with vault.transaction() as db:
            slot = db.execute("SELECT grant_id FROM saas_slots WHERE platform='instagram'").fetchone()
            record = vault.get(db, 'saas-grant', slot['grant_id'])
            grant = record['payload']['grant']
            if condition == 'missing_user': grant.pop('meta_user_grant', None)
            elif condition == 'expired_user': grant['meta_user_grant']['expires_at'] = 1
            else:
                grant['meta_user_grant']['granted_scopes'].remove('instagram_manage_contents')
                graph.scopes.remove('instagram_manage_contents')
            vault.put(db, 'saas-grant', slot['grant_id'], record['payload'], expires=record['expires'])
    elif condition == 'owner': graph.media_owner = '999'
    elif condition == 'tasks': graph.page_tasks = []
    elif condition == 'wrong_secret': connection = {**connection, 'connection_secret': 'other-credential'}
    else: approved['expected_before']['caption'] = 'Forged baseline'
    response = call(client, connection, 'mutate', **approved)
    assert response.status_code in {400, 403, 404, 409}, response.text
    assert not graph.writes and 'PRIVATE' not in response.text


def test_route_rejects_non_user_actions_and_unapproved_mutation_shape(tmp_path):
    client, _, graph, connection = connected(tmp_path)
    response = call(client, connection, edit={'action': 'post-delete', 'target_id': '123_789'})
    assert response.status_code in {400, 422}
    assert call(client, connection, 'mutate').status_code == 422
    assert not graph.writes


def test_mention_reply_keeps_user_token_on_server_and_checks_exact_mention(tmp_path):
    client, _, graph, connection = connected(tmp_path)
    edit = {'action': 'mention-reply', 'target_id': '456', 'media_id': '789',
            'comment_id': '800', 'message': 'Approved reply'}
    approved = proposal(client, connection, edit)
    response = call(client, connection, 'mutate', edit=edit, **approved)
    assert response.status_code == 200, response.text
    assert response.json()['receipt'] == {'id': '901', 'accepted': True}
    assert graph.writes == [('POST', '456/mentions')]


@pytest.mark.parametrize('mention', [False, True])
@pytest.mark.parametrize('lose_response', [False, True, 'http408', 'redirect', 'unknown403', 'permissions_revoked', 'token_unavailable'])
def test_public_meta_changes_use_remote_user_route_without_exporting_user_token(tmp_path, monkeypatch, mention, lose_response):
    from socialctl.brands import crear_brand
    from socialctl.models import Platform
    from socialctl.management.meta_client import MetaClient
    from socialctl.management.meta_changes import MetaStore, prepare_meta, apply_meta
    from socialctl.connections import keychain
    server, _, graph, connection = connected(tmp_path / 'server')
    brand = crear_brand(tmp_path / 'client', 'Example')
    brand.cuentas = {'facebook': {'page_id': '123'}, 'instagram': {'ig_user_id': '456'}}
    brand.guardar_secreto(Platform.INSTAGRAM, {'version': 1, 'auth_mode': 'broker', 'protocol': 'saas-v1',
        'service_url': 'https://social.example', 'account_id': '456', 'connection_id': connection['connection_id']})
    monkeypatch.setattr(keychain, 'get', lambda *args: connection['connection_secret'])
    responses = []
    def transport(request):
        assert request.url.host == 'social.example', 'User operations must not contact Graph from the client'
        if lose_response == 'token_unavailable' and request.url.path.endswith('/token'):
            return httpx.Response(409, json={'detail': 'provider_authorization_expired: Expired'})
        response = server.request(request.method, str(request.url), headers=dict(request.headers), content=request.content)
        responses.append(response.text)
        if lose_response and json.loads(request.content or b'{}').get('phase') == 'mutate':
            if lose_response in {'http408', 'redirect', 'unknown403'}:
                return httpx.Response({'http408': 408, 'redirect': 302, 'unknown403': 403}[lose_response], content=b'Intermediary response')
            if lose_response == 'permissions_revoked':
                graph.scopes.remove('instagram_manage_comments' if mention else 'instagram_manage_contents')
            raise httpx.ReadTimeout('Lost response after acceptance', request=request)
        return httpx.Response(response.status_code, content=response.content, headers=response.headers)
    http = httpx.Client(transport=httpx.MockTransport(transport))
    store = MetaStore(brand.raiz)
    edit = {'action': 'mention-reply', 'target_id': '456', 'media_id': '789',
            'comment_id': '800', 'message': 'Approved reply'} if mention else EDIT
    change = prepare_meta(MetaClient(brand, Platform.INSTAGRAM, http), store, edit)
    assert change.identity['token_mode'] == 'facebook_user'
    assert not graph.writes
    result = apply_meta(MetaClient(brand, Platform.INSTAGRAM, http), store, change.id, change.fingerprint)
    assert result.status == 'accepted_unverifiable'
    assert result.receipt
    apply_meta(MetaClient(brand, Platform.INSTAGRAM, http), store, change.id, change.fingerprint)
    assert len(graph.writes) == 1
    assert 'USER-PRIVATE' not in ''.join(responses)
    assert 'PRIVATE' not in store.load(change.id).model_dump_json()


def replace_grant(client, connection):
    connections = client.app.state.saas_connections
    vault = client.app.state.saas.vault
    with vault.transaction() as db:
        cap = vault.get(db, 'saas-capability', connection['connection_id'])['payload']
        grant = vault.get(db, 'saas-grant', cap['grant_id'])['payload']['grant']
        candidate = connections.candidate(db, cap['installation_id'], cap['user_id'],
            'instagram', 'MyBrand', cap['account'], grant=grant, expected=cap['grant_id'])
    assert activate(client, candidate).status_code == 200
    return candidate


@pytest.mark.parametrize('new_operation', [False, True])
def test_uncertain_reply_guard_survives_replacement_grant(tmp_path, new_operation):
    client, _, graph, connection = connected(tmp_path)
    edit = {'action': 'mention-reply', 'target_id': '456', 'media_id': '789',
            'comment_id': '800', 'message': 'Approved reply'}
    approved = proposal(client, connection, edit)
    graph.timeout = True
    assert call(client, connection, 'mutate', edit=edit, **approved).status_code == 409
    replacement = replace_grant(client, connection)
    assert call(client, replacement, 'status', edit=edit, **approved).status_code == 409
    graph.timeout = False
    if new_operation:
        approved['operation_id'] = str(uuid.uuid4())
    response = call(client, replacement, 'mutate', edit=edit, **approved)
    assert response.status_code == 409 and 'operation_uncertain' in response.text
    assert graph.writes == [('POST', '456/mentions')]


def test_accepted_receipt_is_available_after_replacement_without_provider_access(tmp_path):
    client, _, graph, connection = connected(tmp_path)
    approved = proposal(client, connection)
    accepted = call(client, connection, 'mutate', **approved)
    assert accepted.status_code == 200
    replacement = replace_grant(client, connection)
    graph.scopes.clear()
    result = call(client, replacement, 'status', **approved)
    assert result.status_code == 200
    assert result.json() == accepted.json()
    assert graph.writes == [('DELETE', '789')]


def test_explicit_disconnect_removes_history_from_replaced_grants(tmp_path):
    client, _, graph, connection = connected(tmp_path)
    approved = proposal(client, connection)
    assert call(client, connection, 'mutate', **approved).status_code == 200
    replacement = replace_grant(client, connection)
    vault = client.app.state.saas.vault
    with vault.transaction() as db:
        cap = vault.get(db, 'saas-capability', replacement['connection_id'])['payload']
        client.app.state.saas_lifecycle.disconnect_grant(db, cap['user_id'], 'instagram', cap['grant_id'])
        assert db.execute("SELECT count(*) FROM records WHERE kind LIKE 'saas-meta-%'").fetchone()[0] == 0
    assert graph.writes == [('DELETE', '789')]


def test_account_deleted_during_provider_write_does_not_resurrect_operation_data(tmp_path):
    from tests.test_saas_auth import USER
    client, _, graph, connection = connected(tmp_path)
    approved = proposal(client, connection)
    graph.after_write = lambda: client.app.state.saas_lifecycle.request_delete(USER)
    response = call(client, connection, 'mutate', **approved)
    assert response.status_code == 409
    assert 'operation_uncertain' in response.text
    vault = client.app.state.saas.vault
    with vault.read_transaction() as db:
        assert db.execute("SELECT count(*) FROM records WHERE kind LIKE 'saas-meta-%'").fetchone()[0] == 0
    assert graph.writes == [('DELETE', '789')]


@pytest.mark.parametrize('missing', ['pages_read_engagement', 'pages_show_list'])
def test_mention_reply_checks_documented_page_permissions_before_writing(tmp_path, missing):
    client, _, graph, connection = connected(tmp_path)
    edit = {'action': 'mention-reply', 'target_id': '456', 'media_id': '789',
            'comment_id': '800', 'message': 'Approved reply'}
    approved = proposal(client, connection, edit)
    graph.scopes.remove(missing)
    response = call(client, connection, 'mutate', edit=edit, **approved)
    assert response.status_code == 409 and 'provider_permissions_changed' in response.text
    assert not graph.writes


def test_hosted_story_inspect_and_approved_delete(tmp_path):
    class StoryProvider(MetaProvider):
        def __call__(self, request):
            if request.method == 'GET' and request.url.path.endswith('/789'):
                fields = set(request.url.params['fields'].split(','))
                if fields - {'id', 'owner', 'media_type', 'media_product_type', 'timestamp', 'permalink'}:
                    return httpx.Response(400, json={'error': {'code': 10, 'message': 'Comments cannot be made on story media.'}})
                return httpx.Response(200, json={'id': '789', 'owner': {'id': '456'},
                    'media_type': 'VIDEO', 'media_product_type': 'STORY'})
            return super().__call__(request)
    client, _, graph, connection = connected(tmp_path, StoryProvider())
    approved = proposal(client, connection)
    assert approved['expected_before']['media_product_type'] == 'STORY'
    response = call(client, connection, 'mutate', **approved)
    assert response.status_code == 200, response.text
    assert graph.writes == [('DELETE', '789')]

@pytest.mark.parametrize('comment_id', [None, '800'])
def test_read_only_mention_inspection_needs_no_reply(tmp_path, comment_id):
    class MentionProvider(MetaProvider):
        def __call__(self, request):
            if request.method == 'GET' and request.url.path.endswith('/456') and 'mentioned_media' in str(request.url):
                return httpx.Response(200, json={'id': '456', 'mentioned_media': {'id': '789', 'caption': 'Mention', 'media_type': 'IMAGE'}})
            return super().__call__(request)
    client, _, graph, connection = connected(tmp_path, MentionProvider())
    body = {'media_id': '789'}
    if comment_id is not None:
        body['comment_id'] = comment_id
    response = client.post('/api/saas/v1/connections/' + connection['connection_id'] + '/instagram-mention-inspect',
        headers={'Authorization': 'Bearer ' + connection['connection_secret']}, json=body)
    assert response.status_code == 200, response.text
    assert response.json()['before']['id'] == (comment_id or '789')
    assert 'PRIVATE' not in response.text and not graph.writes

@pytest.mark.parametrize('body', [{'media_id': 'bad'}, {'media_id': '789', 'comment_id': None},
    {'media_id': '789', 'message': 'Invented'}, {'media_id': '999'},
    {'media_id': '789', 'comment_id': '999'}])
def test_mention_inspection_rejects_invalid_or_mismatched_targets_without_writes(tmp_path, body):
    client, _, graph, connection = connected(tmp_path)
    response = client.post('/api/saas/v1/connections/' + connection['connection_id'] + '/instagram-mention-inspect',
        headers={'Authorization': 'Bearer ' + connection['connection_secret']}, json=body)
    assert response.status_code in {400, 422}
    assert not graph.writes and 'PRIVATE' not in response.text


def test_mention_inspection_requires_current_user_scopes(tmp_path):
    client, _, graph, connection = connected(tmp_path)
    graph.scopes.remove('instagram_manage_comments')
    response = client.post('/api/saas/v1/connections/' + connection['connection_id'] + '/instagram-mention-inspect',
        headers={'Authorization': 'Bearer ' + connection['connection_secret']}, json={'media_id': '789'})
    assert response.status_code == 409
    assert not graph.writes

@pytest.mark.parametrize('condition', ['missing_user', 'expired_user'])
def test_mention_inspection_never_refreshes_invalid_user_grants(tmp_path, condition):
    client, _, graph, connection = connected(tmp_path)
    vault = client.app.state.saas.vault
    with vault.transaction() as db:
        slot = db.execute("SELECT grant_id FROM saas_slots WHERE platform='instagram'").fetchone()
        record = vault.get(db, 'saas-grant', slot['grant_id'])
        grant = record['payload']['grant']
        if condition == 'missing_user':
            grant.pop('meta_user_grant')
        else:
            grant['meta_user_grant']['expires_at'] = 1
        vault.put(db, 'saas-grant', slot['grant_id'], record['payload'], expires=record['expires'])
    response = client.post('/api/saas/v1/connections/' + connection['connection_id'] + '/instagram-mention-inspect',
        headers={'Authorization': 'Bearer ' + connection['connection_secret']}, json={'media_id': '789'})
    assert response.status_code == 409 and not graph.writes


def test_public_mention_reader_uses_server_user_without_page_token_export(tmp_path, monkeypatch):
    from socialctl.brands import crear_brand
    from socialctl.models import Platform
    from socialctl.management.meta_client import MetaClient
    from socialctl.connections import keychain
    server, _, graph, connection = connected(tmp_path / 'server')
    brand = crear_brand(tmp_path / 'client', 'Example')
    brand.cuentas = {'facebook': {'page_id': '123'}, 'instagram': {'ig_user_id': '456'}}
    brand.guardar_secreto(Platform.INSTAGRAM, {'version': 1, 'auth_mode': 'broker', 'protocol': 'saas-v1',
        'service_url': 'https://social.example', 'account_id': '456', 'connection_id': connection['connection_id']})
    monkeypatch.setattr(keychain, 'get', lambda *args: connection['connection_secret'])
    def transport(request):
        assert request.url.host == 'social.example'
        assert request.url.path.endswith('/instagram-mention-inspect')
        assert json.loads(request.content) == {'media_id': '789', 'comment_id': '800'}
        response = server.request(request.method, str(request.url), headers=dict(request.headers), content=request.content)
        assert 'PRIVATE' not in response.text
        return httpx.Response(response.status_code, content=response.content)
    http = httpx.Client(transport=httpx.MockTransport(transport))
    assert MetaClient(brand, Platform.INSTAGRAM, http).inspect_mention('789', '800') == {
        'id': '800', 'media_id': '789', 'text': 'Consenting test mention'}
    assert not graph.writes
