"""Account self-service and durable deletion never touch another owner."""
from dataclasses import replace
import httpx
from tests.test_saas_auth import USER,EMAIL,login,fields
from tests.test_saas_connections import setup,start,select,selected,activate,token,new_installation
from socialctl.saas.models import AuthenticatedUser


def connected(tmp_path):
    client,now,installation=setup(tmp_path)
    value,proof=start(client,installation);select(client,value);connection=selected(client,value,proof)
    assert activate(client,connection).status_code==200
    return client,now,installation,connection


def post(client,path,data):
    return client.post(path,data=data,headers={'Origin':'https://social.example'},follow_redirects=False)


def test_account_lists_owned_connections_and_revokes_one_agent_after_trial_expiry(tmp_path):
    client,now,first,connection=connected(tmp_path);second=new_installation(client)
    now[0]=1210600;login(client)
    page=client.get('/account/')
    assert 'Connected accounts' in page.text and 'Creator two' in page.text and 'Assistants' in page.text
    response=post(client,'/account/installations/'+second['installation_id']+'/revoke',fields(page))
    assert response.status_code==303
    assert client.get('/api/saas/v1/installations/'+second['installation_id'],headers={'Authorization':'Bearer '+second['installation_secret']}).status_code==401
    assert client.get('/api/saas/v1/installations/'+first['installation_id'],headers={'Authorization':'Bearer '+first['installation_secret']}).status_code==200


def test_web_disconnect_rejects_cross_owner_and_clears_only_own_social_grant(tmp_path):
    client,now,installation,connection=connected(tmp_path);metadata=client.app.state.saas
    other='22222222-2222-4222-8222-222222222222'
    with metadata.vault.transaction() as db:
        metadata.ensure_user(db,AuthenticatedUser(other,'other@example.test'),now[0])
        metadata.vault.put(db,'saas-installation','other-installation',{'user_id':other,'label':'Other'},expires=now[0]+900)
    page=client.get('/account/')
    assert post(client,'/account/installations/other-installation/revoke',fields(page)).status_code==404
    assert post(client,'/account/connections/youtube/disconnect',fields(page)).status_code==303
    assert token(client,connection).status_code in {404,409}
    with metadata.vault.transaction() as db:assert metadata.vault.get(db,'saas-installation','other-installation') is not None


def test_delete_is_explicit_immediately_blocks_tokens_and_retries_external_cleanup(tmp_path):
    client,now,installation,connection=connected(tmp_path);metadata=client.app.state.saas
    sessions=client.app.state.saas_sessions
    sessions.identity.settings=replace(sessions.identity.settings,auth_admin_key='fixture-admin-secret')
    fail=[True];calls=[]
    def identity(request):
        calls.append(request)
        if request.method=='DELETE':
            assert request.url.path=='/admin/users/'+USER
            assert request.headers['authorization']=='Bearer fixture-admin-secret'
            import sqlite3
            with sqlite3.connect(metadata.vault.path,timeout=.05) as db:db.execute('BEGIN IMMEDIATE');db.rollback()
            return httpx.Response(503 if fail[0] else 200,json={})
        return httpx.Response(200,json={'id':USER,'email':EMAIL,'email_confirmed_at':'verified'})
    sessions.identity.client=httpx.Client(transport=httpx.MockTransport(identity))
    page=client.get('/account/delete')
    assert page.status_code==200
    form=fields(page)
    assert post(client,'/account/delete',form).status_code==400
    assert token(client,connection).status_code==200
    form['confirmation']='DELETE'
    reply=post(client,'/account/delete',form)
    assert reply.status_code==200 and 'Deletion requested' in reply.text
    assert token(client,connection).status_code in {401,404}
    assert client.get('/account/',follow_redirects=False).status_code==303
    with metadata.vault.transaction() as db:
        assert metadata.user(db,USER)['deleting']==1
        assert db.execute("SELECT count(*) FROM records WHERE kind='saas-grant'").fetchone()[0]==0
        assert db.execute('SELECT state FROM saas_deletions WHERE user_id=?',(USER,)).fetchone()[0]=='pending'
    from socialctl.saas.lifecycle import run_deletions
    assert run_deletions(metadata,sessions.identity,None,lambda:now[0])['pending']==1
    fail[0]=False;now[0]+=301
    assert run_deletions(metadata,sessions.identity,None,lambda:now[0])['completed']==1
    count=len(calls);run_deletions(metadata,sessions.identity,None,lambda:now[0]);assert len(calls)==count
    with metadata.vault.transaction() as db:
        assert metadata.user(db,USER) is None
        assert metadata.vault.get(db,'saas-user',USER) is None
        assert db.execute('SELECT count(*) FROM saas_trial_markers').fetchone()[0]>0


def test_deletion_waits_for_checkout_in_flight_and_cancels_only_owned_billing(tmp_path):
    from tests.test_saas_billing import setup as billed,expire
    from socialctl.saas.lifecycle import AccountLifecycle,run_deletions
    client,now,metadata,stripe,billing=billed(tmp_path);expire(metadata,now);billing.checkout(USER);stripe.paid()
    lifecycle=AccountLifecycle(metadata,lambda:now[0]);lifecycle.request_delete(USER)
    with metadata.vault.transaction() as db:
        db.execute('UPDATE saas_billing SET lease_until=? WHERE user_id=?',(now[0]+60,USER))
    identity=client.app.state.saas_sessions.identity
    identity.settings=replace(identity.settings,auth_admin_key='fixture-admin')
    identity.client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={})))
    before=len(stripe.calls)
    assert run_deletions(metadata,identity,billing,lambda:now[0])['pending']==1
    assert len(stripe.calls)==before
    now[0]+=301
    stripe.subscriptions[0]['metadata']['user_id']='another-owner'
    assert run_deletions(metadata,identity,billing,lambda:now[0])['pending']==1
    assert not any(c[0]=='DELETE' for c in stripe.calls)


def test_completed_deletion_cancels_own_subscription_and_expires_open_checkout(tmp_path):
    from tests.test_saas_billing import setup as billed,expire
    from socialctl.saas.lifecycle import AccountLifecycle,run_deletions
    client,now,metadata,stripe,billing=billed(tmp_path);expire(metadata,now);billing.checkout(USER);stripe.paid()
    original=stripe.__call__;operations=[]
    def api(request):
        if request.method=='POST' and request.url.path.endswith('/expire'):
            operations.append('expire')
            stripe.sessions['cs_test_social']['status']='expired'
            return httpx.Response(200,json=stripe.sessions['cs_test_social'])
        if request.method=='DELETE' and request.url.path=='/v1/customers/cus_social':
            operations.append('delete-customer')
            return httpx.Response(200,json={'id':'cus_social','deleted':True})
        if request.method=='DELETE':
            assert request.url.path=='/v1/subscriptions/sub_social'
            operations.append('cancel');stripe.subscriptions[0]['status']='canceled'
            return httpx.Response(200,json=stripe.subscriptions[0])
        return original(request)
    billing.client=httpx.Client(transport=httpx.MockTransport(api))
    identity=client.app.state.saas_sessions.identity
    identity.settings=replace(identity.settings,auth_admin_key='fixture-admin')
    identity.client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={})))
    AccountLifecycle(metadata,lambda:now[0]).request_delete(USER)
    assert run_deletions(metadata,identity,billing,lambda:now[0])['completed']==1
    assert operations==['expire','cancel','delete-customer']
    with metadata.vault.transaction() as db:assert metadata.user(db,USER) is None


def test_unrecognized_identity_404_is_not_proof_of_deletion(tmp_path):
    import pytest
    from socialctl.saas.auth import IdentityUnavailable
    client,now,_,_=connected(tmp_path)
    identity=client.app.state.saas_sessions.identity
    identity.settings=replace(identity.settings,auth_admin_key='fixture-admin')
    identity.client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(404,json={'detail':'Not Found'})))
    with pytest.raises(IdentityUnavailable):identity.delete_user(USER)
    identity.client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(404,json={'error_code':'user_not_found'})))
    identity.delete_user(USER)


def test_delete_removes_preexisting_pair_delivery_secret_and_preserves_other_user(tmp_path):
    from socialctl.saas.lifecycle import AccountLifecycle
    client,now,installation,_=connected(tmp_path);metadata=client.app.state.saas
    other='22222222-2222-4222-8222-222222222222'
    with metadata.vault.transaction() as db:
        metadata.ensure_user(db,AuthenticatedUser(other,'other@example.test'),now[0])
        metadata.vault.put(db,'saas-pair','earlier-pair-format',{'status':'approved','installation_id':installation['installation_id'],'installation_secret':'delivered-secret'},expires=now[0]+600)
    AccountLifecycle(metadata,lambda:now[0]).request_delete(USER)
    with metadata.vault.transaction() as db:
        assert metadata.vault.get(db,'saas-pair','earlier-pair-format') is None
        assert metadata.vault.get(db,'saas-user',other)['payload']['email']=='other@example.test'


def test_checkout_completing_during_account_deletion_cannot_leave_active_subscription(tmp_path):
    from tests.test_saas_billing import setup as billed,expire
    from socialctl.saas.lifecycle import AccountLifecycle,run_deletions
    client,now,metadata,stripe,billing=billed(tmp_path);expire(metadata,now);billing.checkout(USER)
    original=stripe.__call__
    def racing(request):
        if request.url.path=='/v1/checkout/sessions' and request.method=='GET':
            stripe.paid();stripe.sessions['cs_test_social'].update(status='complete',subscription='sub_social')
        if request.method=='DELETE' and request.url.path=='/v1/customers/cus_social':
            stripe.customers['cus_social']={'id':'cus_social','deleted':True}
            return httpx.Response(200,json=stripe.customers['cus_social'])
        if request.method=='DELETE' and request.url.path=='/v1/subscriptions/sub_social':
            stripe.subscriptions[0]['status']='canceled'
            return httpx.Response(200,json=stripe.subscriptions[0])
        return original(request)
    billing.client=httpx.Client(transport=httpx.MockTransport(racing))
    identity=client.app.state.saas_sessions.identity
    identity.settings=replace(identity.settings,auth_admin_key='fixture-admin')
    identity.client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={})))
    AccountLifecycle(metadata,lambda:now[0]).request_delete(USER)
    assert run_deletions(metadata,identity,billing,lambda:now[0])['completed']==1
    assert all(s['status']=='canceled' for s in stripe.subscriptions)
    assert stripe.customers['cus_social'].get('deleted') is True


def test_expired_grant_cleanup_removes_slot_and_capabilities_without_other_owner_loss(tmp_path):
    from socialctl.saas.lifecycle import AccountLifecycle
    client,now,installation,connection=connected(tmp_path);metadata=client.app.state.saas
    with metadata.vault.transaction() as db:
        slot=db.execute('SELECT grant_id FROM saas_slots WHERE user_id=?',(USER,)).fetchone()
        record=metadata.vault.get(db,'saas-grant',slot['grant_id'])
        metadata.vault.put(db,'saas-grant',slot['grant_id'],record['payload'],expires=now[0]-1)
    AccountLifecycle(metadata,lambda:now[0]).prune_expired()
    with metadata.vault.transaction() as db:
        assert db.execute('SELECT count(*) FROM saas_slots WHERE user_id=?',(USER,)).fetchone()[0]==0
        assert metadata.vault.get(db,'saas-capability',connection['connection_id']) is None
        assert metadata.vault.get(db,'saas-installation',installation['installation_id']) is not None


def test_missing_expired_facebook_grant_does_not_crash_instagram_activation(tmp_path):
    client,now,installation,connection=connected(tmp_path);metadata=client.app.state.saas
    connections=client.app.state.saas_connections
    with metadata.vault.transaction() as db:
        db.execute('INSERT INTO saas_slots VALUES(?,?,?)',(USER,'facebook','already-purged-grant'))
        candidate=connections.candidate(db,installation['installation_id'],USER,'instagram','MyBrand',
            {'id':'ig-fictional','page_id':'fb-fictional'},grant={'access_token':'fictional','expires_at':now[0]+3600,'granted_scopes':[]})
    reply=activate(client,candidate)
    assert reply.status_code==200,reply.text


def test_last_disconnect_releases_brand_but_preserves_trial(tmp_path):
    client,now,installation,connection=connected(tmp_path);metadata=client.app.state.saas
    with metadata.vault.read_transaction() as db:
        deadline=db.execute('SELECT trial_end FROM saas_entitlements WHERE user_id=?',(USER,)).fetchone()[0]
    assert post(client,'/account/connections/youtube/disconnect',fields(client.get('/account/'))).status_code==303
    with metadata.vault.read_transaction() as db:
        assert db.execute('SELECT * FROM saas_brands WHERE user_id=?',(USER,)).fetchone() is None
        assert db.execute('SELECT trial_end FROM saas_entitlements WHERE user_id=?',(USER,)).fetchone()[0]==deadline


def test_rename_brand_preserves_connections_and_escapes_label(tmp_path):
    client,now,installation,connection=connected(tmp_path);metadata=client.app.state.saas
    page=client.get('/account/')
    form=fields(page);form['brand']='New & Better'
    assert post(client,'/account/brand',form).status_code==303
    assert 'New &amp; Better' in client.get('/account/').text
    assert token(client,connection).status_code==200
    with metadata.vault.read_transaction() as db:
        assert db.execute('SELECT label FROM saas_brands WHERE user_id=?',(USER,)).fetchone()[0]=='New & Better'
    form['brand']='  '
    assert post(client,'/account/brand',form).status_code==400
    assert post(client,'/account/brand',{'brand':'Attacker'}).status_code==403


def test_delete_ignores_unrelated_corrupt_pairs_logins_and_unclaimed_records(tmp_path):
    client,now,installation,_=connected(tmp_path);metadata=client.app.state.saas;vault=metadata.vault
    with vault.transaction() as db:
        for kind in ('saas-pair','saas-login'):
            vault.put(db,kind,'other-'+kind,{'user_id':'other','email':'other@example.test'},expires=now[0]+600)
            db.execute('UPDATE records SET payload=? WHERE kind=? AND id=?',(b'corrupt',kind,'other-'+kind))
            vault.put(db,kind,'pending-'+kind,{'status':'pending'},expires=now[0]+600)
        vault.put(db,'saas-login','matching-email',{'email':EMAIL,'status':'pending'},expires=now[0]+600)
    client.app.state.saas_lifecycle.request_delete(USER)
    with vault.read_transaction() as db:
        assert vault.get(db,'saas-login','matching-email') is None
        assert db.execute("SELECT count(*) FROM records WHERE id LIKE 'other-%'").fetchone()[0]==2
        assert db.execute("SELECT count(*) FROM records WHERE id LIKE 'pending-%'").fetchone()[0]==2


def test_prune_uses_indexed_links_without_decrypting_live_records(tmp_path):
    client,now,installation,connection=connected(tmp_path);vault=client.app.state.saas.vault
    with vault.transaction() as db:
        vault.put(db,'saas-capability','orphan',{'user_id':'other','installation_id':'missing','grant_id':'missing'},expires=now[0]+600)
        vault.put(db,'saas-capability','unclaimed',{'status':'pending'},expires=now[0]+600)
        db.execute("UPDATE records SET payload=? WHERE kind IN ('saas-grant','saas-capability')",(b'corrupt',))
    client.app.state.saas_lifecycle.prune_expired()
    with vault.read_transaction() as db:
        assert db.execute("SELECT id FROM records WHERE id='orphan'").fetchone() is None
        assert db.execute("SELECT id FROM records WHERE id='unclaimed'").fetchone() is not None
        assert db.execute('SELECT grant_id FROM saas_slots').fetchone() is not None
        assert db.execute("SELECT id FROM records WHERE kind='saas-capability' AND id=?",(connection['connection_id'],)).fetchone() is not None
