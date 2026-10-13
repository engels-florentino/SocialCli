# SocialCli SaaS billing — test candidate

The branch adds test-only billing to the connection service. It is not enabled
on the existing public service. Do not enable commercial access until the legacy
cutoff/migration and remaining account lifecycle work are verified.

## Private configuration

Merge `billing.env.example` into the **server's private** `service.env` alongside
`SOCIALCLI_SAAS_ENABLED=1`, the private Auth URL/key and existing vault keys.
Map the parent's `SECRET_KEY` to `STRIPE_SECRET_KEY` and `PUBLISH_KEY` to
`STRIPE_PUBLISHABLE_KEY` during provisioning, never in the distributed CLI.
The API key must start with `sk_test_`; live keys fail configuration validation.
Keep test and live state/databases separate. API version: `2025-09-30.clover`.

Price validation requires USD 1000 cents, monthly interval count 1, licensed
quantity 1. Checkout starts only after the original SocialCli 14-day trial.
No Stripe trial, advance card collection, discount code or variable client price.
Checkout uses SocialCli's blue/background and the compatible Stripe default font.
The dedicated Portal configuration allows invoices, card updates and cancellation
at period end; it disables plan changes. Portal/receipt colors and logos inherit
account-wide Stripe branding. Do not alter branding on a shared Stripe account
without reviewing its impact on other products. Full hosted-page visual approval
and test email review remain rollout gates.

## Webhooks and reconciliation

After the reviewed HTTPS service is available, create a **test** Stripe endpoint
at `https://socialcli.app/api/saas/v1/billing/webhook` with the pinned API version.
Subscribe to the `EVENTS` set in `socialctl/saas/webhooks.py`. Store the unique
endpoint secret in `STRIPE_WEBHOOK_SECRET`. Account webhook deliveries may omit
`account`; the endpoint secret binds them, and canonical API reads verify the
configured account ID. Connected-account mismatches are rejected.

A 200 acknowledges the durable event and pending job, not confirmed access.
The success return page cannot grant access. The maintenance process reads
canonical subscriptions and paid invoices, validates owner/customer/price and
updates local entitlements. It also reconciles known customers every five minutes
to recover missing webhooks. No Stripe call runs inside a SQLite transaction.

Install the separate `socialcli-saas-billing.service` and `.timer` after deploying
this image/configuration. Check the Docker binary/container name on the host,
then enable the timer. This does not modify the private socialctl executor or its
timers. Run manually with:

```sh
docker exec socialcli-connections python -m socialctl.saas.maintenance
```

Output contains only counts. Exit 1 indicates durable retry work remains. Inspect
`saas_billing.review` and events in `state='review'` using a privileged local
operator; do not expose encrypted event payloads in application logs. Stripe
outages keep jobs pending and never grant access based on a redirect or event body.

Refund/dispute events are kept for operator review; they never automatically
refund or cancel. Decide the commercial policy before live billing. Event payloads
are encrypted and retained for 365 days provisionally; finalize retention with the
account deletion policy before public launch.

## Checkout recovery

A short per-user lease serializes Checkout, Portal and reconciliation. Customer
and Checkout intent keys persist before network requests. Pending sessions are
recovered by canonical customer/session reads. If an uncertain creation cannot be
resolved within 23 hours, creation fails closed for operator review; this avoids
reusing a Stripe idempotency key after Stripe may have pruned it. Resolve the
existing customer/session in Stripe before resetting an intent; never clear it
blindly. Self-service status and billing management remain available after trial
expiry. Provider tokens already delivered retain their provider expiration.

## Verification

```sh
uv run pytest -q tests/test_saas_billing.py tests/test_saas_webhooks.py
uv run pytest -q
python scripts/build_ui_assets.py --check
```

The automated tests use fictional Stripe HTTP responses and signed fixture events.
Real test-mode API evidence is recorded separately; it does not prove webhook
reachability, live billing approval or completion of the hosted browser checkout.
