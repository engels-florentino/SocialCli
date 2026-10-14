# Community write actions implementation plan

> Use superpowers:executing-plans inline, TDD and one independent final review.

**Goal:** Give agents explicit JSON outcomes for approved Meta comments/inbox replies
and YouTube community mutations without weakening exact approval or write journals.
**Architecture:** Small output helper handles fixed safe diagnostics, full stderr
preview/prompt in explicit JSON mode, and additive result error_detail. Existing
service functions and durable proposals remain authoritative. No new approval flag.
**Tech stack:** Existing Typer/HTTPX/Pydantic; no dependency changes.
**Spec:** Critique6 in2026-10-13-socialcli-agent-readiness.md plus contract below.

## Contract and scope

- Add --json to Meta apply/reconcile/apply-draft/reconcile-draft and YouTube
  community apply/reconcile/status; fix Meta status JSON errors.
- Preserve legacy successful result shapes, IDs, journals and normal CLI output.
  JSON mode emits exactly one document on stdout, full preview/prompt on stderr.
- Exact fingerprint remains mandatory. EOF, interruption and wrong approval cannot
  send a mutation. Never add --yes or an automatic approval path.
- Typed result states select fixed actions; never derive actions from provider text.
  Uncertain/partially applied/accepted-unverifiable outcomes require reconciliation,
  never retryable=true. Verified outputs remain unannotated.
- Exceptions after entering the apply service may follow a successful remote write.
  Unknown/persistence/interruption failures then require reconciliation. No automatic
  replay. Before apply, missing workspace/proposal and approval failures are explicit.
- Preserve existing exit codes, including reconcile/status legacy non-error exits.
- Reconciliation does not send another mutation. Test real stores and service code
  over fictional offline transports; no real accounts/media/network.
- Publish/retry/native-schedule/other management command adoption remains a subsequent
  increment, explicitly tracked. This is not completion of all error-taxonomy work.

## Task1: Approved community mutation JSON

- [x] Tests: complete preview/prompt separated from JSON, correct/wrong/EOF approval,
  lost POST response durable uncertainty, reconcile never reposts, storage failure
  after dispatch, absent brand/change, status compatibility. Observe RED.
- [x] Add operation-output helper and minimal command integrations. Verify GREEN.
- [x] Update canonical/generated guide and critique tracker with actual coverage.
- [x] Full suite, build/install smoke, independent review, Important fix pass, commit.

## Review focus

No duplicate write after uncertain outcome; JSON cannot bypass approval; no preview
loss; no secret diagnostics; unchanged IDs/journals; failure before vs after dispatch;
Meta inbox ownership/frequency guards; old command behavior and exit compatibility.

## Verified result

Baseline361bfb5. Focused existing/new community CLI+inbox tests57passed; entire
suite2591passed,51.24s, one upstream Starlette deprecation warning. Build artifacts
validated. Clean installed Mac encrypted-credential smoke and installed Meta/YouTube
apply JSON missing-workspace checks passed with zero external calls.

One independent final review: no Critical/Important findings;22new tests passed
independently. Deferred minor: interrupting read-only reconciliation/status says
"Approval was cancelled" although those commands did not request approval. The
hint remains nonretryable and no write is sent; improve diagnostic wording later.

Rulings: JSON approval UI belongs on stderr so stdout remains one machine document;
agents must retain both streams and show the full preview. No additional product
approval gate was introduced for this already-authorized bounded change. Community
mutation coverage is an increment; publish/retry/native/other management adoption
remains pending, not claimed complete. Exceptions immediately after service entry
conservatively request reconciliation even when an internal preflight prevented the
write; cost is an extra status/reconciliation check, avoiding unsafe replay.

The review does not prove live provider acceptance or completion of the whole SaaS.
Local work and artifacts remain in feat/socialcli-saas; no public publication,
GitHub push, server deployment, live account operation or media change this increment.
Public apex resolves71.250.63.180; pilot.socialcli.app still absent in DNS.
