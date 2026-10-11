# Community reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve useful YouTube observations and verify Meta interactions without duplicate writes.

**Architecture:** Keep existing provider-specific ChangeSets, ownership checks and operation locks. Fix endpoint-specific assumptions and add bounded direct reconciliation; do not replace an uncertain result with guessed success.

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

### Task 1: YouTube partial reads and exact reply verification

**Files:** Modify `socialctl/management/youtube_community.py`, `socialctl/management/community_changes.py`, `tests/test_youtube_community.py`, `tests/test_youtube_community_cli.py`; update `docs/ai-agents.md`.

**Interfaces:** Preserve list result fields items/pages/complete/error; add optional provider cursor only when valid. Existing prepare/apply signatures remain. Exact parent/thread/video inspection is independent of unrelated listing completeness.

- [ ] **Step 1 — Write regression tests.** Add sanitized response fixtures for valid missing/approximate totals per official endpoint contract; valid rows remain when later metadata/page fails. Repeated cursor and duplicate ID stay incomplete. Preparing a reply to a verified parent can succeed with unrelated partial enumeration; an unresolved previous identical write still blocks replay.
- [ ] **Step 2 — Observe failure.** Run `uv run pytest tests/test_youtube_community.py tests/test_youtube_community_cli.py -q`; expected failure is the missing behavior described above, not fixture/import errors.
- [ ] **Step 3 — Implement.** Fetch official comments/commentThreads pagination contracts first. Validate item identity before accepting rows; distinguish uncertain completeness from invalid items. Remove unsupported total equality assumptions only where provider docs/fixtures justify it. Keep exact actor/parent/video verification, durable operation identity and destructive-action checks.
- [ ] **Step 4 — Verify.** Run `uv run pytest tests/test_youtube_community.py tests/test_youtube_community_cli.py -q` and `uv run pytest -q`; expect all tests to pass. Inspect `git diff --check` and changed files for credentials.
- [ ] **Step 5 — Commit.** Stage only the listed implementation/tests/docs and commit with `fix: preserve verified partial YouTube community reads`.

### Task 2: Meta comment reconciliation and likes diagnostics

**Files:** Modify `socialctl/management/meta_comments.py`, `socialctl/management/meta_client.py`, `socialctl/management/meta_changes.py`, `tests/test_meta_comments.py`, `tests/test_meta_management.py`, `tests/test_meta_comments_cli.py`; update `docs/platforms.html`, `SETUP.md`.

**Interfaces:** Add `MetaCommentsClient.inspect_comment(comment_id: str) -> dict` with provider-supported actor/parent/content verification. Keep reconcile_comment and apply_comment public signatures. Share sanitized Graph error extraction across the two Meta clients.

- [ ] **Step 1 — Write regression tests.** Test verified provider-returned ID despite different listing representation, wrong actor/parent/text, delayed readback, deleted object and partial listing. Ambiguous matches remain uncertain and cause no second POST. Error tests cover code/subcode, token expiry, unsupported target, permissions, quota and redaction. Likes require correct Page identity; unsupported Instagram actions issue no write.
- [ ] **Step 2 — Observe failure.** Run `uv run pytest tests/test_meta_comments.py tests/test_meta_management.py tests/test_meta_comments_cli.py -q`; expected failure is the missing behavior described above, not fixture/import errors.
- [ ] **Step 3 — Implement.** Check current official readback/likes contracts and a sanitized representative failure before defining ID normalization. Prefer exact direct reads; validate any alias from provider evidence rather than guessed string conversion. Bound eventual-consistency read retries with ReadBudget; never retry mutation. Include actionable permission guidance only when the provider code supports it. Document video-rating/comment-like differences and app-vs-granted scope distinction.
- [ ] **Step 4 — Verify.** Run `uv run pytest tests/test_meta_comments.py tests/test_meta_management.py tests/test_meta_comments_cli.py -q` and `uv run pytest -q`; expect all tests to pass. Inspect `git diff --check` and changed files for credentials.
- [ ] **Step 5 — Commit.** Stage only the listed implementation/tests/docs and commit with `fix: reconcile Meta comments and expose actionable errors`.

Stage release: rerun all community and publication durability regressions, build/install, and publish the capability matrix with untested live-provider behavior marked explicitly. A live write pilot needs its own complete approved preview.
