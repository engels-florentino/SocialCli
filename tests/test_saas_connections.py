"""Provider selection, activation and direct tokens share the owner/access boundary."""
import hashlib
import secrets
import httpx
from fastapi.testclient import TestClient
from tests.test_saas_auth import service,login,fields,USER
from tests.test_saas_pairing import pair,approve,poll
from tests.test_connection_service import begin_browser,SCOPE
from urllib.parse import urlsplit,parse_qs


def provider(request):
    if request.url.host=='oauth2.googleapis.com':
        return httpx.Response(200,json={'access_token':'social-access','refresh_token':'social-refresh','expires_in':3600,'scope':SCOPE})
    return httpx.Response(200,json={'items':[{'id':'UC-two','snippet':{'title':'Creator two'}}]})


def setup(tmp_path):
    client,now,calls=service(tmp_path,provider=provider);login(client)
    return client,now,new_installation(client)


def new_installation(client):
    value,proof=pair(client);approve(client,value)
    return poll(client,value,proof).json()


def start(client,cap):
    secret=secrets.token_urlsafe(32)
    response=client.post('/api/saas/v1/installations/'+cap['installation_id']+'/authorizations',
        headers={'Authorization':'Bearer '+cap['installation_secret']},json={'platform':'youtube','brand':'MyBrand','poll_challenge':hashlib.sha256(secret.encode()).hexdigest()})
    assert response.status_code==201,response.text
    return response.json(),{'Authorization':'Bearer '+secret}


def select(client,value,account='UC-two'):
    response=begin_browser(client,value['browser_url'])
    state=parse_qs(urlsplit(response.headers['location']).query)['state'][0]
    page=client.get('/oauth/youtube/callback',params={'state':state,'code':'fictional-code'})
    assert page.status_code==200 and 'Choose your YouTube account' in page.text
    assert '<title>Choose your account · YouTube · SocialCli</title>' in page.text
    assert 'href="/connect/assets/youtube.svg"' in page.text
    data=fields(page);data['account_id']=account
    return client.post('/account/connect/'+value['authorization_id'],data=data,headers={'Origin':'https://social.example'})


def selected(client,value,proof):
    return client.post('/api/saas/v1/authorizations/'+value['authorization_id']+'/poll',headers=proof).json()


def activate(client,result):
    return client.post('/api/saas/v1/connections/'+result['connection_id']+'/activate',headers={'Authorization':'Bearer '+result['connection_secret']})


def token(client,result,refresh=True):
    return client.post('/api/saas/v1/connections/'+result['connection_id']+'/token'+('' if refresh else '?refresh=false'),headers={'Authorization':'Bearer '+result['connection_secret']})


def test_user_selects_exact_account_in_browser_then_local_install_activation_starts_trial(tmp_path):
    client,now,cap=setup(tmp_path)
    value,proof=start(client,cap)
    assert value['expires_in']==600
    confirmation=select(client,value)
    assert confirmation.status_code==200
    assert 'Account confirmed · YouTube' in confirmation.text
    assert 'You can close this page and continue with your assistant.' in confirmation.text
    assert 'href="/account/">My Account</a>' in confirmation.text
    assert 'Keep the SocialCli command running' in confirmation.text
    assert 'href="/connect/assets/youtube.svg"' in confirmation.text
    result=selected(client,value,proof)
    assert result['status']=='selected' and result['account']['id']=='UC-two'
    assert 'social-refresh' not in str(result) and 'social-access' not in str(result)
    assert token(client,result).status_code==409
    assert activate(client,result).status_code==200
    assert activate(client,result).status_code==200
    assert token(client,result).json()['access_token']=='social-access'
    with client.app.state.saas.vault.transaction() as db:
        row=db.execute('SELECT trial_start,trial_end FROM saas_entitlements WHERE user_id=?',(USER,)).fetchone()
        assert tuple(row)==(1000,1210600)
    assert client.post('/v1/connections/'+result['connection_id']+'/token',headers={'Authorization':'Bearer '+result['connection_secret']}).status_code==404
    assert client.post('/v1/authorizations/'+value['authorization_id']+'/poll',headers=proof).status_code==404


def test_another_browser_cannot_start_or_select_owner_authorization(tmp_path):
    client,now,cap=setup(tmp_path)
    value,proof=start(client,cap)
    stranger=TestClient(client.app,base_url='https://social.example')
    page=stranger.get(value['browser_url'])
    response=stranger.post(value['browser_url'],data=fields(page),headers={'Origin':'https://social.example'},follow_redirects=False)
    assert response.status_code in {401,403}
    assert select(client,value,account='UC-not-owned').status_code==400
    assert selected(client,value,proof)['status']=='ready'


def test_second_agent_attaches_existing_grant_and_revocation_blocks_only_its_tokens(tmp_path):
    client,now,cap=setup(tmp_path)
    value,proof=start(client,cap);select(client,value);one=selected(client,value,proof);activate(client,one)
    second=new_installation(client)
    response=client.post('/api/saas/v1/installations/'+second['installation_id']+'/attach',headers={'Authorization':'Bearer '+second['installation_secret']},json={'platform':'youtube','brand':'MyBrand'})
    assert response.status_code==201,response.text
    two=response.json();assert activate(client,two).status_code==200
    assert token(client,two).status_code==200
    revoke='/api/saas/v1/installations/'+second['installation_id']
    assert client.delete(revoke,headers={'Authorization':'Bearer '+second['installation_secret']}).status_code==200
    assert token(client,two).status_code==401
    assert token(client,one).status_code==200


def test_trial_expiry_denies_tokens_and_new_oauth_without_destroying_status(tmp_path):
    client,now,cap=setup(tmp_path)
    value,proof=start(client,cap);select(client,value);result=selected(client,value,proof);activate(client,result)
    now[0]=1210600
    assert token(client,result).status_code==402
    path='/api/saas/v1/connections/'+result['connection_id']
    assert client.get(path,headers={'Authorization':'Bearer '+result['connection_secret']}).status_code==200
    assert client.delete(path,headers={'Authorization':'Bearer '+result['connection_secret']}).status_code==200
    assert token(client,result).status_code==404


def test_replacement_preserves_old_grant_until_activation_and_compare_swap_prevents_race(tmp_path):
    client,now,cap=setup(tmp_path)
    value,proof=start(client,cap);select(client,value);old=selected(client,value,proof);activate(client,old)
    a,pa=start(client,cap);b,pb=start(client,cap)
    select(client,a,'UC-two');select(client,b,'UC-two')
    one,two=selected(client,a,pa),selected(client,b,pb)
    assert token(client,old).status_code==200
    assert activate(client,one).status_code==200
    assert activate(client,two).status_code==409
    assert token(client,old).status_code==409
    assert token(client,one).status_code==200


def test_read_without_refresh_reports_renewal_without_reconnect(tmp_path):
    client,now,cap=setup(tmp_path)
    value,proof=start(client,cap);select(client,value);result=selected(client,value,proof);activate(client,result)
    # Provider fixture expiry follows the actual wall clock, not the trial clock.
    with client.app.state.saas.vault.transaction() as db:
        slot=db.execute('SELECT * FROM saas_slots WHERE user_id=?',(USER,)).fetchone()
        grant=client.app.state.saas.vault.get(db,'saas-grant',slot['grant_id'])
        grant['payload']['grant']['expires_at']=999
        client.app.state.saas.vault.put(db,'saas-grant',slot['grant_id'],grant['payload'],expires=grant['expires'])
    reply=token(client,result,False)
    assert reply.status_code==409 and 'renewal_required' in reply.text
    assert token(client,result).status_code==200


def test_verified_other_user_cannot_select_or_attach_connection_by_shared_brand_name(tmp_path):
    import json
    from tests.test_saas_auth import begin
    other_id='22222222-2222-4222-8222-222222222222';other_email='other@example.test'
    def identity(request):
        if request.url.path=='/verify' and json.loads(request.content).get('email')==other_email:
            return httpx.Response(200,json={'access_token':'other-identity-secret','refresh_token':'other-refresh-secret','expires_in':3600,
                'user':{'id':other_id,'email':other_email,'email_confirmed_at':'2026-10-13T00:00:00Z'}})
        if request.url.path=='/user' and request.headers.get('authorization')=='Bearer other-identity-secret':
            return httpx.Response(200,json={'id':other_id,'email':other_email,'email_confirmed_at':'2026-10-13T00:00:00Z'})
    client,now,_=service(tmp_path,handler=identity,provider=provider);login(client);cap=new_installation(client)
    value,proof=start(client,cap)
    other=TestClient(client.app,base_url='https://social.example')
    form=begin(other,email=other_email);form['code']='123456'
    assert other.post('/account/verify',data=form,headers={'Origin':'https://social.example'}).status_code==200
    response=begin_browser(client,value['browser_url'])
    state=parse_qs(urlsplit(response.headers['location']).query)['state'][0]
    client.get('/oauth/youtube/callback',params={'state':state,'code':'fictional-code'})
    assert other.get('/account/connect/'+value['authorization_id']).status_code==403
    form=fields(client.get('/account/connect/'+value['authorization_id']));form['account_id']='UC-two'
    assert client.post('/account/connect/'+value['authorization_id'],data=form,headers={'Origin':'https://social.example'}).status_code==200
    result=selected(client,value,proof);activate(client,result)
    other_cap=new_installation(other)
    reply=other.post('/api/saas/v1/installations/'+other_cap['installation_id']+'/attach',json={'platform':'youtube','brand':'MyBrand'},headers={'Authorization':'Bearer '+other_cap['installation_secret']})
    assert reply.status_code==402  # no owned activated trial/grant
    assert token(client,result).status_code==200


def test_signed_out_connection_resumes_after_email_login_without_consuming_oauth(tmp_path):
    from tests.test_saas_auth import begin
    client, now, cap = setup(tmp_path)
    value, proof = start(client, cap)
    client.cookies.clear()
    path = urlsplit(value['browser_url']).path
    page = client.get(path, follow_redirects=False)
    assert page.status_code == 303
    assert parse_qs(urlsplit(page.headers['location']).query)['next'] == [path]
    assert selected(client, value, proof)['status'] == 'pending'
    form = begin(client, next_path=path); form['code'] = '123456'
    result = client.post('/account/verify', data=form, headers={'Origin':'https://social.example'}, follow_redirects=False)
    assert result.headers['location'] == path
    assert select(client, value).status_code == 200


def test_session_expiring_after_connection_preview_redirects_without_consuming(tmp_path):
    client, now, cap = setup(tmp_path)
    value, proof = start(client, cap)
    page = client.get(value['browser_url']); form = fields(page)
    client.cookies.delete('socialcli-session')
    result = client.post(value['browser_url'], data=form, headers={'Origin':'https://social.example'}, follow_redirects=False)
    assert result.status_code == 303
    assert parse_qs(urlsplit(result.headers['location']).query)['next'] == [urlsplit(value['browser_url']).path]
    assert selected(client, value, proof)['status'] == 'pending'


def test_connection_return_paths_reject_external_or_malformed_targets():
    from socialctl.saas.routes import safe_next
    for path in ['/connect/'+'a'*24, '/account/connect/'+'a'*24]:
        assert safe_next(path) == path
    for path in ['//evil.example/connect/'+'a'*24, '/connect/'+'a'*24+'?next=https://evil.example', '/connect/../account/', '/connect/short']:
        assert safe_next(path) == '/account/'


def test_tiktok_web_login_reaches_browser_selection_without_client_credentials(tmp_path):
    """TikTok shares the owner/browser flow; its app secret stays server-side."""
    from socialctl.connection_service.settings import AppCredentials

    def tiktok_provider(request):
        assert request.url.host == 'open.tiktokapis.com'
        if request.url.path == '/v2/oauth/token/':
            body = parse_qs(request.content.decode())
            assert body['client_key'] == ['fictional-tiktok-app']
            assert body['redirect_uri'] == ['https://social.example/oauth/tiktok/callback']
            assert body['grant_type'] == ['authorization_code']
            assert 'client_id' not in body and 'code_verifier' not in body
            return httpx.Response(200, json={
                'access_token': 'fictional-tiktok-access', 'refresh_token': 'fictional-tiktok-refresh',
                'expires_in': 86400, 'open_id': 'fictional-creator',
                'scope': 'user.info.basic,video.upload,user.info.stats,video.list'})
        assert request.url.path == '/v2/user/info/'
        return httpx.Response(200, json={'data': {'user': {
            'open_id': 'fictional-creator', 'display_name': 'Fictional creator'}}, 'error': {'code': 'ok'}})

    client, _, _ = service(tmp_path, provider=tiktok_provider)
    client.app.state.saas_connections.providers.settings.providers['tiktok'] = AppCredentials(
        'fictional-tiktok-app', 'fictional-tiktok-secret')
    login(client)
    cap = new_installation(client)
    secret = secrets.token_urlsafe(32)
    proof = {'Authorization': 'Bearer ' + secret}
    response = client.post('/api/saas/v1/installations/' + cap['installation_id'] + '/authorizations',
        headers={'Authorization': 'Bearer ' + cap['installation_secret']}, json={
            'platform': 'tiktok', 'brand': 'MyBrand', 'poll_challenge': hashlib.sha256(secret.encode()).hexdigest()})
    assert response.status_code == 201
    value = response.json()
    page = client.get(value['browser_url'])
    assert 'Connect to TikTok' in page.text
    assert 'https://www.tiktok.com' in page.headers['content-security-policy']
    redirect = begin_browser(client, value['browser_url'])
    assert redirect.status_code == 303
    url = urlsplit(redirect.headers['location'])
    assert url.scheme == 'https' and url.netloc == 'www.tiktok.com' and url.path == '/v2/auth/authorize/'
    params = parse_qs(url.query)
    assert params['client_key'] == ['fictional-tiktok-app']
    assert params['redirect_uri'] == ['https://social.example/oauth/tiktok/callback']
    assert 'client_secret' not in params and 'code_challenge' not in params
    callback = client.get('/oauth/tiktok/callback', params={'state': params['state'][0], 'code': 'fictional-code'})
    assert callback.status_code == 200 and 'Fictional creator' in callback.text
    assert 'fictional-tiktok-secret' not in callback.text
    form = fields(callback); form['account_id'] = 'fictional-creator'
    confirmed = client.post('/account/connect/' + value['authorization_id'], data=form,
                            headers={'Origin': 'https://social.example'})
    assert confirmed.status_code == 200
    result = selected(client, value, proof)
    assert result['account'] == {'id': 'fictional-creator', 'name': 'Fictional creator'}
    assert activate(client, result).status_code == 200
    assert token(client, result).json()['access_token'] == 'fictional-tiktok-access'
