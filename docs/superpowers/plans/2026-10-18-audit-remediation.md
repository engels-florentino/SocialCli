# Confirmed Audit Findings Remediation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This document is a plan, not evidence of implementation, live success or provider approval.

**Goal:** Resolve the confirmed October 18 audit findings, complete the outstanding evidence, and establish separate package-release and commercial-launch decisions without repeating passed live tests.

**Architecture:** Preserve the current credential broker, typed server operations, local media transfer, approval fingerprints and durable uncertainty records. Reconcile existing public routes before adding integrations. Treat a hosted Meta event receiver and independent monitoring as separate infrastructure deliverables; do not turn a catalogue correction into an unreviewed architecture change.

**Tech Stack:** Existing Python CLI, FastAPI, httpx, SQLite, pytest, systemd, Docker and protected release workflows; provider documentation through Context7 and official sources.

**Spec:** `docs/superpowers/specs/2026-10-18-live-qa-gaps-scope.md`; this plan extends `docs/superpowers/plans/2026-10-18-remaining-live-qa.md` for confirmed audit findings. The scope and acceptance decisions below are the audit addendum. They do not replace its private variant ledger.

## Global Constraints

Owner refinement: target a stable release whose offered functions pass their required checks. Remove operations without a supported API or viable implementation; never remove a legitimate API function merely to hide a failed test. See `docs/stable-scope.md`. Preserve the existing record of unverified variants and exact passed exclusions.

- Do not repeat successful live tests; reuse evidence and exclude completed variants.
- Histopast is the authorized brand. Never infer another brand/account.
- Product text is English; operator discussion may be Spanish.
- Never generate, crop or reencode media; use owner-supplied originals.
- Full exact preview and existing explicit approval rules apply to every write.
- Preserve originals, uncertain journals and existing grants. Do not replay uncertain writes.
- New automated tests use fictional accounts; never publish from pytest.
- Do not contact platform support or submit false abuse reports.
- Requested scopes, granted scopes, token type, app approval and live success are separate evidence.
- Use the canonical `app/SocialCli` checkout; no second permanent source tree.
- Preserve unrelated changes and private workspace records. No blanket Git cleanup, blanket staging or deletion of historical evidence.
- A disabled feature or external blocker is not a passed feature. Any launch exclusion must be explicit and must not silently discard the user's outstanding QA request.
- Existing verified operational tests are reused too. Do not send duplicate test emails, repeat restores, reconnect accounts or exercise working payment flows merely because this audit names them; first check the saved evidence and changed conditions.

## Scope and starting evidence

| Finding | Disposition and owner task |
| --- | --- |
| AUD-01 provider approvals | Task 6: verify current dashboards and submission receipts. The Google demo already exists; do not order another recording from October 16 notes. |
| AUD-02 scheduling and Instagram integration | Tasks 2–3: reconcile `dispatch`/`deliver`, existing `submit-api`, and currently internal Instagram delivery. |
| AUD-03 catalogue discrepancy | Task 1: resolve Instagram moderation contract; retain the already-correct Facebook description-only scope. |
| AUD-04 English messages | Task 7: two small corrections, not a launch architecture blocker. |
| AUD-05 stale versions | Task 7: distinguish source, published package, actual runtime, Muse and template versions. |
| AUD-06 receiver and SQLite | Task 4: receiver delivery. Keep SQLite; no migration justified solely by this audit. |
| AUD-07 remaining QA and host alerts | Tasks 5 and 8: reuse the original QA ledger; independent outage detection. |
| AUD-08 Git and legal files | Task 7: scoped commits and actual served-policy checks; Histopast and SocialCli have different policy scopes. |
| Overstated security guarantees | Task 1: accurately describe exported bearer-token authority and local workflow safeguards. Per-action server enforcement is a separate architecture project if the product promises it. |
| Commercial cutover and SMTP reminders in the audit's launch recommendation | Task 9: explicit operational evidence, test/live separation, billing lifecycle, notification delivery and rollback gates. |

Starting evidence: rc16 client and compatible rc15 runtime; release and Muse adoption receipts in the private QA directory. `InstagramDelivery` exists but its presence does not prove a public supported route. `dispatch_ready` defaults adapters/capabilities to `None`; `submit-api` has a separate eligibility gate. Meta receiver helpers explicitly describe offline operation. These are starting observations, not instructions to bypass those boundaries.

## Review Focus

1. A disabled capability reached through a generic command must not write remotely (Task 1).
2. One due job seen by two workers, or restarted after a lost response, must not publish twice (Task 2).
3. Changed media bytes, expired hosted URLs or a carousel with one failed child must not result in a different approved publication (Task 3).
4. Validly signed events for another owner, duplicates and delayed events must not leak data or trigger writes (Task 4).
5. Host-down detection must work when the existing host and its mailer are unavailable (Task 5).

## Task 1: Reconcile the public feature and security contracts

**Files:** `socialctl/capabilities.yml`, `socialctl/management/meta_changes.py`, `socialctl/management/meta_cli.py`, `socialctl/management/meta_schema.py`, `docs/architecture/agent-permission-boundaries.md`, `docs/ai-agents.md`; tests `tests/test_capabilities.py`, `tests/test_meta_management.py`, `tests/test_meta_cli.py`.

**Interfaces:** Preserve `prepare_meta(client, store, raw)` and `apply_meta(client, store, change_id, approval_digest)`. Capability decisions must agree across prepare, apply, CLI help and catalogue; reconciliation must remain available for existing uncertain changes.

- [ ] Compare every audited route against its catalogue and the existing variant ledger. Keep the corrected Facebook description-only contract; do not add title editing.
- [ ] Resolve the exact Instagram moderation API contract through Context7 resolve → query and official references. Record token type, permission, ownership and comment eligibility separately.
- [ ] Add failing fictional regressions: disabled Instagram moderation produces zero mutation requests through generic prepare/apply paths; stale prepared changes cannot bypass a new deny; read-only reconciliation remains possible.
- [ ] Run the new regressions red, then implement consistent enforcement. Do not merely flip `enabled` to true. Avoid a circular gate requiring live success before any authorized test can run: after contract and automated checks pass, define a candidate-only pilot state bound to the exact approved installation/account/operation, denied to other tenants and absent from general-availability claims. Exercise the ordinary validated public route under that policy, not raw HTTP or a hidden bypass. Promote the scope to general availability only after its required provider and live evidence exists. If no such pilot mechanism exists, design and test it before the live attempt.
- [ ] Correct any product claims that fingerprints cryptographically prove human approval, prevent media generation, or constrain all uses of exported provider tokens. Preserve the existing scoped Instagram server route. If per-action restrictions are promised, record that as an unresolved launch requirement requiring a separate server-enforcement design, not a documentation-only fix.
- [ ] Run `uv run pytest tests/test_capabilities.py tests/test_meta_management.py tests/test_meta_cli.py -q`; commit only this task's files.

**Close when:** Public claims and actual enforcement agree; unsupported operations cannot slip through a generic handler. Actual moderation success remains a separate live verdict.

## Task 2: Connect supported scheduling routes without bypassing eligibility

**Files:** `socialctl/native_schedule/cli.py`, `socialctl/native_schedule/dispatcher.py`, `socialctl/native_schedule/api_delivery.py`, existing platform submission modules, `tests/test_native_schedule_cli.py`, `tests/test_native_schedule_dispatcher.py`, `tests/test_native_schedule_exact_cli.py`.

**Interfaces:** Preserve `dispatch_ready(now, brand, *, store, job_id=None, budget=10, adapters=None, capabilities=None) -> list[str]` and `DeliveryAdapter.deliver(job, payload, checkpoint) -> str`. Feed only account-bound, current evidence and actual configured adapters. Reuse the existing `submit-api` eligibility/submission machinery where appropriate; do not build a second unguarded publication path.

- [ ] Map native provider scheduling, at-time worker publication and UI handoff independently. Identify which are advertised, supported and currently gated. Document the route chosen for each before changing code.
- [ ] Add failing CLI integration regressions: an eligible due API job reaches a fake adapter exactly once; an ineligible job makes zero network mutations; future and expired jobs do not publish; two workers and restart after uncertainty do not repeat an effect.
- [ ] Run those tests red. Implement the smallest shared runtime composition used by `dispatch` and `deliver`; retain exact brand/account/approval/media checks and durable claim/checkpoint behavior. A missing proof must still deny.
- [ ] Ensure CLI output distinguishes submitted, verified, blocked and awaiting UI; a command returning normally cannot certify remote publication.
- [ ] Run `uv run pytest tests/test_native_schedule_cli.py tests/test_native_schedule_dispatcher.py tests/test_native_schedule_exact_cli.py -q`; commit the isolated correction.
- [ ] Add only the still-unexercised supported route variants to the existing live queue. Verify provider readback and exact cleanup; do not repeat passed upload tests as a substitute for scheduling evidence.

**Close when:** Each advertised scheduling mode is reachable and verified through its intended public route. A blocked mode stays open in the remediation ledger, even if explicitly excluded from a narrower release. No generic all-platform scheduling claim.

## Task 3: Expose and verify the intended Instagram delivery variants

**Files:** `socialctl/native_schedule/instagram_delivery.py`, `socialctl/native_schedule/cli.py`, `socialctl/adapters/instagram.py`, `socialctl/capabilities.yml`; existing Instagram delivery tests and `tests/test_adapter_instagram.py`, `tests/test_native_schedule_cli.py`.

**Interfaces:** Reuse `InstagramDelivery(client, access_token, *, max_polls=5, sleep_s=60.0, allowed_media_hosts=frozenset())` and its `deliver(job, payload, checkpoint) -> str`. Keep supplied original media and explicit approved host allowlists.

- [ ] Verify current official carousel, resumable-upload and Story contracts with Context7 before selecting supported format/route combinations. Do not describe worker publication at a future time as provider-native Instagram scheduling.
- [ ] Add failing public-entry tests for the pending carousel/resumable routes; assert approved order/count/bytes, required permissions and correct account survive from preview to adapter.
- [ ] Add regressions for one failed carousel child, changed hosted bytes, expired media URL and ambiguous publish response: no parent publish with a missing child, no replacement media, no blind retry.
- [ ] Wire the supported variants into the route selected in Task 2; preserve checkpoints through container creation, upload and final publication.
- [ ] Run the relevant Instagram and native CLI test files, then commit only the integration change.
- [ ] Test only pending variants with authorized owner-supplied fixtures and exact previews. Verify publication and cleanup separately. Keep already-passed feed image, Reel and JPEG Story evidence excluded.

**Close when:** Each selected variant has public-route evidence and its own live result. Unsupported or fixture-blocked variants remain named, not silently counted as successful.

## Task 4: Deliver Meta webhooks as a separate subsystem

**Existing boundaries:** `socialctl/management/meta_webhooks.py`, `socialctl/management/meta_cli.py`, `socialctl/connection_service/app.py`, `socialctl/connection_service/store.py`, `tests/test_meta_webhooks.py`. Existing offline interfaces: `verify_challenge(mode, provided_token, challenge, expected_token)`, `WebhookStore.ingest(raw, signature, app_secret)`, `events()` and read-only reconciliation.

- [ ] First make CLI/help/catalogue state clearly that current helpers ingest offline events; do not advertise automatic delivery before it exists.
- [ ] Produce a focused receiver design before implementation: proposed `socialctl/saas/meta_webhooks.py`, `tests/test_saas_meta_webhooks.py`, GET/POST `/api/saas/v1/meta/webhook`, tenant binding, durable per-owner event storage, authenticated consumption, subscription lifecycle, deletion and retention. Do not reuse a local brand directory as a hosted multi-tenant store.
- [ ] Include bounded queues/backpressure, sensitive-payload minimization, secret rotation, subscription ownership and policy/consent impact in that design. Persist only necessary event data with an explicit retention period; extend account deletion and restore safeguards to the new storage. A legitimate batch containing multiple tenants must be routed safely without exposing another tenant's payload or making one unknown account discard all valid events.
- [ ] Validate exact callback and subscription requirements with current Meta documentation and the existing Meta developer MCP read access. Do not assume read-only MCP authorizes subscription mutations.
- [ ] In that design's implementation plan, require failing tests for raw-body HMAC, challenge rejection, body limits, foreign-account events, duplicates, late events, revocation, owner deletion and a transient storage failure. A successful response must follow durable acceptance; an event must never directly trigger a social write.
- [ ] Implement and deploy the reviewed receiver through the existing release process; configure the exact required subscription under applicable authorization.
- [ ] Capture one genuine event for each offered subscribed field with a disposable approved fixture. Trace receipt → owner-bound storage → authenticated consumption → owned GET; reuse those events for offline-helper QA.
- [ ] Snapshot existing subscriptions first. Verify subscription read/set/remove separately using only temporary changes; preserve real subscriptions and restore the exact baseline. Pending read/reply mentions remain separate cases: one comment event does not certify a mention reply.

**Close when:** A real event traverses the complete supported path. Local HMAC tests, an HTTP 200 or a dashboard subscription alone do not close delivery. This task remains open until that evidence exists.

## Task 5: Add independent outage monitoring

**Files:** `deploy/saas/backup_alert.py`, existing backup systemd service/timer files, `deploy/saas/backup-restore.md`, `tests/test_saas_backup_alerts.py`; new external-monitor configuration/runbook selected during infrastructure design.

- [ ] Inventory any already available independently hosted monitoring service. Choose a checker outside the application host and a notification delivery path that does not require that host. Record any new provider/cost decision before provisioning.
- [ ] Monitor both service availability and freshness of a verified off-host backup. A successful HTTP health check must not reset a stale-backup incident.
- [ ] Keep reports minimal: opaque check identifier, completion timestamp and status, without creator data, paths, secrets or account identifiers. Reuse the existing verified-backup completion boundary rather than treating backup start as success.
- [ ] Add failing tests for a fresh heartbeat, missing/expired heartbeat, rejected credentials, independent health/backup failures and deduplicated recovery. Implement the smallest connector and run `uv run pytest tests/test_saas_backup_alerts.py -q` plus connector tests.
- [ ] Exercise an isolated test check by suppressing its heartbeat, without stopping the production host. Verify the off-host detector and actual owner notification, then restore the check and verify recovery. A real full-host outage drill is a separately scheduled operation.
- [ ] Reuse an existing successful isolated restore receipt if it covers the deployed schema and keys. If new event storage or migration changes invalidate that evidence, verify restore in an isolated environment, including deletion/revocation manifests; never restore over production to manufacture coverage.

**Close when:** Independent detection and notification are demonstrated while the originating test heartbeat is absent. Existing same-host backup alerts remain in place.

## Task 6: Establish actual provider-review status and finish submissions

**Files:** Existing provider-review/cutover documents under `deploy/saas/`; private receipts under the existing operations directory. No public document contains credentials, reviewer passwords or private account IDs.

- [ ] Read the current Google verification state, requested scopes and submission evidence. Match the already-uploaded demo against current requirements; reuse it unless a specific missing requirement is demonstrated. Record missing evidence rather than claiming the video is absent.
- [ ] Read Meta App Review, Allowed usage and Data handling, exact requested permissions and reviewer account readiness. Compare them to the supported features from Task 1 and actual grants; permission availability for testing is not approval.
- [ ] Verify reviewer login, entitlement duration, minimum necessary account access and the demo's reachability without exposing private credentials in the plan. Preserve truthful processor/location declarations; a provider's US headquarters alone does not prove exclusive US processing. Check published privacy/terms/deletion URLs and the actual retention/revocation behavior they describe. Record evidence gaps instead of inventing compliance facts.
- [ ] Check TikTok's current production submission and exact offered posting mode. Do not make Direct Post a hidden requirement if the launch product offers only creator-reviewed inbox upload; do not advertise Direct Post without its required implementation/audit.
- [ ] Prepare complete review packages and evidence using existing successful demonstrations. Submit only within the owner's actual authorization, keeping review receipts and statuses distinct from approval. Do not contact support.
- [ ] Track provider-controlled waits as `waiting_provider_review`, with receipt/date/next check. A successful pilot, approved brand identity or production OAuth toggle does not establish approval of sensitive permissions.

**Close when:** Each offered integration has a verified current status and any required complete submission. Commercial approval remains a separate provider-controlled gate; do not mark it closed on submission.

## Task 7: Correct low-risk source, documentation and deployment drift

**Files:** `socialctl/management/meta_webhooks.py`, `socialctl/management/meta_comments.py`, `README.md`, `deploy/saas/candidate.compose.yaml`, `deploy/saas/candidate-status.md`, `deploy/saas/pilot-proxy.md`, `deploy/saas/release.md`; inspect `docs/privacy.html` and the separately scoped outer `legal/` files.

- [ ] Replace the two Spanish errors with `unknown webhook event` and `Meta: transport interrupted; outcome unconfirmed`; preserve error codes, uncertainty and retry behavior. Inspect existing assertions and run `uv run pytest tests/test_meta_webhooks.py tests/test_meta_comments.py -q`; no new test solely mirroring translated text.
- [ ] Reconcile source version, package-index version, runtime image/version, published guides and Muse's installed version from their own evidence. Do not label everything rc16 when the compatible runtime is rc15.
- [ ] Update current-state documentation and the intended deployment template from verified release evidence. Do not overwrite historical rollout records or accidentally deploy an old image by copying a stale template.
- [ ] Inspect `dist/` as build output. Build new release artifacts in a clean dedicated directory; archive/remove stale local artifacts only after confirming they are reproducible, unreferenced and not the sole retained evidence. No repository-wide cleanup.
- [ ] Verify which policy is actually served and linked from SocialCli, consent and provider consoles. Correct wrong links or actual content mismatches; do not replace Histopast's distinct policy just because its date differs. Never invent unimplemented data-handling promises.
- [ ] Remove any inaccurate single-container SQLite claim. Retain SQLite; require measured load/lock evidence before a separate migration proposal.
- [ ] Run `git diff --check`, inspect the scoped diff and commit only this task's files. Preserve unrelated local edits.

**Close when:** Current docs and deployment inputs identify the real components, English errors retain semantics, and each product links its intended policy. A clean entire private workspace is not an acceptance requirement.

## Task 8: Finish outstanding QA and apply separate release gates

**Files:** `docs/superpowers/plans/2026-10-18-remaining-live-qa.md`, `deploy/saas/release.md`, existing private `permissions-and-gaps.csv`, `variant-checklist.csv`, `passed-exclusions.csv`, Reporting follow-up and release receipts.

- [ ] Merge audit-related cases into the existing ledger by case/variant, never by raw test counts. Preserve successful exclusions; queue only failed, uncertain or unexercised variants. Attach this plan's task number to each affected case.
- [ ] Check the existing Reporting job at its recorded follow-up time. Download/import an actual generated report when available, verify it, then delete only that authorized job. Do not recreate it to restart the wait. Absence of data after the expected window is a diagnosis, not a pass.
- [ ] Diagnose the saved Analytics Groups 403 using exact scope alternatives, token, actor/resource eligibility and current API evidence. Retry a live mutation only after a demonstrated relevant change and its authorized exact preview; do not retry unchanged permissions.
- [ ] Resume all remaining failed/unexercised functions from the existing plan, including blocked fixtures and uncertain readbacks. Preserve distinct outcomes: implementation fixed, automated pass, live pass, provider limitation, missing fixture, waiting provider, cleanup verified.
- [ ] After code changes, run each affected focused suite; at the release boundary run `uv run pytest`, artifact checks and the existing Linux/macOS clean-install workflow. Automated regressions may rerun; the user's exclusion applies to passed live certification, not regression safety.
- [ ] Release an immutable new candidate through the existing protected workflow; verify downloaded hashes, server compatibility and Muse adoption independently. Do not overwrite rc16 or assume updating source updates users.
- [ ] Before deployment, capture the exact previous image/configuration/schema and a verified backup reference. Document code-only versus schema-aware rollback, timers, worker ownership and in-flight writes; do not roll back by erasing uncertainty receipts or resurrecting revoked/deleted data. After deployment verify health, maintenance, backup freshness and client/server compatibility using existing read-only checks.
- [ ] **Stable PyPI gate:** accurate supported scope, no unresolved release-blocking defect, required automated/package checks passed, exact release approval and verified immutable publication. External SaaS approvals do not automatically govern package publication, but restricted features must be honestly documented.
- [ ] **Commercial SaaS gate:** provider approval for the offered scope, supported routes verified, independent monitoring, actual served policies and the completed Task 9 cutover checks. Make no real charge without specific authorization. Test-mode success is not live billing evidence.
- [ ] Publish a final closure table with evidence and any externally blocked item. Do not say the whole plan is complete while a receiver, live fixture, report, review or billing gate is still open.

## Task 9: Verify billing cutover and operational email explicitly

**Files:** `socialctl/saas/billing.py`, `socialctl/saas/billing_reconcile.py`, `socialctl/saas/webhooks.py`, `socialctl/saas/reminders.py`, `socialctl/saas/mail.py`, `socialctl/saas/maintenance.py`, `socialctl/saas/operations.py`, `deploy/saas/release.md`; `tests/test_saas_billing.py`, `tests/test_saas_webhooks.py`, `tests/test_saas_reminders.py`, `tests/test_saas_mail.py`, `tests/test_saas_maintenance_budget.py`.

**Interfaces:** Preserve `BillingSettings.expected_livemode`, existing price/configuration validation, canonical billing reconciliation and `Reminders.run(sender, limit=100, *, budget=None)`. Reuse SMTP's durable send boundary; do not resend an uncertain email.

- [ ] Inventory existing billing/email evidence and current runtime configuration privately. Separate verified behavior, missing delivery evidence and configuration that merely exists. Test only uncovered or changed boundaries.
- [ ] Write a cutover runbook that identifies test/live API keys, prices, webhook secrets, customer/subscription/event records, entitlement handling and reconciliation workers. Determine the existing storage boundary before choosing separate live billing storage or a reviewed mode-aware migration; never discard identity/OAuth state or treat test payments as live access. Include deliberate treatment of existing owner/reviewer grants.
- [ ] Use current official Stripe documentation through Context7 for the configured API version and exact cutover operations. Specify cancellation, failed renewal, refund/dispute and duplicate/out-of-order event handling. Agree the actual commercial refund policy with the owner; code alone cannot invent it.
- [ ] Where evidence or implementation is missing, add fictional regressions for cross-mode objects/events, signature rejection, duplicate/out-of-order events, entitlement expiry, cancellation and the selected refund/dispute behavior. Run them red before changing code; verify `uv run pytest tests/test_saas_billing.py tests/test_saas_webhooks.py -q` after relevant changes.
- [ ] Audit the deployed reminder timer/queue and SMTP configuration. Reuse verified receipts where available. For uncovered behavior, test the existing three-day and one-day trial milestones, paid/deleting users, missed windows, rejection and uncertain delivery with a fictional clock/sender. Run `uv run pytest tests/test_saas_reminders.py tests/test_saas_mail.py tests/test_saas_maintenance_budget.py -q` after changes.
- [ ] If real email delivery remains unproven, prepare one isolated owner-addressed test with no customer data, send only under explicit messaging authorization, verify receipt and remove the isolated test job. Do not alter a real customer's trial or send a batch of reminders to test SMTP.
- [ ] Validate live configuration read-only before switching traffic. Reconcile a specifically authorized real transaction when available, retaining its exact receipt and any authorized cleanup/refund; do not count test-mode events as live payment evidence. If no live transaction is authorized, keep this evidence gate explicit.
- [ ] Verify the documented rollback preserves identity, grants, paid entitlements and uncertain records. Complete Task 8's deployment checks before opening commercial access.

**Close when:** Mode separation, the chosen billing lifecycle, reminder behavior and required delivery evidence are verified, with a reviewed cutover/rollback procedure. A configured live key or accepted SMTP send alone does not prove the complete customer journey.

## Explicit carry-forward coverage from the original QA plan

Every case below retains its original per-variant verdict and evidence. This is a routing map, not a new test queue. Audit Tasks 1–7 and 9 supplement the original plan; Task 8 coordinates its outstanding cases. Completed exact variants remain excluded even when their family contains unfinished variants.

| Case IDs | Required carry-forward work | Owning audit task / original QA task |
| --- | --- | --- |
| GAP-001–004, 012–015, 024–025, 030, 068, 086 | Captions, playlist images/items, video metadata/thumbnails and playlist fields; localized creation already passed | 8 / 7 |
| GAP-005–011, 017–023, 026–029 | Channel branding/assets/sections, subscriptions, comments, moderation and ratings; abuse reporting stays not safely testable without a genuine violation | 8 / 8 |
| GAP-016 | Activities pagination; retain partial observations and do not weaken consistency checks to obtain a pass | 8 / 8 |
| GAP-031–036, 069 | Analytics group lifecycle and uncovered query families; no unchanged 403 retries | 8 / 5 |
| GAP-037–038, 080 | Generated report get/download, data-row ingestion, Studio imports and provenance | 8 / 6, 12 |
| GAP-039–040, 043, 046–054 | Facebook profile/video/moderation and uncertain Meta deletion readbacks; unknown baseline is not empty | 8 / 3 |
| GAP-041–042, 044–045, 060–062, 073 | Real webhook challenges/events, subscription lifecycle, event consumption/reconciliation | 4 / 4 |
| GAP-055–056, 070 | Instagram media/deletion by format; feed JPEG/API cleanup and JPEG Story already passed, other types separate | 3, 8 / 2, 4 |
| GAP-057–058 | TikTok inbox/manual completion versus Direct Post implementation, UX and provider eligibility | 6, 8 / 9 |
| GAP-059 | Muse additive network access with YouTube preserved, using an isolated authorized installation | 8 / 9 |
| GAP-063–065 | Instagram moderation contract and eligible mention read/reply; scoped User-token route | 1, 8 / 4 |
| GAP-066–067 | Facebook scheduled-post reschedule/cancel with a disposable unpublished fixture | 2, 8 / 3 |
| GAP-071–072 | Facebook Story/Group local manual handoffs; no automatic API publication claim | 8 / 12 |
| GAP-074–077 | Metadata/community batch, exact restoration, inbox drafts and first-comment recovery without replay | 8 / 12 |
| GAP-078–079 | Local/remote scheduler, worker lifecycle, native API/UI routes and migration/rollback | 2, 8 / 12 |
| GAP-081 | Supplied-media transfer, byte identity, hosted expiry and temporary-media cleanup | 3, 8 / 12 |
| GAP-082–084 | Account/credential lifecycle and retention/revocation cleanup in isolated fixtures | 8 / 12 |
| GAP-085 | Catalogue/schema/public-route consistency; preserve completed description-only correction | 1 / 1, 12 |
| GAP-087 | Public Instagram resumable/carousel delivery route and its format-specific eligibility | 2, 3 / 12 |

- [ ] Before each execution batch, reconcile each mapped case with its child variants and passed exclusions. Keep historical rows as history; use the latest verified receipt, not an old unchecked box, to decide whether to run anything.
- [ ] Maintain an owner-resource manifest for every new live fixture: purpose, exact approved preview, original values, created IDs, receipt, readback, cleanup dependency and final cleanup evidence. Share fixtures across outstanding cases where safe; never delete genuine community content or replay old uncertain writes.
- [ ] For every blocked item record dependency, next concrete action and next-check time where applicable. A scheduled time in a document is not an installed automatic follow-up; state whether automation exists.

## Execution order and handoff

1. Task 1 establishes the contract; Task 6 starts status verification early and Task 8 checks the existing delayed Reporting job when due.
2. Task 2 precedes Task 3. Do not enable unverified routes to make a test pass.
3. Tasks 4 and 5 are independently reviewable infrastructure deliveries, with their own focused designs before code or provisioning.
4. Task 7 can be completed independently; it must not delay the functional work or become a substitute for it.
5. Task 9 prepares billing/email independently; its final commercial cutover follows the functional, provider and infrastructure gates. Task 8 consolidates outcomes and performs the release gates after relevant dependencies are satisfied.

Recommended execution: native, task by task in the canonical checkout, preserving the current QA evidence. Review each functional change before its live validation. The user requested this plan; creating it does not itself publish code, submit provider reviews, provision services or change production.

## Plan self-review

- All eight audit IDs map to tasks; the overstated security claim has an explicit contract correction and conditional architecture gate.
- Existing Facebook catalogue corrections and successful live variants are excluded from repeat certification.
- Scheduler interfaces retain the current signatures and the separate `submit-api` eligibility path.
- Receiver and monitoring are explicit new subsystem deliveries rather than unspecified edits to local helpers.
- All five review-focus risks are assigned concrete tests/acceptance evidence.
- Provider waits, unsupported variants and launch exclusions cannot be mislabeled as successful tests or blanket completion.
- Second review: all 87 case IDs have explicit owners; 197 child variants, 167 command rows, 163 catalogue rows and 66 passed-exclusion entries are accounted for as inventories, not success counts. SMTP reminders, billing mode separation/lifecycle, subscription restoration, resource cleanup and deployment rollback now have explicit steps. The underlying private verdicts were not changed by this planning review.
