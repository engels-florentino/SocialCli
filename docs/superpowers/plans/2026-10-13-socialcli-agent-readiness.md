# SocialCli SaaS: agent readiness and media delivery

Owner decision: apply the recommended launch scope and retain all eight points of
the 0.2.6 product critique. This extends the approved SaaS plan; it does not replace
the outstanding rollout, backup, billing, provider review or real-agent pilots.

## Product contract

- USD 10/month; 14-day trial begins with the first successful social connection.
- No customer videos or large documents on the SocialCli SaaS server. Accounts,
  subscription state, encrypted credentials and small operation records only.
- Original media goes from the customer's environment to the destination platform.
  Any required public media source belongs to, and is approved by, the customer.
- Prefer platform-owned future publication for YouTube and Facebook. Never claim
  scheduling from a local queue or an accepted upload alone. Preserve ID, exact
  timestamp/timezone, account, unchanged approval and remote verification.
- Instagram: immediate publishing for launch. TikTok: creator-review inbox or
  eligible audited Direct Post. No unattended native API scheduling promise for
  either; no dependency on a transient Muse/Grok conversation staying alive.
- Existing local and remote executor workflows remain compatible, explicitly
  customer-operated. They must not silently send media to the SaaS server.
- Keep the shared UI tokens/components and consistent wording. Distinguish
  preparation, upload, pending verification, platform scheduling, public visibility,
  creator confirmation and uncertainty. First comments remain separate follow-ups.
- No automatic generation, cropping or transcoding of user media.

## Eight-point critique tracker

| Point | Verified existing behavior | Work and acceptance |
|---|---|---|
| 1. Packaged agent guide | Candidate has packaged bootstrap text and `/agents`; 0.2.6 source has a detailed guide. | Extended the canonical packaged guide with publish/schedule/metrics/inbox/reply/recovery flows and offline `agent-guide`. `delivery-options --brand --json` exposes actual delivery constraints. Generated web copy stays synchronized. Remaining: real Muse/Grok fresh-install and new-conversation acceptance. |
| 2. Headless authentication | Candidate pairs via browser approval and polling, without guessing account ID; encrypted-file backend exists. | Preserve the tested headless path, durable root and external protected key. Real encrypted-store and interrupted-delivery tests run with this change. Remaining: real persistent storage in each agent runtime; no claim that ephemeral conversations provide supervision. |
| 3. Portable/reliable scheduler | Local queue, fingerprints and heartbeat exist; macOS installer could falsely report success. | Installer now fails without launchctl or successful bootstrap. Added portable `schedule-doctor`: missing/stale/error evidence and unverified persistent supervision require attention; a recent run is not future availability. Native Reel transport sends SCHEDULED with exact time and checks readback. `native-schedule submit-api` connects YouTube/Facebook adapters behind explicit pilot evidence. Exact per-publication time/timezone and multiple same-day instants now preserve legacy reservations. Linux systemd user installation/uninstallation and observed supervision diagnostics are now implemented; user-manager and logout-persistence requirements remain explicit. Remaining: real runtime/account pilots. |
| 4. Unified engagement | Inbox already joins YouTube/Facebook/Instagram with partial coverage; Meta drafts and immutable cross-provider batches exist. | Document the read → draft → full approval → apply → reconcile workflow, preserving existing safeguards. Remaining: cohesive cross-provider draft UX and pending-state presentation; TikTok comments unsupported. Do not rebuild the existing inbox or report empty partial reads as no comments. |
| 5. Metrics to insights | Snapshots and the generated summary already include deltas and platform metrics. | Implemented offline `report`: per-platform account changes, exact daily baselines, actual observation intervals, top observed lifetime/views changes and explicit partial coverage. Matching saved account identity is required; legacy snapshots remain useful without fabricated growth. Audience online hours are not available, so no best-time recommendation is invented. No additional LLM dependency or provider calls. |
| 6. Agent error taxonomy | Duplicate-risk journals and typed SaaS renewal errors exist; JSON errors are not uniform. | Added fixed `code`, `action`, `retryable`, `requires_user_action` for account login/status JSON failures. Distinguish reconnect, sign-in, recovery, reconciliation and safe read retry. Unknown/provider-controlled codes are sanitized. Extended to stats/unified inbox errors and legacy comments/community JSON reads, preserving partial observations and cursors. Typed transient reads can retry; uncertain writes/token-refresh POSTs reconcile; generic auth never implies expiry. Meta comments/inbox apply/reconcile/status and YouTube community apply/reconcile/status now support explicit JSON with complete stderr preview/prompt, unchanged exact approval, durable result guidance, and conservative reconciliation after failed apply. Primary publish/retry now expose opt-in JSON with complete preview, typed per-result actions, retained observations on interruption/persistence failure and explicit risky/protected retry exclusions. Native scheduling now offers opt-in JSON across local/SSH lifecycle operations with exact times, approval documents, retained checkpoint IDs, next actions and validated remote envelopes; invalid remote replies never trigger replay. Remaining: other management entrypoints and deeper typed cause propagation through adapters that currently wrap errors. |
| 7. Per-agent permissions | Owner-bound installations, connection scope and revocation exist. Provider tokens reach the client. | Architectural follow-up: narrower provider grants or service-mediated operations for enforceable publish/read/reply/delete boundaries. A local “deny delete” switch is not security against an agent holding a broader token. Do not market fine-grained enforcement as available. |
| 8. Media pipeline | Format/size/provenance validation and original-byte hashes exist. | Preserve validation and explain rejected media before submission. No automatic transcoding under the owner's instruction; no SocialCli media hosting. Remaining: consolidate diagnostics and platform spec coverage. The claim that formats cause most failures is unproven. |

## Scheduling release gates

Exact scheduling is now opt-in per publication with an explicit IANA timezone and
matching local offset, preserving each user's chosen instant without overflow.
Multiple distinct times per account/day are accepted; conflicts and invalid DST
local times are rejected. Legacy manifests and reservations keep the existing
09:00 America/New_York daily policy and unchanged approval serialization. Schema 3
blocks incompatible old writers after the first exact intent is persisted.
The implementation plan is `2026-10-13-exact-native-scheduling.md`. Real account
pilots and provider eligibility remain release gates; this is not live-pilot proof.

The new Facebook transport uses the official Page `video_reels` fields
`video_state=SCHEDULED` and `scheduled_publish_time`; the Video node exposes
`published` and `scheduled_publish_time`. Readback checks account, description,
unpublished state and exact time. A conservative local window is >10 minutes and
<=28 days; recheck before FINISH after a long upload. No immediate-publication fallback.
Persist remote IDs/checkpoints before further effects. A lost response is uncertain,
not permission to start again. An immediate-publication pilot cannot unlock native
scheduling in the generic dispatcher.

`submit-api` requires the existing immutable approval and an operator-provided
account-specific eligibility record with a local evidence reference. Missing proof
leaves the API closed. The record is an operator attestation, not proof of provider
approval by itself. Authenticated Page identity is checked before Facebook writes.
Native API results remain pending calendar verification; no live media was used for
these tests. Other Facebook formats remain closed. Google production remains deferred.

## Sources checked

- [Meta official SDK, Page Reels request](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/page.py)
- [Meta official SDK, Video read fields](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/advideo.py)
- [Instagram container expiry and publication](https://developers.facebook.com/documentation/instagram-platform/content-publishing)
- [TikTok Direct Post](https://developers.tiktok.com/docs/en/content-posting-api-reference-direct-post)
- [TikTok creator-review upload](https://developers.tiktok.com/docs/en/content-posting-api-reference-upload-video)
- [YouTube private scheduling](https://developers.google.com/youtube/v3/docs/videos#status.publishAt)

## Validation and rollout

Tests use fictional accounts and HTTP simulations only. Validate native scheduling,
expiry during upload, readback mismatch, duplicate prevention, headless recovery,
JSON actions, scheduler false-success, installed guide and complete suite. Build
the wheel/sdist and verify the packaged guide from outside the checkout. Run a fresh
review before committing. No production deployment, release or provider publication
is implied by passing these tests. Rebuild and review candidate artifacts before
replacing the currently deployed candidate; its old hashes no longer describe this code.


## Verified increment — 2026-10-13

Implemented the guide/discovery commands, conservative scheduler diagnostics,
account JSON action taxonomy, explicit TikTok preview, gated native API submission
and Facebook scheduled-Reel transport described above. All eight critique items
remain tracked; this is not a declaration that the whole SaaS launch is complete.

Independent review found no critical issues and two important fixes: preserve the
Facebook checkpoint target after interrupted submission, and ship usable offline
input schemas. Both are corrected. Also corrected the human diagnostic for a
recent executor error. Regression tests reproduce a lost FINISH response followed
by a fresh CLI invocation with the same remote ID and no additional requests;
another test executes the packaged manifest/inventory/observation examples through
preview, approval and fictional local reconciliation. Canonical workspace defaults
and legacy-versus-SaaS authentication instructions are consistent.

Final verification: **2,465 tests passed**, one upstream Starlette deprecation
warning; generated UI assets and `git diff --check` passed. Wheel/sdist verification
passed. Installed macOS smoke tests outside the checkout passed for workspace,
encrypted credentials, offline guide/schemas, delivery options, scheduler doctor,
account error JSON and submission help. No real platform writes, deployment,
GitHub push, release tag or package publication were performed for this increment.
