"""Signed deliveries persist before acknowledgement and converge after reordering."""
import hashlib
import hmac
import json
import pytest
from socialctl.saas.access import AccessControl
from tests.test_saas_auth import USER
from tests.test_saas_billing import setup,expire


def event(kind='customer.subscription.updated',event_id='evt_social',**changes):
    return {'id':event_id,'object':'event','type':kind,'api_version':'2025-09-30.clover','livemode':False,
            'created':900,'data':{'object':{'id':'sub_social','customer':'cus_social','status':'active'}},**changes}


def wire(client,billing):
    from socialctl.saas.webhooks import install_webhook_route
    install_webhook_route(client.app,billing)


def send(client,now,payload,secret='whsec_fictional',timestamp=None):
    raw=json.dumps(payload,separators=(',',':')).encode();timestamp=int(now[0] if timestamp is None else timestamp)
    signature=hmac.new(secret.encode(),str(timestamp).encode()+b'.'+raw,hashlib.sha256).hexdigest()
    return client.post('/api/saas/v1/billing/webhook',content=raw,
        headers={'Content-Type':'application/json','Stripe-Signature':f't={timestamp},v1={signature}'})


def test_webhook_persisted_before_ack_and_duplicate_does_not_repeat_job(tmp_path):
    client,now,metadata,stripe,billing=setup(tmp_path);expire(metadata,now);billing.checkout(USER);stripe.paid();wire(client,billing)
    before=len(stripe.calls)
    assert send(client,now,event()).status_code==200
    assert send(client,now,event()).status_code==200
    assert len(stripe.calls)==before
    with metadata.vault.transaction() as db:
        rows=db.execute('SELECT * FROM saas_billing_events').fetchall()
        assert len(rows)==1 and rows[0]['state']=='pending'
    assert not AccessControl(metadata).access_for(USER,now[0]).allowed
    from socialctl.saas.billing_reconcile import run_pending
    assert run_pending(billing)['processed']==1
    assert AccessControl(metadata).access_for(USER,now[0]).allowed
    with metadata.vault.transaction() as db:assert db.execute('SELECT state FROM saas_billing_events').fetchone()[0]=='done'


@pytest.mark.parametrize('changes',[{'livemode':True},{'api_version':'2024-06-20'},{'account':'acct_other'}])
def test_wrong_mode_account_or_api_version_is_rejected(tmp_path,changes):
    client,now,metadata,_,billing=setup(tmp_path);wire(client,billing)
    assert send(client,now,event(**changes)).status_code==400
    with metadata.vault.transaction() as db:assert db.execute('SELECT count(*) FROM saas_billing_events').fetchone()[0]==0


def test_invalid_signature_and_stale_timestamp_never_persist(tmp_path):
    client,now,metadata,_,billing=setup(tmp_path);wire(client,billing)
    assert send(client,now,event(),secret='whsec_other').status_code==400
    assert send(client,now,event(),timestamp=1).status_code==400
    assert client.post('/api/saas/v1/billing/webhook',content=b'x'*1048577).status_code==413


def test_out_of_order_delivery_uses_current_stripe_status_and_recovers_lost_webhook(tmp_path):
    client,now,metadata,stripe,billing=setup(tmp_path);expire(metadata,now);billing.checkout(USER);stripe.paid('past_due');wire(client,billing)
    assert send(client,now,event()).status_code==200
    from socialctl.saas.billing_reconcile import run_pending
    run_pending(billing)
    assert not AccessControl(metadata).access_for(USER,now[0]).allowed
    stripe.paid('active');now[0]+=301
    run_pending(billing)
    assert AccessControl(metadata).access_for(USER,now[0]).allowed
    stripe.paid('canceled');stripe.subscriptions[0]['cancel_at_period_end']=True
    assert send(client,now,event(event_id='evt_older',created=1)).status_code==200
    run_pending(billing)
    assert AccessControl(metadata).access_for(USER,now[0]).allowed


def test_stripe_outage_keeps_durable_job_for_retry(tmp_path):
    client,now,metadata,stripe,billing=setup(tmp_path);expire(metadata,now);billing.checkout(USER);wire(client,billing)
    send(client,now,event())
    import httpx
    def unavailable(request):return httpx.Response(503,text='private diagnostic')
    original=billing.client;billing.client=httpx.Client(transport=httpx.MockTransport(unavailable))
    from socialctl.saas.billing_reconcile import run_pending
    run_pending(billing)
    with metadata.vault.transaction() as db:assert db.execute('SELECT state FROM saas_billing_events').fetchone()[0]=='pending'
    billing.client=original;stripe.paid();now[0]+=301;run_pending(billing)
    assert AccessControl(metadata).access_for(USER,now[0]).allowed


def test_refunds_and_disputes_are_reviewed_without_automatic_cancellation(tmp_path):
    client,now,metadata,stripe,billing=setup(tmp_path);expire(metadata,now);billing.checkout(USER);stripe.paid();billing.reconcile(USER);wire(client,billing)
    from socialctl.saas.billing_reconcile import run_pending
    send(client,now,event(kind='charge.dispute.created'));run_pending(billing)
    with metadata.vault.transaction() as db:assert db.execute('SELECT state FROM saas_billing_events').fetchone()[0]=='review'
    assert AccessControl(metadata).access_for(USER,now[0]).allowed


def test_same_event_id_with_different_body_is_rejected(tmp_path):
    client,now,_,_,billing=setup(tmp_path);wire(client,billing)
    assert send(client,now,event()).status_code==200
    assert send(client,now,event(created=901)).status_code==400


def test_periodic_reconciliation_visits_oldest_due_customer_without_starvation(tmp_path):
    from socialctl.saas.models import AuthenticatedUser
    from socialctl.saas.billing_reconcile import run_pending
    client,now,metadata,stripe,billing=setup(tmp_path)
    with metadata.vault.transaction() as db:
        for n in range(3):
            user=f'periodic-user-{n}'
            metadata.ensure_user(db,AuthenticatedUser(user,f'periodic{n}@example.test'),now[0])
            customer=f'cus_periodic{n}'
            stripe.customers[customer]={'id':customer,'livemode':False,'metadata':{'app':'socialcli','user_id':user}}
            db.execute('INSERT INTO saas_billing(user_id,customer_id,customer_key,customer_started) VALUES(?,?,?,?)',(user,customer,user,0))
    for _ in range(3):
        run_pending(billing,limit=1);now[0]+=301
    with metadata.vault.transaction() as db:
        assert all(r[0]>0 for r in db.execute('SELECT sync_due FROM saas_billing'))


def test_failing_oldest_customer_does_not_starve_later_customers(tmp_path):
    import httpx
    from socialctl.saas.models import AuthenticatedUser
    from socialctl.saas.billing_reconcile import run_pending
    client,now,metadata,stripe,billing=setup(tmp_path)
    with metadata.vault.transaction() as db:
        for n in range(2):
            user=f'retry-user-{n}';customer=f'cus_retry{n}'
            metadata.ensure_user(db,AuthenticatedUser(user,f'retry{n}@example.test'),now[0])
            stripe.customers[customer]={'id':customer,'livemode':False,'metadata':{'app':'socialcli','user_id':user}}
            db.execute('INSERT INTO saas_billing(user_id,customer_id,customer_key,customer_started) VALUES(?,?,?,?)',(user,customer,user,0))
    def failing(request):
        if request.url.path=='/v1/customers/cus_retry0':return httpx.Response(503)
        return stripe(request)
    billing.client=httpx.Client(transport=httpx.MockTransport(failing))
    for _ in range(2):run_pending(billing,limit=1);now[0]+=301
    with metadata.vault.transaction() as db:
        assert db.execute("SELECT sync_due FROM saas_billing WHERE user_id='retry-user-1'").fetchone()[0]>0


def test_unresolved_event_retry_does_not_starve_new_delivery(tmp_path):
    from socialctl.saas.billing_reconcile import run_pending
    client,now,metadata,_,billing=setup(tmp_path);wire(client,billing)
    send(client,now,event(event_id='evt_unresolved'));run_pending(billing,limit=1)
    now[0]+=301
    send(client,now,event(event_id='evt_newer'));run_pending(billing,limit=1)
    with metadata.vault.transaction() as db:
        assert db.execute("SELECT next_attempt FROM saas_billing_events WHERE event_id='evt_newer'").fetchone()[0]>0
