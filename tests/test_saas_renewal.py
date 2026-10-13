"""Durable renewal outcomes with fictional provider transports, never live OAuth."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from urllib.parse import parse_qs

import httpx
import pytest

from tests.test_saas_auth import service, login, USER
from tests.test_saas_connections import new_installation, start, select, selected, activate, token, provider


def connected(tmp_path, outcome):
    calls=[]
    def transport(request):
        if request.url.host=='oauth2.googleapis.com' and parse_qs(request.content.decode()).get('grant_type')==['refresh_token']:
            calls.append(request)
            return outcome(request)
        return provider(request)
    client,now,_=service(tmp_path,provider=transport);login(client)
    cap=new_installation(client);value,proof=start(client,cap);select(client,value)
    result=selected(client,value,proof);assert activate(client,result).status_code==200
    vault=client.app.state.saas.vault
    with vault.transaction() as db:
        grant_id=db.execute('SELECT grant_id FROM saas_slots WHERE user_id=?',(USER,)).fetchone()[0]
        record=vault.get(db,'saas-grant',grant_id)
        record['payload']['grant']['expires_at']=999
        vault.put(db,'saas-grant',grant_id,record['payload'],expires=record['expires'])
    return client,now,result,calls,grant_id


def status(client,result):
    response=client.get('/api/saas/v1/connections/'+result['connection_id'],headers={'Authorization':'Bearer '+result['connection_secret']})
    assert response.status_code==200,response.text
    assert 'social-refresh' not in response.text
    return response.json()


def success(request):
    return httpx.Response(200,json={'access_token':'renewed-access','refresh_token':'rotated-refresh','expires_in':3600})


@pytest.mark.parametrize('error',[httpx.ConnectError,httpx.ConnectTimeout,httpx.PoolTimeout])
def test_failure_before_request_can_retry_after_backoff_without_reconnect(tmp_path,error):
    def outcome(request):
        if len(calls)==1: raise error('fictional-sensitive-detail',request=request)
        return success(request)
    client,now,result,calls,_=connected(tmp_path,outcome)
    response=token(client,result)
    assert response.status_code==503 and 'renewal_retryable:' in response.text
    assert 'fictional-sensitive-detail' not in response.text
    assert status(client,result)['needs_reconnect'] is False
    assert token(client,result).status_code==503 and len(calls)==1
    now[0]+=31
    assert token(client,result,False).status_code==409 and len(calls)==1
    assert token(client,result).json()['access_token']=='renewed-access'
    assert len(calls)==2 and status(client,result)['renewal_status']=='ready'


def test_confirmed_revocation_is_durable_and_shared_between_agents(tmp_path):
    client,now,result,calls,_=connected(tmp_path,lambda r:httpx.Response(400,json={'error':'invalid_grant','error_description':'fictional-secret'}))
    response=token(client,result)
    assert response.status_code==409 and 'provider_authorization_expired:' in response.text
    assert 'fictional-secret' not in response.text
    assert status(client,result)['needs_reconnect'] is True
    cap=new_installation(client)
    second=client.post('/api/saas/v1/installations/'+cap['installation_id']+'/attach',headers={'Authorization':'Bearer '+cap['installation_secret']},json={'platform':'youtube','brand':'MyBrand'}).json()
    assert activate(client,second).status_code==200
    now[0]+=301
    assert token(client,second).status_code==409 and len(calls)==1
    assert status(client,second)['needs_reconnect'] is True
    assert 'authorization expired or was revoked' in client.get('/account/').text


@pytest.mark.parametrize('code',['invalid_client','unauthorized_client','invalid_request','unsupported_grant_type'])
def test_provider_configuration_rejection_is_retryable_after_operator_fix(tmp_path,code):
    def outcome(request):
        return httpx.Response(401,json={'error':code}) if len(calls)==1 else success(request)
    client,now,result,calls,_=connected(tmp_path,outcome)
    response=token(client,result)
    assert response.status_code==503 and 'provider_configuration:' in response.text
    assert status(client,result)['needs_reconnect'] is False
    now[0]+=301
    assert token(client,result).status_code==200 and len(calls)==2


@pytest.mark.parametrize('outcome',[
    lambda r: (_ for _ in ()).throw(httpx.ReadTimeout('secret',request=r)),
    lambda r: (_ for _ in ()).throw(httpx.WriteTimeout('secret',request=r)),
    lambda r: httpx.Response(503,json={'error':'server_error'}),
    lambda r: httpx.Response(200,json={'access_token':'rotated-but-invalid','expires_in':False}),
    lambda r: httpx.Response(200,text='not json'),
])
def test_uncertain_result_is_not_replayed_or_misreported_as_pending_forever(tmp_path,outcome):
    client,now,result,calls,_=connected(tmp_path,outcome)
    assert 'renewal_uncertain:' in token(client,result).text
    now[0]+=301
    response=token(client,result)
    assert response.status_code==503 and 'renewal_uncertain:' in response.text
    assert len(calls)==1
    view=status(client,result)
    assert view['renewal_status']=='uncertain' and view['needs_reconnect'] is False
    assert 'Renewal could not be confirmed' in client.get('/account/').text
    # A fresh explicit owner-approved OAuth replacement is the escape hatch.
    cap=new_installation(client);value,proof=start(client,cap);select(client,value)
    replacement=selected(client,value,proof);assert activate(client,replacement).status_code==200
    assert token(client,replacement).status_code==200
    assert token(client,result).status_code==409


def test_crash_lease_is_reported_uncertain_without_automatic_replay(tmp_path):
    client,now,result,calls,grant_id=connected(tmp_path,success)
    vault=client.app.state.saas.vault
    with vault.transaction() as db:
        record=vault.get(db,'saas-grant',grant_id)
        record['payload'].update(refresh_lease='fictional-lease',refresh_started_at=now[0])
        vault.put(db,'saas-grant',grant_id,record['payload'],expires=record['expires'])
    assert status(client,result)['renewal_status']=='pending'
    now[0]+=121
    assert status(client,result)['renewal_status']=='uncertain'
    assert 'renewal_uncertain:' in token(client,result).text and calls==[]


def test_two_agents_never_send_duplicate_refresh_and_rotated_secret_is_persisted(tmp_path):
    entered,release=Event(),Event()
    def outcome(request):
        entered.set();assert release.wait(5)
        return success(request)
    client,now,result,calls,grant_id=connected(tmp_path,outcome)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(token,client,result)
        try:
            assert entered.wait(5)
            assert token(client,result).status_code==503
            assert status(client,result)['renewal_status']=='pending'
        finally: release.set()
        assert pending.result().status_code==200
    assert len(calls)==1
    with client.app.state.saas.vault.transaction() as db:
        record=client.app.state.saas.vault.get(db,'saas-grant',grant_id)
        assert record['payload']['grant']['refresh_token']=='rotated-refresh'
        assert 'refresh_lease' not in record['payload']


def test_renewal_response_after_disconnect_cannot_restore_grant(tmp_path):
    def outcome(request):
        response=client.delete('/api/saas/v1/connections/'+result['connection_id'],headers={'Authorization':'Bearer '+result['connection_secret']})
        assert response.status_code==200
        return success(request)
    client,now,result,calls,grant_id=connected(tmp_path,outcome)
    assert token(client,result).status_code==409
    with client.app.state.saas.vault.transaction() as db:
        assert client.app.state.saas.vault.get(db,'saas-grant',grant_id) is None


def test_provider_permissions_reduced_on_refresh_requires_explicit_consent(tmp_path):
    client,now,result,calls,_=connected(tmp_path,lambda r:httpx.Response(200,json={
        'access_token':'reduced-access','refresh_token':'rotated-refresh','expires_in':3600,
        'scope':'https://www.googleapis.com/auth/youtube.readonly'}))
    response=token(client,result)
    assert response.status_code==409 and 'provider_permissions_changed:' in response.text
    assert status(client,result)['needs_reconnect'] is True
    assert 'permissions changed' in client.get('/account/').text
    now[0]+=301
    assert token(client,result).status_code==409 and len(calls)==1


@pytest.mark.parametrize('code',['renewal_required','renewal_pending','renewal_retryable','renewal_uncertain','provider_configuration','provider_permissions_changed','provider_authorization_expired'])
def test_cli_diagnostics_keep_fixed_error_code_without_provider_text(code):
    from socialctl.connections.saas_client import SaaSClient,ServiceRejected
    status_code=409 if code in {'renewal_required','provider_permissions_changed','provider_authorization_expired'} else 503
    def handler(request): return httpx.Response(status_code,json={'detail':code+': fictional-secret'})
    client=SaaSClient('https://social.example',httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ServiceRejected) as caught:
        client.request('POST','/v1/connections/fake/token',secret='fictional-proof')
    assert caught.value.code==code and 'fictional-secret' not in str(caught.value)
    if code not in {'provider_permissions_changed','provider_authorization_expired'}:
        assert 'reconnect' not in str(caught.value).lower()
    else:
        assert 'explicitly' in str(caught.value).lower()


def test_expired_nonrenewable_or_refresh_token_reports_reconnect_without_network(tmp_path):
    client,now,result,calls,grant_id=connected(tmp_path,success)
    vault=client.app.state.saas.vault
    for platform in ('youtube','facebook','instagram'):
        with vault.transaction() as db:
            record=vault.get(db,'saas-grant',grant_id)
            record['payload']['platform']=platform
            if platform=='youtube': record['payload']['grant']['refresh_expires_at']=999
            else: record['payload']['grant'].pop('refresh_expires_at',None)
            vault.put(db,'saas-grant',grant_id,record['payload'],expires=record['expires'])
        assert status(client,result)['needs_reconnect'] is True
        assert token(client,result).status_code==409
    assert calls==[]


@pytest.mark.parametrize('error,text',[
    ('renewal_retryable','retry later'),('renewal_uncertain','renewal uncertain'),
    ('provider_configuration','configuration needs attention'),
    ('provider_permissions_changed','reconnect required'),
    ('provider_authorization_expired','reconnect required'),
    (None,'renewal required'),('pending','renewal pending'),
])
def test_plain_cli_connection_status_reports_actual_renewal_state(tmp_path,monkeypatch,error,text):
    from tests.test_saas_cli import wire,runner,app,crear_brand
    server,now,ring=wire(tmp_path,monkeypatch);crear_brand(tmp_path,'MyBrand')
    common=['--brand','MyBrand','--root',str(tmp_path)]
    assert runner.invoke(app,['account','login',*common,'--service','https://social.example','--no-browser']).exit_code==0
    assert runner.invoke(app,['connect','youtube',*common,'--no-browser']).exit_code==0
    vault=server.app.state.saas.vault
    with vault.transaction() as db:
        grant_id=db.execute('SELECT grant_id FROM saas_slots WHERE user_id=?',(USER,)).fetchone()[0]
        record=vault.get(db,'saas-grant',grant_id);data=record['payload'];data['grant']['expires_at']=999
        if error=='pending': data.update(refresh_lease='fictional-lease',refresh_started_at=now[0])
        elif error: data['refresh_error']=error
        vault.put(db,'saas-grant',grant_id,data,expires=record['expires'])
    response=runner.invoke(app,['connections',*common])
    assert response.exit_code==0,response.output
    assert text in response.output and ' — connected' not in response.output


def test_broker_inactivity_is_not_reported_as_provider_revocation(tmp_path):
    client,now,result,calls,grant_id=connected(tmp_path,success)
    vault=client.app.state.saas.vault
    with vault.transaction() as db:
        record=vault.get(db,'saas-grant',grant_id)
        vault.put(db,'saas-grant',grant_id,record['payload'],expires=999)
    response=token(client,result)
    assert response.status_code==409 and 'connection_inactive:' in response.text
    response=client.get('/api/saas/v1/connections/'+result['connection_id'],headers={'Authorization':'Bearer '+result['connection_secret']})
    assert response.status_code==409 and 'connection_inactive:' in response.text
    assert 'expired after inactivity' in client.get('/account/').text
    assert calls==[]


@pytest.mark.parametrize('platform,refresh_value',[(p,v) for p in ('youtube','tiktok') for v in (None,'',False)]+[('tiktok','missing')])
def test_invalid_refresh_token_response_is_uncertain(platform,refresh_value):
    from types import SimpleNamespace
    from socialctl.connection_service.providers import Providers,RefreshFailure
    from socialctl.connection_service.settings import AppCredentials
    body={'access_token':'new-access','expires_in':3600,'open_id':'fictional-open-id','scope':'user.info.basic'}
    if refresh_value!='missing': body['refresh_token']=refresh_value
    providers=Providers(SimpleNamespace(providers={platform:AppCredentials('fictional-app','fictional-secret')}),
        httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=body))))
    grant={'access_token':'old-access','refresh_token':'old-refresh','expires_at':1,
        'open_id':'fictional-open-id','granted_scopes':['user.info.basic']}
    with pytest.raises(RefreshFailure) as error:
        providers.refresh(platform,grant)
    assert error.value.code=='renewal_uncertain'
