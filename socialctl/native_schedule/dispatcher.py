"""Deliver approved native or due API jobs; never invoke the legacy executor."""
from datetime import datetime, timedelta, timezone
from socialctl.capabilities import delivery_route
from .approval import verify_approval
from .handoff import prepare_handoff
from .models import aware
from .store import NativeStore


def dispatch_ready(now: datetime, brand: str, *, store: NativeStore,
                   job_id: str | None = None, budget: int = 10,
                   adapters: dict | None = None, capabilities: dict | None = None,
                   native_submit=None, delivery_context=None) -> list[str]:
    """Budget counts delivery attempts, independently of editorial reservations.

    API jobs send in a bounded five-minute window from the approved instant.
    Missing capability evidence never selects an adapter or a UI fallback.
    """
    now = aware(now)
    if brand != store.brand or budget < 0:
        raise ValueError('Explicit brand and nonnegative delivery budget required')
    delivered = []
    for job in store.list_jobs():
        if job_id is not None and job.id != job_id:
            continue
        if job.state not in {'approved', 'waiting_window', 'ready'}:
            continue
        payload = store.get_payload(job.id)
        try:
            verify_approval(job, payload)
        except ValueError:
            store.notice(job.id,'Approval or media changed; new preview required',now=now)
            continue
        delivery_mode = payload.get('delivery_mode', 'native_schedule')
        if delivery_mode not in {'native_schedule', 'at_time'}:
            store.notice(job.id,'Unknown delivery mode; new preview required',now=now)
            continue
        deadline = job.publish_at + (timedelta(minutes=5)
                                     if job.route == 'api' and delivery_mode == 'at_time'
                                     else timedelta())
        if deadline <= now:
            store.transition(job.id,job.state,'blocked',now=now)
            store.notice(job.id,'Delivery window expired; no late publication',now=now)
            continue
        if job.route == 'api' and delivery_mode == 'at_time' and now < job.publish_at:
            continue
        cooldown=store.cooldown_until(job.id)
        if job.dispatch_after > now or (cooldown is not None and cooldown > now):
            if job.state == 'approved':
                store.transition(job.id,job.state,'waiting_window',now=now)
            continue
        if len(delivered) >= budget:
            break
        if job.route == 'api':
            if delivery_mode == 'native_schedule' and native_submit is not None:
                try:
                    result = native_submit(job, payload)
                    if result.state == 'pending_verification':
                        delivered.append(job.id)
                    elif result.state == 'ui_required':
                        store.transition(job.id, job.state, 'blocked', now=now)
                        store.notice(job.id, result.reason, now=now)
                    else:
                        store.notice(job.id, 'Native API result requires reconciliation; no automatic resend', now=now)
                except Exception:
                    current = store.get(job.id)
                    # Native submitters own their durable claim and uncertainty
                    # handling. A competing worker may have claimed this job;
                    # this outer worker must never alter that attempt's state.
                    if current.state == job.state and current.state in {'approved', 'waiting_window', 'ready'}:
                        store.transition(job.id, current.state, 'blocked', now=now)
                    store.notice(job.id, 'Native API submission unavailable; inspect eligibility and existing attempts before another approval', now=now)
                continue
            capability = (capabilities or {}).get(job.platform, {})
            adapter = (adapters or {}).get(job.platform)
            if delivery_mode == 'at_time' and delivery_context is not None:
                try:
                    adapter, capability = delivery_context(job, payload)
                except Exception:
                    store.transition(job.id, job.state, 'blocked', now=now)
                    reason = ('Unsupported Instagram first-comment combination or eligibility; no publication attempted'
                              if job.platform == 'instagram' and payload.get('first_comment') else
                              'API delivery eligibility or account check failed; no publication attempted')
                    store.notice(job.id, reason, now=now)
                    continue
            native_unverified = (delivery_mode == 'native_schedule'
                and capability.get('verified_delivery_mode') != 'native_schedule')
            if native_unverified or delivery_route(job, capability, payload.get('options', {}).get('format')) != 'api' or adapter is None:
                store.transition(job.id,job.state,'blocked',now=now)
                store.notice(job.id,'API capability/account unverified or adapter absent; new approval required',now=now)
                continue
            claimed = store.claim(job.id,job.state,now=now)
            if claimed is None:
                continue
            def checkpoint(value, *, claimed=claimed):
                store.checkpoint(claimed.id,claimed.attempt_id,value,now=now)
            try:
                remote_id = adapter.deliver(claimed,payload,checkpoint)
                store.mark_api_submitted(claimed.id,claimed.attempt_id,remote_id,now=now)
                delivered.append(claimed.id)
            except Exception:
                # The adapter may have sent bytes even if its response failed.
                # Preserve the claim and every checkpoint for reconciliation.
                current = store.get(claimed.id)
                if current and current.state == 'dispatching':
                    store.transition(claimed.id,'dispatching','uncertain',now=now)
                store.notice(claimed.id,'API result uncertain; reconcile before another send',now=now)
            continue
        if job.route != 'ui':
            store.transition(job.id,job.state,'blocked',now=now)
            store.notice(job.id,'API capability closed; prepare and approve a new UI intent; route unchanged',now=now)
            continue
        try:
            prepare_handoff(job,payload,store=store,now=now,require_new=True)
            delivered.append(job.id)
        except Exception:
            # A competing worker may own the claim. Never alter its state.
            # An interrupted claim remains dispatching until explicit recovery.
            store.notice(job.id,'Delivery requires operator review; inspect existing attempt before retry',now=now)
    return delivered
