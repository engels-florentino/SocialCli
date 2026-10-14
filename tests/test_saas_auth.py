"""Real browser/auth/store boundaries with a fictional external identity service."""
import re
import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from socialctl.connection_service.app import create_app
from socialctl.connection_service.settings import Settings
from socialctl.saas.settings import SaaSSettings

USER = '11111111-1111-4111-8111-111111111111'
EMAIL = 'creator@example.test'


def service(tmp_path, handler=None, now=None, provider=None):
    now = now or [1000.0]
    calls = []
    def identity(request):
        calls.append(request)
        if handler:
            override = handler(request)
            if override is not None:
                return override
        if request.url.path == '/otp':
            return httpx.Response(200, json={})
        if request.url.path == '/verify':
            import json
            if json.loads(request.content)['token'] != '123456':
                return httpx.Response(403, json={'error':'expired'})
            return httpx.Response(200, json={'access_token':'identity-access-secret',
                'refresh_token':'identity-refresh-secret','token_type':'bearer','expires_in':3600,
                'user':{'id':USER,'email':EMAIL,'email_confirmed_at':'2026-10-13T00:00:00Z'}})
        if request.url.path == '/user':
            assert request.headers['authorization'] == 'Bearer identity-access-secret'
            return httpx.Response(200, json={'id':USER,'email':EMAIL,'email_confirmed_at':'2026-10-13T00:00:00Z'})
        if request.url.path == '/logout':
            return httpx.Response(204)
        return httpx.Response(404)
    from socialctl.connection_service.settings import AppCredentials
    settings = Settings(public_url='https://social.example', database=tmp_path/'service.sqlite3',
        encryption_keys=[Fernet.generate_key()], providers={'youtube':AppCredentials('fictional-app','fictional-secret')} if provider else {}, saas=SaaSSettings(auth_url='https://auth.example'))
    app = create_app(settings, provider_client=httpx.Client(transport=httpx.MockTransport(provider)) if provider else None, clock=lambda:now[0], identity_client=httpx.Client(transport=httpx.MockTransport(identity)))
    return TestClient(app, base_url='https://social.example'), now, calls


def fields(page):
    from tests.test_connection_ui import Elements
    return {a['name']:a.get('value','') for t,a in Elements(page.text).tags if t=='input' and 'name' in a}


def begin(client, email=EMAIL, next_path='/account/'):
    page = client.get('/account/login', params={'next':next_path})
    form = fields(page)
    form['email'] = email
    reply = client.post('/account/login', data=form, headers={'Origin':'https://social.example'})
    assert reply.status_code == 200
    return fields(reply)


def login(client):
    form = begin(client)
    form['code'] = '123456'
    reply = client.post('/account/verify', data=form, headers={'Origin':'https://social.example'}, follow_redirects=False)
    assert reply.status_code == 303
    return reply


def test_verified_otp_creates_private_cookie_session_and_logout_revokes_copy(tmp_path):
    client, now, calls = service(tmp_path)
    reply = login(client)
    cookie = client.cookies.get('socialcli-session')
    assert cookie and 'HttpOnly' in reply.headers['set-cookie'] and 'Secure' in reply.headers['set-cookie']
    page = client.get('/account/')
    assert page.status_code == 200 and EMAIL in page.text
    assert 'identity-access-secret' not in page.text and 'identity-refresh-secret' not in page.text
    copied = TestClient(client.app, base_url='https://social.example')
    copied.cookies.set('socialcli-session',cookie)
    assert copied.get('/account/').status_code == 200
    form = fields(page)
    assert client.post('/account/logout',data=form,headers={'Origin':'https://social.example'},follow_redirects=False).status_code == 303
    assert copied.get('/account/',follow_redirects=False).status_code == 303


def test_attempt_is_browser_bound_and_only_consumed_once(tmp_path):
    client, _, calls = service(tmp_path)
    form = begin(client)
    form['code'] = '123456'
    other = TestClient(client.app,base_url='https://social.example')
    assert other.post('/account/verify',data=form,headers={'Origin':'https://social.example'}).status_code == 403
    assert client.post('/account/verify',data=form,headers={'Origin':'https://evil.example'}).status_code == 403
    assert not any(r.url.path=='/verify' for r in calls)
    assert client.post('/account/verify',data=form,headers={'Origin':'https://social.example'},follow_redirects=False).status_code == 303
    assert client.post('/account/verify',data=form,headers={'Origin':'https://social.example'},follow_redirects=False).status_code in {403,409}
    assert sum(r.url.path=='/verify' for r in calls) == 1


def test_bad_codes_are_limited_and_expired_attempt_never_contacts_provider(tmp_path):
    client, now, calls = service(tmp_path)
    form = begin(client)
    form['code'] = '999999'
    for _ in range(5):
        assert client.post('/account/verify',data=form,headers={'Origin':'https://social.example'}).status_code == 400
    assert client.post('/account/verify',data=form,headers={'Origin':'https://social.example'}).status_code == 429
    assert sum(r.url.path=='/verify' for r in calls) == 5
    now[0] += 601
    form['code'] = '123456'
    assert client.post('/account/verify',data=form,headers={'Origin':'https://social.example'}).status_code == 403


@pytest.mark.parametrize('next_path',['https://evil.example','//evil.example','/account/\\evil','/api/saas/v1/secret'])
def test_login_never_redirects_to_unapproved_destination(tmp_path,next_path):
    client, _, _ = service(tmp_path)
    form = begin(client,next_path=next_path)
    form['code'] = '123456'
    reply = client.post('/account/verify',data=form,headers={'Origin':'https://social.example'},follow_redirects=False)
    assert reply.headers['location'] == '/account/'


def test_expired_or_invalid_provider_identity_fails_closed(tmp_path):
    revoked = [False]
    def handler(request):
        if revoked[0] and request.url.path=='/user':
            return httpx.Response(401,json={'error':'invalid token'})
    client, now, _ = service(tmp_path,handler)
    login(client)
    revoked[0] = True
    assert client.get('/account/',follow_redirects=False).status_code == 303
    revoked[0] = False
    login(client)
    now[0] += 3600
    assert client.get('/account/',follow_redirects=False).status_code == 303


def test_identity_in_verify_response_cannot_substitute_email(tmp_path):
    def handler(request):
        if request.url.path=='/verify':
            return httpx.Response(200,json={'access_token':'bad-secret','expires_in':3600,
                'user':{'id':USER,'email':'other@example.test','email_confirmed_at':'now'}})
    client, _, _ = service(tmp_path,handler)
    form = begin(client)
    form['code']='123456'
    assert client.post('/account/verify',data=form,headers={'Origin':'https://social.example'}).status_code == 400
    assert client.get('/account/',follow_redirects=False).status_code == 303


def test_mail_failure_has_recovery_without_secrets_or_session(tmp_path):
    def handler(request):
        if request.url.path=='/otp':
            return httpx.Response(503,text='SMTP_PASSWORD=private')
    client, _, _ = service(tmp_path,handler)
    page=client.get('/account/login')
    data=fields(page); data['email']=EMAIL
    reply=client.post('/account/login',data=data,headers={'Origin':'https://social.example'})
    assert reply.status_code == 503 and 'private' not in reply.text and 'try again' in reply.text.lower()
    assert not client.cookies.get('socialcli-session')


def test_forged_cookie_and_missing_origin_are_rejected(tmp_path):
    client, _, calls = service(tmp_path)
    page=client.get('/account/login'); data=fields(page); data['email']=EMAIL
    assert client.post('/account/login',data=data).status_code == 403
    client.cookies.set('socialcli-session','forged-session-with-valid-length')
    assert client.get('/account/',follow_redirects=False).status_code == 303
    assert not calls


def test_account_actions_work_after_preauth_window_until_session_expiry(tmp_path):
    import time
    client, now, _ = service(tmp_path)
    login(client)
    now[0] += 1900
    # Emulate browser expiry of cookies 31 minutes after sign-in.
    cutoff = time.time() + 1900
    for cookie in list(client.cookies.jar):
        if cookie.expires is not None and cookie.expires <= cutoff:
            client.cookies.jar.clear(cookie.domain, cookie.path, cookie.name)
    page = client.get('/account/', follow_redirects=False)
    assert page.status_code == 200
    reply = client.post('/account/logout', data=fields(page),
                        headers={'Origin':'https://social.example'}, follow_redirects=False)
    assert reply.status_code == 303
    assert client.get('/account/', follow_redirects=False).status_code == 303


def test_authenticated_forms_do_not_require_auxiliary_login_cookie(tmp_path):
    client, _, _ = service(tmp_path)
    login(client)
    client.cookies.delete('socialcli-browser')
    page = client.get('/account/')
    assert page.status_code == 200
    assert client.post('/account/logout', data=fields(page),
                       headers={'Origin':'https://social.example'}, follow_redirects=False).status_code == 303
