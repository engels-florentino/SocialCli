"""Google disconnect is immediate locally and recoverable across provider outages."""
import sqlite3
import pytest
import httpx
from tests.test_saas_lifecycle import connected, post
from tests.test_saas_auth import USER, fields
from tests.test_saas_connections import token


def revoker(client, reply):
    provider=client.app.state.saas_connections.providers
    calls=[]
    def handle(request):
        assert request.url == 'https://oauth2.googleapis.com/revoke'
        assert request.method == 'POST'
        assert request.content == b'token=social-refresh'
        with sqlite3.connect(client.app.state.saas.vault.path,timeout=.05) as db:
            db.execute('BEGIN IMMEDIATE'); db.rollback()
        calls.append(request)
        return reply(request)
    provider.client=httpx.Client(transport=httpx.MockTransport(handle))
    return provider,calls


def test_disconnect_retries_encrypted_minimal_token_after_restart(tmp_path):
    client,now,_,connection=connected(tmp_path)
    providers,calls=revoker(client,lambda r:httpx.Response(503,json={}))
    reply=client.delete('/api/saas/v1/connections/'+connection['connection_id'],headers={'Authorization':'Bearer '+connection['connection_secret']})
    assert reply.status_code == 200
    assert len(calls)==1
    assert reply.json()['provider_revocation_status']=='pending'
    assert token(client,connection).status_code in {404,409}
    vault=client.app.state.saas.vault
    with vault.read_transaction() as db:
        job=db.execute('SELECT * FROM saas_google_revocations').fetchone()
        assert job['state']=='pending' and job['attempts']==1
        saved=vault.get(db,'google-revocation',job['id'])
        assert saved['payload']=={'token':'social-refresh'}
        assert all(b'social-refresh' not in row[0] for row in db.execute('SELECT payload FROM records'))
    from socialctl.saas.revocation import run_revocations
    providers,calls=revoker(client,lambda r:httpx.Response(200))
    now[0]+=301
    assert run_revocations(client.app.state.saas,providers,lambda:now[0])['completed']==1
    with vault.read_transaction() as db:
        assert vault.get(db,'google-revocation',job['id']) is None
        assert db.execute('SELECT state FROM saas_google_revocations').fetchone()[0]=='complete'
    assert run_revocations(client.app.state.saas,providers,lambda:now[0])['completed']==0
    assert len(calls)==1


def test_owner_disconnect_sends_revocation_and_preserves_backup_and_other_owner(tmp_path):
    client,now,_,connection=connected(tmp_path)
    providers,calls=revoker(client,lambda r:httpx.Response(200))
    vault=client.app.state.saas.vault
    with vault.transaction() as db:
        vault.put(db,'operator-backup','backup',{'token':'separate-backup-token'},expires=now[0]+999)
        vault.put(db,'saas-grant','other',{'user_id':'other','platform':'youtube','grant':{'refresh_token':'other-token'}},expires=now[0]+999)
    assert post(client,'/account/connections/youtube/disconnect',fields(client.get('/account/'))).status_code==303
    assert len(calls)==1
    with vault.read_transaction() as db:
        assert vault.get(db,'operator-backup','backup')
        assert vault.get(db,'saas-grant','other')
        assert db.execute("SELECT count(*) FROM saas_deletion_tombstones WHERE kind='grant'").fetchone()[0]==1


def test_assistant_only_revoke_does_not_revoke_google(tmp_path):
    client,now,installation,connection=connected(tmp_path)
    providers,calls=revoker(client,lambda r:httpx.Response(200))
    assert post(client,'/account/installations/'+installation['installation_id']+'/revoke',fields(client.get('/account/'))).status_code==303
    assert calls==[]
    with client.app.state.saas.vault.read_transaction() as db:
        assert db.execute("SELECT count(*) FROM records WHERE kind='saas-grant'").fetchone()[0]==1


def test_account_deletion_keeps_failed_revocation_after_identity_cleanup(tmp_path):
    client,now,_,connection=connected(tmp_path)
    providers,calls=revoker(client,lambda r:httpx.Response(503))
    data=fields(client.get('/account/delete'));data['confirmation']='DELETE'
    assert post(client,'/account/delete',data).status_code==200
    assert len(calls)==1
    vault=client.app.state.saas.vault
    with vault.read_transaction() as db:
        assert db.execute('SELECT count(*) FROM saas_record_owners WHERE user_id=?',(USER,)).fetchone()[0]==0
        job=db.execute('SELECT * FROM saas_google_revocations').fetchone()
        assert vault.get(db,'google-revocation',job['id'])['payload']=={'token':'social-refresh'}
        assert db.execute("SELECT id FROM saas_deletion_tombstones WHERE kind='user'").fetchone()[0]==USER


def test_pending_revocation_blocks_reconnect_until_cleanup_completes(tmp_path):
    from tests.test_saas_connections import start
    import hashlib
    client,now,installation,connection=connected(tmp_path)
    providers,calls=revoker(client,lambda r:httpx.Response(503))
    assert post(client,'/account/connections/youtube/disconnect',fields(client.get('/account/'))).status_code==303
    reply=client.post('/api/saas/v1/installations/'+installation['installation_id']+'/authorizations',
        headers={'Authorization':'Bearer '+installation['installation_secret']},
        json={'platform':'youtube','brand':'MyBrand','poll_challenge':hashlib.sha256(b'fictional').hexdigest()})
    assert reply.status_code==409 and 'google_revocation_pending' in reply.text


def test_provider_revocation_only_accepts_confirmed_or_exact_invalid_token(tmp_path):
    import pytest
    from socialctl.connection_service.providers import Providers
    for status,body,accepted in [(200,{},True),(400,{'error':'invalid_token'},True),
                                (400,{'error':'invalid_request'},False),(401,{},False),(302,{},False),(500,{},False)]:
        requests=[]
        def handle(request):
            requests.append(request)
            return httpx.Response(status,json=body,headers={'location':'https://untrusted.example'})
        provider=Providers(None,httpx.Client(transport=httpx.MockTransport(handle)))
        if accepted:provider.revoke_google('fictional-token')
        else:
            with pytest.raises(ValueError,match='^google_revocation_pending$'):
                provider.revoke_google('fictional-token')
        assert len(requests)==1
        assert not requests[0].url.query and requests[0].content==b'token=fictional-token'


def test_revocation_lease_budget_and_overdue_survive_expiry_prune(tmp_path):
    from socialctl.saas.revocation import run_revocations
    from socialctl.saas.budget import WorkBudget
    client,now,_,connection=connected(tmp_path)
    providers,calls=revoker(client,lambda r:httpx.Response(503))
    vault=client.app.state.saas.vault
    with vault.transaction() as db:
        job=client.app.state.saas_lifecycle.disconnect(db,USER,'youtube')
    result=run_revocations(client.app.state.saas,providers,lambda:now[0],budget=WorkBudget(0))
    assert result['due']==1 and not calls
    with vault.transaction() as db:
        db.execute('UPDATE saas_google_revocations SET lease=?,lease_until=?',('crashed-worker',now[0]+300))
    assert run_revocations(client.app.state.saas,providers,lambda:now[0])['completed']==0
    assert not calls
    now[0]+=31*86400
    client.app.state.saas_lifecycle.prune_expired()
    result=run_revocations(client.app.state.saas,providers,lambda:now[0])
    assert result['overdue']==1 and len(calls)==1
    with vault.read_transaction() as db:assert vault.get(db,'google-revocation',job)


def test_generic_expiry_cannot_discard_only_google_revocation_credential(tmp_path):
    client,now,_,connection=connected(tmp_path)
    vault=client.app.state.saas.vault
    with vault.transaction() as db:
        grant_id=db.execute('SELECT grant_id FROM saas_slots WHERE user_id=?',(USER,)).fetchone()[0]
        record=vault.get(db,'saas-grant',grant_id)
        vault.put(db,'saas-grant',grant_id,record['payload'],expires=now[0]-1)
        vault.purge(db,now=now[0])
        assert vault.get(db,'saas-grant',grant_id)
    client.app.state.saas_lifecycle.prune_expired()
    with vault.read_transaction() as db:
        assert vault.get(db,'saas-grant',grant_id) is None
        assert vault.get(db,'google-revocation',grant_id)['payload']['token']=='social-refresh'


def test_external_revocation_blocks_tokens_and_status_requests_local_cleanup(tmp_path):
    from socialctl.saas.renewal import record_failure
    client,now,_,connection=connected(tmp_path)
    vault=client.app.state.saas.vault
    with vault.transaction() as db:
        grant_id=db.execute('SELECT grant_id FROM saas_slots WHERE user_id=?',(USER,)).fetchone()[0]
        entry=vault.get(db,'saas-grant',grant_id)
        record_failure(entry['payload'],'provider_authorization_expired',now[0])
        vault.put(db,'saas-grant',grant_id,entry['payload'],expires=entry['expires'])
    assert token(client,connection).status_code==409
    status=client.get('/api/saas/v1/connections/'+connection['connection_id'],headers={'Authorization':'Bearer '+connection['connection_secret']})
    assert status.status_code==200
    assert status.json()['data_cleanup_required'] is True
    assert status.json()['needs_reconnect'] is True


def test_disconnect_cancels_preexisting_google_oauth_result_and_queues_its_token(tmp_path):
    client,now,installation,connection=connected(tmp_path)
    vault=client.app.state.saas.vault
    with vault.transaction() as db:
        candidate=client.app.state.saas_connections.candidate(db,installation['installation_id'],USER,
            'youtube','MyBrand',{'id':'UC-pending'},grant={'refresh_token':'pending-refresh','granted_scopes':[]})
        vault.put(db,'authorization','pending-oauth',{'platform':'youtube','saas':{'user_id':USER},
            'result':{'grant':{'refresh_token':'oauth-refresh'}}},expires=now[0]+600)
        client.app.state.saas_lifecycle.disconnect(db,USER,'youtube')
    with vault.read_transaction() as db:
        assert vault.get(db,'saas-candidate',candidate['connection_id']) is None
        assert vault.get(db,'authorization','pending-oauth') is None
        assert vault.get(db,'google-revocation',candidate['connection_id'])['payload']=={'token':'pending-refresh'}
        assert vault.get(db,'google-revocation','pending-oauth')['payload']=={'token':'oauth-refresh'}


@pytest.mark.parametrize('action',['disconnect','delete','assistant_only'])
def test_late_oauth_exchange_after_owner_revocation_keeps_cleanup_token(tmp_path,monkeypatch,action):
    from tests.test_saas_connections import start
    from tests.test_connection_service import begin_browser
    from urllib.parse import parse_qs,urlsplit
    client,now,installation,connection=connected(tmp_path)
    value,_=start(client,installation)
    redirect=begin_browser(client,value['browser_url'])
    state=parse_qs(urlsplit(redirect.headers['location']).query)['state'][0]
    metadata=client.app.state.saas;lifecycle=client.app.state.saas_lifecycle
    providers=client.app.state.saas_connections.providers;original=providers.exchange
    def exchange(*args,**kwargs):
        grant=original(*args,**kwargs)
        grant['grant']['refresh_token']='late-exchange-refresh'
        if action=='delete':lifecycle.request_delete(USER)
        else:
            with metadata.vault.transaction() as db:
                if action=='disconnect':lifecycle.disconnect(db,USER,'youtube')
                else:lifecycle.revoke(db,USER,installation['installation_id'])
        if action!='assistant_only':
            from socialctl.saas.revocation import run_revocations
            cleanup=run_revocations(metadata,providers,lambda:now[0])
            assert cleanup['completed']==1 and cleanup['awaiting_credentials']==1
            if action=='disconnect':
                from fastapi import HTTPException
                with metadata.vault.read_transaction() as db, pytest.raises(HTTPException,match='google_revocation_pending'):
                    client.app.state.saas_connections.require_platform(db,installation['installation_id'],'youtube')
        return grant
    monkeypatch.setattr(providers,'exchange',exchange)
    result=client.get('/oauth/youtube/callback',params={'state':state,'code':'fictional-code'})
    with metadata.vault.read_transaction() as db:
        assert metadata.vault.get(db,'authorization',value['authorization_id']) is None
        cleanup=metadata.vault.get(db,'google-revocation',value['authorization_id'])
        if action=='assistant_only':assert cleanup is None
        else:
            assert cleanup and cleanup['payload']=={'token':'late-exchange-refresh'}
            assert result.status_code==409


@pytest.mark.parametrize('action',['disconnect','delete'])
def test_rotated_refresh_returned_after_revocation_gets_its_own_durable_cleanup(tmp_path,monkeypatch,action):
    from socialctl.saas.revocation import run_revocations
    client,now,installation,connection=connected(tmp_path)
    metadata=client.app.state.saas;vault=metadata.vault;lifecycle=client.app.state.saas_lifecycle
    providers=client.app.state.saas_connections.providers
    with vault.transaction() as db:
        grant_id=db.execute('SELECT grant_id FROM saas_slots WHERE user_id=?',(USER,)).fetchone()[0]
        entry=vault.get(db,'saas-grant',grant_id)
        entry['payload']['grant']['expires_at']=now[0]
        vault.put(db,'saas-grant',grant_id,entry['payload'],expires=entry['expires'])
    def refresh(platform,current):
        if action=='delete':lifecycle.request_delete(USER)
        else:
            with vault.transaction() as db:lifecycle.disconnect(db,USER,'youtube')
        cleanup=run_revocations(metadata,providers,lambda:now[0])
        assert cleanup['completed']==1
        assert cleanup['awaiting_credentials']==1
        if action=='disconnect':
            from fastapi import HTTPException
            with vault.read_transaction() as db, pytest.raises(HTTPException,match='google_revocation_pending'):
                client.app.state.saas_connections.require_platform(db,installation['installation_id'],'youtube')
        return {**current,'access_token':'late-access','refresh_token':'late-rotated-refresh','expires_at':now[0]+3600}
    monkeypatch.setattr(providers,'refresh',refresh)
    assert token(client,connection).status_code==409
    with vault.read_transaction() as db:
        jobs=db.execute("SELECT id FROM saas_google_revocations WHERE state='pending'").fetchall()
        assert len(jobs)==1
        assert vault.get(db,'google-revocation',jobs[0]['id'])['payload']=={'token':'late-rotated-refresh'}
        assert vault.get(db,'saas-grant',grant_id) is None
