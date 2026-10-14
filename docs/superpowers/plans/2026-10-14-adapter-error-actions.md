# Adapter error actions implementation plan

> Use superpowers:executing-plans inline, TDD and one final independent review.

**Goal:** Preserve typed recovery guidance across sanitized authentication and
YouTube/Meta adapter exceptions, including partial paginated reads.
**Architecture:** Carry only a validated fixed error code across an explicit
exception wrapper. Never retain raw provider exceptions in new diagnostic fields.
The classifier accepts hints only from known wrapper types; uncertain-write types
always take precedence. Existing report shapes gain actionable partial diagnostics.
**Tech stack:** Existing Python/HTTPX; no dependency or endpoint changes.
**Spec:** Critique 6 in 2026-10-13-socialcli-agent-readiness.md and the established
read/write contract in 2026-10-14-agent-error-actions.md.

## Global constraints

No live account operations, reconnection, write replay, media work or deployment.
Keep existing exception inheritance, sanitized human messages and exit behavior.
Unknown/provider text cannot choose a code. A POST refresh can be uncertain even
when the requested command is a read. Retain partial items and coverage.

## Task 1: Preserve typed failures through adapters and pagination

Files: agent_errors.py, auth.py, management/youtube.py, youtube_resources.py,
meta_client.py, meta_comments.py, youtube_community.py, read_reports.py,
metricas/base.py, management/meta_comment_inbox.py, inbox.py;
new tests/test_adapter_error_actions.py; canonical/generated agent guide and tracker.

Interfaces: add with_error_code(wrapper, cause, *, read_only, fallback=None),
returning the same wrapper with a fixed internal diagnostic code, and an HTTP-status
helper using method/status evidence. error_document consumes only known wrappers.
Partial results carry error_detail from the caught exception; annotation validates
its code against the catalogue and regenerates action flags.

- [x] Offline transport tests: GET timeout/429/503 versus auth/permission denial;
  POST timeout/response-limit remains reconcile; expired SaaS versus generic auth;
  sanitized wrapper message and diagnostics never expose raw tokens; partial items
  survive pagination failure with correct action. Run to observe RED.
- [x] Implement code-only propagation at the actual catch boundaries, HTTP method/
  status classification and partial result preservation. No exception-text parsing.
- [x] Run focused adapter/action/auth tests, then full suite. Expected: all pass.
- [x] Update guide/tracker with exact coverage and remaining command families.
- [x] One read-only final review, fix Important findings, commit locally.

## Review focus

1. Write-uncertain types override all attached retry hints.
2. Authentication refresh failures retain POST semantics across nested wrappers.
3. Legacy exception strings remain sanitized; no raw cause chain is introduced.
4. Partial pagination failure retains actual rows and does not prove absence.
5. Unknown/malicious messages and unsupported hint codes never request reconnect.

Management JSON entrypoint expansion and other unmodified adapter families remain
open critique work. This increment does not establish complete SaaS acceptance.

## Verification and review

Baseline `54e37f1`. Offline transport tests reproduced 24 lost-cause errors before
implementation. Additional RED cases covered refresh HTTP503, four lost SaaS token
POSTs and the metrics Auth branch dropping the typed code. Known uncertain-write
behavior and unknown-code guards also remain tested.

One independent final review found an Important consumer omission: comments sync
rebuilt a partial result without its error detail. Unified inbox had the analogous
problem in enumeration, comments and replies. Four actual consumer regressions
failed before the fix, then passed after preserving typed guidance through the
aggregation paths. Existing deadline coverage now checks the structured code while
retaining six verified comments and exactly one failed reply read. The focused
adapter/inbox/store suite passed 124 tests. No second review loop.

Review boundaries remain open requirements: live provider acceptance, unconverted
management commands, browser/deployment behavior and complete SaaS readiness.
Offline typed diagnostics do not establish those outcomes. No social operations,
public release, deployment or media storage changes occurred in this increment.

Final post-review verification: **2761 tests passed**, one existing Starlette/httpx
warning, 55.62 seconds. Generated assets check and wheel/sdist allowlist passed.
A clean installed macOS wheel with `headless-credentials` passed the existing
workspace/encrypted-store/packaged-guide smoke and typed refresh/inbox checks;
zero external calls. The temporary virtual environment was removed afterward.
An initial smoke invocation omitted that optional extra and failed for missing
cryptography; it was rerun with the intended installation profile.

Final artifact SHA256:
- Wheel: f3d930e381d29d70b921d5557947f9baebdbcb548fc6209b3fe1ea0f692af025
- Sdist: ddf79682b5ec818881df64efa7c499c06b2d2e9ad9b0eb608747100cf840d9eb
