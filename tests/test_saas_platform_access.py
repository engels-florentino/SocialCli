"""Server enforcement with fictional owners and provider grants, not CLI guards."""
import hashlib
import secrets
import pytest
from tests.test_saas_auth import service,login,fields
from tests.test_saas_pairing import pair,poll
from tests.test_saas_connections import provider,start,select,selected,activate,token,setup


def restricted(client,platforms):
    value,proof=pair(client)
    form=fields(client.get(value['browser_url']))
    for name in list(form):
        if name.startswith('allow_'):form.pop(name)
    form.update({f'allow_{p}':'1' for p in platforms})
    result=client.post(value['browser_url'],data=form,headers={'Origin':'https://social.example'})
    assert result.status_code==200,result.text
    return poll(client,value,proof).json()


def installation_auth(cap):return {'Authorization':'Bearer '+cap['installation_secret']}


def mutate_policy(client,cap,value):
    vault=client.app.state.saas.vault
    with vault.transaction() as db:
        record=vault.get(db,'saas-installation',cap['installation_id'])
        if value is None:record['payload'].pop('platform_access',None)
        else:record['payload']['platform_access']=value
        vault.put(db,'saas-installation',cap['installation_id'],record['payload'],expires=record['expires'],credential_hash=record['credential_hash'])


def test_owner_platform_selection_visible_in_status_and_account(tmp_path):
    client,_,_=service(tmp_path,provider=provider);login(client)
    cap=restricted(client,['youtube'])
    data=client.get('/api/saas/v1/installations/'+cap['installation_id'],headers=installation_auth(cap)).json()
    assert data['platform_access']['platforms']==['youtube']
    assert data['platform_access']['action_restrictions_enforced'] is False
    page=client.get('/account/')
    assert 'YouTube' in page.text and 'Previously issued' in page.text
    start(client,cap)


def test_unselected_platform_cannot_start_oauth_or_attach(tmp_path):
    client,_,_=service(tmp_path,provider=provider);login(client)
    cap=restricted(client,['facebook'])
    base='/api/saas/v1/installations/'+cap['installation_id']
    body={'platform':'youtube','brand':'MyBrand'}
    for route in ('attach','authorizations'):
        data={**body}
        if route=='authorizations':data['poll_challenge']=hashlib.sha256(b'fictional').hexdigest()
        response=client.post(base+'/'+route,json=data,headers=installation_auth(cap))
        assert response.status_code==403 and 'platform_not_allowed' in response.text
    with client.app.state.saas.vault.transaction() as db:
        assert db.execute("SELECT count(*) FROM records WHERE kind IN ('authorization','saas-candidate')").fetchone()[0]==0


@pytest.mark.parametrize('policy',[None,{}, {'version':2,'platforms':['youtube']},
    {'version':1,'platforms':'youtube'}, {'version':1,'platforms':['youtube','unknown']},
    {'version':True,'platforms':['youtube']}])
def test_missing_or_unknown_policy_cannot_export_existing_grant(tmp_path,policy):
    client,_,cap=setup(tmp_path);value,proof=start(client,cap);select(client,value)
    grant=selected(client,value,proof);activate(client,grant)
    mutate_policy(client,cap,policy)
    reply=token(client,grant)
    assert reply.status_code==403 and 'installation_permissions_invalid' in reply.text
    # Owners can still inspect/revoke this installation to recover.
    assert client.get('/api/saas/v1/installations/'+cap['installation_id'],headers=installation_auth(cap)).status_code==200


def test_policy_rechecked_before_activation_and_token_after_refresh(tmp_path,monkeypatch):
    client,now,cap=setup(tmp_path);value,proof=start(client,cap);select(client,value)
    grant=selected(client,value,proof)
    mutate_policy(client,cap,{'version':1,'platforms':[]})
    assert activate(client,grant).status_code==403
    mutate_policy(client,cap,{'version':1,'platforms':['youtube']})
    assert activate(client,grant).status_code==200
    connections=client.app.state.saas_connections;vault=connections.vault
    with vault.transaction() as db:
        record=vault.get(db,'saas-capability',grant['connection_id'])
        key=record['payload']['grant_id'];saved=vault.get(db,'saas-grant',key)
        saved['payload']['grant']['expires_at']=now[0]
        vault.put(db,'saas-grant',key,saved['payload'],expires=saved['expires'])
    def refresh(platform,current):
        mutate_policy(client,cap,{'version':1,'platforms':[]})
        return {**current,'access_token':'new-token-must-not-escape','expires_at':now[0]+3600}
    monkeypatch.setattr(connections.providers,'refresh',refresh)
    reply=token(client,grant)
    assert reply.status_code==403 and 'new-token-must-not-escape' not in reply.text


def test_new_pairing_without_networks_is_valid_and_unknown_checkbox_rejected(tmp_path):
    client,_,_=service(tmp_path,provider=provider);login(client)
    cap=restricted(client,[])
    reply=client.post('/api/saas/v1/installations/'+cap['installation_id']+'/attach',
        json={'platform':'youtube','brand':'MyBrand'},headers=installation_auth(cap))
    assert reply.status_code==403
    value,_=pair(client);form=fields(client.get(value['browser_url']));form['allow_unknown']='1'
    assert client.post(value['browser_url'],data=form,headers={'Origin':'https://social.example'}).status_code==400


def test_schema_seven_makes_legacy_approval_explicit_without_broadening_new_policy(tmp_path):
    from socialctl.saas.store import SaaSStore
    client,_,cap=setup(tmp_path);metadata=client.app.state.saas
    mutate_policy(client,cap,None)
    with metadata.vault.transaction() as db:
        db.execute('DELETE FROM saas_schema WHERE version>=7')
        db.execute('ALTER TABLE saas_rates DROP COLUMN expires')
        db.execute('ALTER TABLE saas_billing_events DROP COLUMN semantic_hash')
    upgraded=SaaSStore(metadata.vault,metadata.eligibility_keys)
    with upgraded.vault.transaction() as db:
        data=upgraded.vault.get(db,'saas-installation',cap['installation_id'])['payload']
        assert data['platform_access']['source']=='legacy_approval'
        assert set(data['platform_access']['platforms'])=={'youtube','facebook','instagram','tiktok'}
        assert db.execute('SELECT max(version) FROM saas_schema').fetchone()[0]==9
    mutate_policy(client,cap,{'version':1,'platforms':['youtube']})
    SaaSStore(metadata.vault,metadata.eligibility_keys)
    with metadata.vault.transaction() as db:
        assert metadata.vault.get(db,'saas-installation',cap['installation_id'])['payload']['platform_access']['platforms']==['youtube']


def test_legacy_migration_and_pending_authorization_recheck_platform(tmp_path):
    from tests.test_saas_migration import old_connection,migration
    client,_,cap=setup(tmp_path)
    old=old_connection(client)
    value,proof=start(client,cap)
    mutate_policy(client,cap,{'version':1,'platforms':[]})
    assert migration(client,cap,old).status_code==403
    response=client.post('/api/saas/v1/authorizations/'+value['authorization_id']+'/poll',headers=proof)
    assert response.status_code==403
    page=client.get(value['browser_url'])
    assert page.status_code in {403,404}


@pytest.mark.parametrize('code',['platform_not_allowed','installation_permissions_invalid'])
def test_permission_denial_has_fixed_user_action_without_retry_or_reconnect(code):
    from socialctl.agent_errors import error_for_code
    result=error_for_code(code)
    assert result['code']==code and result['action']=='user_action'
    assert result['requires_user_action'] and not result['retryable']


@pytest.mark.parametrize('when',['before_exchange','during_exchange'])
def test_oauth_callback_never_exchanges_or_saves_ready_grant_after_policy_loss(tmp_path,monkeypatch,when):
    from tests.test_connection_service import begin_browser
    from urllib.parse import urlsplit,parse_qs
    client,_,cap=setup(tmp_path);value,proof=start(client,cap)
    redirect=begin_browser(client,value['browser_url'])
    state=parse_qs(urlsplit(redirect.headers['location']).query)['state'][0]
    connections=client.app.state.saas_connections
    calls=[];original=connections.providers.exchange
    def exchange(*args,**kwargs):
        calls.append(True)
        mutate_policy(client,cap,{'version':1,'platforms':[]})
        return original(*args,**kwargs)
    monkeypatch.setattr(connections.providers,'exchange',exchange)
    if when=='before_exchange':mutate_policy(client,cap,{'version':1,'platforms':[]})
    response=client.get('/oauth/youtube/callback',params={'state':state,'code':'fictional-code'})
    assert response.status_code==403
    assert len(calls)==(1 if when=='during_exchange' else 0)
    with connections.vault.transaction() as db:
        record=connections.vault.get(db,'authorization',value['authorization_id'])
        assert record['payload']['status']!='ready' and 'result' not in record['payload']
