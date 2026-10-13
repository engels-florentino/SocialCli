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


def main():
    if sys.argv[1:]:
        raise SystemExit('Usage: python -m socialctl.saas.maintenance')
    for name in ('httpx','httpcore'):
        logging.getLogger(name).disabled=True
    settings=Settings.from_environment()
    if not settings.saas or not settings.saas.billing:
        raise SystemExit('SaaS test billing must be configured before maintenance starts.')
    metadata=SaaSStore(Store(settings.database,settings.encryption_keys),settings.encryption_keys)
    with httpx.Client(timeout=20,follow_redirects=False) as client:
        billing=BillingService(metadata,settings.saas.billing,client,time.time,settings.public_url)
        result=run_pending(billing)
    print(json.dumps(result))
    if result['retry_pending']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
