"""Rotation changes one installation's capabilities only after durable preparation."""
import hashlib
import secrets
import pytest
from tests.test_saas_connections import setup,start,select,selected,activate,token,new_installation


def connected(tmp_path):
    client,now,cap=setup(tmp_path)
    value,proof=start(client,cap);select(client,value);connection=selected(client,value,proof);activate(client,connection)
    return client,now,cap,connection


def prepare(client,cap,connection=None,rotation_id=None,body=None):
    rotation_id=rotation_id or secrets.token_urlsafe(24)
    body=body or {'installation_challenge':hashlib.sha256(b'new-installation-secret').hexdigest(),
        'connections':([{'connection_id':connection['connection_id'],'challenge':hashlib.sha256(b'new-connection-secret').hexdigest()}] if connection else [])}
    response=client.post('/api/saas/v1/installations/'+cap['installation_id']+'/rotations/'+rotation_id,
        headers={'Authorization':'Bearer '+cap['installation_secret']},json=body)
    return response,rotation_id,body


def commit(client,rotation_id):
    return client.post('/api/saas/v1/rotations/'+rotation_id+'/commit',headers={'Authorization':'Bearer new-installation-secret'})


def status(client,cap):
    return client.get('/api/saas/v1/installations/'+cap['installation_id'],headers={'Authorization':'Bearer '+cap['installation_secret']})


def test_rotation_retry_changes_both_secrets_without_affecting_second_agent(tmp_path):
    client,now,cap,one=connected(tmp_path);second=new_installation(client)
    response=client.post('/api/saas/v1/installations/'+second['installation_id']+'/attach',headers={'Authorization':'Bearer '+second['installation_secret']},json={'platform':'youtube','brand':'MyBrand'})
    two=response.json();activate(client,two)
    now[0]+=100
    reply,rid,body=prepare(client,cap,one);assert reply.status_code==201,reply.text
    result=reply.json();assert prepare(client,cap,one,rid,body)[0].json()==result
    new={'installation_id':result['installation_id'],'installation_secret':'new-installation-secret'}
    conn={**result['connections'][0],'connection_secret':'new-connection-secret'}
    assert status(client,new).status_code==401
    assert token(client,one).status_code==200
    assert commit(client,rid).status_code==200
    assert commit(client,rid).status_code==200
    assert status(client,cap).status_code==401
    assert token(client,one).status_code in {401,404}
    assert token(client,conn).status_code==200
    assert token(client,two).status_code==200
    assert status(client,new).json()['expires_at']==7777100


@pytest.mark.parametrize('change',['expiry','revoke','replace'])
def test_pending_rotation_does_not_resurrect_expired_or_revoked_access(tmp_path,change):
    client,now,cap,one=connected(tmp_path)
    reply,rid,body=prepare(client,cap,one);assert reply.status_code==201
    if change=='expiry':now[0]+=601
    elif change=='revoke':client.delete('/api/saas/v1/installations/'+cap['installation_id'],headers={'Authorization':'Bearer '+cap['installation_secret']})
    else:
        value,proof=start(client,cap);select(client,value);activate(client,selected(client,value,proof))
    assert commit(client,rid).status_code in {401,404,409}
    if change=='expiry':assert token(client,one).status_code==200


def test_other_installation_capability_cannot_be_rotated_and_proof_cannot_change(tmp_path):
    client,now,cap,one=connected(tmp_path);second=new_installation(client)
    assert prepare(client,second,one)[0].status_code==403
    reply,rid,body=prepare(client,cap,one);assert reply.status_code==201
    body['installation_challenge']=hashlib.sha256(b'different-new-secret').hexdigest()
    assert prepare(client,cap,one,rid,body)[0].status_code==409
    assert client.post('/api/saas/v1/rotations/'+rid+'/commit',headers={'Authorization':'Bearer incorrect-rotation-secret'}).status_code==404
    assert commit(client,rid).status_code==200


def test_rotation_does_not_extend_provider_inactivity_or_expired_trial(tmp_path):
    client,now,cap,one=connected(tmp_path)
    vault=client.app.state.saas.vault
    with vault.transaction() as db:original=db.execute("SELECT expires FROM records WHERE kind='saas-grant'").fetchone()[0]
    now[0]+=14*86400
    reply,rid,body=prepare(client,cap,one);assert reply.status_code==201
    assert commit(client,rid).status_code==200
    new={**reply.json()['connections'][0],'connection_secret':'new-connection-secret'}
    assert token(client,new).status_code==402
    with vault.transaction() as db:assert db.execute("SELECT expires FROM records WHERE kind='saas-grant'").fetchone()[0]==original


def test_expired_pending_rotation_can_be_cancelled_but_committed_rotation_cannot(tmp_path):
    client,now,cap,one=connected(tmp_path)
    reply,rid,body=prepare(client,cap,one);assert reply.status_code==201
    path='/api/saas/v1/installations/'+cap['installation_id']+'/rotations/'+rid
    now[0]+=601
    assert client.delete(path,headers={'Authorization':'Bearer '+cap['installation_secret']}).status_code==200
    assert token(client,one).status_code==200
    reply,rid,body=prepare(client,cap,one);assert reply.status_code==201
    assert commit(client,rid).status_code==200
    path='/api/saas/v1/installations/'+cap['installation_id']+'/rotations/'+rid
    assert client.delete(path,headers={'Authorization':'Bearer '+cap['installation_secret']}).status_code==401


def test_rotation_never_decrypts_other_owners_capabilities(tmp_path):
    client,now,cap,one=connected(tmp_path);vault=client.app.state.saas.vault
    with vault.transaction() as db:
        vault.put(db,'saas-capability','other-owner',{'user_id':'other','installation_id':'other-installation'},expires=now[0]+600)
        db.execute("UPDATE records SET payload=? WHERE id='other-owner'",(b'corrupt',))
    reply,rid,body=prepare(client,cap,one);assert reply.status_code==201
    assert commit(client,rid).status_code==200
    with vault.read_transaction() as db:
        assert db.execute("SELECT id FROM records WHERE id='other-owner'").fetchone() is not None
        assert vault.get(db,'saas-capability',one['connection_id']) is None
