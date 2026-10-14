# Agent error actions implementation plan

> Use superpowers:executing-plans inline, TDD and one final independent review.

**Goal:** Extend actionable errors to metrics, unified inbox and comment/community
reads while retaining partial observations and never authorizing a write replay.
**Architecture:** Shared fixed error catalogue and typed-exception classifier;
ReadReport adds compatible action fields; metrics retain a prospective failure
code. Legacy partial comment result dictionaries retain shape and gain error_detail.
**Tech stack:** Existing Python, Pydantic, HTTPX and Typer; no dependency changes.
**Spec:** Critique6 of2026-10-13-socialcli-agent-readiness.md and contract below.

## Contract

- Every emitted read error has code, action, retryable, requires_user_action and
  fixed safe guidance. Existing sanitized technical message/coverage/data remain.
- Use exception types and internal state codes, never parse arbitrary provider
  messages to infer permissions, expiry, retry or a successful mutation.
- Only transient read failures may be retryable; read budget expiry asks to adjust
  scope/budget, oversized responses cannot retry unchanged, interruptions do not
  auto-resume. Known expired SaaS grants request user reconnection; generic auth
  failure requests review, never automatic reconnection.
- Timeout/network/5xx after a write requires reconciliation, never retryable=true.
- Missing accounts and unsupported integrations have explicit actions. Cursor/state
  failures remain inspect/correct-input; keep any retained partial observations.
- Preserve exit codes and existing successful JSON shapes. Add error_detail to
  legacy partial comments/community dictionaries, without replacing their items,
  cursors, completion/interruption flags or approved mutation behavior.
- No real reads, reconnects, social writes, media changes or deployment in tests.

## Task1: Classifier and read integrations

Files: agent_errors.py, read_reports.py, metricas/modelos.py and base.py, cli.py,
inbox.py/inbox_cli.py, management/comments_cli.py/community_cli.py, tests and guides.

- [x] Test typed HTTPX failures for read/write, expired grants, response-size/deadline,
  interrupted/partial results and unknown-provider messages; observe RED then GREEN.
- [x] Integrate optional metrics failure_code and JSON stats; test typed read failure
  alongside successful metrics, preserved stored observations and actionable errors.
- [x] Integrate inbox and legacy partial comments/community responses; test that
  partial items/cursors survive and every diagnostic is actionable, not guessed.
- [x] Update canonical/generated agent guide and critique tracker. Document remaining
  publish/legacy mutation adoption separately; this does not bypass approvals.
- [x] Full suite, artifact/installed smoke, one review and Important fixes; local commit.

## Review focus

No retryable uncertain writes, no reconnection from generic authentication failure,
no discarded partial observations, no arbitrary provider text interpreted as code,
no JSON shape/exit-code regression in legacy readers, no implicit real account use.

## Verification and review

Completed against baseline067f032. Final offline suite:2569passed (51.15s),
one upstream Starlette deprecation warning. Focused actions/reads:41passed.
Wheel/sdist validation and clean installed Mac encrypted-credentials smoke passed;
all accounts fictional, zero live provider writes or account connections.

One independent final review found a POST budget-deadline gap. Regression tests
using the actual offline BudgetTransport reproduced deadline and oversized-body
misclassification (2RED); both now reconcile through ReadReport (4GREEN).
No Critical or Minor findings. No second review loop.

Rulings and limits: this increment covers read diagnostics/shared classification.
Legacy mutation entrypoint adoption and deeper typed cause propagation through
wrapped adapters remain in the critique tracker; those wrappers safely fall back
to inspection/account review, with less specific guidance. This incremental review
does not establish live provider acceptance or re-review historical SaaS changes.
The cost of these boundaries is incomplete taxonomy coverage and remaining pilot
verification; they do not authorize automatic reconnection or write replay.

Kept on the existing feature branch for continued SaaS work. No public publication,
GitHub push, deployment or media storage changes in this increment.
