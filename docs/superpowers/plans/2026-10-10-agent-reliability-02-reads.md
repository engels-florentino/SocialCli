# Bounded reads and structured diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make metrics and community reads finish within a known budget and provide parseable results.

**Architecture:** Add one read-budget transport boundary and versioned report helpers. Keep existing readers and local snapshot retention; adopt common behavior at CLI boundaries without replacing historical schemas.

**Tech Stack:** Python >=3.11, Typer, HTTPX, Pydantic, pytest; existing FastAPI and cryptography where needed.

**Spec:** [../specs/2026-10-10-agent-reliability-design.md](../specs/2026-10-10-agent-reliability-design.md) (approved 2026-10-10).

## Global Constraints

- All application text and documentation remain English.
- Public application: `app/SocialCli`; Python imports remain `socialctl`.
- Preserve current command defaults, historical journals, stored credentials, and existing approval requirements. Version new data formats explicitly.
- Never automatically replay an uncertain write. Never infer absence from incomplete results.
- Never publish or apply remote changes without the full preview and explicit approval of exact effects.
- Use fictional accounts and isolated workspaces for tests. No live publication in tests.
- Run commands from `app/SocialCli`; creator data stays outside the package.
- Keep the existing single application folder; do not create another checkout/worktree.
- Every task follows red/green regression testing, then the full `uv run pytest -q` suite before commit.
- Provider assumptions must be checked against current official documentation through Context7 (official web documentation as fallback) before implementation.

## Review Focus

1. Closed/noninteractive stdin must fail before OAuth unless explicit account binding is supplied (connection task).
2. Preview in another browser must not claim exclusive OAuth ownership (landing-page task).
3. Slow/chunked responses and retry waits must count against the whole deadline (bounded-reads task).
4. Changing comment text or account after preparation must invalidate approval (batch task).
5. Partial scans, invalid cursors and daylight-saving changes must not silently discard comments (inbox task).

Each plan lists tests for its owned focus cases; other focus cases belong to the named component plans in the index.

### Task 1: Whole-operation deadline and bounded retries

**Files:** Create `socialctl/read_budget.py`, `tests/test_read_budget.py`; modify `socialctl/cli.py`, `socialctl/management/community_cli.py`, `socialctl/management/comments_cli.py`, `socialctl/metricas/base.py`.

**Interfaces:** `ReadBudget(seconds: float, *, clock=time.monotonic)` exposes `remaining() -> float` and `check() -> None`. `BudgetTransport(inner: httpx.AsyncBaseTransport, budget: ReadBudget)` provides a synchronous HTTPX transport facade over a cancellable async request-and-body read, with one owned event loop and explicit close. A wall-clock cancellation scope covers both headers and body; per-socket timeouts alone are insufficient. Raise `ReadDeadlineExceeded` with a sanitized fixed message.

- [ ] **Step 1 — Write regression tests.** Test simulated clock advances during chunks, token retrieval and retry waits stop within budget; pending platforms are not started; earlier observations survive. Test invalid/zero/infinite timeout; 429 Retry-After never exceeds remaining budget. A write is never automatically retried.
- [ ] **Step 2 — Observe failure.** Run `uv run pytest tests/test_read_budget.py tests/test_cli_stats.py -q`; expected failure is the missing behavior described above, not fixture/import errors.
- [ ] **Step 3 — Implement.** Add --timeout default 120 seconds for the whole read operation, validate finite positive values, and cap individual request timeouts to min(existing timeout, remaining). Enforce the remaining wall-clock budget around the complete async network/body operation; do not claim a total deadline from synchronous per-read timeout settings alone. Bound buffered read bodies to 8 MiB and return a structured partial/error when exceeded; do not use this read-only adapter for uploads or large media downloads. Progress identifies platform and page on stderr without secrets. Add bounded GET retries only (at most two retries, honoring Retry-After within budget); preserve write uncertainty semantics. Interrupts close transports and report partial state.
- [ ] **Step 4 — Verify.** Run `uv run pytest tests/test_read_budget.py tests/test_cli_stats.py -q` and `uv run pytest -q`; expect all tests to pass. Inspect `git diff --check` and changed files for credentials.
- [ ] **Step 5 — Commit.** Stage only the listed implementation/tests/docs and commit with `feat: bound read operations with visible progress`.

### Task 2: Explicit JSON output and diagnostic envelope

**Files:** Create `socialctl/read_reports.py`, `tests/test_read_reports.py`; modify `socialctl/cli.py`, `socialctl/management/community_cli.py`, `socialctl/management/comments_cli.py`, `tests/test_cli_stats.py`, `tests/test_youtube_community_cli.py`, `tests/test_meta_comments_cli.py`, `docs/ai-agents.md`.

**Interfaces:** `ReadReport` version 1 includes status, observed_at, per-account coverage, data, errors and optional supported cursor. `emit_report(report: ReadReport) -> int` prints one JSON object and returns 0 for complete success or 1 for partial/failure. Existing JSON-only commands retain their current result schema and gain a compatible --json flag.

- [ ] **Step 1 — Write regression tests.** Parse stdout as exactly one JSON document on complete, partial and failed stats; stderr contains progress; no missing values converted to zero. Assert existing community JSON schema is unchanged with/without --json. Errors retain safe code/context and remove exact known secret plus sensitive URL fields.
- [ ] **Step 2 — Observe failure.** Run `uv run pytest tests/test_read_reports.py tests/test_cli_stats.py tests/test_youtube_community_cli.py tests/test_meta_comments_cli.py -q`; expected failure is the missing behavior described above, not fixture/import errors.
- [ ] **Step 3 — Implement.** Add --json to stats/comments/community surfaces. Keep human default where it exists and legacy exit behavior where promised. Put snapshot path, fresh-vs-merged coverage and observation provenance in stats report; keep retention warnings off JSON stdout. Centralize sanitized diagnostic fields rather than dumping exception/request objects.
- [ ] **Step 4 — Verify.** Run `uv run pytest tests/test_read_reports.py tests/test_cli_stats.py tests/test_youtube_community_cli.py tests/test_meta_comments_cli.py -q` and `uv run pytest -q`; expect all tests to pass. Inspect `git diff --check` and changed files for credentials.
- [ ] **Step 5 — Commit.** Stage only the listed implementation/tests/docs and commit with `feat: expose structured read results and safe diagnostics`.

Stage release: build/install the wheel, publish updated examples, and verify complete/partial/timeout CLI cases against fictional responses. Increment the client patch release and update lock/build assumptions together. No service deployment unless broker behavior changes.
