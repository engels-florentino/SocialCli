"""Canonical Stripe reconciliation: event timestamps never decide access."""
from socialctl.saas.billing import fixed_price, unavailable, stripe_id
from socialctl.saas.budget import using_budget


def paid_until(billing,customer,subscription,invoices):
    end=0
    for invoice in invoices:
        parent=invoice.get('parent') or {}
        details=parent.get('subscription_details') or {}
        if (invoice.get('livemode') is not False or invoice.get('customer')!=customer
                or invoice.get('status')!='paid' or invoice.get('paid_out_of_band')
                or invoice.get('currency')!='usd' or invoice.get('amount_paid')!=1000
                or details.get('subscription')!=subscription['id']):
            continue
        lines=invoice.get('lines') or {}
        data=lines.get('data',[])
        if lines.get('has_more') is not False or len(data)!=1:continue
        line=data[0];price=(line.get('pricing') or {}).get('price_details') or {}
        if line.get('currency')!='usd' or line.get('amount')!=1000 or line.get('quantity')!=1 or price.get('price')!=billing.settings.price_id:continue
        period=line.get('period') or {}
        start,finish=period.get('start'),period.get('end')
        if type(start) not in (int,float) or type(finish) not in (int,float) or not 0<finish-start<=32*86400:continue
        if start<=billing.clock():end=max(end,finish)
    return end


def reconcile(billing,user_id):
    with billing.lease(user_id) as (row,token):
        if not row['customer_id']:return
        if billing.api('GET','/account').get('id')!=billing.settings.account_id:raise unavailable()
        customer=billing.customer(user_id,row,token)
        subscriptions=billing.listing('/subscriptions',{'customer':customer,'status':'all'})
        ongoing=[s for s in subscriptions if s.get('status') not in {'canceled','incomplete_expired'}]
        candidates=ongoing or [s for s in subscriptions if s.get('status')=='canceled']
        status='';end=0;selected=None;cancel=False;review=''
        if len(ongoing)>1:
            review='multiple_subscriptions'
        else:
            invoices=billing.listing('/invoices',{'customer':customer,'status':'paid'}) if candidates else []
            for sub in candidates:
                items=sub.get('items') or {};data=items.get('data',[])
                valid=(billing.owned(sub,user_id) and sub.get('customer')==customer and items.get('has_more') is False
                       and len(data)==1 and data[0].get('quantity')==1 and fixed_price(data[0].get('price',{}),billing.settings.price_id))
                if not valid:
                    review='subscription_mismatch';continue
                until=paid_until(billing,customer,sub,invoices)
                if selected is None or until>end:
                    selected=stripe_id(sub.get('id'),'sub_');status=sub.get('status','')
                    end=until;cancel=sub.get('cancel_at_period_end') is True
                    if sub.get('pause_collection'):status='paused'
                    if status=='trialing':review='unexpected_stripe_trial'
        if review and review!='unexpected_stripe_trial':status='';end=0
        with billing.vault.transaction() as db:
            current=db.execute('SELECT * FROM saas_billing WHERE user_id=?',(user_id,)).fetchone()
            user=billing.metadata.user(db,user_id)
            if not current or current['lease']!=token or current['lease_until']<=billing.clock() or not user or user['deleting']:
                raise unavailable()
            db.execute('INSERT OR IGNORE INTO saas_entitlements(user_id) VALUES(?)',(user_id,))
            db.execute('UPDATE saas_entitlements SET subscription_status=?,paid_until=? WHERE user_id=?',(status,end,user_id))
            db.execute('UPDATE saas_billing SET subscription_id=?,cancel_at_period_end=?,review=?,sync_due=? WHERE user_id=?',
                       (selected,int(cancel),review,billing.clock()+300,user_id))
        return {'status':status,'paid_until':end,'review':review}


def run_pending(billing,limit=100,*,budget=None):
    with using_budget(budget,billing):
        return _run_pending(billing,limit,budget)


def _run_pending(billing,limit,budget):
    """Run on the isolated five-minute maintenance timer; safe to retry on crash."""
    from fastapi import HTTPException
    with billing.vault.transaction() as db:
        events=[dict(r) for r in db.execute("SELECT * FROM saas_billing_events WHERE state='pending' AND next_attempt<=? ORDER BY next_attempt,received,event_id LIMIT ?",(billing.clock(),limit))]
        due=[r[0] for r in db.execute('SELECT b.user_id FROM saas_billing b JOIN saas_users u ON u.id=b.user_id WHERE b.customer_id IS NOT NULL AND b.sync_due<=? AND u.deleting=0 ORDER BY b.sync_due,b.user_id LIMIT ?',(billing.clock(),limit))]
    processed=0;failed=0;visited=set()
    # Reserve half the phase for periodic reconciliation, even under event load.
    event_budget=budget.portion(0.5) if budget else None
    if event_budget:billing.budget=event_budget
    for event in events:
        if event_budget and event_budget.expired():break
        key=(event['account_id'],event['mode'],event['event_id'])
        with billing.vault.transaction() as db:
            row=db.execute('SELECT user_id FROM saas_billing WHERE customer_id=?',(event['customer_id'],)).fetchone()
        if not row:
            # A customer write may still be in flight. Preserve the event until
            # that write finishes; unrelated deliveries eventually need review.
            state='review' if billing.clock()-event['received']>86400 else 'pending'
        else:
            user_id=row['user_id']
            try:
                billing.reconcile(user_id)
                state='done';processed+=1;visited.add(user_id)
            except HTTPException:
                state='pending';failed+=1
        with billing.vault.transaction() as db:
            db.execute("UPDATE saas_billing_events SET state=?,next_attempt=? WHERE account_id=? AND mode=? AND event_id=? AND state='pending'",(state,billing.clock()+300,*key))
    if budget:billing.budget=budget
    for user_id in due:
        if budget and budget.expired():break
        if user_id in visited:continue
        try:
            billing.reconcile(user_id)
        except HTTPException:
            failed+=1
            with billing.vault.transaction() as db:
                db.execute('UPDATE saas_billing SET sync_due=? WHERE user_id=? AND lease_until<=?',
                           (billing.clock()+300,user_id,billing.clock()))
    with billing.vault.transaction() as db:
        event_due=db.execute("SELECT count(*) FROM saas_billing_events WHERE state='pending' AND next_attempt<=?",(billing.clock(),)).fetchone()[0]
        customer_due=db.execute("SELECT count(*) FROM saas_billing b JOIN saas_users u ON u.id=b.user_id WHERE b.customer_id IS NOT NULL AND b.sync_due<=? AND u.deleting=0",(billing.clock(),)).fetchone()[0]
    return {'processed':processed,'retry_pending':failed,'due':event_due+customer_due,
            'budget_exhausted':bool(budget and budget.expired())}
