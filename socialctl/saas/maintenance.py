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


def main():
    if sys.argv[1:]:
        raise SystemExit('Usage: python -m socialctl.saas.maintenance')
    for name in ('httpx','httpcore'):
        logging.getLogger(name).disabled=True
    settings=Settings.from_environment()
    if not settings.saas:
        raise SystemExit('SaaS identity must be configured before maintenance starts.')
    metadata=SaaSStore(Store(settings.database,settings.encryption_keys),settings.encryption_keys)
    with httpx.Client(timeout=20,follow_redirects=False) as client:
        billing=BillingService(metadata,settings.saas.billing,client,time.time,settings.public_url) if settings.saas.billing else None
        result=run_pending(billing) if billing else {'processed':0,'retry_pending':0}
        result['deletions']=run_deletions(metadata,SupabaseAuth(settings.saas,client),billing,time.time)
    AccountLifecycle(metadata,time.time).prune_expired()
    result['reminders']=Reminders(metadata,time.time,settings.public_url).run(
        SMTPMailer(settings.saas.mail) if settings.saas.mail else None)
    print(json.dumps(result))
    if (result['retry_pending'] or result['deletions']['pending']
            or any(result['reminders'].get(key,0) for key in ('due','uncertain','rejected','retry_pending'))):
        raise SystemExit(1)


if __name__=='__main__':
    main()
