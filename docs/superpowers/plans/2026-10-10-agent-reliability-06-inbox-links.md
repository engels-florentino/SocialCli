# Unified inbox and exact source links Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Unify supported comments and propose correct source links for each explicit clip.

**Architecture:** Reuse Meta inbox persistence, add YouTube read adapters and normalized coverage. Keep source-video mapping in the creator brand workspace and resolve it before existing previews; inbox never executes actions.

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

### Task 1: Unified incremental inbox

**Files:** Create `socialctl/inbox.py`, `socialctl/inbox_cli.py`, `tests/test_inbox.py`, `tests/test_inbox_cli.py`; modify `socialctl/management/meta_comment_inbox.py`, `socialctl/cli.py`, `docs/ai-agents.md`.

**Interfaces:** `resolve_since(value: str, timezone_name: str, now: datetime) -> datetime`; `sync_brand_inbox(brand, *, since: datetime, budget: ReadBudget) -> ReadReport`. CLI `inbox --brand BRAND --since VALUE --timezone IANA --json`; defaults to human output.

- [x] **Step 1 — Write regression tests.** Test yesterday/ayer/date/RFC3339 intervals, missing/invalid timezone, daylight-saving boundary, per-account dedup, edited comments and partial failures. Invalid provider cursor triggers bounded overlap recovery without advancing complete watermark. Unsupported TikTok remains visible unavailable rather than empty success.
- [x] **Step 2 — Observe failure.** Run `uv run pytest tests/test_inbox.py tests/test_inbox_cli.py tests/test_meta_comment_inbox.py -q`; expected failure is the missing behavior described above, not fixture/import errors.
- [x] **Step 3 — Implement.** Enumerate eligible owned videos/media through existing inventory/provider readers within the shared budget. Reuse Meta sync and add YouTube adapter. Keep platform/account/comment identity, create/update/observation times, coverage and pending replies distinct. Persist complete watermarks only after complete traversal; retain partial progress separately. --since filters by documented creation semantics, while edited observations are reported distinctly. Never infer deletion from partial reads or auto-reply/classify with an external model.
- [x] **Step 4 — Verify.** Run `uv run pytest tests/test_inbox.py tests/test_inbox_cli.py tests/test_meta_comment_inbox.py -q` and `uv run pytest -q`; expect all tests to pass. Inspect `git diff --check` and changed files for credentials.
- [x] **Step 5 — Commit.** Stage only the listed implementation/tests/docs and commit with `feat: expose unified brand comment inbox`.

### Task 2: Exact clip-to-video mapping and approved linking

**Files:** Create `socialctl/source_links.py`, `tests/test_source_links.py`; modify `socialctl/cli.py`, `socialctl/publication_steps.py`, existing first-comment preview tests, `tests/test_first_comment_durability.py`, `docs/ai-agents.md`, `SETUP.md`.

**Interfaces:** Brand `source-videos.yml` version 1 maps explicit post slug to exact YouTube video_id and optional series. `resolve_source_link(brand, post_slug: str) -> str | None` returns canonical URL or an explicit mapping error. Existing publication-step machinery owns approved follow-up comments.

- [x] **Step 1 — Write regression tests.** Test duplicate YAML keys/slugs, malformed IDs, conflicting explicit link/mapping, missing map and ambiguous series; no latest-title guessing. Preview includes exact generated text/destination/effect; changing map after approval invalidates intent. Interruption/retry cannot duplicate follow-up comment.
- [x] **Step 2 — Observe failure.** Run `uv run pytest tests/test_source_links.py tests/test_first_comment_durability.py -q`; expected failure is the missing behavior described above, not fixture/import errors.
- [x] **Step 3 — Implement.** Validate strict YAML schema and resolve by exact post slug. Expose missing mapping as unresolved when source linking is requested; require a valid mapping before proposing that effect. Keep generated links inside complete preview and existing durable steps. Platform support determines placement; do not promise clickable Instagram captions or unsupported pinning/comments. No additional posting or cadence changes.
- [x] **Step 4 — Verify.** Run `uv run pytest tests/test_source_links.py tests/test_first_comment_durability.py -q` and `uv run pytest -q`; expect all tests to pass. Inspect `git diff --check` and changed files for credentials.
- [x] **Step 5 — Commit.** Stage only the listed implementation/tests/docs and commit with `feat: propose exact approved source-video links`.

## Final release acceptance

- [x] Run `uv run pytest -q`, `git diff --check` and `uv build --wheel`; verify clean default/headless installs and command discovery.
- [x] Publish capability/permission/limitation matrix, CLI JSON/exit contracts, provider review restrictions and agent examples. Check web pages against Markdown.
- [x] Record exact release/commit and compatible server version; preserve rollback image/database, verify docs/health and safe disposable pairing cleanup.
- [x] Report verified tests, untested live-provider behavior, deferred approval requirements and remaining issues separately. No claim of 100% coverage.

Acceptance evidence: [release 0.2.3 verification](../../release-0.2.3.md). No live provider writes performed.
