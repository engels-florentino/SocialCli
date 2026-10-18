"""Mode-validated Stripe billing with durable checkout intents and short local locks."""
from contextlib import contextmanager
from dataclasses import dataclass, field
import os
import re
import secrets
from urllib.parse import urlsplit
import httpx
from fastapi import HTTPException
from socialctl.saas.access import AccessControl
from socialctl.saas.budget import BudgetExpired

API_VERSION = '2025-09-30.clover'


class AnchorRejected(Exception):
    """Stripe rejected the anchor before starting Checkout execution."""



@dataclass(frozen=True)
class BillingSettings:
    secret_key: str = field(repr=False)
    webhook_secret: str = field(repr=False)
    price_id: str
    account_id: str
    portal_configuration: str
    mode: str = 'test'

    def __post_init__(self):
        if self.mode not in {'test','live'} or not self.secret_key.startswith('sk_'+self.mode+'_'):
            raise ValueError('Stripe mode and secret key must match')
        for value, prefix in [(self.webhook_secret,'whsec_'),(self.price_id,'price_'),
                              (self.account_id,'acct_'),(self.portal_configuration,'bpc_')]:
            if not re.fullmatch(re.escape(prefix)+r'[A-Za-z0-9_]+',value):
                raise ValueError('Incomplete Stripe configuration')

    @property
    def expected_livemode(self):
        return self.mode == 'live'

    @classmethod
    def from_environment(cls):
        mode=os.environ.get('SOCIALCLI_BILLING_MODE','')
        if not mode:
            return None
        return cls(secret_key=os.environ.get('STRIPE_SECRET_KEY',''),
                   webhook_secret=os.environ.get('STRIPE_WEBHOOK_SECRET',''),
                   price_id=os.environ.get('STRIPE_PRICE_ID',''),
                   account_id=os.environ.get('STRIPE_ACCOUNT_ID',''),
                   portal_configuration=os.environ.get('STRIPE_PORTAL_CONFIGURATION',''),mode=mode)


def unavailable():
    return HTTPException(503,'Billing is temporarily unavailable. Please try again; do not start another purchase.')


def stripe_id(value,prefix):
    if not isinstance(value,str) or not re.fullmatch(re.escape(prefix)+r'[A-Za-z0-9_]+',value):
        raise unavailable()
    return value


def fixed_price(price,expected,*,active=False,expected_livemode=False):
    recurring=price.get('recurring') or {}
    return (price.get('id')==expected and price.get('livemode') is expected_livemode
            and price.get('type')=='recurring' and price.get('currency')=='usd'
            and price.get('unit_amount')==1000 and recurring.get('interval')=='month'
            and recurring.get('interval_count')==1 and recurring.get('usage_type')=='licensed'
            and not recurring.get('trial_period_days') and not price.get('transform_quantity')
            and (not active or price.get('active') is True))


class BillingService:
    def __init__(self,metadata,settings,client,clock,origin):
        self.metadata,self.vault,self.settings=metadata,metadata.vault,settings
        self.client,self.clock,self.origin=client,clock,origin
        self.access=AccessControl(metadata)
        self.budget=None
        self.bind_environment()

    def bind_environment(self):
        """Never reinterpret persisted sandbox access by changing configuration."""
        expected={'account_id':self.settings.account_id,'mode':self.settings.mode}
        error='The billing environment differs from persisted state; keep commercial access closed and use the reviewed cutover procedure.'
        with self.vault.transaction() as db:
            bound=self.vault.get(db,'saas-billing-environment','active')
            if bound:
                if bound['payload']!=expected:
                    raise ValueError(error)
                return
            # No unbound billing history proves which account created customer
            # pointers or uncertain intents, even if all recorded events agree.
            # Legacy enrollment requires a separately reviewed offline procedure.
            legacy=(db.execute('SELECT 1 FROM saas_billing LIMIT 1').fetchone()
                or db.execute('SELECT 1 FROM saas_billing_events LIMIT 1').fetchone()
                or db.execute("SELECT 1 FROM saas_entitlements WHERE paid_until>0 OR subscription_status<>'' LIMIT 1").fetchone()
                or db.execute("SELECT 1 FROM records WHERE kind IN ('saas-checkout-intent','saas-stripe-event') LIMIT 1").fetchone())
            if legacy:
                raise ValueError(error)
            self.vault.put(db,'saas-billing-environment','active',expected,expires=253402300799)

    def api(self,method,path,data=None,key=None):
        headers={'Authorization':'Bearer '+self.settings.secret_key,'Stripe-Version':API_VERSION}
        if key:headers['Idempotency-Key']=key
        try:
            with self.client.stream(method,'https://api.stripe.com/v1'+path,headers=headers,
                    params=data if method=='GET' else None,data=data if method!='GET' else None,
                    timeout=self.budget.timeout(20) if self.budget else 20,follow_redirects=False) as response:
                content=bytearray()
                for chunk in response.iter_bytes():
                    if self.budget and self.budget.expired():raise BudgetExpired()
                    content.extend(chunk)
                    if len(content)>2*1024*1024:raise unavailable()
                import json
                value=json.loads(content)
                if response.status_code!=200:
                    error=value.get('error',{}) if isinstance(value,dict) else {}
                    if (response.status_code==400 and method=='POST' and path=='/checkout/sessions'
                            and error.get('type')=='invalid_request_error'
                            and error.get('param')=='subscription_data[billing_cycle_anchor]'):
                        raise AnchorRejected()
                    raise unavailable()
                if not isinstance(value,dict):raise unavailable()
                return value
        except (httpx.HTTPError,ValueError,TypeError,BudgetExpired):
            raise unavailable() from None

    def verify_configuration(self):
        if self.api('GET','/account').get('id')!=self.settings.account_id:
            raise unavailable()
        if not fixed_price(self.api('GET','/prices/'+self.settings.price_id),self.settings.price_id,active=True,expected_livemode=self.settings.expected_livemode):
            raise unavailable()

    def owned(self,obj,user_id):
        return obj.get('livemode') is self.settings.expected_livemode and obj.get('metadata',{}).get('app')=='socialcli' and obj.get('metadata',{}).get('user_id')==user_id

    @contextmanager
    def lease(self,user_id,*,checkout=False):
        token=secrets.token_urlsafe(24)
        with self.vault.transaction() as db:
            user=self.metadata.user(db,user_id)
            if user is None or user['deleting']:
                raise HTTPException(403,'This account is unavailable.')
            if checkout and self.access.decision(db,user_id,self.clock()).reason not in {'trial','trial_expired'}:
                raise HTTPException(409,'Connect your first social account to start your trial, or manage your existing subscription.')
            db.execute('INSERT OR IGNORE INTO saas_billing(user_id,customer_key,customer_started) VALUES(?,?,?)',
                       (user_id,secrets.token_hex(20),0))
            row=db.execute('SELECT * FROM saas_billing WHERE user_id=?',(user_id,)).fetchone()
            if row['lease_until']>self.clock():
                raise HTTPException(409,'Your billing request is already being processed. Please try again shortly.')
            db.execute('UPDATE saas_billing SET lease=?,lease_until=? WHERE user_id=?',(token,self.clock()+180,user_id))
            row=dict(row)
        try:
            yield row,token
        finally:
            with self.vault.transaction() as db:
                db.execute('UPDATE saas_billing SET lease=NULL,lease_until=0 WHERE user_id=? AND lease=?',(user_id,token))

    def save(self,user_id,token,**values):
        allowed={'customer_id','subscription_id','checkout_id','attempt','attempt_started','sync_due','cancel_at_period_end','review','customer_started'}
        if not values or not values.keys()<=allowed:raise ValueError('Invalid billing update')
        with self.vault.transaction() as db:
            user=self.metadata.user(db,user_id)
            if user is None or user['deleting']:raise HTTPException(403,'This account is unavailable.')
            count=db.execute('UPDATE saas_billing SET '+','.join(k+'=?' for k in values)+' WHERE user_id=? AND lease=? AND lease_until>?',
                             (*values.values(),user_id,token,self.clock())).rowcount
            if count!=1:raise HTTPException(409,'Billing is still being checked. Please try again.')

    def customer(self,user_id,row,token,*,create=False):
        customer_id=row['customer_id']
        if customer_id:
            customer=self.api('GET','/customers/'+stripe_id(customer_id,'cus_'))
        elif create:
            # Stripe may prune idempotency after 24h. Never retry an unresolved
            # customer creation beyond that window and risk a second customer.
            if row['customer_started'] and self.clock()-row['customer_started']>=23*3600:
                raise HTTPException(409,'An earlier billing request needs review before another purchase.')
            if not row['customer_started']:
                self.save(user_id,token,customer_started=self.clock())
                row['customer_started']=self.clock()
            customer=self.api('POST','/customers',{'metadata[app]':'socialcli','metadata[user_id]':user_id},'socialcli-customer-'+row['customer_key'])
            customer_id=stripe_id(customer.get('id'),'cus_')
            if not self.owned(customer,user_id):raise unavailable()
            self.save(user_id,token,customer_id=customer_id)
            row['customer_id']=customer_id
        else:
            raise HTTPException(409,'There is no billing account yet.')
        if not self.owned(customer,user_id):raise unavailable()
        return customer_id

    def listing(self,path,params):
        result=[]
        for _ in range(10):
            response=self.api('GET',path,{**params,'limit':100})
            data=response.get('data')
            if not isinstance(data,list):raise unavailable()
            result.extend(data)
            if response.get('has_more') is False:return result
            if not data or not data[-1].get('id'):raise unavailable()
            params={**params,'starting_after':data[-1]['id']}
        raise unavailable()

    def redirect_url(self,value,host):
        url=urlsplit(value or '')
        if url.scheme!='https' or url.hostname!=host or url.username or url.password or url.port not in (None,443):
            raise unavailable()
        return value

    def checkout(self,user_id):
        with self.lease(user_id,checkout=True) as (row,token):
            self.verify_configuration()
            customer=self.customer(user_id,row,token,create=True)
            subscriptions=self.listing('/subscriptions',{'customer':customer,'status':'all'})
            if any(s.get('status') not in {'canceled','incomplete_expired'} for s in subscriptions):
                raise HTTPException(409,'You already have a subscription. Use Manage billing to update your payment or cancel.')
            from socialctl.saas.billing_reconcile import paid_until
            canceled=[s for s in subscriptions if s.get('status')=='canceled']
            invoices=self.listing('/invoices',{'customer':customer,'status':'paid'}) if canceled else []
            if any(paid_until(self,customer,s,invoices)>self.clock() for s in canceled):
                raise HTTPException(409,'Your paid access has not ended. You can subscribe again after it ends.')
            sessions=self.listing('/checkout/sessions',{'customer':customer})
            opened=[s for s in sessions if s.get('mode')=='subscription' and s.get('status')=='open']
            if len(opened)>1:raise HTTPException(409,'Multiple payment requests need review. Please use Manage billing.')
            if opened:
                session=opened[0]
                if not self.owned(session,user_id) or session.get('customer')!=customer:raise unavailable()
                items=self.listing('/checkout/sessions/'+stripe_id(session.get('id'),'cs_')+'/line_items',{})
                if len(items)!=1 or items[0].get('quantity')!=1 or not fixed_price(items[0].get('price',{}),self.settings.price_id,expected_livemode=self.settings.expected_livemode):raise unavailable()
                self.save(user_id,token,checkout_id=stripe_id(session.get('id'),'cs_'))
                return self.redirect_url(session.get('url'),'checkout.stripe.com')
            previous=next((s for s in sessions if s.get('metadata',{}).get('attempt')==row['attempt']),None)
            if previous and previous.get('status')=='complete' and not subscriptions:
                raise HTTPException(409,'Your previous purchase is being checked. Please use Manage billing.')
            attempt=row['attempt']
            if not attempt or previous and previous.get('status') in {'expired','complete'}:
                attempt=secrets.token_hex(20)
                self.save(user_id,token,attempt=attempt,attempt_started=self.clock(),checkout_id=None)
            elif self.clock()-row['attempt_started']>=23*3600:
                raise HTTPException(409,'An earlier payment request needs review before another purchase.')
            # Persist the complete time-sensitive choice before the HTTP request.
            # Retrying an uncertain request must retain exactly the same parameters.
            with self.vault.transaction() as db:
                intent=self.vault.get(db,'saas-checkout-intent',attempt)
                if intent:
                    deferred=intent['payload']['deferred']
                else:
                    entitlement=db.execute('SELECT trial_end FROM saas_entitlements WHERE user_id=?',(user_id,)).fetchone()
                    deadline=int(entitlement['trial_end']) if entitlement and entitlement['trial_end'] else 0
                    deferred=({'subscription_data[billing_cycle_anchor]':deadline,
                               'subscription_data[proration_behavior]':'none',
                               'payment_method_collection':'always'} if deadline>self.clock() else {})
                    self.vault.put(db,'saas-checkout-intent',attempt,{'user_id':user_id,'deferred':deferred},expires=self.clock()+30*86400)
            try:
                session=self.api('POST','/checkout/sessions',{
                    **deferred,
                    'mode':'subscription','customer':customer,'line_items[0][price]':self.settings.price_id,
                    'line_items[0][quantity]':1,'success_url':self.origin+'/account/?payment=processing',
                    'cancel_url':self.origin+'/account/?payment=canceled','client_reference_id':user_id,
                    'metadata[app]':'socialcli','metadata[user_id]':user_id,'metadata[attempt]':attempt,
                    'subscription_data[metadata][app]':'socialcli','subscription_data[metadata][user_id]':user_id,
                    'payment_method_types[0]':'card','allow_promotion_codes':'false',
                    'branding_settings[display_name]':'SocialCli','branding_settings[background_color]':'#f5f7fb',
                    'branding_settings[button_color]':'#1464ec','branding_settings[font_family]':'default',
                },'socialcli-checkout-'+attempt)
            except AnchorRejected:
                if deferred.get('subscription_data[billing_cycle_anchor]',float('inf'))<=self.clock():
                    # Only a definitive pre-execution validation failure releases
                    # this intent. Timeouts and ambiguous failures keep the same key.
                    self.save(user_id,token,attempt=None,attempt_started=None,checkout_id=None)
                    raise HTTPException(409,'Your trial ended while Checkout was opening. Open Subscribe again to review your first payment.') from None
                raise unavailable() from None
            if not self.owned(session,user_id) or session.get('customer')!=customer or session.get('mode')!='subscription':raise unavailable()
            self.save(user_id,token,checkout_id=stripe_id(session.get('id'),'cs_'))
            return self.redirect_url(session.get('url'),'checkout.stripe.com')

    def portal(self,user_id):
        with self.lease(user_id) as (row,token):
            if self.api('GET','/account').get('id')!=self.settings.account_id:raise unavailable()
            customer=self.customer(user_id,row,token)
            session=self.api('POST','/billing_portal/sessions',{'customer':customer,
                'configuration':self.settings.portal_configuration,'return_url':self.origin+'/account/'},
                'socialcli-portal-'+token)
            return self.redirect_url(session.get('url'),'billing.stripe.com')

    def reconcile(self,user_id):
        from socialctl.saas.billing_reconcile import reconcile
        return reconcile(self,user_id)

    def delete_account_billing(self,user_id,row):
        """No new charges: wait for in-flight Checkout, expire sessions, cancel subscriptions."""
        if row['lease_until']>self.clock():raise HTTPException(409,'Billing cleanup is waiting for an earlier request.')
        if self.api('GET','/account').get('id')!=self.settings.account_id:raise unavailable()
        customer=row['customer_id']
        if not customer:
            if not row['customer_started']:return
            if self.clock()-row['customer_started']>=23*3600:raise unavailable()
            result=self.api('POST','/customers',{'metadata[app]':'socialcli','metadata[user_id]':user_id},'socialcli-customer-'+row['customer_key'])
            if not self.owned(result,user_id):raise unavailable()
            customer=stripe_id(result.get('id'),'cus_')
            with self.vault.transaction() as db:
                db.execute('UPDATE saas_billing SET customer_id=? WHERE user_id=?',(customer,user_id))
        owner=self.api('GET','/customers/'+stripe_id(customer,'cus_'))
        if owner.get('deleted') is True and owner.get('id')==customer:return
        if not self.owned(owner,user_id):raise unavailable()
        sessions=self.listing('/checkout/sessions',{'customer':customer})
        if any(not self.owned(s,user_id) or s.get('customer')!=customer for s in sessions):raise unavailable()
        # Close hosted payment pages before taking the subscription snapshot.
        # A page completing concurrently either appears in the later read or
        # makes expiration fail, leaving the durable job pending for another pass.
        for session in sessions:
            if session.get('status')=='open':
                self.api('POST','/checkout/sessions/'+stripe_id(session.get('id'),'cs_')+'/expire',{},'socialcli-delete-session-'+session['id'])
        subscriptions=self.listing('/subscriptions',{'customer':customer,'status':'all'})
        if any(not self.owned(s,user_id) or s.get('customer')!=customer for s in subscriptions):raise unavailable()
        subscription_ids={s['id'] for s in subscriptions}
        for session in sessions:
            if session.get('mode')=='subscription' and session.get('status')=='complete' and session.get('subscription') not in subscription_ids:
                raise unavailable()
        for sub in subscriptions:
            if sub.get('status') not in {'canceled','incomplete_expired'}:
                self.api('DELETE','/subscriptions/'+stripe_id(sub.get('id'),'sub_'))
        # Delete the dedicated, owner-verified Stripe customer as a final fence:
        # Stripe prevents subsequent subscriptions and cancels any concurrent one.
        # Historical invoice records remain with Stripe. A repeat GET reports deleted.
        deleted=self.api('DELETE','/customers/'+stripe_id(customer,'cus_'))
        if deleted.get('id')!=customer or deleted.get('deleted') is not True:raise unavailable()
