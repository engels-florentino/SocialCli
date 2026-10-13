"""Approval belongs to the signed-in owner and the polling installation."""
import hashlib
import secrets
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from tests.test_saas_auth import service,login,fields


def pair(client,label='Muse'):
    secret=secrets.token_urlsafe(32)
    reply=client.post('/api/saas/v1/installations/pair',json={'label':label,'poll_challenge':hashlib.sha256(secret.encode()).hexdigest()})
    assert reply.status_code==201
    return reply.json(),{'Authorization':'Bearer '+secret}


def approve(client,value):
    page=client.get(value['browser_url'])
    assert page.status_code==200
    return client.post(value['browser_url'],data=fields(page),headers={'Origin':'https://social.example'})


def poll(client,value,headers):
    return client.post('/api/saas/v1/pairings/'+value['pairing_id']+'/poll',headers=headers)


def test_pair_approval_delivers_only_scoped_capability_and_ack_is_recoverable(tmp_path):
    client,now,_=service(tmp_path)
    value,proof=pair(client)
    page=client.get(value['browser_url'],follow_redirects=False)
    assert page.status_code==303 and page.headers['location'].startswith('/account/login?next=')
    assert poll(client,value,proof).json()['status']=='pending'
    login(client)
    assert approve(client,value).status_code==200
    ready=poll(client,value,proof).json()
    assert ready['status']=='approved' and ready['installation_id'] and ready['installation_secret']
    assert 'identity-access-secret' not in str(ready) and 'socialcli-session' not in str(ready)
    # A lost response can be recovered with the same proof without a second installation.
    assert poll(client,value,proof).json()==ready
    assert poll(client,value,{'Authorization':'Bearer '+secrets.token_urlsafe(32)}).status_code==404
    ack='/api/saas/v1/pairings/'+value['pairing_id']+'/ack'
    assert client.post(ack,headers=proof).status_code==200
    assert client.post(ack,headers=proof).status_code==200
    assert 'installation_secret' not in poll(client,value,proof).json()
    status='/api/saas/v1/installations/'+ready['installation_id']
    auth={'Authorization':'Bearer '+ready['installation_secret']}
    assert client.get(status,headers=auth).json()['label']=='Muse'
    now[0]+=90*86400
    assert client.get(status,headers=auth).status_code==401


def test_pairing_expires_in_thirty_minutes_without_creating_provider_oauth(tmp_path):
    client,now,_=service(tmp_path)
    value,proof=pair(client)
    assert value['expires_in']==1800
    now[0]+=1800
    assert poll(client,value,proof).status_code==404
    with client.app.state.saas.vault.transaction() as db:
        assert db.execute("SELECT count(*) FROM records WHERE kind IN ('connection','authorization','saas-installation')").fetchone()[0]==0


def test_double_approval_creates_one_installation_and_csrf_is_required(tmp_path):
    client,_,_=service(tmp_path)
    login(client)
    value,proof=pair(client)
    data=fields(client.get(value['browser_url']))
    assert client.post(value['browser_url'],data={},headers={'Origin':'https://social.example'}).status_code==403
    cookie='; '.join(f'{k}={v}' for k,v in client.cookies.items())
    def submit(_):
        browser=TestClient(client.app,base_url='https://social.example')
        return browser.post(value['browser_url'],data=data,headers={'Origin':'https://social.example','Cookie':cookie}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit,range(2)))==[200,409]
    with client.app.state.saas.vault.transaction() as db:
        assert db.execute("SELECT count(*) FROM records WHERE kind='saas-installation'").fetchone()[0]==1


def test_revoking_one_agent_does_not_revoke_another_and_wrong_secret_cannot_read(tmp_path):
    client,_,_=service(tmp_path);login(client)
    caps=[]
    for label in ('Muse','Grok'):
        value,proof=pair(client,label);approve(client,value);caps.append(poll(client,value,proof).json())
    one,two=caps
    one_url='/api/saas/v1/installations/'+one['installation_id']
    two_url='/api/saas/v1/installations/'+two['installation_id']
    a={'Authorization':'Bearer '+one['installation_secret']};b={'Authorization':'Bearer '+two['installation_secret']}
    assert client.get(two_url,headers=a).status_code==401
    assert client.delete(one_url,headers=a).status_code==200
    assert client.get(one_url,headers=a).status_code==401
    assert client.get(two_url,headers=b).status_code==200


def test_pair_label_is_escaped_and_preview_never_approves(tmp_path):
    client,_,_=service(tmp_path);login(client)
    value,proof=pair(client,'<script>alert(1)</script>')
    page=client.get(value['browser_url'])
    assert '<script>' not in page.text and '&lt;script&gt;' in page.text
    assert client.get(value['browser_url']).status_code==200
    assert poll(client,value,proof).json()['status']=='pending'
