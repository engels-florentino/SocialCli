"""Billing uses real durable state and a fictional Stripe HTTP boundary."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs
import httpx
import pytest
from fastapi import HTTPException
from socialctl.saas.access import AccessControl
from tests.test_saas_auth import service, login, USER, EMAIL, fields


class StripeFixture:
    def __init__(self):
        self.calls=[]; self.customers={}; self.sessions={}; self.subscriptions=[]; self.invoices=[]
        self.responses={}; self.lose_checkout=False; self.price_changes={}
        self.block=None

    def price(self):
        return dict(id='price_social',object='price',active=True,livemode=False,currency='usd',unit_amount=1000,
                    type='recurring',recurring={'interval':'month','interval_count':1,'usage_type':'licensed'})

    def __call__(self,request):
        path=request.url.path; data={k:v[0] for k,v in parse_qs(request.content.decode()).items()}
        self.calls.append((request.method,path,data,dict(request.headers)))
        assert request.headers['stripe-version']=='2025-09-30.clover'
        # Every external call must permit an independent write transaction.
        if hasattr(self,'vault'):
            import sqlite3
            with sqlite3.connect(self.vault.path,timeout=.05) as db:
                db.execute('BEGIN IMMEDIATE');db.rollback()
        key=request.headers.get('idempotency-key')
        if request.method=='POST' and key in self.responses:
            return httpx.Response(200,json=self.responses[key])
        if path=='/v1/account': result={'id':'acct_social'}
        elif path=='/v1/prices/price_social':
            result=self.price();result.update(self.price_changes)
        elif path=='/v1/customers' and request.method=='POST':
            result={'id':'cus_social','livemode':False,'metadata':{'app':'socialcli','user_id':data['metadata[user_id]']}}
            self.customers[result['id']]=result
        elif path.startswith('/v1/customers/'):
            result=self.customers.get(path.split('/')[-1],{})
        elif path=='/v1/subscriptions': result={'data':self.subscriptions,'has_more':False}
        elif path=='/v1/invoices': result={'data':self.invoices,'has_more':False}
        elif path.endswith('/expire'):
            result=self.sessions[path.split('/')[-2]];result['status']='expired'
        elif path.endswith('/line_items'):result={'has_more':False,'data':[{'price':self.price(),'quantity':1}]}
        elif path=='/v1/checkout/sessions' and request.method=='GET': result={'data':list(self.sessions.values()),'has_more':False}
        elif path=='/v1/checkout/sessions':
            if self.block:self.block()
            result={'id':'cs_test_social','livemode':False,'customer':data['customer'],'mode':'subscription',
                    'status':'open','expires_at':3000000,'url':'https://checkout.stripe.com/c/pay/cs_test_social',
                    'metadata':{'app':'socialcli','user_id':data['metadata[user_id]'],'attempt':data['metadata[attempt]']}}
            self.sessions[result['id']]=result
            self.responses[key]=result
            if self.lose_checkout:
                self.lose_checkout=False
                raise httpx.ReadTimeout('secret transport details',request=request)
        elif path=='/v1/billing_portal/sessions': result={'url':'https://billing.stripe.com/p/session/test_social'}
        else: raise AssertionError(path)
        if request.method=='POST':self.responses[key]=result
        return httpx.Response(200,json=result)

    def paid(self,status='active',end=3802600):
        self.subscriptions=[{'id':'sub_social','livemode':False,'customer':'cus_social','status':status,
            'cancel_at_period_end':False,'metadata':{'app':'socialcli','user_id':USER},'pause_collection':None,
            'items':{'data':[{'price':self.price(),'quantity':1,'current_period_end':end}],'has_more':False}}]
        self.invoices=[{'id':'in_social','livemode':False,'customer':'cus_social','status':'paid','currency':'usd',
            'amount_paid':1000,'paid_out_of_band':False,'parent':{'subscription_details':{'subscription':'sub_social'}},
            'lines':{'has_more':False,'data':[{'amount':1000,'currency':'usd','quantity':1,
                'pricing':{'price_details':{'price':'price_social'}},'period':{'start':1210600,'end':end}}]}}]


def setup(tmp_path):
    from socialctl.saas.billing import BillingService, BillingSettings
    client,now,_=service(tmp_path);login(client);metadata=client.app.state.saas
    stripe=StripeFixture();stripe.vault=metadata.vault
    settings=BillingSettings(secret_key='sk_test_fictional',webhook_secret='whsec_fictional',price_id='price_social',account_id='acct_social',portal_configuration='bpc_social')
    billing=BillingService(metadata,settings,httpx.Client(transport=httpx.MockTransport(stripe)),lambda:now[0],'https://social.example')
    return client,now,metadata,stripe,billing


def expire(metadata,now):
    with metadata.vault.transaction() as db: AccessControl(metadata).activate_trial(db,USER,1000)
    now[0]=1210600


def test_checkout_only_after_trial_and_reuses_session_with_fixed_server_price(tmp_path):
    _,now,metadata,stripe,billing=setup(tmp_path)
    with pytest.raises(HTTPException) as error:billing.checkout(USER)
    assert error.value.status_code==409 and not stripe.calls
    with metadata.vault.transaction() as db:AccessControl(metadata).activate_trial(db,USER,1000)
    with pytest.raises(HTTPException):billing.checkout(USER)
    now[0]=1210600
    first=billing.checkout(USER);second=billing.checkout(USER)
    assert first==second=='https://checkout.stripe.com/c/pay/cs_test_social'
    posts=[c for c in stripe.calls if c[0]=='POST' and c[1]=='/v1/checkout/sessions']
    assert len(posts)==1
    assert posts[0][2]['line_items[0][price]']=='price_social'
    assert posts[0][2]['line_items[0][quantity]']=='1'
    assert posts[0][2]['branding_settings[font_family]']=='default'
    assert not any('trial' in k for k in posts[0][2])
    assert not AccessControl(metadata).access_for(USER,now[0]).allowed


def test_lost_checkout_response_recovers_without_duplicate_payment(tmp_path):
    _,now,metadata,stripe,billing=setup(tmp_path);expire(metadata,now)
    stripe.lose_checkout=True
    with pytest.raises(HTTPException):billing.checkout(USER)
    assert billing.checkout(USER).startswith('https://checkout.stripe.com/')
    assert len([c for c in stripe.calls if c[:2]==('POST','/v1/checkout/sessions')])==1


def test_concurrent_checkout_is_serialized_and_does_not_hold_sqlite_lock(tmp_path):
    _,now,metadata,stripe,billing=setup(tmp_path);expire(metadata,now)
    started=threading.Event();release=threading.Event()
    stripe.block=lambda:(started.set(),release.wait(5))
    with ThreadPoolExecutor(max_workers=2) as pool:
        future=pool.submit(billing.checkout,USER)
        assert started.wait(3)
        try:
            with pytest.raises(HTTPException) as error:billing.checkout(USER)
            assert error.value.status_code==409
        finally:release.set()
        assert future.result().startswith('https://checkout.stripe.com/')
    assert len(stripe.sessions)==1


@pytest.mark.parametrize('change',[{'currency':'eur'},{'unit_amount':999},{'recurring':{'interval':'year','interval_count':1}},{'livemode':True}])
def test_wrong_price_cannot_create_checkout(tmp_path,change):
    _,now,metadata,stripe,billing=setup(tmp_path);expire(metadata,now);stripe.price_changes=change
    with pytest.raises(HTTPException):billing.checkout(USER)
    assert not stripe.sessions


def test_live_keys_are_rejected_and_secrets_not_in_settings_repr():
    from socialctl.saas.billing import BillingSettings
    with pytest.raises(ValueError):BillingSettings(secret_key='sk_live_rejected',webhook_secret='whsec_test',price_id='price_social',account_id='acct_social',portal_configuration='bpc_social')
    settings=BillingSettings(secret_key='sk_test_private',webhook_secret='whsec_private',price_id='price_social',account_id='acct_social',portal_configuration='bpc_social')
    assert 'private' not in repr(settings)


@pytest.mark.parametrize('status,allowed',[('active',True),('canceled',True),('past_due',False),('unpaid',False),('paused',False),('trialing',False),('incomplete',False)])
def test_reconciliation_uses_current_subscription_and_verified_paid_period(tmp_path,status,allowed):
    _,now,metadata,stripe,billing=setup(tmp_path);expire(metadata,now);billing.checkout(USER)
    stripe.paid(status)
    billing.reconcile(USER)
    assert AccessControl(metadata).access_for(USER,now[0]).allowed is allowed
    now[0]=4000000
    assert not AccessControl(metadata).access_for(USER,now[0]).allowed


def test_unpaid_or_foreign_invoice_does_not_unlock_active_subscription(tmp_path):
    _,now,metadata,stripe,billing=setup(tmp_path);expire(metadata,now);billing.checkout(USER);stripe.paid()
    stripe.invoices[0]['customer']='cus_other'
    billing.reconcile(USER)
    assert not AccessControl(metadata).access_for(USER,now[0]).allowed


def test_owner_mismatch_and_existing_subscription_cannot_create_second_checkout(tmp_path):
    _,now,metadata,stripe,billing=setup(tmp_path);expire(metadata,now);billing.checkout(USER);stripe.paid('past_due')
    with pytest.raises(HTTPException):billing.checkout(USER)
    assert billing.portal(USER).startswith('https://billing.stripe.com/')
    stripe.customers['cus_social']['metadata']['user_id']='another-user'
    with pytest.raises(HTTPException):billing.portal(USER)
    assert len(stripe.sessions)==1


def test_account_page_shows_trial_expiry_and_checkout_success_does_not_grant_access(tmp_path):
    from socialctl.saas.billing_routes import install_billing_routes
    client,now,metadata,stripe,billing=setup(tmp_path)
    install_billing_routes(client.app,billing)
    page=client.get('/account/')
    assert 'Your 14 free days start' in page.text
    expire(metadata,now);login(client)
    page=client.get('/account/')
    assert 'USD 10/month' in page.text and '/account/billing/checkout' in page.text
    form=fields(page)
    assert client.post('/account/billing/checkout',data=form,headers={'Origin':'https://evil.example'}).status_code==403
    response=client.post('/account/billing/checkout',data=form,headers={'Origin':'https://social.example'},follow_redirects=False)
    assert response.status_code==303 and response.headers['location'].startswith('https://checkout.stripe.com/')
    assert client.get('/account/?payment=processing&paid=true').status_code==200
    assert not AccessControl(metadata).access_for(USER,now[0]).allowed
    stripe.paid();billing.reconcile(USER)
    stripe.subscriptions[0]['cancel_at_period_end']=True;billing.reconcile(USER)
    page=client.get('/account/')
    assert 'Will not renew' in page.text and 'Manage billing' in page.text


def test_billing_settings_wire_routes_and_can_load_from_environment(tmp_path,monkeypatch):
    from socialctl.saas.settings import SaaSSettings
    from dataclasses import replace
    from socialctl.connection_service.app import create_app
    from socialctl.connection_service.settings import Settings
    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient
    values={'SOCIALCLI_SAAS_ENABLED':'1','SOCIALCLI_AUTH_URL':'https://auth.example','SOCIALCLI_BILLING_MODE':'test',
            'STRIPE_SECRET_KEY':'sk_test_fictional','STRIPE_WEBHOOK_SECRET':'whsec_fictional','STRIPE_PRICE_ID':'price_social',
            'STRIPE_ACCOUNT_ID':'acct_social','STRIPE_PORTAL_CONFIGURATION':'bpc_social'}
    for key,value in values.items():monkeypatch.setenv(key,value)
    saas=SaaSSettings.from_environment()
    assert saas.billing.price_id=='price_social'
    settings=Settings(public_url='https://social.example',database=tmp_path/'wired.sqlite3',encryption_keys=[Fernet.generate_key()],providers={},saas=saas)
    with TestClient(create_app(settings,billing_client=httpx.Client(transport=httpx.MockTransport(StripeFixture()))),base_url='https://social.example') as client:
        assert client.post('/api/saas/v1/billing/webhook',content=b'{}').status_code==400
        assert client.post('/account/billing/checkout').status_code==403


def test_existing_unexpired_canceled_subscription_prevents_early_repurchase(tmp_path):
    _,now,metadata,stripe,billing=setup(tmp_path);expire(metadata,now);billing.checkout(USER);stripe.paid('canceled')
    stripe.sessions['cs_test_social']['status']='complete'
    with pytest.raises(HTTPException):billing.checkout(USER)
    now[0]=4000000
    assert billing.checkout(USER).startswith('https://checkout.stripe.com/')
    assert len([c for c in stripe.calls if c[:2]==('POST','/v1/checkout/sessions')])==2


def test_resuming_checkout_rejects_changed_line_items(tmp_path):
    _,now,metadata,stripe,billing=setup(tmp_path);expire(metadata,now);billing.checkout(USER)
    original=stripe.__call__
    def changed(request):
        if request.url.path.endswith('/line_items'):
            return httpx.Response(200,json={'has_more':False,'data':[{'price':{**stripe.price(),'unit_amount':1500},'quantity':1}]})
        return original(request)
    billing.client=httpx.Client(transport=httpx.MockTransport(changed))
    with pytest.raises(HTTPException):billing.checkout(USER)


def test_configuration_failure_does_not_expire_customer_creation_before_it_starts(tmp_path):
    _,now,metadata,stripe,billing=setup(tmp_path);expire(metadata,now)
    stripe.price_changes={'unit_amount':1500}
    with pytest.raises(HTTPException):billing.checkout(USER)
    now[0]+=86400;stripe.price_changes={}
    assert billing.checkout(USER).startswith('https://checkout.stripe.com/')


def test_payment_reconciliation_restores_existing_connection_and_impayment_blocks_tokens(tmp_path):
    from socialctl.saas.billing import BillingService,BillingSettings
    from tests.test_saas_connections import setup as connected, start,select,selected,activate,token
    client,now,installation=connected(tmp_path)
    value,proof=start(client,installation);select(client,value)
    result=selected(client,value,proof);assert activate(client,result).status_code==200
    metadata=client.app.state.saas;stripe=StripeFixture();stripe.vault=metadata.vault
    billing=BillingService(metadata,BillingSettings(secret_key='sk_test_fictional',webhook_secret='whsec_fictional',price_id='price_social',account_id='acct_social',portal_configuration='bpc_social'),httpx.Client(transport=httpx.MockTransport(stripe)),lambda:now[0],'https://social.example')
    now[0]=1210600
    assert token(client,result).status_code==402
    billing.checkout(USER)
    assert token(client,result).status_code==402
    stripe.paid();billing.reconcile(USER)
    assert token(client,result).status_code==200
    stripe.paid('past_due');billing.reconcile(USER)
    assert token(client,result).status_code==402
    stripe.paid();billing.reconcile(USER)
    assert token(client,result).status_code==200


def test_portal_timestamp_cancellation_is_shown_without_removing_paid_access(tmp_path):
    """Real Clover Portal returns cancel_at with cancel_at_period_end=false."""
    from socialctl.saas.billing_routes import install_billing_routes
    client,now,metadata,stripe,billing=setup(tmp_path)
    install_billing_routes(client.app,billing)
    expire(metadata,now);login(client);billing.checkout(USER);stripe.paid()
    subscription=stripe.subscriptions[0]
    subscription['cancel_at']=subscription['items']['data'][0]['current_period_end']
    assert subscription['cancel_at_period_end'] is False
    billing.reconcile(USER)
    assert 'Will not renew' in client.get('/account/').text
    assert AccessControl(metadata).access_for(USER,now[0]).reason=='paid'
    # Reversing the cancellation must remove the notice on canonical refresh.
    subscription['cancel_at']=None
    billing.reconcile(USER)
    assert 'Will not renew' not in client.get('/account/').text
    assert AccessControl(metadata).access_for(USER,now[0]).reason=='paid'


@pytest.mark.parametrize('cancel_at',[None,0,True,'3802600'])
def test_non_timestamp_values_do_not_report_scheduled_cancellation(tmp_path,cancel_at):
    client,now,metadata,stripe,billing=setup(tmp_path)
    expire(metadata,now);billing.checkout(USER);stripe.paid()
    stripe.subscriptions[0]['cancel_at']=cancel_at
    billing.reconcile(USER)
    with metadata.vault.transaction() as db:
        assert db.execute('SELECT cancel_at_period_end FROM saas_billing').fetchone()[0]==0
