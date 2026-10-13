# SocialCli SaaS execution

Approved master: `2026-10-13-socialcli-saas.md`. Execute natively in an isolated branch. External rollout, PyPI publication and live billing require a reviewed artifact.

### Task 1: Shared UI
- [ ] Preserve approved connection layout; extract canonical design tokens and shared components.
- [ ] Build static web and packaged connection bundles from the same inputs.
- [ ] Test generated output, assets over HTTP, packaging and existing flows.
- [ ] Document email and hosted Stripe branding; prepare visual fixtures and review.

### Task 2: Identity and web accounts
- [ ] Add versioned SQLite metadata and verified Supabase email OTP adapter.
- [ ] Test request limits, browser-bound attempts, local revocation, CSRF, expiry, cross-user isolation and logout.
- [ ] Render all account states using shared UI.

### Task 3: Agent pairing and owner-bound connections
- [ ] Separate 30-minute installation pairing from 600-second provider OAuth.
- [ ] Implement browser selection, scoped installations, recovery and explicit migration.
- [ ] Add CLI login/status/logout without leaking browser sessions.

### Task 4: Trial and plan limits
- [ ] Atomic one-time 14-day trial, one brand and four platform slots.
- [ ] Test concurrency, replacements, expiry and eligibility retention.

### Task 5: Stripe test billing
- [ ] Validate fixed USD 10/month price; idempotent checkout/portal.
- [ ] Persist verified webhooks and reconciliation jobs, then process outside transactions.
- [ ] Exercise signed test events and subscription transitions.

### Task 6: Token gate and maintenance
- [ ] Enforce entitlement, ownership, revocation and commercial legacy cutoff.
- [ ] Idempotent deletion/reminders/reconciliation, isolated maintenance service and restore test.

### Task 7: Distribution and pilot
- [ ] Package candidate and installation/agent documentation; prepare OIDC workflow.
- [ ] Validate installed wheel outside checkout, complete simulated flow and visual review.
- [ ] Review branch; report external pilot/publication prerequisites without claiming deployment.
