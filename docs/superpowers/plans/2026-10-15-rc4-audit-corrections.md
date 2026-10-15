# rc4 audit corrections implementation plan

> **For agentic workers:** Use Superpowers systematic debugging and regression-first verification. Independent backend, engagement and CLI work is delegated under dispatching-parallel-agents; integrate and review before release.

**Goal:** Correct verified A-01–A-08 and agent usability findings without weakening ownership, approval or uncertain-write safeguards.

**Architecture:** Retain one canonical main checkout, encrypted SaaS broker and locally executed client. Bound database operations by indexed ownership; keep exact immutable approvals, explicit noninteractive inputs and durable provider receipts. Release changed code under a new immutable candidate, never overwrite rc4 artifacts.

**Tech stack:** Python, Typer, FastAPI, SQLite, Docker Compose, systemd; official provider APIs.

**Spec:** User audit supplied October 15, 2026: A-01–A-08 plus batch, inbox, noninteractive approval, preflight and diagnostic findings.

## Constraints

- Product copy in English; operator conversation in Spanish.
- Work in app/SocialCli on main, preserving all existing creator data and uncommitted root changes.
- No live social writes required for regression tests. Never replay uncertain writes or relax own-comment deletion verification.
- Stripe remains test; do not submit provider reviews. No new large local backups.
- External provider limitations must be documented honestly, not bypassed with invented APIs.

## Review focus

- Cancelled media upload with a worker still holding an FD: never release quota before all local writes are finished.
- Account deletion/refresh racing with concurrent requests: no revoked session resurrection.
- Legacy orphan records during ownership-index migration: preserve deletion guarantees without scanning globally per request.
- Open nonTTY stdin: fail promptly unless explicit stdin approval opted in.
- Partial accepted batch: preserve receipts, perform bounded read-only verification, never resend accepted children.

## Tasks

### 1. Deployment definitions (A-01, A-02)
- [x] Verify active timer/service and composed image; distinguish historical files from deployed faults.
- [x] Remove obsolete overlay image override; align documented service pair/container and prevent regression with deployment composition/unit tests.
- [x] Check merged Compose and live systemd maintenance without exposing env secrets.

### 2. Indexed lifecycle, upload cleanup and web auth (A-03–A-06)
- [x] Add failing bounded-decryption tests for rotation, account deletion and pruning, including historical unowned pairs/logins.
- [x] Implement indexed associations/migration and read exact candidate records only.
- [x] Add failed/cancelled upload quota tests; release only after writer closure and filesystem cleanup, preserving concurrent DELETE safety.
- [x] Test concurrent refresh using unexpired access for reads, token expiry and logout fences.
- [x] Distinguish invalid email HTTP400 from identity outage HTTP503, retain abuse rate limiting.
- [x] Run backend regression suites and record schema/deploy implications.

### 3. Approval, credential storage and preflight (A-07 plus CLI findings)
- [x] Reproduce nonTTY hangs with open input; add explicit approval flags/helper and opt-in stdin, preserve all exact-digest checks.
- [x] Use English --only diagnostic; test secure 2700 directories without allowing group/other access.
- [x] Provide read-only combined preflight using existing brand, connection, scope and media validators; report unverified scopes honestly.
- [x] Document prepare creates fresh IDs/digests and commands for noninteractive approval; run CLI suites.

### 4. Engagement correctness and diagnostics
- [x] Reproduce delayed batch child readback and unknown Facebook author errors with fictional fixtures.
- [x] Complete known successful children via bounded read-only reconciliation; preserve sequential stop for true uncertainty.
- [x] Name failing child, platform, target and actionable reason in sanitized errors/JSON.
- [x] Allow replies to exact verified comments with hidden author when API permits, retaining stricter ownership checks for edit/delete.
- [x] Implement nested reply selection/parent mapping only where supported; test and document provider limits.
- [x] Keep stdout machine-readable and stderr progress bounded; run engagement suites.

### 5. Canonical brand workspace (A-08)
- [x] Inventory root uncommitted source, scripts and active scheduler references; preserve exact originals in a private compressed archive before removal.
- [x] Compare operational differences and ensure canonical CLI handles existing data/flags.
- [x] Replace root project metadata with dependency on canonical app; remove duplicate importable source/tests from root only after hash-verifying archive.
- [x] Update workspace instructions/active launcher paths, verify root CLI resolves canonical source and dry read-only commands work.

### 6. Integration, docs and release
- [x] Review integrated diff, run full suite, build/check clean archives and smoke installed wheel.
- [x] Update capability/agent/help and audit closure docs with evidence and remaining provider limits.
- [ ] Publish new immutable candidate through existing protected workflow; deploy after verified backup and check migrations, health, maintenance, public guide/version.
- [ ] Do not claim external release approval, live social acceptance or finished work without matching evidence.
