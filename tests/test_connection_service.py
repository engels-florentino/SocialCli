"""Exercise the real service protocol with a fictional Google HTTP provider."""
import hashlib
import importlib.util
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from socialctl.connection_service.settings import Settings, AppCredentials
from socialctl.connection_service.store import Store

SCOPE = 'https://www.googleapis.com/auth/youtube.readonly https://www.googleapis.com/auth/youtube.upload'


def make_service(tmp_path, *, clock=None, handler=None):
    assert importlib.util.find_spec('socialctl.connection_service.app') is not None, 'connection HTTP API is missing'
    from socialctl.connection_service.app import create_app
    settings = Settings('https://social.example', tmp_path / 'vault.sqlite3', [Fernet.generate_key()],
                        providers={'youtube':AppCredentials('client-one','app-secret-one')})
    def provider(request):
        if request.url.host == 'oauth2.googleapis.com':
            return httpx.Response(200, json={'access_token':'provider-access-one','refresh_token':'provider-refresh-one','expires_in':3600,'scope':SCOPE})
        return httpx.Response(200, json={'items':[{'id':'UC-one','snippet':{'title':'Creator one'}}]})
    store = Store(settings.database, settings.encryption_keys)
    app = create_app(settings, store=store, provider_client=httpx.Client(transport=httpx.MockTransport(handler or provider)), clock=clock or time.time)
    return TestClient(app, base_url=settings.public_url), store


def start(client, secret='poll-secret-one-12345678901234567890'):
    response = client.post('/v1/authorizations', json={'platform':'youtube','poll_challenge':hashlib.sha256(secret.encode()).hexdigest()})
    assert response.status_code == 201
    value = response.json()
    return value, {'Authorization':f'Bearer {secret}'}


def authorize(client, value, *, error=False):
    response = client.get(value['browser_url'], follow_redirects=False)
    assert response.status_code == 303
    state = parse_qs(urlsplit(response.headers['location']).query)['state'][0]
    callback = '/oauth/youtube/callback'
    result = client.get(callback, params={'state':state, **({'error':'access_denied'} if error else {'code':'fictional-code'})})
    return state, result


def complete(client, value, headers):
    response = client.post(f"/v1/authorizations/{value['authorization_id']}/complete", headers=headers, json={'account_id':'UC-one'})
    assert response.status_code == 201
    return response.json()


def connection_headers(value):
    return {'Authorization':f"Bearer {value['connection_secret']}"}


def test_browser_flow_requires_explicit_account_completion_and_never_returns_refresh_tokens(tmp_path):
    client, store = make_service(tmp_path)
    value, headers = start(client)
    assert value['expires_in'] == 600
    state, response = authorize(client, value)
    assert response.status_code == 200
    assert 'Return to SocialCli' in response.text
    poll = client.post(f"/v1/authorizations/{value['authorization_id']}/poll", headers=headers).json()
    assert poll['status'] == 'ready'
    assert poll['accounts'] == [{'id':'UC-one','name':'Creator one'}]
    assert not any(s in str(poll) for s in ['provider-access-one','provider-refresh-one','app-secret-one'])
    connected = complete(client, value, headers)
    assert connected['account']['id'] == 'UC-one'
    token = client.post(f"/v1/connections/{connected['connection_id']}/token", headers=connection_headers(connected))
    assert token.json()['access_token'] == 'provider-access-one'
    assert 'refresh_token' not in token.text and 'app-secret-one' not in token.text
    assert token.headers['cache-control'] == 'no-store'
    assert client.post(f"/v1/authorizations/{value['authorization_id']}/complete", headers=headers, json={'account_id':'UC-one'}).status_code == 404
    assert client.get('/oauth/youtube/callback', params={'state':state,'code':'fictional-code'}).status_code == 400


def test_another_creator_cannot_poll_complete_or_read_connection(tmp_path):
    client, _ = make_service(tmp_path)
    value, headers = start(client)
    authorize(client, value)
    wrong = {'Authorization':'Bearer other-creators-secret'}
    assert client.post(f"/v1/authorizations/{value['authorization_id']}/poll", headers=wrong).status_code == 404
    assert client.post(f"/v1/authorizations/{value['authorization_id']}/complete", headers=wrong, json={'account_id':'UC-one'}).status_code == 404
    assert client.post(f"/v1/authorizations/{value['authorization_id']}/complete", headers=headers, json={'account_id':'UC-other'}).status_code == 400
    connected = complete(client, value, headers)
    path = f"/v1/connections/{connected['connection_id']}"
    assert client.get(path, headers=wrong).status_code == 404
    assert client.post(path+'/token', headers=wrong).status_code == 404
    assert client.delete(path, headers=wrong).status_code == 404
    assert client.delete(path, headers=connection_headers(connected)).status_code == 200
    assert client.post(path+'/token', headers=connection_headers(connected)).status_code == 404


def test_callback_requires_bound_browser_and_state_and_is_single_use(tmp_path):
    client, _ = make_service(tmp_path)
    value, headers = start(client)
    response = client.get(value['browser_url'], follow_redirects=False)
    state = parse_qs(urlsplit(response.headers['location']).query)['state'][0]
    cookies = dict(client.cookies)
    client.cookies.clear()
    assert client.get('/oauth/youtube/callback', params={'state':state,'code':'code'}).status_code == 400
    client.cookies.update(cookies)
    assert client.get('/oauth/youtube/callback', params={'state':state+'bad','code':'code'}).status_code == 400
    assert client.get('/oauth/youtube/callback', params={'state':state,'code':'code'}).status_code == 200
    assert client.get('/oauth/youtube/callback', params={'state':state,'code':'code'}).status_code == 400


def test_authorization_expiration_and_denial_do_not_create_connections(tmp_path):
    now = [time.time()]
    client, _ = make_service(tmp_path, clock=lambda:now[0])
    value, headers = start(client)
    _, response = authorize(client, value, error=True)
    assert response.status_code == 200
    poll = client.post(f"/v1/authorizations/{value['authorization_id']}/poll", headers=headers).json()
    assert poll['status'] == 'denied'
    assert client.post(f"/v1/authorizations/{value['authorization_id']}/complete", headers=headers, json={'account_id':'UC-one'}).status_code == 409
    value, headers = start(client, 'new-secret-12345678901234567890')
    now[0] += 601
    assert client.get(value['browser_url'], follow_redirects=False).status_code == 404
    assert client.post(f"/v1/authorizations/{value['authorization_id']}/poll", headers=headers).status_code == 404


def test_disabled_connectors_and_optional_scope_mismatch_are_rejected(tmp_path):
    client, _ = make_service(tmp_path)
    challenge = hashlib.sha256(b's').hexdigest()
    assert client.post('/v1/authorizations', json={'platform':'tiktok','poll_challenge':challenge}).status_code == 503
    assert client.post('/v1/authorizations', json={'platform':'facebook','poll_challenge':challenge,'management':True}).status_code == 503
    mismatch = client.post('/v1/authorizations', json={'platform':'tiktok','poll_challenge':challenge,'management':True})
    assert mismatch.status_code == 400
    assert mismatch.json()['detail'] == 'requested optional permissions are not supported for this platform'
    assert client.post('/v1/authorizations', json={'platform':'youtube','poll_challenge':'not-a-hash'}).status_code == 422
    assert client.get('/healthz').json()['configured_platforms'] == ['youtube']


def test_refresh_is_serialized_and_rotated_secret_survives_reopen(tmp_path):
    refresh_calls = []
    def handler(r):
        if r.url.host == 'oauth2.googleapis.com':
            params = parse_qs(r.content.decode())
            if params.get('grant_type') == ['refresh_token']:
                refresh_calls.append(params['refresh_token'][0])
                return httpx.Response(200,json={'access_token':'new-access','refresh_token':'rotated-refresh','expires_in':3600,'scope':SCOPE})
            return httpx.Response(200,json={'access_token':'original-access','refresh_token':'original-refresh','expires_in':3600,'scope':SCOPE})
        return httpx.Response(200,json={'items':[{'id':'UC-one'}]})
    client, store = make_service(tmp_path, handler=handler)
    value, headers = start(client)
    authorize(client,value)
    connected = complete(client,value,headers)
    cid = connected['connection_id']
    with store.transaction() as db:
        record = store.get(db,'connection',cid)
        record['payload']['grant']['expires_at'] = 1
        store.put(db,'connection',cid,record['payload'],expires=record['expires'],credential_hash=record['credential_hash'])
    def fetch(_):
        return client.post(f'/v1/connections/{cid}/token',headers=connection_headers(connected))
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(fetch,range(2)))
    assert all(r.status_code == 200 and r.json()['access_token'] == 'new-access' for r in replies)
    assert refresh_calls == ['original-refresh']
    with store.transaction() as db:
        assert store.get(db,'connection',cid)['payload']['grant']['refresh_token'] == 'rotated-refresh'


def test_provider_failure_is_sanitized_and_cannot_complete(tmp_path):
    client,_ = make_service(tmp_path,handler=lambda r:httpx.Response(400,json={'error':'app-secret-one'}))
    value,headers = start(client)
    _,response = authorize(client,value)
    assert response.status_code == 200
    poll = client.post(f"/v1/authorizations/{value['authorization_id']}/poll",headers=headers)
    assert poll.json()['status'] == 'failed'
    assert 'app-secret-one' not in response.text+poll.text


def test_service_disables_api_docs_and_adds_security_headers(tmp_path):
    client,_ = make_service(tmp_path)
    assert client.get('/docs').status_code == 404
    assert client.get('/openapi.json').status_code == 404
    r = client.get('/healthz')
    assert r.headers['referrer-policy'] == 'no-referrer'
    assert r.headers['x-content-type-options'] == 'nosniff'
    assert r.headers['cache-control'] == 'no-store'
    for _ in range(5):
        start(client)
    assert client.post('/v1/authorizations',json={'platform':'youtube','poll_challenge':hashlib.sha256(b's').hexdigest()}).status_code == 429


def test_read_only_token_requests_never_refresh_an_expired_grant(tmp_path):
    client,store=make_service(tmp_path)
    value,headers=start(client)
    authorize(client,value)
    connected=complete(client,value,headers)
    cid=connected['connection_id']
    with store.transaction() as db:
        record=store.get(db,'connection',cid)
        record['payload']['grant']['expires_at']=1
        store.put(db,'connection',cid,record['payload'],expires=record['expires'],credential_hash=record['credential_hash'])
    result=client.post(f'/v1/connections/{cid}/token?refresh=false',headers=connection_headers(connected))
    assert result.status_code==409
    with store.transaction() as db:
        assert store.get(db,'connection',cid)['payload']['grant']['expires_at']==1


def test_uninstalled_connection_expires_within_pairing_window(tmp_path):
    now=[time.time()]
    client,store=make_service(tmp_path,clock=lambda:now[0])
    value,headers=start(client)
    authorize(client,value)
    connected=complete(client,value,headers)
    path=f"/v1/connections/{connected['connection_id']}"
    now[0]+=601
    assert client.get(path,headers=connection_headers(connected)).status_code==404


@pytest.mark.parametrize('platform,expected', [
    ('facebook', {'pages_read_user_content', 'pages_manage_engagement', 'pages_manage_metadata', 'read_insights'}),
    ('instagram', {'instagram_manage_comments', 'pages_manage_metadata', 'instagram_manage_insights'}),
])
def test_meta_optional_scopes_survive_service_authorization_roundtrip(tmp_path, platform, expected):
    from socialctl.connection_service.app import create_app
    settings = Settings('https://social.example', tmp_path / 'vault.sqlite3', [Fernet.generate_key()],
        providers={platform:AppCredentials('fictional-client','fictional-secret')})
    client = TestClient(create_app(settings), base_url=settings.public_url)
    secret = 'fictional-poll-secret-12345678901234567890'
    response = client.post('/v1/authorizations', json={'platform':platform,
        'poll_challenge':hashlib.sha256(secret.encode()).hexdigest(), 'management':True, 'analytics':True})
    assert response.status_code == 201
    value = response.json()
    redirect = client.get(value['browser_url'], follow_redirects=False)
    assert redirect.status_code == 303
    scopes = set(parse_qs(urlsplit(redirect.headers['location']).query)['scope'][0].split(','))
    assert expected <= scopes
    assert client.delete('/v1/authorizations/'+value['authorization_id'], headers={'Authorization':'Bearer '+secret}).status_code == 200
