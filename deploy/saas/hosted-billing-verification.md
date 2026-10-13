# Hosted Stripe Sandbox verification — 13 October 2026

Performed in Safari against the isolated candidate with a newly generated fictional
Auth owner and Stripe customer. Its trial was set to have elapsed in the isolated
vault only, allowing the normal `BillingService.checkout` path to run. No social
accounts, real card, real funds or real-recipient email were used. Link saving was
deselected. The generated fixture is retained privately for the next webhook and
browser-return checks; its subscription is scheduled not to renew.

## Verified hosted behavior

- Stripe Sandbox Checkout displayed SocialCli, USD 10 per month and one brand / four
  platforms. Submitted Stripe's documented 4242 test card with fictional details.
- Stripe reported Checkout `complete`, payment `paid`, subscription `active` and a
  validated paid invoice. Canonical SaaS reconciliation granted `paid` access through
  13 November 2026, without relying on the return URL.
- Hosted Portal displayed the paid USD 10 invoice, card management and cancellation;
  plan changes were not offered. Its cancellation confirmation retained service
  through 13 November. After confirmation, Portal visibly showed “Cancels Nov 13”.

## Defect found by the real test

Stripe's pinned Clover API returned `cancel_at=1794613354` and
`cancel_at_period_end=false` for the Portal cancellation. The item period ended at
that same timestamp. The dedicated Portal configuration confirmed `at_period_end`.
SocialCli originally read only the boolean, so its own UI omitted “Will not renew”.
Two canonical reads reproduced the mismatch; this was not a redirect/cache delay.

The regression test recreates that response, asserts the account page notice and
preserved paid access, then clears `cancel_at` and confirms the notice disappears.
It failed before the fix. Reconciliation now recognizes a positive integer
`cancel_at` as scheduled cancellation as well as the original boolean. Non-timestamp
values do not set the notice. This changes display metadata, not paid-period access.

## Open rollout issues

- Checkout title/authorization and Portal business name still say **FloRent**.
  Session branding correctly says SocialCli, but the account-wide merchant identity
  remains FloRent. Do not rename a shared Stripe account to fix one product without
  the user's decision; a dedicated SocialCli account is the clean separation.
- Successful payment returned to `https://socialcli.app/account/?payment=processing`,
  currently a 404 because the public domain still serves the old static site. The
  isolated candidate has the account route, but public proxy/browser return is not
  deployed. Requested DNS `pilot.socialcli.app` for the separate hosted pilot.
- No actual signed Stripe webhook delivery has reached this candidate yet. This
  test proves hosted Checkout/Portal and canonical reconciliation, not webhooks.
- Full OTP/browser registration and Muse/Grok installation still need the hosted
  pilot route. No production/live Stripe configuration was changed.

References: [Stripe subscription cancellation changes](https://docs.stripe.com/changelog/basil/2025-07-30/cancel-at-enums),
[Portal business identity](https://docs.stripe.com/customer-management/configure-portal).

Regression verification: focused billing/webhook tests **40 passed**; full suite
**2,401 passed**, one upstream Starlette deprecation warning (48.60 seconds).

The fix was deployed to the isolated candidate at checkpoint `fe9700c`, after a
verified SQLite snapshot. Reconciliation of the same real Sandbox subscription
now records scheduled cancellation, preserves paid access through Nov 13 and
reports no billing review. Both candidate and original public containers remain
healthy. The screenshot/UI steps establish Stripe's hosted flow; the public
SocialCli return and delivery of a webhook are still unverified.
