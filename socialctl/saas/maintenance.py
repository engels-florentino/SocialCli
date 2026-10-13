"""Isolated, one-shot SaaS billing maintenance; never runs publication jobs."""
import json
import logging
import sys
import time
import httpx
from socialctl.connection_service.settings import Settings
from socialctl.connection_service.store import Store
from socialctl.saas.store import SaaSStore
from socialctl.saas.billing import BillingService
from socialctl.saas.billing_reconcile import run_pending
from socialctl.saas.auth import SupabaseAuth
from socialctl.saas.lifecycle import AccountLifecycle,run_deletions
from socialctl.saas.reminders import Reminders
from socialctl.saas.mail import SMTPMailer
from socialctl.saas.budget import WorkBudget,hard_deadline
from socialctl.saas.operations import write_heartbeat,inspect_status


def run(settings):
    metadata=SaaSStore(Store(settings.database,settings.encryption_keys),settings.encryption_keys)
    with httpx.Client(timeout=20,follow_redirects=False) as client:
        billing=BillingService(metadata,settings.saas.billing,client,time.time,settings.public_url) if settings.saas.billing else None
        result=run_pending(billing,budget=WorkBudget(45)) if billing else {'processed':0,'retry_pending':0,'due':0}
        result['deletions']=run_deletions(metadata,SupabaseAuth(settings.saas,client),billing,time.time,budget=WorkBudget(45))
    AccountLifecycle(metadata,time.time).prune_expired()
    result['reminders']=Reminders(metadata,time.time,settings.public_url).run(
        SMTPMailer(settings.saas.mail) if settings.saas.mail else None,budget=WorkBudget(45))
    return result


def main():
    if sys.argv[1:]:
        raise SystemExit('Usage: python -m socialctl.saas.maintenance')
    for name in ('httpx','httpcore'):
        logging.getLogger(name).disabled=True
    settings=None
    with hard_deadline():
        try:
            settings=Settings.from_environment()
            if not settings.saas:raise ValueError('Identity is required.')
            # Do not create a fresh database accidentally from a mistyped path.
            if not settings.database.is_file():raise ValueError('Database is required.')
            write_heartbeat(settings.database,'running',time.time())
            result=run(settings)
            attention=(result['retry_pending'] or result['due']
                or result['deletions']['pending'] or result['deletions']['due']
                or any(result['reminders'].get(key,0) for key in ('due','uncertain','rejected','retry_pending')))
            write_heartbeat(settings.database,'attention' if attention else 'complete',time.time())
            result['operations']=inspect_status(settings.database,settings.encryption_keys,now=time.time())
            attention=attention or result['operations']['status']!='ok'
        except Exception:
            # Never print exception text: providers and SQL may include private data.
            result={'status':'failed','error':'maintenance_failed'}
            attention=True
            if settings is not None and settings.database.is_file():
                try:write_heartbeat(settings.database,'failed',time.time())
                except Exception:pass
        print(json.dumps(result))
        if attention:raise SystemExit(1)


if __name__=='__main__':
    main()
