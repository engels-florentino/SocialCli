# Remaining Live QA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Execution method is proposed, not yet selected by the owner.

**Goal:** Resolve every failed, inconclusive or unexercised existing function with a permission audit and scoped live evidence, without repeating passed live tests.

**Architecture:** Keep the existing adapters, ownership checks, ChangeSets and no-replay guards. Maintain one private case ledger with method/field/variant evidence; diagnose a reproduced failure before selecting a minimal code correction. Platform tasks are independent except for version/grant preparation and shared disposable fixtures.

**Tech Stack:** Python, pytest, httpx, existing SocialCli/SaaS clients, provider REST APIs, Context7, official browser UI where needed.

**Spec:** `docs/superpowers/specs/2026-10-18-live-qa-gaps-scope.md`

## Global Constraints

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

## Review Focus

1. A scope added in source but absent from the deployed server or consent must not become a granted capability (Tasks 1–2).
2. A missing baseline, asset or previous value must not become an empty value that permits destructive restoration (Tasks 3, 7–8).
3. Accepted writes with delayed/missing readback must not trigger duplicate requests or false passes (Tasks 3–6).
4. A callback signature or complete empty list does not prove real webhook delivery or generated report download (Tasks 4, 6).
5. Setup/cleanup for a missing variant must not reopen the already-passed function or touch real community content (all live tasks).

## Evidence contract and work order

Keep private files under `Histopast/operacion/qa-gaps-2026-10-18/` in the outer
workspace, never inside the distributable package. The accompanying
`permissions-and-gaps.csv` is the initial case inventory. It compares the latest
saved grant evidence, not a new provider validation. Never silently upgrade that
comparison to current authorization.

For each case record: `case_id`, capability/method, field/variant, old evidence,
required scopes (AND/OR), source/version/date, requested scopes, observed grants
and timestamp, token kind, app review status, actor/owner, fixture, baseline,
approved fingerprint, request receipt, readback, restoration, code version,
diagnosis and next action. Public summaries omit private account/resource IDs.

Statuses: `not_exercised`, `failed`, `uncertain`, `blocked_permission`,
`blocked_token`, `blocked_fixture`, `waiting_provider_data`, `live_pass`,
`provider_limitation_documented`, `not_safely_testable`. A blocker never counts as pass.

Order: 1 → 2; start the delayed Reporting case in 6 early, then run 3–5 and 7–9
while it generates. Finish with 10. MCP evaluation is independent (11).

## Task 1: Reconcile the exact outstanding inventory and permission evidence

**Files:** Read `socialctl/capabilities.yml`, `socialctl/connection_service/providers.py`,
`socialctl/management/{meta_schema,owned_schema,community_schema}.py`; update the private CSV.
**Interfaces:** Consume old capability matrix, closeout receipts and current command schemas;
produce one row per remaining method/field/variant with explicit AND/OR scope checks.

- [ ] Join all 107 enabled catalog entries to the latest saved live evidence. Preserve passed rows in an exclusion ledger, not the new execution queue.
- [ ] Add existing commands missing from the catalog: Instagram media delete, TikTok upload/direct, assistant platform expansion and any exposed subscription/mention/scheduling variants. A code handler alone does not establish a supported public command: inspect installed help and routing.
- [ ] Expand partial methods: five missing Facebook profile fields; actual phone-digit change if advertised; Instagram image-feed publication; YouTube mutable parts/asset variants.
- [ ] For each queued row use Context7 resolve → query for the exact method; confirm official docs/discovery when retrieval is stale. Record scope alternatives correctly, not as all mandatory.
- [ ] Compare requested scopes to saved grants and current read-only grant diagnostics; record Page versus User tokens and app review separately. Do not reconnect on read failure.
- [ ] Record missing fixtures and exact permitted cleanup before scheduling a write. Keep metadata reads needed for this audit distinct from repeated QA.

**Acceptance:** Every failed/unexercised offered scope has a row; no previously passed scope is assigned another certification run. Every row has a permission conclusion or a named evidence gap.

## Task 2: Instagram deletion authorization and client/server adoption

**Files:** `socialctl/connection_service/providers.py`, `socialctl/management/meta_client.py`,
`socialctl/saas/connections.py`, `socialctl/connections/saas_client.py`,
`tests/test_connection_providers.py`, `tests/test_connection_service.py`,
`tests/test_saas_connections.py`, `tests/test_meta_management.py`, `docs/platform-permissions.md`.
**Interfaces:** Preserve `Providers.scopes(platform, *, management=False, analytics=False)`;
`MetaClient.identity(*, user_required=False)` must still reject Page-token deletion.

- [x] Reproduce missing `instagram_manage_contents` in the management authorization URL and service redirect; verify declining it blocks completion.
- [x] Add the permission only to Instagram management consent; preserve ordinary/analytics-only consent and other platforms.
- [x] Check the Meta console: permission already exists with Ready for testing. Do not add a duplicate or claim production approval.
- [ ] Audit the full credential route: confirm the server retains the appropriate user grant and the deletion handler can consume it without exporting tokens or replacing Page credentials globally.
  Source inspection already shows the selection path substitutes the Page token and the client defaults to Page mode. Design an operation-specific user-token route with ownership enforcement; do not simply relabel a Page token as a User token.
- [ ] If routing fails, add a fictional integration test at that boundary first; assert the delete uses the user actor, fails before DELETE when scope/token is missing, and existing publishing still selects its intended actor.
- [ ] Package/deploy through the existing release path; verify actual server and Muse versions. Never overwrite an existing immutable release or approve an owner's protected deployment.
- [ ] Hand any renewed-consent link to the owner without opening it. Verify the returned grant explicitly includes the new permission; preserve existing connections.
- [ ] Use a disposable Instagram fixture from Task 4 to verify API deletion and cleanup. UI deletion is fallback cleanup, not an API pass.

**Tests:** `uv run pytest tests/test_connection_providers.py tests/test_connection_service.py tests/test_saas_connections.py tests/test_meta_management.py -q`.

## Task 3: Facebook failures and inconclusive outcomes

**Files:** `socialctl/management/{meta_client,meta_changes,meta_errors,meta_comments}.py`,
`tests/test_meta_management.py`, `tests/test_meta_errors.py`, `tests/test_meta_comments.py`.
**Interfaces:** Keep `prepare_meta(client, store, raw)`, `apply_meta(client, store, change_id, approval_digest)` and read-only reconciliation contracts.

- [ ] Audit permissions for each of `bio`, `description`, `company_overview`, `general_info`, `mission`; obtain an observable original before preparing any change. Missing is unknown. Preserve the old uncertain bio journal.
- [ ] Check the exact Page update contract/SDK and login token, then make one reversible field change at a time only when the baseline can be restored. Never remap bio to about. About/website/phone formatting already passed and are excluded.
- [ ] For phone-digit changes, use only an owner-supplied legitimate alternate number with a restorable baseline; otherwise mark this variant blocked_fixture.
- [ ] Diagnose own-comment hide from the saved 403/code 200. Verify scope, Page task and exact comment eligibility; distinguish that case from a consenting test visitor's comment. Do not moderate unrelated community comments.
- [ ] Test hide → readback hidden → unhide → readback visible on an eligible temporary comment; retain a separate verdict for own comments.
- [ ] Test the unexercised video-description update on a disposable/explicitly authorized demo, then restore its exact description.
- [ ] Audit exposed `schedule-reprogram`/`schedule-cancel` proposals separately from disabled creation routes. Use only a disposable unpublished provider-scheduled fixture with confirmed timing; reschedule/read back/cancel it before publication. Never manufacture a schedule by bypassing a disabled creation gate.
- [ ] Review accepted-but-unverified deletion journals offline first. Preserve accepted receipt and independent UI cleanup evidence separately; do not replay those deletes. Only test a new deletion verification fix on a fixture already needed by an outstanding case.
- [ ] For each reproduced bug: write the named failing regression using sanitized structure, run it red, implement the smallest correction, run the focused test green, and commit only its files.

**Acceptance:** Each field and moderation eligibility variant has an honest separate outcome; no repeated like/reply/post/upload success tests.

## Task 4: Instagram remaining format/deletion and Meta webhooks

**Files:** `socialctl/adapters/instagram.py`, `socialctl/management/{meta_webhooks,meta_cli,meta_client}.py`,
`tests/test_adapter_instagram.py`, `tests/test_meta_webhooks.py`, `tests/test_meta_cli.py`.
**Interfaces:** Existing `verify_challenge(mode, provided_token, challenge, expected_token)` and
`WebhookStore.ingest(raw, signature, app_secret)`; no event is authorization to write.

- [ ] Publish one supplied image to the feed, verify returned media ID/type/caption and use it for Task 2 deletion. Do not repeat the passed Reel or Story publication tests.
- [ ] Audit the exposed Instagram comment hide/unhide and mention-read/reply proposals, absent from the old capability matrix. Use eligible consenting fixtures, confirm each scope/token contract, restore hidden state and remove only the temporary reply. The current mention handler requires a User token and shares the Task 2 routing dependency.
- [ ] Audit existing subscribed fields, callback ownership and `pages_manage_metadata`/Instagram grants. Preserve original subscriptions; no blanket unsubscribe.
  Include Page `subscriptions` read, `subscription-set` and `subscription-remove` as separate cases; the current schema enables feed only. Never delete an existing real subscription to test cleanup.
- [ ] Locate an existing receiver before proposing one: the CLI webhook helpers alone provide no HTTP listener. If no receiver exists, document that product boundary and design a separate receiver change rather than silently claiming delivery support.
- [ ] Obtain one real Meta verification challenge and signed event for each enabled platform using an isolated owned callback/subscription. Verify signature, exact account, ingestion and read-only reconciliation; reuse the same captured event to check deduplication locally.
- [ ] Restore only the temporary subscription changes. Real event delivery, local signature verification and synthetic tests receive separate outcomes.

**Tests:** `uv run pytest tests/test_adapter_instagram.py tests/test_meta_webhooks.py tests/test_meta_cli.py -q`.

## Task 5: YouTube Analytics group 403

**Files:** `socialctl/metricas/{analytics_client,analytics_changes,analytics_schema}.py`,
`tests/test_youtube_analytics.py`, `tests/test_youtube_analytics_changes.py`.
**Interfaces:** Preserve `AnalyticsClient.require_scope(scopes)` and owned group binding; keep sanitized `provider_reason` diagnostics.

- [ ] Compare the saved 403 request shape, authenticated channel and grant to official groups.insert; do not assume force-ssl substitutes for every API's authorization.
- [ ] The documented readonly Analytics alternative is already in the saved grant. Verify endpoint/body/itemType, channel ownership and account eligibility before asking for a broader grant.
- [ ] State a single evidenced hypothesis; fix only a reproduced defect with a failing regression. A generic forbidden response with unchanged conditions is not a reason for another create attempt.
- [ ] After the blocking condition changes, preview a new disposable group and execute create → exact read → rename → add item → list item → remove item → delete group, verifying each step. Use existing demo media; do not upload again.
- [ ] If rejection persists, record the exact limitation/unknown cause and do not count unattempted dependent methods as successful. Do not contact support.

**Tests:** `uv run pytest tests/test_youtube_analytics.py tests/test_youtube_analytics_changes.py -q`.

## Task 6: Generated YouTube Reporting data

**Files:** `socialctl/metricas/{reporting_client,analytics_ingest}.py`, `tests/test_youtube_reporting.py`.
**Interfaces:** Existing job/report IDs and bounded download/import APIs; fixtures stay private.

- [ ] Confirm recorded `yt-analytics.readonly` and find an existing generated report. If absent, create a temporary reporting job as fixture setup only; job CRUD already passed.
- [ ] Keep the job for up to 48 hours from creation, with bounded later status checks while other tasks proceed. Save job ID and next-check time; do not block the session with a 48-hour sleep.
- [ ] Exercise the untested report-get and media-download methods against a generated report. Validate report identity, period, allowed download host, CSV header and import without overwriting unrelated files.
- [ ] A generated header-only report may prove retrieval/download; record that data-row ingestion still lacks real coverage rather than inventing rows.
- [ ] Delete only the temporary job after obtaining the required evidence. Record provider delay explicitly if the report is still unavailable.

**Tests:** `uv run pytest tests/test_youtube_reporting.py -q` after a reproduced code correction.

## Task 7: YouTube caption, thumbnail, metadata and playlist-image gaps

**Files:** `socialctl/management/{youtube_resources,resource_changes,youtube_metadata,metadata_parts,youtube_owned,owned_changes,owned_reorder}.py`,
`tests/test_youtube_resources.py`, `tests/test_youtube_metadata_parts.py`, `tests/test_youtube_owned.py`.
**Interfaces:** Existing exact-ID resource proposals and approved file hashes; no arbitrary API payload interface.

- [ ] Audit each method's scope separately against the recorded force-ssl/upload/readonly grants. Check ownership, quota and feature eligibility, not scope strings alone.
- [ ] Use an authorized disposable video and supplied subtitle files: caption insert → processing/readback → download → content update → explicit draft update → delete. Empty caption listing already passed; it is not a substitute for this lifecycle.
- [ ] Use supplied image originals for thumbnail-set; do not claim a platform-resized image is a byte-exact original. Prefer a disposable video to avoid unrecoverable originals.
- [ ] Exercise every enabled writable metadata part/field with preserving updates and readback; include recording date separately. Do not enable disabled defaultAudioLanguage/location edits or falsify audience/licensing declarations.
- [ ] Create a private playlist only as shared fixture setup for untested item-update/reordering and playlistImages list/insert/update/delete. Test metadata-only and supplied-file variants actually advertised; preserve item video IDs.
- [ ] Restore values or remove only disposable resources, with per-variant verdicts. Existing playlist CRUD/membership successes remain excluded.

**Tests:** `uv run pytest tests/test_youtube_resources.py tests/test_youtube_metadata_parts.py tests/test_youtube_owned.py -q` after relevant fixes.

## Task 8: YouTube channel and community gaps

**Files:** `socialctl/management/{youtube_owned,owned_assets,youtube_community,community_changes,community_schema}.py`,
`tests/test_youtube_owned.py`, `tests/test_youtube_community.py`.
**Interfaces:** `OwnedEdit` / `CommunityEdit` exact field scopes, owner-bound target IDs.

- [ ] Before channel updates, save complete branding/localizations/sections state and original banner/watermark source files. Missing originals block that asset test; do not pretend thumbnail URLs are original files.
- [ ] Test enabled channel branding/localization variants, banner upload/application, watermark set/unset and a temporary section create/update/delete separately, restoring exact originals. Truthful audience values require owner confirmation, never arbitrary toggles for coverage.
- [ ] Test top-level comment creation, thread/reply list, reply creation, own edit/delete and eligible visitor moderation on disposable/approved demo content. Use a consenting fixture author for moderation when self-comments are ineligible.
- [ ] Test video rating and subscription insert/delete only on an explicitly selected legitimate target. Capture initial state; never remove an existing subscription as cleanup.
- [ ] Diagnose activities pagination using saved pages, documented totals and cursor termination. Preserve partial observations; do not remove consistency checks merely to obtain a green result. Retest only activities after a justified correction.
- [ ] `videos.reportAbuse` stays listed as not safely live-testable without a genuine violation and explicit target/reason authorization. Never submit a false report. This is an honest coverage exception, not an API prohibition.

**Tests:** `uv run pytest tests/test_youtube_owned.py tests/test_youtube_community.py -q` after relevant fixes.

## Task 9: TikTok posting and Muse platform expansion

**Files:** `socialctl/adapters/tiktok.py`, `socialctl/schedule_remote.py`,
`socialctl/connection_service/providers.py`, `socialctl/connections/account_cli.py`,
`socialctl/saas/permissions.py`, `tests/test_adapter_tiktok.py`,
`tests/test_schedule_remote.py`, `tests/test_saas_platform_expansion.py`.
**Interfaces:** Existing inbox/direct adapter modes and owner-owned permission link; preserve installation identity and YouTube grant.

- [ ] Separate the stale remote executor diagnosis from the valid TikTok identity/statistics connection; do not retest the passed statistics/inventory functions.
- [ ] Verify the exact runner version, mode, requested/granted `video.upload` or `video.publish`, creator privacy options and actual app audit/configuration. Never set `auditada=true` to bypass approval.
  The saved grant contains both upload and publish scopes; the hosted request contains upload only. Keep these two facts separate.
- [ ] Test the unexercised inbox upload with supplied media; verify delivery and manual completion separately. Remove the temporary video through a supported authorized route; do not invent a delete endpoint.
- [ ] Direct mode needs its own consent/UX/provider approval work if those are absent. Document the scope/configuration gap and design the necessary change before promising production Direct Post.
- [ ] With updated Muse, use a separate owner-authorized installation initially limited to YouTube to test additive network access. Do not revoke platforms from the existing healthy installation to manufacture the scenario.
- [ ] Have the owner approve the added platform, verify link completion and preserved YouTube installation/grant, then clean up only the temporary installation.

## Task 10: Focused fixes, release and closeout

**Files:** Affected implementation/tests only, private case ledger, `docs/platform-permissions.md`,
`docs/capabilities.md`, `socialctl/capabilities.yml` and one new release audit under `deploy/saas/`.
**Interfaces:** Consume verified cases and exact blocker reasons; produce shipped version and coverage report.

- [ ] For every fix record failing regression → minimal implementation → focused pass. Commit independently reviewable corrections without unrelated dirty files.
- [ ] Run the required automated suite and package checks when code changes warrant them. This is regression validation, not a repeat of passed live tests.
- [ ] Verify actual hosted/Muse version and owner-protected release status separately; code on disk is not deployment.
- [ ] Reconcile the disposable resource manifest and originals. Keep genuine community replies and source-link comments intact.
- [ ] Publish an honest result list: newly passed, still blocked with dependency, uncertain, and not safely testable. No unsupported percentage or blanket production-approval claim.

## Task 11: Meta Social Technologies MCP evaluation

**Files:** `docs/superpowers/specs/2026-10-18-meta-mcp-assessment.md` (planning artifact only).
**Interfaces:** Administrative read tools supplement SocialCli; they do not replace its publishing/token/approval contracts.

- [x] Read the official documentation (updated October 7, 2026) through the authenticated browser when web retrieval failed.
- [ ] Decide whether to connect a read-only pilot scoped only to SocialCli. Installing/authenticating it is separate from evaluating it; the owner performs any new OAuth authorization.
- [ ] If authorized, compare app settings, review privileges/requirements, API health and webhook subscriptions with the console. Never transmit support messages.
- [ ] Keep Manage scope unnecessary for the initial audit. The documented write tools concern webhooks; app-lifecycle tools are still described as coming soon and must not be assumed callable.

## Self-review and handoff

The scope covers the original matrix gaps plus missing exposed-command variants,
preserves all known successful live results, separates grant checks from execution,
and handles delayed reporting and unrecoverable actions explicitly. Unknown causes
are diagnostic tasks, not invented fixes. Recommended execution is sequential in
this session with independent review of code changes, because all live tasks share
account state and restoration records. The newly requested Instagram scope fix is
already authorized separately; the rest remains a plan for review.
