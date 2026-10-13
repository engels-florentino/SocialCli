"""Authenticate raw Stripe deliveries and durably queue before acknowledgement."""
import asyncio
import hashlib
import hmac
import json
import re
from fastapi import HTTPException, Request
from socialctl.saas.billing import API_VERSION

WEBHOOK_PATH='/api/saas/v1/billing/webhook'
MAX_BODY=1024*1024
EVENTS={'checkout.session.completed','checkout.session.expired','invoice.paid','invoice.payment_failed',
        'invoice.payment_action_required','customer.subscription.created','customer.subscription.updated',
        'customer.subscription.deleted','customer.subscription.paused','customer.subscription.resumed',
        'charge.refunded','charge.dispute.created','charge.dispute.updated','charge.dispute.closed'}


def verified_event(raw,signature,settings,now):
    try:
        parts=[part.split('=',1) for part in signature.split(',')]
        timestamps=[v for k,v in parts if k=='t']
        signatures=[v for k,v in parts if k=='v1']
        if len(timestamps)!=1 or len(signatures)>10:raise ValueError()
        timestamp=int(timestamps[0])
        expected=hmac.new(settings.webhook_secret.encode(),timestamps[0].encode()+b'.'+raw,hashlib.sha256).hexdigest()
        if abs(now-timestamp)>300 or not any(hmac.compare_digest(expected,s) for s in signatures):raise ValueError()
        event=json.loads(raw)
        if (not isinstance(event,dict) or event.get('object')!='event' or event.get('livemode') is not False
                or event.get('api_version')!=API_VERSION or event.get('account') not in (None,settings.account_id)
                or not re.fullmatch(r'evt_[A-Za-z0-9_]{1,200}',event.get('id',''))
                or not isinstance(event.get('type'),str) or not isinstance(event.get('data',{}).get('object'),dict)):
            raise ValueError()
        return event
    except (ValueError,TypeError,KeyError,AttributeError):
        raise HTTPException(400,'Invalid Stripe event.') from None


def install_webhook_route(app,billing):
    @app.post(WEBHOOK_PATH)
    async def webhook(request:Request):
        raw=bytearray()
        try:
            async with asyncio.timeout(10):
                async for chunk in request.stream():
                    raw.extend(chunk)
                    if len(raw)>MAX_BODY:raise HTTPException(413,'Event is too large.')
        except TimeoutError:
            raise HTTPException(408,'Event timed out.') from None
        event=verified_event(bytes(raw),request.headers.get('stripe-signature',''),billing.settings,billing.clock())
        def persist():
            digest=hashlib.sha256(raw).hexdigest()
            identity=(billing.settings.account_id,'test',event['id'])
            with billing.vault.transaction() as db:
                previous=db.execute('SELECT body_hash FROM saas_billing_events WHERE account_id=? AND mode=? AND event_id=?',identity).fetchone()
                if previous:
                    if previous['body_hash']!=digest:raise HTTPException(400,'Conflicting Stripe event.')
                    return
                customer=event['data']['object'].get('customer')
                if not isinstance(customer,str) or not re.fullmatch(r'cus_[A-Za-z0-9_]{1,200}',customer):customer=None
                state='pending' if event['type'] in EVENTS else 'ignored'
                # Refunds/disputes remain available for explicit operator review.
                if event['type'].startswith(('charge.refund','charge.dispute')):state='review'
                db.execute('INSERT INTO saas_billing_events(account_id,mode,event_id,body_hash,customer_id,type,state,received) VALUES(?,?,?,?,?,?,?,?)',
                           (*identity,digest,customer,event['type'],state,billing.clock()))
                billing.vault.put(db,'saas-stripe-event',':'.join(identity),event,expires=billing.clock()+365*86400)
        await asyncio.to_thread(persist)
        return {'received':True}
