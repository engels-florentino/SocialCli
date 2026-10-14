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

## Verified checkpoints and remaining rollout

- Private Auth: actual inbox delivery, isolated PostgreSQL restore with all28table
  digests matching, fresh-volume migrations, restricted runtime, real candidate
  adapter OTP/lookup/idempotent deletion with a fictional user. No new real mail.
- Vault: consistent SQLite online snapshot and restored application boundaries;
  protected key separation documented. Candidate/Auth encrypted hourly backups
  now active, exact local snapshot pair restored offline. Drive off-host access,
  independent off-host password recovery, retention and stale alerts remain.
- Candidate distribution: 0.3.0rc1, packaged `/agents`, explicit version discovery,
  protected OIDC workflow, archive allowlist/verifier and clean-install smoke work.
  Headless storage revealed installation-slot and backend migration gaps; fixed
  with real encrypted-store CLI tests, including interrupted pairing acknowledgement.
- Before completion: private readiness/alerts and bounded maintenance; scheduled
  backup and actual recovery destination; HTTPS candidate and browser session flow;
  hosted Stripe test Checkout/webhook/Portal; public proxy cutoff/rollback rehearsal;
  TestPyPI/PyPI publisher setup and downloaded artifact verification; real Muse/Grok
  pilot; final whole-branch review and approved public rollout.
- Google production and live billing remain a separate, explicitly deferred phase.

A green unit suite or local package does not mark these external requirements done.
