# SocialCli agent reliability design

Date: 2026-10-10
Status: Approved and implemented. Client 0.2.3 verified by 2,114 fictional-transport tests and clean default/headless installs; release rollout tracked in the delivery plan. Live provider production eligibility is not established by these tests.

## Purpose

Make SocialCli practical for supervised AI agents on desktop and headless servers. Remove avoidable interactive failures, make bounded reads observable, preserve verified partial results, and support exact approval of community-action batches. All application text and documentation remain English.

Success means an agent can install, connect a specifically authorized account, obtain bounded machine-readable observations, propose exact actions, obtain one approval for an immutable batch, and reconcile interruptions without duplicating writes.

## Existing implementation and compatibility

- Public application: `app/SocialCli`; Python imports remain `socialctl`.
- Shared OAuth service: `https://social.florentino.pro`; creator capabilities are stored locally and provider credentials remain server-side.
- `connect --no-browser` exists, but final account selection/confirmation still requires interactive input. GET `/connect/{id}` currently starts the single-use authorization.
- Metrics already have per-request timeouts and some progress; they lack a command-wide deadline and a consistent JSON interface.
- YouTube community reads already return JSON. Pagination validation can reject a response before accepting otherwise useful items. Diagnose against official endpoint contracts and sanitized representative responses before changing validation.
- Meta comment inbox, durable ChangeSets, resource locks, reconciliation, and YouTube metadata batches already exist. Extend these foundations rather than replacing their stores.
- Preserve current command defaults, historical journals, stored credentials, and existing approval requirements. Version new data formats explicitly. Additive JSON flags must not silently replace existing output contracts.

## Scope and delivery order

1. Account connection and recovery.
2. Bounded reads, diagnostics, and machine-readable output.
3. YouTube partial reads and Meta reconciliation.
4. Optional encrypted file credential store.
5. Immutable community-action batches.
6. Unified inbox and exact source-video mapping.
7. Capability documentation and release verification, updated with every stage.

Each stage is independently tested and released. New provider privileges, production approval, unsupported provider actions, unattended publication, media generation, and cadence changes are outside this design.

## 1. Account connection and recovery

### CLI contract

Retain interactive `connect` behavior and `--no-browser`. Add `--account-id ID` and `--yes` for explicit noninteractive binding. `--yes` requires `--account-id`; it authorizes only binding the matching account to the explicitly named brand. It never authorizes publication or community actions. A numeric position is not a stable account identity and is not the noninteractive selector.

In noninteractive execution, reject missing confirmation/selection arguments before creating a pairing session. Validate brand, credential-store availability, existing bindings, and supported options before OAuth. Validate the returned account against the requested ID and linked Facebook Page; never select a different account automatically. Replacement of independent credentials must be disclosed and explicitly authorized; otherwise reject before creating a session. Existing shared connections continue to require explicit disconnect/reconnect.

Print the login URL immediately and flush output. Keep the process alive while the creator authorizes. Document agent runners that yield partial output without terminating the command. Respect the service's expiry and poll interval. Do not prompt for passwords or expose polling secrets.

### Preview-resistant authorization

GET `/connect/{id}` returns a minimal informational page and an explicit Connect button; GET/HEAD do not consume authorization or redirect to the provider. The page is not cached or indexed, has no third-party resources, and uses a restrictive referrer policy. An explicit POST starts OAuth and consumes the pending authorization atomically. Protect POST with a browser-bound form nonce; retain OAuth state, PKCE where applicable, callback browser binding, and existing origin protections.

Repeated clicks cannot create multiple exchanges. Reloading the landing page cannot reset or steal an already-started session. Used/expired links display a clear instruction to restart the CLI; they do not silently issue a new authorization. Opening the landing page in several browsers must not prevent the creator from starting in their chosen browser; a mere preview must not acquire exclusive session ownership.

### Recovery and compatibility

Preserve previously installed credentials on declined, incomplete, failed, or interrupted installation. Retain provisional server expiry and activation semantics; report cleanup failures accurately. Use existing brand locks to prevent concurrent installations. Do not promise automatic recovery after SIGKILL or power loss; detect inconsistent local binding and provide a safe diagnostic/recovery path.

The URL shape and broker protocol remain compatible with older clients; old clients open the new landing page and the user presses Connect. New clients must also work with the old direct-redirect server during rollout. Deploy server first, then client/documentation.

## 2. Bounded reads and JSON

Add a configurable command-wide `--timeout` to affected metrics/community/inbox reads. Keep finite individual network timeouts, page/item limits, and cancellation handling. The deadline includes account credential retrieval, pagination, and retry waits; it must be enforced during requests, not only between pages. Stop additional work when the budget is exhausted and preserve already validated observations.

Progress goes to stderr; JSON stdout contains one parseable document. Keep current default human output. Existing JSON-only commands may accept `--json` as an explicit compatibility flag without changing their default schema. New unified reports use an explicitly versioned envelope with platform/account identity, observation time, completeness, items/data, sanitized errors, and a resumable cursor only where supported.

Use consistent exit semantics for new reports: 0 complete successful read; 1 partial/failed operation with a structured report; 2 invalid invocation. Preserve legacy exit contracts unless a documented versioned migration is necessary. Partial is not zero and unavailable is not empty. Local snapshot merging and retention must preserve provenance and not overwrite successful data with fabricated empty success.

Bound read retries for transient transport/provider failures. Honor provider retry instructions within the total deadline. Do not automatically replay writes on timeout, rate limiting, or ambiguous responses. Preserve safe provider error code/subcode, diagnostic message and request reference; redact credentials, authorization codes, sensitive URLs, and capability values.

## 3. YouTube and Meta community reliability

### YouTube

Validate pagination against the official contract of each endpoint rather than imposing one total-count rule on all responses. Preserve independently validated rows when later metadata/pages fail. Report `complete=false` and a reason for missing metadata, changed totals, repeated cursors, quota limits, page limits, and interrupted reads. Never infer absence or deduplication from an incomplete listing.

Preparing a reply may proceed despite unrelated incomplete listing only after exact parent/thread/video and actor verification. Use durable operation identity and existing journals to prevent repeated submission. If uncertainty about an earlier identical write cannot be resolved, block a new write rather than relaxing deduplication. Destructive operations retain their stronger verification requirements.

### Meta

Investigate real returned comment IDs and supported direct readback. Verify actor, target parent/media, exact text and returned object identity using provider-supported fields. Do not treat a guessed numeric/composite-ID transformation or a text-only match as proof. Allow bounded eventual-consistency readback; leave ambiguous results uncertain. Never repost solely because the original object is absent from a partial listing.

Improve HTTP errors with sanitized Graph error details and operation context. An HTTP 400 is not automatically a permission error. Distinguish invalid target, token failure, unavailable capability, insufficient permission, and rate/quota limits where provider evidence supports that distinction.

Facebook like/unlike uses the connected Page identity and actual granted permissions. Diagnose the failed operation before changing endpoints or scopes. Document YouTube video ratings separately from comment likes. Unsupported Instagram/TikTok like actions fail before any write. Validate current provider capabilities from official documentation during implementation; do not promise access based on app settings alone.

## 4. Optional encrypted credential store

Default remains the supported OS keyring. Add an explicit credential-store configuration and a documented `encrypted-file` opt-in; never silently fall back to plaintext or to file storage when keyring fails.

Use maintained authenticated encryption primitives from the existing cryptography dependency, exposed through an appropriate client extra. Store only the creator capability and necessary versioned metadata, not service operator secrets or provider refresh tokens. Bind encrypted records to service origin, brand identity, platform, and account ID. Keep the master key outside the encrypted file and Git repository. Support a protected external key file for headless deployment; do not place key values in CLI arguments or examples.

Use restrictive permissions, reject symlink/path substitution, atomic replacement and interprocess locking. Fail clearly for missing/wrong keys, corruption, inaccessible storage, and unsupported versions. Migration between stores verifies destination retrieval before removing the source. Key rotation is atomic and recoverable. Backups require the external key; document that lost keys cannot be recovered by SocialCli. Disconnect and credential diagnostics must honor the selected backend.

## 5. Community-action batches

Extend existing ChangeSet/journal/lock foundations without changing the semantics of current metadata batches. Prepare a versioned community batch referencing explicit child proposals. Preview every platform, account, target, text, effect, permission requirement and limitation. Reject duplicate/conflicting operations within a batch.

A batch fingerprint covers brand/account identities, ordered child IDs and fingerprints, and all intended effects. Exclude mutable execution receipts from the approval digest. One explicit approval authorizes only this immutable list. Provide a noninteractive approval digest argument for supervised agents; `--yes` alone cannot approve writes. Never persist blanket approval for future actions.

Before each write verify binding, current target, and proposal preconditions. Default to sequential execution and stop on a rejected, conflicted, or uncertain child. Persist independent child outcomes atomically. Resume only pending eligible operations; completed operations are skipped and uncertain attempts are reconciled without replay. Cross-batch locks and operation identity prevent duplicates created through another proposal ID. Do not promise cross-provider transactions or rollback of already published replies.

## 6. Unified inbox and source-video map

### Inbox

Add a brand-scoped `socialcli inbox --brand BRAND --since VALUE --json` read/sync surface. Initially unify YouTube, Facebook and Instagram, reusing the existing Meta inbox. TikTok is reported as unsupported/unavailable unless its configured integration genuinely supports the requested read.

Accept explicit RFC3339/date values and documented relative dates such as `yesterday` and `ayer`. Resolve relative dates once using an explicit `--timezone` or brand timezone; report the resolved interval and reject ambiguity. A brand timezone is required for relative dates when no flag is supplied.

Deduplicate by platform/account/comment ID. Retain created, provider-updated when available, and observed timestamps separately. Preserve original comments and distinguish subsequent edits. Report per-account coverage, failures and pagination state. Persist a complete watermark only after complete traversal; use bounded overlap/deduplication for incremental reads and handle invalid cursors without silently losing history. An inbox read does not reply, react, or publish.

### Exact video mapping

Add a versioned brand-owned YAML mapping from explicit clip/post identity to its exact YouTube source video ID. Series membership is optional organization, not sufficient evidence of a source. Validate duplicates, malformed IDs and conflicting mappings. Resolve a canonical URL from the exact source; never guess by title or newest episode.

Show generated link text and placement in the full publication/comment preview. Publication and follow-up comments retain explicit approval for every effect. Missing/ambiguous mapping produces a visible unresolved status; it never inserts an unrelated URL or changes cadence. Record completed follow-up linking so retries cannot duplicate comments. Respect platform limitations such as non-clickable links and unsupported pinning.

## 7. Documentation, tests, release and operations

Maintain an English capability matrix: action, platform, implementation support, app eligibility, required/granted scopes, actor role, object constraints, and verification limits. Cover comment likes versus video ratings, inbox delivery versus public publication, partial coverage, provider revocation versus local disconnection, and exact approval requirements. Provide desktop/headless agent examples and troubleshooting without exposing secrets.

Verification uses fictional provider responses and isolated creator workspaces. Add regression tests for each reported failure, plus success, missing permissions, multi-account selection, previews/HEAD, double submit, expired links, noninteractive stdin, store corruption, cancellation, pagination changes, error redaction, concurrency, partial batches and reconciliation. Run the full project suite and build/install the wheel in a clean environment. No tests publish live content.

Release with identifiable client version/release notes and documented client-server compatibility. Update packaging, lockfiles and deployment build assumptions together if the version changes. Verify public documentation and installed command help. Retain a server rollback image and database/config compatibility; never roll back by discarding creator state. Live provider pilots require explicitly authorized accounts and exact previews before any write.

## Acceptance gate

A stage is complete only when its regression tests, full suite, package installation and relevant deployed behavior have been verified. Report confirmed results separately from untested provider behavior and pending app review. A release must not claim universal coverage or production approval from sandbox success.
