# SocialCli 0.2.3 release verification

Date: October 10, 2026. Implementation commit: `1e10a00`.

## Delivered

- Exact noninteractive account binding and explicit browser-start OAuth landing.
- Command-wide read deadlines, bounded GET retries, stderr progress and JSON reports.
- Verified partial YouTube observations, Meta readback safeguards and actionable Graph errors.
- Optional authenticated encrypted capability files with verified migration/rotation.
- Immutable supervised community batches, with durable stop/resume/reconcile safeguards.
- Unified supported-account inbox and exact clip-to-source mapping inside approved previews.
- English agent examples, capability/permission matrix and matching hosted documentation.

## Evidence

Full suite: **2,114 passed**, one upstream Starlette test-client deprecation warning,
36.11 seconds. Focused post-commit component acceptance tests also passed.
`git diff --check` and wheel build passed. Clean default install loaded every new
command without cryptography; clean headless-extra install verified encrypted
save/get/delete with fictional capabilities. No provider content was published.

Installed client 0.2.3 successfully created, polled and cancelled a disposable
pending authorization against `https://social.florentino.pro`; cleanup was verified
by HTTP 404. Its browser URL was never fetched and OAuth never started.
The public health endpoint returned 200. Hosted agent, connection, capability,
platform and installation pages returned 200 and exactly matched repository bytes.

The connection container remains **0.2.1**, with version 1 broker protocol; no
service upgrade is necessary for these client changes. Rollback image
`socialcli-connections:before-agent-reliability-20261010` remains available.
Existing service state/configuration is retained. The previous HTML generation
was backed up under the operator's connection-service docs-backups directory.

## Limits and outstanding external verification

Tests use fictional provider transports; they do not establish live Meta ID alias
behavior, eventual consistency, new permission grants or provider production
eligibility. A live write pilot requires its own complete approved preview.
YouTube community traversals restart bounded scans; very large threads may stay
partial until provider-specific cursor continuation is implemented. Complete
scope watermarks never imply global absence. Meta/YouTube inventory checkpoints
resume where their existing contracts support it.

No reviewed Minor findings remain deferred. Invalid inbox configuration was
regraded Important and fixed because it broke agents' JSON invocation contract.
Production review, account eligibility and consent remain provider-controlled.

## Delivery decisions

- Work in existing folder on feat/agent-reliability — user explicitly requested a single SocialCli folder — no separate worktree.
- Landing uses Referrer-Policy same-origin rather than global no-referrer — actual Chrome sends Origin null under no-referrer and rejects genuine forms; same-origin preserves CSRF check and suppresses provider Referer.
- Existing rollback, interrupted installation, concurrent writers and declined OAuth fixtures are retained as recovery regressions rather than duplicated.
- Stage 1 release review is running independently; start read-only reliability implementation while the reviewer reads the stage 1 commit range. No overlapping implementation files except planned docs/CLI registration.
- deadline and JSON boundaries share the same CLI changes; commit both together after focused and full verification instead of an artificial intermediate split. Cost if wrong: larger revert scope.
- components 3–6 share one verified 0.2.3 release commit rather than artificial intermediate packaging; component 2 rollout joins this release. Cost if wrong: larger rollback scope.
- one encrypted store per brand and files outside the creator workspace prevent rotation and reserved-file alias damage. Cost if wrong: additional files for multiple brands.
- batch preparation and execution share one new module and are implemented together; separate validated child stores continue to own operation locks and uncertainty. Cost if wrong: larger revert scope.
- explicit maps apply automatically before preview; --source-link requires exact resolution. TikTok uses caption placement and Meta accepts an additive exact English template while preserving legacy Spanish. Cost if wrong: custom Meta comments may need explicit correction; text links may not be clickable.
- invalid inbox configuration is graded Important because supervised agents otherwise receive empty unparseable output — fixed structured JSON/exit2 with regression tests — cost if wrong: slightly larger correction scope.
- YouTube community scans restart bounded reads while inventory checkpoints and compatible Meta cursors resume; complete watermarks remain scope-bound. Cost if wrong: very large community threads can require provider-specific pagination work before complete coverage.
