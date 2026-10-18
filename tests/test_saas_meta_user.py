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
                'caption': 'Disposable fixture', 'media_type': 'IMAGE'})
        if path == '456':
            return httpx.Response(200, json={'id': '456', 'mentioned_comment': {
                'id': '800', 'text': 'Consenting test mention', 'media': {'id': '789'}}})
        return httpx.Response(404)


def connected(tmp_path):
    graph = MetaProvider()
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
@pytest.mark.parametrize('lose_response', [False, True])
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
        response = server.request(request.method, str(request.url), headers=dict(request.headers), content=request.content)
        responses.append(response.text)
        if lose_response and json.loads(request.content or b'{}').get('phase') == 'mutate':
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
