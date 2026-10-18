# Billing and reminder cutover

October 18 runtime observation: billing enabled, configuration valid, mode
`test`; reminder sending disabled and its SMTP configuration absent. Authentication
mail uses a separate configuration. No live payment was attempted.

## Storage boundary

Stripe events are already keyed by account, mode and event ID, and incoming
objects are checked against `expected_livemode`. Customer/subscription pointers
in `saas_billing` and paid entitlements are not independently partitioned by mode.
Changing environment variables alone therefore cannot perform a safe cutover.

The candidate binds each database to one Stripe account and mode in an encrypted
`saas-billing-environment` record. Startup rejects another account/mode before
provider calls. Any unbound database with billing rows, customer or Checkout intents, events,
or paid entitlements rejects startup in both test and live modes. Missing event
history does not establish the original account; even matching event history
cannot establish ownership of every legacy pointer or uncertain intent. Only an
empty billing database initializes automatically.
This guard prevents accidental reuse; it is not a billing-data migration.

Commercial cutover requires a separately reviewed, offline migration that retains
identity, OAuth grants, deletion authority, reviewer/owner access and original
uncertainty records while distinguishing test payments from deliberate access
grants. Do not delete or overwrite the environment record to bypass the guard.
No such migration has been applied to production.

## Required operator sequence

Before deploying this candidate to an existing unbound billing database, complete
a separately reviewed legacy enrollment backed by evidence of the known original
Stripe account and mode. Preserve all state, including incomplete and uncertain
intents; do not delete rows, guess the prior account, or use an environment bypass.
The candidate deliberately provides no automatic enrollment or override. Deployment
remains pending until that procedure has been reviewed and rehearsed.

1. Reconcile existing test checkout/customer/subscription intents. Preserve
   pending, uncertain and review events; take a verified encrypted backup and
   capture the exact previous image, configuration and database schema.
2. Review the mode-aware migration and explicit owner/reviewer access inventory.
   Rehearse it against an isolated copy with current deletion/revocation authority.
   Preserve trial start/end; test payments must not become live paid access.
3. Configure matching live key, live price, account, billing-portal configuration
   and live endpoint signing secret. Keep API version `2025-09-30.clover` until a
   separately tested upgrade. Webhook secrets are endpoint-specific, not API keys.
4. Validate `/account`, the active USD 10 monthly licensed price, object mode and
   portal configuration read-only. Verify signature rejection and canonical
   reconciliation workers before opening Checkout.
5. Complete functional/provider/commercial gates. A live payment needs specific
   owner authorization. Reconcile that exact transaction and retain its receipt;
   sandbox events do not establish live payment success.
6. Observe checkout, canonical entitlement, renewal/cancellation state, deletion
   workers, backup freshness and independent monitoring before wider access.

## Billing lifecycle

Canonical Stripe reads determine entitlement; event arrival order does not.
Duplicate event IDs retain their durable identity and cannot replay a purchase.
Paid access ends at the verified paid-through time; failed renewal does not grant
another period. Scheduled cancellation preserves paid access through that time.
Account deletion closes hosted payment sessions, cancels owned subscriptions and
deletes the dedicated customer with durable cleanup retries.

Refund and dispute events enter operator review without automatic cancellation
or refund. The commercial refund policy still needs an explicit owner decision
before launch; this document does not create a policy or authorize transactions.

## Trial reminders

Before enabling `SOCIALCLI_REMINDERS_ENABLED`, inspect the due queue using counts
and milestone/state only. Do not expose addresses or send a test batch to users.
Configure the reminder transport's `SOCIALCLI_SMTP_*` independently of Auth mail.
Reuse the verified owner backup-alert delivery receipt for the shared mail
transport; it does not prove that the trial reminder queue is enabled.

The existing outbox covers three-day and one-day milestones. Missed windows and
paid/deleting accounts are excluded. Rejected and uncertain sends retain their
state; uncertainty is not automatically replayed. Fictional-clock/sender tests
verify these boundaries. A new real email test, if necessary, must be isolated
and owner-addressed with explicit messaging authorization.

## Rollback

Before the first live charge, roll back application code/configuration only if
the database is compatible, preserving the mode binding and all state. After
live activity, do not switch the same database back to test or restore an old
snapshot over it: that could discard paid entitlements, uncertainty or deletion
authority. Close new checkout traffic, retain the live reconciliation worker,
and deploy compatible previous code or a forward fix. A schema-aware restore
must use the existing quarantine/current-authority workflow in isolation.

Stripe's [API key documentation](https://docs.stripe.com/keys) confirms that each
mode has separate objects and keys. Context7 resolve/query and the official
documentation were checked October 18. Credentials and customer identifiers
belong only in private operational records.
