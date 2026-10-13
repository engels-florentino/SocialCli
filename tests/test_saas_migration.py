"""An old capability plus human consent transfers ownership only on activation."""
import hashlib
import pytest
from fastapi.testclient import TestClient
from tests.test_saas_auth import fields
from tests.test_saas_connections import setup,activate,token
from tests.test_connection_service import start,authorize,complete,connection_headers


def old_connection(client):
    value,proof=start(client);authorize(client,value)
    reply=client.post('/v1/authorizations/'+value['authorization_id']+'/complete',headers=proof,json={'account_id':'UC-two'})
    assert reply.status_code==201
    old=reply.json()
    assert client.post('/v1/connections/'+old['connection_id']+'/token',headers=connection_headers(old)).status_code==200
    return old


def migration(client,cap,old,secret=None):
    return client.post('/api/saas/v1/installations/'+cap['installation_id']+'/migrations',
        headers={'Authorization':'Bearer '+cap['installation_secret'],
                 'X-SocialCli-Legacy-Secret':secret or old['connection_secret']},json={
            'platform':'youtube','brand':'MyBrand','legacy_id':old['connection_id'],
            'poll_challenge':hashlib.sha256(b'migration-poll-secret').hexdigest()})


def consent(client,value):
    page=client.get(value['browser_url']);assert page.status_code==200
    return client.post(value['browser_url'],data=fields(page),headers={'Origin':'https://social.example'})


def poll(client,value):
    return client.post('/api/saas/v1/migrations/'+value['migration_id']+'/poll',
        headers={'Authorization':'Bearer migration-poll-secret'})


def test_migration_preserves_old_until_activation_and_transfers_latest_grant(tmp_path):
    client,now,cap=setup(tmp_path);old=old_connection(client)
    reply=migration(client,cap,old);assert reply.status_code==201,reply.text
    value=reply.json();assert poll(client,value).json()['status']=='pending'
    assert consent(client,value).status_code==200
    result=poll(client,value).json();assert result['status']=='selected'
    assert client.post('/v1/connections/'+old['connection_id']+'/token',headers=connection_headers(old)).status_code==200
    vault=client.app.state.saas.vault
    with vault.transaction() as db:
        record=vault.get(db,'connection',old['connection_id'])
        record['payload']['grant']['access_token']='most-recent-token'
        vault.put(db,'connection',old['connection_id'],record['payload'],expires=record['expires'],credential_hash=record['credential_hash'])
    assert activate(client,result).status_code==200
    assert activate(client,result).status_code==200
    assert token(client,result).json()['access_token']=='most-recent-token'
    assert client.post('/v1/connections/'+old['connection_id']+'/token',headers=connection_headers(old)).status_code==404


def test_migration_requires_old_secret_and_browser_owner_and_csrf(tmp_path):
    client,now,cap=setup(tmp_path);old=old_connection(client)
    assert migration(client,cap,old,'incorrect-secret-that-is-long').status_code==404
    reply=migration(client,cap,old);assert reply.status_code==201
    value=reply.json();stranger=TestClient(client.app,base_url='https://social.example')
    assert stranger.get(value['browser_url'],follow_redirects=False).status_code in {303,401}
    assert client.post(value['browser_url'],data={},headers={'Origin':'https://social.example'}).status_code==403
    assert poll(client,value).json()['status']=='pending'


@pytest.mark.parametrize('change',['delete','expire','competing'])
def test_lost_or_already_claimed_legacy_proof_cannot_activate(tmp_path,change):
    client,now,cap=setup(tmp_path);old=old_connection(client)
    reply=migration(client,cap,old);assert reply.status_code==201
    value=reply.json();consent(client,value);result=poll(client,value).json()
    vault=client.app.state.saas.vault
    if change=='competing':
        second=migration(client,cap,old).json();consent(client,second)
        assert activate(client,poll(client,second).json()).status_code==200
    else:
        with vault.transaction() as db:
            record=vault.get(db,'connection',old['connection_id'])
            if change=='delete':vault.delete(db,'connection',old['connection_id'])
            else:vault.put(db,'connection',old['connection_id'],record['payload'],expires=999,credential_hash=record['credential_hash'])
    assert activate(client,result).status_code in {404,409,410}
    assert token(client,result).status_code!=200


def test_cutoff_unlisted_records_cannot_be_claimed_and_old_grant_survives_abandoned_migration(tmp_path):
    from socialctl.connection_service.legacy import LegacyPolicy
    client,now,cap=setup(tmp_path);old=old_connection(client);vault=client.app.state.saas.vault
    LegacyPolicy(vault,lambda:now[0],close=True)
    reply=migration(client,cap,old);assert reply.status_code==201
    value=reply.json();assert consent(client,value).status_code==200
    with vault.transaction() as db:
        record=vault.get(db,'connection',old['connection_id'])
        vault.put(db,'connection','unlisted-legacy-connection',record['payload'],expires=record['expires'],credential_hash=record['credential_hash'])
    injected={**old,'connection_id':'unlisted-legacy-connection'}
    assert migration(client,cap,injected).status_code==410
    now[0]+=601
    assert poll(client,value).status_code==404
    assert client.post('/v1/connections/'+old['connection_id']+'/token',headers=connection_headers(old)).status_code==200
