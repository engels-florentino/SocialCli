# Account UI verification

## Reproducible fictional catalogue

From a development checkout with the project's test dependencies:

```sh
uv run python scripts/build_ui_assets.py --check
uv run python -m scripts.preview_account_ui
python3 -m http.server 8766 --bind 127.0.0.1 --directory .superpowers/account-ui-catalogue
```

Open `http://127.0.0.1:8766/`. Select a page and a viewport width of 360, 390
or 1440 CSS pixels. These are actual iframe layout widths, not scaled images
or emulated devices. The surrounding catalogue may scroll horizontally on a
screen narrower than 1440px. Open an individual HTML file directly for keyboard
and browser zoom checks. Stop the server with Ctrl-C after review.

The generator uses the existing fictional identity/provider test fixtures and
temporary databases, never deployment configuration or customer accounts.
It does not send mail, charge money or contact a social provider. Saved pages
omit hidden form tokens, block form submission through CSP and run in a
sandboxed iframe. Existing logo and generated CSS are copied to the catalogue;
no media is generated. CSS URLs include a content hash to avoid stale previews.
The files are development artifacts, not part of the customer package or site.

`manifest.json` identifies each page's source and HTTP status (`null` for direct
renderers). Before saving each route response, the generator asserts its expected
status, HTML content type and a distinguishing page marker. Actual responses cover
login, invalid email, OTP, rejected/expired OTP, unavailable identity/email,
empty and connected accounts, assistant approval/success, YouTube connection,
account selection/confirmation and the account-deletion confirmation. A second
assistant uses a long unbroken name. Direct production renderers cover a long
email and eight subscription states: not started, trial, expired, paid,
cancellation, payment attention, processing and suspended. Subscription fixtures
verify presentation, not Stripe state reconciliation. No deletion is performed.

## Findings and evidence — 2026-10-14

Baseline `ffbe100`. Review used native Safari through the computer-use tool.
Screenshots and accessibility observations are recorded in the work session.

- Invalid email and rejected OTP route responses lacked `aria-invalid` and
  `aria-describedby`. They now identify the corresponding alert by a unique ID.
  Normal forms and a temporary identity-service failure do not mark a field
  invalid. A parsed-HTML check verified all five cases after the edit.
- The existing invalid-input border rule lost to the normal-input selector.
  The shared source now gives the invalid rule sufficient specificity. The
  corrected OTP field and its error border were visually verified at 360px.
- A long assistant name was visibly clipped at 390px although the document
  reported no horizontal overflow. Cards now wrap long text, form columns can
  shrink and permission legends remain within their available width. The same
  name was rendered again without the clipped heading. Scrolling to the bottom
  also verified the complete permission legend, four network controls and
  wrapped approval button at 390px. The catalogue also
  reports overflowing headings, paragraphs, buttons, fieldsets, legends and
  cards; document width alone is insufficient evidence.
- A long email rendered without overflowing blocks at 1440px. The entire
  central card was visible; the outer catalogue viewport was narrower than its
  iframe. The keyboard check on the standalone OTP error page moved from the
  input to Sign in with Safari's Option-Tab; the button had a visible focus ring.
- `uv run python scripts/build_ui_assets.py --check` passed. The full existing
  suite passed: **2703 tests**, one existing Starlette/httpx deprecation warning,
  57.71 seconds. These checks do not replace visual acceptance.

## Catalogue width review and billing follow-through — 2026-10-14

At baseline `e006ab9`, native Safari review covered all 25 catalogue states at
360, 390 and 1440 CSS pixels, including their bottom controls and footers.
The following groups were inspected visually and against their accessibility
content; no document-width or checked-block overflow was reported:

| Group | Catalogue states reviewed at all three widths |
| --- | --- |
| Sign in | `login`, `email-invalid`, `code`, `code-invalid`, `code-expired` |
| Account and pairing | `account-empty`, `assistant-approve`, `assistant-linked`, `youtube-connect`, `account-select`, `account-confirmed`, `account-connected`, `account-delete` |
| Long values and outages | `assistant-long-name`, `email-unavailable`, `signin-unavailable`, `code-long-email` |
| Billing | `billing-trial_not_started`, `billing-trial`, `billing-trial_expired`, `billing-paid`, `billing-canceled`, `billing-subscription_required`, `billing-processing`, `billing-suspended` |

For this pass an ignored local comparison page displayed three independent
iframes at the specified CSS widths, scaled to 50% to fit side by side. Each
iframe expanded to its content height. Scaling the previews does not change
their layout widths, but this is **not** physical-device or browser-zoom testing.
Screenshots remain in the computer-use session; no PNG archive was created.
The committed catalogue's width selector reproduces the underlying layouts.

The review found presentation issues in billing, despite correct wrapping:

- An existing subscription needing payment attention inherited the expired-trial
  heading. It now says “Subscription needs attention”.
- Returning from Checkout with unconfirmed access showed both a payment-checking
  notice and a new Subscribe button. That return state now offers “Check account
  status” and preserves Manage billing when a customer exists. The status link
  removes the return hint and rereads the account; it does not grant access or
  start payment. Normal checkout remains available when eligible. The query
  parameter remains a presentation hint, never proof of payment.
- A suspended account without a billing customer claimed billing management was
  available although it had no corresponding action. That sentence now follows
  the same availability condition as the existing portal button.

All three corrected fixture states were rendered again and visually inspected
at all three widths, with complete text/actions and no reported overflow.
An expanded existing Checkout-return integration test failed before the change
and passed afterward, covering absent duplicate checkout action, retained portal,
status-link recovery and unchanged denied entitlement. Billing/auth/access/
connection-UI checks passed: **64 tests**, one existing Starlette/httpx warning.
A separate six-state renderer probe confirmed that a processing hint does not
override active paid/trial, unstarted, suspended or unavailable account states.
The generated-assets check also passed; CSS was not changed in this increment.
One final independent read-only review found no Critical, Important or Minor
issues in this diff and independently reran the 28 billing tests successfully.
That review checked code and evidence boundaries; Safari inspection was performed
by the executor.

## Connection instructions and keyboard/zoom follow-through — 2026-10-14

At baseline `2b0c399`, the shared landing page still told SaaS users to return
to the CLI to choose their account. The renderer now uses the stored authorization's
SaaS flag to explain that selection and confirmation happen in the browser.
Legacy connections retain their existing instruction. Manual fictional route
probes verified both variants return 200 with the appropriate instruction and
retain the provider form-action policy. The corrected SaaS YouTube fixture was
visually checked at 360, 390 and 1440 CSS pixels, including its footer, without
reported document or checked-block overflow.

The catalogue now checks the expected HTTP status and distinguishing marker of
all 16 route-derived fixtures, addressing the earlier review's Minor finding.
Generation succeeded for all 25 pages. Connection UI/service/SaaS-connection
checks passed: **29 tests**, one existing Starlette/httpx deprecation warning.
The generated-assets check passed; no CSS changed in this increment.

Native Safari's standalone `assistant-approve.html` was inspected at **200%
browser zoom**, verified by the browser's zoom menu. The heading, explanatory
copy, permission legend, controls and footer remained readable without visible
horizontal clipping. Option-Tab traversed home, Skip to content, all four network
checkboxes, Allow, Cancel, Privacy, Terms and Help. Activating Skip to content
moved into the main content; the next Option-Tab reached YouTube. Screenshots
confirmed visible focus on home, YouTube and Help, and keyboard traversal scrolled
the page to expose the controls. No approval form was submitted. This verifies
that standalone fixture in the current desktop window, not every viewport or
catalogue page. Safari zoom was restored to 100%, the temporary tab was closed
and the local preview server stopped. The original seven tabs were preserved.

## Still required for full UI acceptance

The 25-state width review does not cover every product surface or interaction.
Complete the remaining 200% browser zoom and keyboard cases across the catalogue,
and reduced-motion acceptance. Complete website/pricing/installation,
email-client and provider-specific fixtures plus the real authenticated journey
through Stripe and social providers. Preserve the real journey's required owner
approvals. Screen-reader announcement testing remains separate from inspecting
ARIA attributes. The master plan's full UI acceptance remains open.

These fixes have not yet been deployed or published. They do not change account
permissions, authentication decisions, subscription pricing or media storage.

## Independent review

The first read-only fresh-context review found no Critical or Important issues.
Its Minor finding about route fixture status/marker checks is resolved by the
capture assertions described above. Generation still does not replace visual
inspection or authenticated journey acceptance.

A final read-only review of the connection-copy/catalogue increment against
`2b0c399` found no Critical, Important or Minor issues and accepted the change.
The reviewer inspected all four changed files and ran `git diff --check`;
tests and Safari observations were executor evidence, not independently repeated.

Review boundaries remain requirements, not exemptions: complete width/zoom/
reduced-motion/keyboard acceptance is still open; native appearance was checked
by the executor, while actual screen-reader announcements remain open; website,
email and provider-specific coverage remains open; real payment/provider flows,
deployment and complete SaaS acceptance remain open.
