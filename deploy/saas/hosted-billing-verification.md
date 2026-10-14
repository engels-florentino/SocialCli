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
- At the original13October checkpoint, no signed webhook had reached the candidate.
  The14October CLI-forwarded receipt below now verifies that path; a permanent
  public HTTPS webhook endpoint remains unverified.
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

## Real signed test event — 14 October 2026

Used the existing fictional hosted Checkout owner and its active test subscription,
with scheduled cancellation preserved. A single diagnostic metadata key generated
an actual `customer.subscription.updated` event in Stripe. The request used a
persisted attempt marker and idempotency key; the subscription price/items, latest
invoice, billing anchor, trial date and cancellation fields were read before and
after and verified unchanged. No real card, charge, email or social action occurred.

Stripe CLI1.53.1 was downloaded directly from its official GitHub release and the
archive SHA256 checked against release metadata:
`032fe0b849a9d6a222488bc846445bafeae993af1026b78a8121d02321ca1fd1`.
Its API key stayed in the server process environment. The CLI signing secret was
captured privately and replaced only the placeholder `STRIPE_WEBHOOK_SECRET` in
candidate configuration. Image, database, public service and provider settings
were unchanged. Candidate billing/backup services were drained during recreation;
maintenance was deliberately paused to observe receipt before processing.

The temporary listener subscribed only to `customer.subscription.updated` from
the test account and forwarded to loopback8789 with Host socialcli.app. TLS to
Stripe remained enabled; no public listener/port or permanent webhook endpoint
was created. Account event version matched pinned `2025-09-30.clover`.

Observed evidence:

- CLI recorded the Stripe event and HTTP200 from the deployed candidate route.
- Exactly one matching event was found in SQLite with state pending and its
  encrypted original parsed payload saved, before maintenance started.
- Actual systemd maintenance reconciled canonical Stripe state and changed that
  event to done. Paid entitlement, paid-until date and cancellation notice remained
  identical; no billing-review issue was introduced.
- A separate forged-signature request returned400 without creating an event row.
- Listener termination was verified; its temporary output was sanitized to remove
  the signing secret. Private receipts and the attempt marker remain on the server.
- Both candidate timers were resumed and systemd maintenance returned0.

This proves actual Stripe-to-CLI-to-candidate signed receipt, durable queueing and
canonical processing. It does not prove direct public HTTPS delivery, DNS/TLS,
Stripe retry/redelivery, 3DS recovery or a full external agent onboarding flow.
The configured secret belongs to this CLI test session/device; a future permanent
Stripe webhook endpoint needs its own signing secret and delivery verification.

During cleanup the WAN address changed and public-name SSH timed out. The same
host was positively identified by its matching stored SSH host-key fingerprint and
reached over its existing LAN address. The app stayed healthy; no duplicate Stripe
trigger was sent. Cleanup and maintenance resumed over that verified connection.

Sources checked through Context7 and official code:
[CLI listen](https://github.com/stripe/stripe-cli/wiki/Listen-command),
[custom Host forwarding](https://github.com/stripe/stripe-cli/blob/v1.53.1/pkg/proxy/endpoint.go),
[subscription metadata update](https://docs.stripe.com/api/subscriptions/update).


## Public HTTPS pilot — 14 October 2026

The separate pilot is live at https://pilot.socialcli.app. The owner completed
real Safari email OTP sign-in and reached the English account page. It showed
USD 10/month and a 14-day trial awaiting the first connection, with no card
required. No social accounts or assistants have been linked by this verification.
The account-form Referrer-Policy defect was fixed in c19e811, with strict Origin
and CSRF validation retained; the full suite passed 2770 tests.

A permanent Stripe test webhook now targets the pilot billing route, subscribes
to the 14 supported event types and pins 2025-09-30.clover. Its signing secret is
stored only in private server configuration and loaded into the healthy candidate.
A diagnostic metadata update on the existing fictional subscription produced a
real signed direct HTTPS delivery. The event was observed pending in SQLite,
then done after actual systemd maintenance. Price/items, invoice, billing anchor,
trial end, status and scheduled cancellation fields were verified unchanged.
A forged signature returned400. No real payment or customer email was generated.

Manual redelivery exposed a real bug: Stripe changed pending_webhooks from3 to1;
the raw-body hash guard rejected this as a conflicting event. Stripe Workbench
confirmed HTTP400 and “Conflicting Stripe event.” Parsed-event comparison found
only that top-level delivery counter changed. The fix preserves raw signature
verification and the original encrypted audit record, while accepting equivalent
JSON content with a changed delivery counter. All other event-content differences
remain rejected. Regression cases failed before the fix and pass afterwards.
Deployment and successful Stripe redelivery must be recorded separately below.


The fix passed all2772tests (one existing Starlette deprecation warning) and an
independent incremental review with no findings. Image054bfd1 was built offline
from the previous verified candidate dependencies and deployed after a successful
local/Drive backup. No schema or public-main service change was required.

A second manual resend after deployment was observed in Stripe Workbench as
**HTTP200, Delivered, Recovered**, with response `{"received":true}`. SQLite still
contained exactly one matching event in done state. The original failing attempts
remain visible as historical evidence. This verifies direct HTTPS delivery and
real Stripe redelivery, without duplicate processing. Fresh encrypted backups were
requested after activation to protect the permanent webhook configuration.

Still open: hosted Checkout/Portal return through the pilot origin, 3DS recovery,
merchant identity separation (shared account still FloRent), real agent onboarding,
and production provider/live-billing approval. The old return-URL404 paragraph above
is historical; current pilot routes and real OTP are verified, but that alone does
not establish the hosted payment return journey.
