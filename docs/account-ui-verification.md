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

`manifest.json` identifies each page's source. Actual route responses cover
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

## Still required for full UI acceptance

Generating the catalogue is not acceptance of every page. Review all states at
all three widths, including the lower portions of long pages, 200% browser zoom,
reduced motion and keyboard traversal. Complete website/pricing/installation,
email-client and provider-specific fixtures plus the real authenticated journey
through Stripe and social providers. Preserve the real journey's required owner
approvals. Screen-reader announcement testing remains separate from inspecting
ARIA attributes. The master plan's full UI acceptance remains open.

These fixes have not yet been deployed or published. They do not change account
permissions, authentication decisions, subscription pricing or media storage.

## Independent review

One read-only fresh-context review found no Critical or Important issues and
accepted this increment. A Minor improvement remains: route fixture capture
checks HTML content type but does not consistently assert the expected HTTP
status and a distinguishing page marker. Until that is strengthened, inspect
fixture content against its manifest source; generation alone is not success.

Review boundaries remain requirements, not exemptions: complete width/zoom/
reduced-motion/keyboard acceptance is still open; native appearance was checked
by the executor, while actual screen-reader announcements remain open; website,
email and provider-specific coverage remains open; real payment/provider flows,
deployment and complete SaaS acceptance remain open.
