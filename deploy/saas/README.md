# SocialCli SaaS billing — test candidate

The SaaS includes mode-validated billing; the deployed pilot stays in test mode. Do not enable commercial access until the legacy
cutoff/migration and remaining account lifecycle work are verified.

## Private configuration

Merge `billing.env.example` into the **server's private** `service.env` alongside
`SOCIALCLI_SAAS_ENABLED=1`, the private Auth URL/key and existing vault keys.
Map the parent's `SECRET_KEY` to `STRIPE_SECRET_KEY` and `PUBLISH_KEY` to
`STRIPE_PUBLISHABLE_KEY` during provisioning, never in the distributed CLI.
The API key must match the configured mode: `sk_test_` for test or `sk_live_` for live.
Keep test and live state/databases separate. API version: `2025-09-30.clover`.

Price validation requires USD 1000 cents, monthly interval count 1, licensed
quantity 1. Checkout is available during the original 14-day trial. A fixed billing-cycle anchor and disabled prorations preserve the original free period.
Early Checkout collects a payment method for the original trial-end charge. No discount code or variable client price is accepted.
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

Install the separate `socialcli-saas-candidate-billing.service` and `.timer` after deploying
this image/configuration. Check the Docker binary/container name on the host,
then enable the timer. This does not modify the private socialctl executor or its
timers. Run manually with:

```sh
docker exec socialcli-saas-candidate python -m socialctl.saas.maintenance
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

## Commercial cutoff and rollback

Billing-enabled SaaS startup closes anonymous legacy creation automatically.
`SOCIALCLI_LEGACY_CUTOFF=1` can close it explicitly. The first cutoff transaction
records existing activated connections, prior authorizations and provisional
connections. Existing attempts may finish/activate only within the original
600-second drain. Activated entries retain their own expiry. No version string
or brand name grants an exception. Restarting or removing the environment flag
cannot erase the persistent cutoff.

Before routing a commercial service, add `nginx-legacy-cutoff.conf` to **each**
public NPM host that can reach the vault (including the old hostname). Verify
Nginx config and direct/encoded/trailing-slash creation requests. Keep the edge
block after any rollback, and never expose the service port outside the proxy
network. A rollback to 0.2.6 is not supported without this permanent edge block;
prefer a compatible cutoff-aware release or maintenance mode. Do not restore an
old database over newer payment/connection state.

## Account cleanup

Configure `SOCIALCLI_AUTH_ADMIN_KEY` with the private self-hosted Auth service-role
JWT, separate from browser/agent credentials. The maintenance command now also
processes account deletion. Requests remove local credentials/sessions and block
new emissions immediately, then expire pending Checkout sessions, cancel owned
subscriptions, delete the dedicated owner-verified Stripe customer as a final
fence against an in-flight Checkout, and delete the exact Auth user. External failures remain queued;
a generic 404 is not taken as proof of identity deletion. Stripe invoice records
remain subject to its required retention; local free-trial eligibility markers
retain their independently published expiry.

## Explicit legacy migration

The candidate CLI supports `socialcli account migrate youtube --brand MyBrand
--no-browser` after `account login`. Give the printed link directly to the user;
only their browser may open and confirm it. The service requires both the old
activated capability and an approved installation belonging to the browser user.
It does not infer ownership from brand names or email addresses.

The old connection remains usable until the client stores the replacement and
activates it. The activation transaction rechecks the legacy proof, takes its
latest provider grant and removes the legacy record atomically. Lost activation
responses use `socialcli account recover youtube --brand MyBrand`; do not repeat
OAuth. Abandoned or expired migration attempts leave legacy access unchanged.
The transfer must use the same vault and keys. The client permits only matching
service origins or the fixed one-way transfer from `https://social.florentino.pro`
to `https://socialcli.app`; arbitrary service changes cannot receive an old secret.
Deploy the reviewed shared-vault service before attempting a real migration.

## Installation credential rotation

Use `socialcli account rotate --brand MyBrand` before this installation's 90-day
expiration. This explicitly replaces its installation credential and locally
installed SaaS capability secrets. It neither repeats provider OAuth nor extends
the free trial or provider-grant inactivity period. Other installations keep
working. The preparation has a 600-second window; all replacement secrets and
an encrypted local recovery journal are saved before the server atomically
invalidates the old installation.

After an interrupted request or local write, rerun the same command. While the
journal is present, token emission and connection changes are blocked locally.
Use `--cancel` to abandon an uncommitted rotation. A committed active replacement
cannot be cancelled locally; resume it. If both old and replacement credentials
are confirmed inactive, cancellation clears only the known local credentials,
allowing `account login` and `account attach` without new OAuth when the provider
grant remains valid. Network uncertainty never authorizes deleting the journal.

## Provider renewal outcomes

SaaS renewal records a durable lease before contacting Google or TikTok. Only a
connection/pool failure known to precede sending the request permits another
attempt after 30 seconds. A confirmed OAuth client/configuration rejection permits
retry after 300 seconds once the operator fixes configuration. No background
retry is triggered by status, the account page, or a `refresh=false` read.

Confirmed `invalid_grant`, expired nonrenewable authorization, or reduced
permissions require explicit user consent for replacement. A timeout while
writing/reading, an unrecognized error response, invalid success body, or a lost
process may mean a rotating refresh token was consumed. These become
`renewal_uncertain`, not a permanent misleading `renewal_pending`. A live lease
is shown as pending for 120 seconds; elapsed time never authorizes replay. The
original worker may still finish and commit its verified response. Otherwise the
owner can approve a new connection; replacement activation atomically retires the
old grant. Do not clear a lease or error manually to force another exchange.

Account status returns `renewal_status` and sets `needs_reconnect` only for known
provider expiration/revocation or permissions changes. The shared account UI and
CLI distinguish these from network and configuration problems. Provider error
bodies, tokens and request URLs are never used as diagnostics. This applies to
`/api/saas/v1`; legacy `/v1` renewal behavior is unchanged and must remain within
the reviewed legacy cutoff/rollback constraints.

References: [HTTPX timeouts](https://www.python-httpx.org/advanced/timeouts/),
[Google token expiration](https://developers.google.com/identity/protocols/oauth2),
and [TikTok OAuth errors](https://developers.tiktok.com/doc/oauth-error-handling/).

## Trial reminders

The same isolated maintenance command now owns the trial reminder outbox. Enable
`SOCIALCLI_REMINDERS_ENABLED=1` only in the reviewed private service configuration,
using `mail.env.example` and the existing dedicated Stalwart mailbox password.
SMTP uses verified STARTTLS and the fixed sender `SocialCli <access@socialcli.app>`;
credentials stay outside Git. This switch does not change Auth's OTP delivery.
The reminder templates use the shared UI tokens and are included in the wheel.

Metadata migration 6 adds one durable job per user/trial deadline/milestone.
The 3-day reminder is valid until the 1-day window begins; delayed maintenance
sends only the current window, never stale reminders after trial expiry. Payment,
suspension, account deletion, a changed deadline or missing verified identity
cancel delivery. Eligibility and the current email are checked again immediately
before SMTP DATA, without holding a SQLite transaction during network calls.
An account change after DATA has begun cannot recall an in-flight message.

A 300-second preparation lease may be reclaimed after a crash. A durable `sending`
marker is committed before DATA: lost acknowledgements or crashes thereafter
become `uncertain` and are never automatically replayed. A confirmed temporary
SMTP rejection retries after 300 seconds with the same Message-ID; confirmed
permanent recipient/DATA rejection remains visible as `rejected`. A Message-ID
alone is not a guarantee that an SMTP relay deduplicates submissions. `accepted`
means SMTP acceptance, not inbox delivery. Use the private relay logs to investigate
uncertainty; do not clear it or force a resend without proving non-delivery.

Maintenance JSON exposes counts only, including delayed retries and uncertain or
rejected jobs; these cause exit 1 for operator attention. Future scheduled jobs
are not failures. Disabling reminder delivery still runs lease recovery, receipt
expiry and backlog reporting; the switch stops SMTP submissions only.
SQL stores IDs, deadlines and states, not addresses or bodies;
recipients are read from the encrypted identity at sending time. Minimal receipts
expire 30 days after trial end. Reminders require ASCII SMTP envelope addresses.
The public account page remains the recovery path if mail delivery fails.

No actual trial reminder or timer activation is claimed by the unit fixtures.
Auth's separately confirmed inbox test establishes the existing route, not this
new message's rendering across all email clients or its live delivery.

## Recovery

Use the [backup and isolated restore guide](backup-restore.md) for the shared
SQLite metadata/vault and the separate Auth database. The snapshot utility never
restores over a live service or processes an external operation. Scheduling,
off-host storage and recovery alerting remain rollout requirements.

After an installation logout or expiry, sign in again and use `account attach`
to restore the same approved account without repeating OAuth. Existing local
capabilities are replaced only after their installation is confirmed inactive.
While any connection recovery is pending, finish `account recover PLATFORM`
before changing that brand or signing out.


## Schema 8 and temporary media rollout

See [temporary-media.md](temporary-media.md). Billing mode must explicitly match
the Stripe key and object livemode; keep `SOCIALCLI_BILLING_MODE=test` in the pilot.
Live mode requires a live price, webhook secret, customer configuration and
a separately verified rollout. Test customer IDs are never accepted as live objects.
Account sessions rotate encrypted refresh tokens within a fixed 12-hour lifetime.
Ownership lookup uses schema 8; do not run schema-7 binaries against that database.
