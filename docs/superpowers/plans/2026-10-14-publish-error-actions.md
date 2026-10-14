# Publish and retry JSON implementation plan

> Use superpowers:executing-plans inline, TDD and one independent final review.

**Goal:** Structured publication outcomes for the primary publish/retry agent flow,
including preview, cancellation, partial success, uncertainty and persistence loss.
**Architecture:** Opt-in command-scoped report routes legacy output to stderr,
collects typed result observations and emits one stdout document. Existing preview,
approval, validation, durable publication and retry guards remain authoritative.
**Tech stack:** Existing Python/Typer/HTTPX; no dependency additions.
**Spec:** Critique6 of2026-10-13-socialcli-agent-readiness.md; contract below.

## Contract

- --json is opt-in for publish and retry. Preserve existing behavior/exit codes
  without it and approval requirements with it. No implicit approval or live write.
- One stdout document includes version, command, brand, slug, status, resultados,
  fixed error actions, persistence evidence and retry exclusions. Full preview,
  progress and existing prompt go to stderr; dry-run also returns exact preview.
- Keep known results/IDs even when another platform fails, execution is interrupted,
  or result/history persistence fails. Collect per-platform completed observations
  without allowing diagnostic callbacks to retry or modify provider behavior.
- Duplicate risk, pending provider confirmation and unfinished first comments get
  distinct conservative guidance; never infer successful publication from no error,
  or retryability/reconnection from provider messages. No automatic write retry.
- Retry preserves existing guards and reports skipped risky/protected platforms.
  An empty/noop retry cannot hide protected unresolved operations.
- Exceptions after apply starts reconcile; before start they cannot claim sent.
  Fixed machine diagnostics never include raw exception secrets. Sanitize technical
  result error/warning fields without modifying user-supplied publication text.
- Tests use fictional brands, preproduced fixture metadata and synthetic adapters
  /offline transport; no real providers, media generation or server storage.
- Other management/native command coverage and live acceptance remain tracked.

## Task1: Primary publication diagnostics

- [x] RED tests: dry-run, refusal, missing/invalid input, success/partial/duplicate
  risk, pending confirmation, partial interruption, persistence failure, mixed retry.
- [x] Implement report and minimal publisher/CLI hooks; run existing and new tests.
- [x] Update canonical/generated guide and critique tracker.
- [x] Full suite and installed build smoke; independent review and Important fixes.
- [x] Commit locally, retaining overall SaaS rollout gates.

## Review focus

No approval bypass; complete preview survives stream separation; retry cannot upload
an uncertain or protected platform; partial results survive interruption; error
hints never contradict durable IDs/first-comment states; legacy exits remain.

## Verification and review

Baselineb48111e. New primary CLI tests16passed; related CLI/publisher/community
suite134passed. Final full suite2607passed,51.71s, one upstream Starlette warning.
Build wheel/sdist validation and clean installed Mac encrypted-credentials smoke
passed. Installed dry-run JSON retained the exact preview with zero external calls
before review fixes; final callbacks exercised through offline integration tests.

One independent review found two Important defects, both fixed in one pass:
reporting errors could block an approved first comment/result persistence, and an
interrupt saving the media journal lost a confirmed ID. Three integration tests
reproduced RED; separate advisory observation now occurs before media save, critical
persistence callback semantics remain intact, reporting failures cannot block effects
or result/history saves, and confirmed IDs survive the interrupted save. No Minor
findings. No second review loop.

Rulings: existing approval and --yes semantics remain; JSON is never permission.
Legacy exit0 for pending platform confirmation/incomplete first comments remains,
with explicit partial JSON status; consumers must inspect it. Reporting failures
also remain diagnostic and require durable-state inspection. Live providers,
native scheduling and other management entrypoints remain outside this increment;
no full-SaaS or production-readiness claim follows from offline tests.

Final artifact hashes:
- wheela124c95159cafa32ae2bfcd07c918978523127b9d119984d1d10e5ba192af995
- sdiste9b0fa3d09c6135c599dddd8f0578c8d89984f47985d0f7be1f489991484cefc

Kept on feat/socialcli-saas for continued work. No public release, GitHub push,
server deployment, real account operation or customer media storage in this increment.
