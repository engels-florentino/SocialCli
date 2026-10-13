"""A durable commercial cutoff cannot be bypassed with the anonymous protocol."""
import hashlib
from fastapi.testclient import TestClient
from tests.test_connection_service import make_service,start,authorize,complete,connection_headers


def close(store,now):
    from socialctl.connection_service.legacy import LegacyPolicy
    return LegacyPolicy(store,lambda:now[0],close=True)


def test_cutoff_closes_anonymous_creation_and_keeps_only_enumerated_connections(tmp_path):
    now=[1000.0];client,store=make_service(tmp_path,clock=lambda:now[0])
    value,proof=start(client);authorize(client,value);connection=complete(client,value,proof)
    path='/v1/connections/'+connection['connection_id']
    assert client.post(path+'/token',headers=connection_headers(connection)).status_code==200
    with store.transaction() as db:original=store.get(db,'connection',connection['connection_id'])
    close(store,now)
    reply=client.post('/v1/authorizations',json={'platform':'youtube','poll_challenge':hashlib.sha256(b'new-client').hexdigest()})
    assert reply.status_code==410 and 'upgrade_required' in reply.text
    assert client.post(path+'/token',headers=connection_headers(connection)).status_code==200
    with store.transaction() as db:
        store.put(db,'connection','injected-after-cutoff',original['payload'],expires=original['expires'],credential_hash=original['credential_hash'])
    assert client.post('/v1/connections/injected-after-cutoff/token',headers=connection_headers(connection)).status_code==410
    now[0]=original['expires']
    assert client.post(path+'/token',headers=connection_headers(connection)).status_code in {404,410}


def test_pre_cutoff_authorization_can_finish_once_during_drain(tmp_path):
    now=[1000.0];client,store=make_service(tmp_path,clock=lambda:now[0])
    value,proof=start(client);close(store,now)
    now[0]+=300
    authorize(client,value);connection=complete(client,value,proof)
    path='/v1/connections/'+connection['connection_id']
    assert client.post(path+'/token',headers=connection_headers(connection)).status_code==200
    now[0]+=301
    assert client.post(path+'/token',headers=connection_headers(connection)).status_code==200


def test_cutoff_drain_does_not_restart_on_completion_or_service_restart(tmp_path):
    now=[1000.0];client,store=make_service(tmp_path,clock=lambda:now[0])
    value,proof=start(client);close(store,now)
    now[0]+=599;authorize(client,value);connection=complete(client,value,proof)
    now[0]+=2;close(store,now)
    assert client.post('/v1/connections/'+connection['connection_id']+'/token',headers=connection_headers(connection)).status_code in {404,410}
    from socialctl.connection_service.legacy import LegacyPolicy
    policy=LegacyPolicy(store,lambda:now[0],close=False)
    with store.transaction() as db:
        import pytest
        from fastapi import HTTPException
        with pytest.raises(HTTPException):policy.allow_creation(db)


def test_nonactivated_existing_connection_must_activate_within_original_drain(tmp_path):
    now=[1000.0];client,store=make_service(tmp_path,clock=lambda:now[0])
    value,proof=start(client);authorize(client,value);connection=complete(client,value,proof)
    close(store,now);now[0]+=601
    assert client.post('/v1/connections/'+connection['connection_id']+'/token',headers=connection_headers(connection)).status_code in {404,410}


def test_new_saas_billing_deployment_always_closes_legacy_creation(tmp_path):
    from socialctl.connection_service.settings import Settings,AppCredentials
    from socialctl.connection_service.app import create_app
    from socialctl.saas.settings import SaaSSettings
    from socialctl.saas.billing import BillingSettings
    from cryptography.fernet import Fernet
    billing=BillingSettings(secret_key='sk_test_fixture',webhook_secret='whsec_fixture',price_id='price_fixture',account_id='acct_fixture',portal_configuration='bpc_fixture')
    settings=Settings('https://social.example',tmp_path/'db.sqlite3',[Fernet.generate_key()],providers={'youtube':AppCredentials('app','secret')},saas=SaaSSettings('https://auth.example',billing=billing))
    with TestClient(create_app(settings),base_url=settings.public_url) as client:
        reply=client.post('/v1/authorizations',json={'platform':'youtube','poll_challenge':hashlib.sha256(b'old-client').hexdigest()})
        assert reply.status_code==410
