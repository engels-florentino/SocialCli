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
    now[0] += 43200
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


def test_browser_account_forms_preserve_same_origin_without_accepting_null_origin(tmp_path):
    client, _, calls = service(tmp_path)
    page=client.get('/account/login')
    # Native form POSTs under no-referrer send Origin:null, unlike our HTTP test client.
    assert page.headers['referrer-policy']=='same-origin'
    form=fields(page);form['email']=EMAIL
    rejected=client.post('/account/login',data=form,headers={'Origin':'null'})
    assert rejected.status_code==403
    assert not calls
    verify=client.post('/account/login',data=form,headers={'Origin':'https://social.example'})
    assert verify.status_code==200 and verify.headers['referrer-policy']=='same-origin'
    assert rejected.headers['referrer-policy']=='same-origin'
    assert client.get('/healthz').headers['referrer-policy']=='no-referrer'


def test_web_session_refreshes_and_caches_identity_with_absolute_limit(tmp_path):
    refreshed=[]
    def handler(request):
        if request.url.path=='/token':
            import json
            refreshed.append(json.loads(request.content)['refresh_token'])
            return httpx.Response(200,json={'access_token':'identity-access-secret','refresh_token':'rotated-refresh-secret',
                'expires_in':3600,'user':{'id':USER,'email':EMAIL,'email_confirmed_at':'verified'}})
    client,now,calls=service(tmp_path,handler);reply=login(client)
    assert 'Max-Age=43200' in reply.headers['set-cookie']
    assert client.get('/account/').status_code==200
    count=sum(r.url.path=='/user' for r in calls)
    assert client.get('/account/').status_code==200
    assert sum(r.url.path=='/user' for r in calls)==count
    now[0]+=3601
    assert client.get('/account/').status_code==200
    assert refreshed==['identity-refresh-secret']
    from socialctl.saas.auth import digest
    vault=client.app.state.saas.vault
    with vault.read_transaction() as db:
        session=vault.get(db,'saas-session',digest(client.cookies.get('socialcli-session')))
        assert session['payload']['refresh_token']=='rotated-refresh-secret'
        assert session['expires']==44200
    now[0]=44201
    assert client.get('/account/',follow_redirects=False).status_code==303
    assert refreshed==['identity-refresh-secret']


def test_refresh_cannot_resurrect_session_deleted_during_network_call(tmp_path):
    from socialctl.saas.auth import digest
    holder={}
    def handler(request):
        if request.url.path=='/token':
            client=holder['client'];vault=client.app.state.saas.vault
            with vault.transaction() as db:vault.delete(db,'saas-session',digest(client.cookies.get('socialcli-session')))
            return httpx.Response(200,json={'access_token':'new-access-secret','refresh_token':'new-refresh-secret',
                'expires_in':3600,'user':{'id':USER,'email':EMAIL,'email_confirmed_at':'verified'}})
    client,now,_=service(tmp_path,handler);holder['client']=client;login(client);now[0]+=3601
    assert client.get('/account/',follow_redirects=False).status_code==303
    with client.app.state.saas.vault.read_transaction() as db:
        assert db.execute("SELECT count(*) FROM records WHERE kind='saas-session'").fetchone()[0]==0


def test_real_gotrue_twelve_character_refresh_token_is_accepted(tmp_path):
    def handler(request):
        if request.url.path in {'/verify','/token'}:
            return httpx.Response(200,json={'access_token':'identity-access-secret','refresh_token':'aB3dE6gH9jK2',
                'expires_in':3600,'user':{'id':USER,'email':EMAIL,'email_confirmed_at':'verified'}})
    client,now,_=service(tmp_path,handler)
    login(client)
    now[0]+=3601
    assert client.get('/account/').status_code==200


def test_ambiguous_refresh_never_replays_old_provider_token(tmp_path):
    refreshes=[]
    def handler(request):
        if request.url.path=='/token':
            refreshes.append(request)
            raise httpx.ReadTimeout('private provider transport',request=request)
    client,now,_=service(tmp_path,handler);login(client);now[0]+=3601
    assert client.get('/account/',follow_redirects=False).status_code==503
    assert client.get('/account/',follow_redirects=False).status_code==503
    now[0]+=31
    assert client.get('/account/',follow_redirects=False).status_code==303
    assert len(refreshes)==1


def test_concurrent_refresh_issues_exactly_one_rotation(tmp_path):
    from threading import Event
    from concurrent.futures import ThreadPoolExecutor
    entered=Event();release=Event();refreshes=[]
    def handler(request):
        if request.url.path=='/token':
            refreshes.append(request);entered.set();assert release.wait(5)
            return httpx.Response(200,json={'access_token':'identity-access-secret','refresh_token':'AbCdEf123456',
                'expires_in':3600,'user':{'id':USER,'email':EMAIL,'email_confirmed_at':'verified'}})
    client,now,_=service(tmp_path,handler);login(client);now[0]+=3601
    other=TestClient(client.app,base_url='https://social.example')
    other.cookies.set('socialcli-session',client.cookies.get('socialcli-session'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(client.get,'/account/')
        assert entered.wait(3)
        try:assert other.get('/account/',follow_redirects=False).status_code==503
        finally:release.set()
        assert first.result().status_code==200
    assert len(refreshes)==1


@pytest.mark.parametrize('status',[400,422])
def test_rejected_email_is_input_error_and_still_consumes_delivery_budget(tmp_path,status):
    def handler(request):
        if request.url.path=='/otp':
            return httpx.Response(status,json={'error':'private provider validation'})
    client,_,calls=service(tmp_path,handler)
    form=fields(client.get('/account/login'));form['email']=EMAIL
    for _ in range(3):
        reply=client.post('/account/login',data=form,headers={'Origin':'https://social.example'})
        assert reply.status_code==400
        assert 'Enter a valid email address.' in reply.text
        assert 'private provider' not in reply.text
    assert client.post('/account/login',data=form,headers={'Origin':'https://social.example'}).status_code==429
    assert sum(r.url.path=='/otp' for r in calls)==3
    with client.app.state.saas.vault.read_transaction() as db:
        assert db.execute("SELECT count(*) FROM records WHERE kind='saas-login'").fetchone()[0]==0


def test_concurrent_read_uses_valid_access_during_refresh_without_replaying_refresh(tmp_path):
    from threading import Event
    from concurrent.futures import ThreadPoolExecutor
    entered=Event();release=Event();refreshes=[]
    def handler(request):
        if request.url.path=='/token':
            refreshes.append(request);entered.set();assert release.wait(5)
            return httpx.Response(200,json={'access_token':'identity-access-secret','refresh_token':'AbCdEf123456',
                'expires_in':3600,'user':{'id':USER,'email':EMAIL,'email_confirmed_at':'verified'}})
    client,now,_=service(tmp_path,handler);login(client);now[0]+=3541
    other=TestClient(client.app,base_url='https://social.example')
    other.cookies.set('socialcli-session',client.cookies.get('socialcli-session'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(client.get,'/account/')
        assert entered.wait(3)
        try:assert other.get('/account/',follow_redirects=False).status_code==200
        finally:release.set()
        assert first.result().status_code==200
    assert len(refreshes)==1


@pytest.mark.parametrize('change',['logout','version','deletion','expiry','lease_expiry'])
def test_read_during_refresh_rechecks_session_and_access_expiry_after_verification(tmp_path,change):
    from socialctl.saas.auth import digest
    holder={}
    def handler(request):
        if request.url.path=='/user':
            client=holder['client'];vault=client.app.state.saas.vault
            with vault.transaction() as db:
                if change=='logout':vault.delete(db,'saas-session',holder['key'])
                elif change=='version':db.execute('UPDATE saas_users SET session_version=session_version+1')
                elif change=='deletion':db.execute('UPDATE saas_users SET deleting=1')
                else:holder['now'][0]+=60 if change=='lease_expiry' else 11
    client,now,_=service(tmp_path,handler);login(client);now[0]+=3541
    key=digest(client.cookies.get('socialcli-session'));holder.update(client=client,key=key,now=now)
    vault=client.app.state.saas.vault
    with vault.transaction() as db:
        record=vault.get(db,'saas-session',key)
        record['payload'].update(refresh_lease='fictional-active-refresh',refresh_until=now[0]+30)
        if change=='expiry':record['payload']['access_expires']=now[0]+10
        vault.put(db,'saas-session',key,record['payload'],expires=record['expires'])
    assert client.get('/account/',follow_redirects=False).status_code==(503 if change=='expiry' else 303)


def test_parallel_read_accepts_new_verified_token_when_refresh_finishes_first(tmp_path):
    from threading import Event
    from concurrent.futures import ThreadPoolExecutor
    entered=Event();release=Event();completed=Event()
    def handler(request):
        if request.url.path=='/token':
            entered.set();assert release.wait(5)
            return httpx.Response(200,json={'access_token':'new-identity-access-secret','refresh_token':'AbCdEf123456',
                'expires_in':3600,'user':{'id':USER,'email':EMAIL,'email_confirmed_at':'verified'}})
        if request.url.path=='/user':
            release.set();assert completed.wait(5)
    client,now,_=service(tmp_path,handler);login(client);now[0]+=3541
    other=TestClient(client.app,base_url='https://social.example')
    other.cookies.set('socialcli-session',client.cookies.get('socialcli-session'))
    def first_request():
        try:return client.get('/account/')
        finally:completed.set()
    with ThreadPoolExecutor(max_workers=1) as pool:
        first=pool.submit(first_request)
        assert entered.wait(3)
        try:assert other.get('/account/',follow_redirects=False).status_code==200
        finally:release.set()
        assert first.result().status_code==200


@pytest.mark.parametrize('status',[401,403])
def test_otp_service_authentication_failure_is_delivery_unavailable_not_invalid_email(tmp_path,status):
    def handler(request):
        if request.url.path=='/otp':return httpx.Response(status,json={'error':'private service key'})
    client,_,_=service(tmp_path,handler)
    form=fields(client.get('/account/login'));form['email']=EMAIL
    reply=client.post('/account/login',data=form,headers={'Origin':'https://social.example'})
    assert reply.status_code==503
    assert 'Email delivery is temporarily unavailable' in reply.text
    assert 'Enter a valid email address.' not in reply.text and 'private service key' not in reply.text
    with client.app.state.saas.vault.read_transaction() as db:
        assert db.execute("SELECT count(*) FROM records WHERE kind='saas-login'").fetchone()[0]==0
