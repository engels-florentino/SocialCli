# Stripe test billing — implementation and verification

Status: implemented in `feat/socialcli-saas`; not deployed to the public service.
This is the billing phase, not completion of the full SaaS launch plan.

## Behavior

- Original 14-day trial begins on first successful social connection. Verified
  recreated identities inherit previous eligibility; login never starts a trial.
- One USD 10/month subscription. Checkout appears after trial expiry and any
  valid paid period; no card, Stripe trial or advance charge during the free trial.
- Browser actions use verified identity and same-origin CSRF. Request-supplied
  customer IDs, prices and success query strings cannot grant access.
- Durable customer/Checkout intents, per-user leases and Stripe idempotency
  prevent duplicate purchases. Canonical reads recover lost creation responses;
  uncertain writes older than 23h require operator review rather than duplication.
- Signed raw webhook bodies are persisted with their pending jobs before 200.
  Canonical subscription/invoice reads resolve duplicates and out-of-order events.
  The isolated maintenance timer reconciles every five minutes in fair batches.
- Paid access, cancellation date and payment recovery appear in the shared account
  UI. Existing connections recover after verified payment, without another OAuth.
- Refunds/disputes are recorded for review. No automatic refund or suspension.

## Stripe objects created (test only)

- Product: `prod_VQyiLnDgKC71xh` — SocialCli.
- Price: `price_1UQ6lREGnZhvTl38KMj7cRR3` — USD 1000 cents, month, interval count 1.
- Dedicated Portal configuration: `bpc_1UQ6lREGnZhvTl38X2QDwtHd` — invoices, payment
  methods, cancellation at period end; plan changes disabled.
- API version pinned to `2025-09-30.clover`. Configuration rejects live keys.

Secrets are stored only in the ignored private working directory, mode 0600;
no API or endpoint secret is included here or in the CLI package. The actual
endpoint signing secret is intentionally pending the HTTPS service deployment.

## Evidence

Final full suite: **2259 passed** in 41.43s. One existing upstream
Starlette TestClient deprecation warning remains. UI build consistency,
`git diff --check` and a private-key scan passed.

Fictional HTTP fixtures exercise fixed price validation, pre-trial/active-trial
rejection, concurrent Checkout, lost responses, wrong owner, changed session
line items, active/canceled/past-due/unpaid/paused/trialing states, invoice binding,
signature/timestamp/mode/account/API-version rejection, durable retry, duplicate
and out-of-order webhooks, periodic recovery and fair work scheduling. An
integration test verifies the social token route blocks after expiry, unlocks
only after payment reconciliation, blocks on impayment and recovers after payment.

Real Stripe **test API** checks passed:

- Hosted Checkout session and Customer Portal session creation.
- Paid subscription invoice -> local paid access.
- Cancellation at period end -> paid access preserved until its exact end.
- Local decision at paid-period boundary -> access denied.
- Declined saved card -> `incomplete`, no access.
- Authentication-required card -> `incomplete`, no access.
- Synthetic test subscriptions canceled after verification. No emails or live
  payments were issued. Paid subscription creation used the API; it does not
  claim completion of the hosted browser payment flow or its 3DS challenge.

Eighteen offline panel views (six states at 360, 390 and 1440px) passed horizontal
layout and keyboard-focus checks; desktop CSS 200% zoom was checked. Mobile
expired-trial view was visually inspected with the packaged logo and styles.
These checks do not cover Stripe-hosted pages or actual receipt email appearance.

Independent read-only billing review found two Important issues: a customer
creation timer starting too early, and reconciliation starvation. Both were
reproduced with failing tests and fixed, including failed-customer/event fairness.
The follow-up reviewer verified the fixes and reported no outstanding Critical
or Important issue within the billing scope.

## Rulings and remaining launch work

Already-paid access is preserved through its verified end even when Stripe marks
cancellation effective earlier. Cancellation does not itself imply a refund.
The spec table was clarified to match this existing precedence rule.

Public deployment, endpoint registration/secret and timer activation remain
pending. Validate real delivery/retries and finish a hosted Checkout round trip
with a test card after deployment. Receipt/Portal visual branding can inherit
shared Stripe account settings; do not change other products' branding blindly.
The test API/fixture work does not prove this hosted visual approval.

The full SaaS launch still needs legacy commercial cutoff/migration, remaining
installation/account lifecycle work, email DNS/delivery, distribution and the
real Muse/Grok pilot. Policy for refunds/disputes and provisional 365-day retention
must be finalized before live billing. Existing SocialCli 0.2.6 remains unchanged
on the public server; this work is not published to GitHub/PyPI yet.

Documentation consulted with Context7 and official Stripe references:
[Checkout](https://docs.stripe.com/api/checkout/sessions/create),
[webhook verification](https://docs.stripe.com/webhooks),
[Portal configuration](https://docs.stripe.com/api/customer_portal/configurations),
[test cards and saved-card declines](https://docs.stripe.com/testing).
