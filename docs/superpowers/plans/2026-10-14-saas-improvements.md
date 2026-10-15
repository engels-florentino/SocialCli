# SocialCli SaaS improvements — October 14, 2026

User-authorized scope: the seven product findings and technical findings H01–H08.
Work on canonical main; all product language English and existing UI components.
Preserve provider consent, encryption, duplicate prevention and durable recovery.

1. Schema 8 owner index, WAL with read transactions; release an empty brand and
   add CSRF-protected account rename; expiry-aware rate bucket pruning and
   trusted-proxy-only real-IP handling. Tests: storage upgrade/lifecycle/peer.
2. Early Checkout with original trial-end billing anchor and no prorations,
   USD10 monthly price and fixed idempotency parameters. Permit invoice credits
   and tax lines while verifying the subscription price/period. Explicit test or
   live mode with matching keys/objects; keep runtime in test. Ignore foreign
   events and remove reconciled blobs, retaining semantic/raw hashes. Tests:
   billing/webhooks, network outside DB locks, cross-mode/owner fences.
3. Twelve-hour absolute web sessions, encrypted rotating refresh token, 45-second
   identity cache. No resurrection after logout/deletion or replay after ambiguous
   rotation. Tests: auth, lifecycle, concurrency and provider failures.
4. Authenticated temporary original-file upload on florentino.pro for Instagram:
   private storage outside backups, quotas, checksums, bounded streaming,
   owner-bound uploads/deletes and opaque HTTPS downloads with exact expiry at
   900 seconds after reservation. CLI cleanup on operation end; application
   janitor plus independent host timer/startup cleanup. Upload local scheduled
   jobs at dispatch, not when scheduled. No transformations or archives. Test
   upload/download/range/expiry, failures, quota, restart and adapter lifecycle.
5. Compact /agents bootstrap, packaged topic guides and complete offline manual.
   Update privacy/terms for SaaS, temporary media and trial markers, consistent
   Account navigation. Verify generated UI assets and offline CLI topic tests.
6. Run full suite, review complete diff with fresh reviewer and resolve material
   findings. Build immutable future candidate (never overwrite rc3). Verify
   backup before deployment, temp storage excluded, proxy forwarding/body limits,
   expiry timer, actual server revision, connections and DNS. Document external
   production approvals accurately; do not submit TikTok review or switch billing
   live. Protected package environments require owner approval if encountered.

Technical references: Stripe Checkout billing-cycle documentation and Supabase
Auth REST documentation fetched through Context7/official documentation. Checkout
trial_end requires at least two days; a future billing_cycle_anchor with
proration_behavior=none preserves the original trial even in its final 48 hours.
