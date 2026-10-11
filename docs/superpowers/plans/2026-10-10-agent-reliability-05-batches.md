# Community approval batches Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply an immutable list of explicitly approved community actions with durable per-action results.

**Architecture:** Reuse provider ChangeSets, apply/reconcile functions, resource locks and journals. Add a separate community-batch schema and orchestrator; retain the existing YouTube metadata batch schema unchanged.

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

### Task 1: Immutable batch preparation and complete preview

**Files:** Create `socialctl/management/community_batches.py`, `socialctl/management/community_batch_cli.py`, `tests/test_community_batches.py`, `tests/test_community_batch_cli.py`; modify command registration in `socialctl/cli.py`.

**Interfaces:** `CommunityBatch` version 1 references ordered provider/child change IDs and approved child fingerprints; contains stable brand/account bindings. `prepare_community_batch(brand, child_refs: list[dict]) -> CommunityBatch`. CLI `changes prepare-batch --file PATH --brand BRAND --dry-run` prints all effects.

- [ ] **Step 1 — Write regression tests.** Test batch digest changes with child text/account/target/order, duplicate or conflicting operations reject, missing child/proposal reject, preview contains every effect and binding. Existing metadata batch commands/schema still work.
- [ ] **Step 2 — Observe failure.** Run `uv run pytest tests/test_community_batches.py tests/test_community_batch_cli.py tests/test_youtube_batches.py -q`; expected failure is the missing behavior described above, not fixture/import errors.
- [ ] **Step 3 — Implement.** Restrict child types initially to YouTube replies/video ratings, Meta comments/replies and Facebook like/unlike already supported. Load children through existing validated stores; never accept arbitrary endpoint payloads. Hash immutable ordered effects; keep receipts outside digest. Persist the proposal without remote writes.
- [ ] **Step 4 — Verify.** Run `uv run pytest tests/test_community_batches.py tests/test_community_batch_cli.py tests/test_youtube_batches.py -q` and `uv run pytest -q`; expect all tests to pass. Inspect `git diff --check` and changed files for credentials.
- [ ] **Step 5 — Commit.** Stage only the listed implementation/tests/docs and commit with `feat: prepare exact community action batches`.

### Task 2: Approved apply, resume and reconciliation

**Files:** Modify new batch modules/tests, `socialctl/management/community_changes.py`, `socialctl/management/meta_comments.py`, `socialctl/management/meta_changes.py`, `docs/ai-agents.md`.

**Interfaces:** `apply_community_batch(brand, batch_id: str, approval_digest: str) -> CommunityBatch` dispatches existing child apply functions with their exact digests. CLI `changes apply-batch ID --approval DIGEST --brand BRAND`; `changes reconcile-batch ID` only reconciles previous uncertain attempts.

- [ ] **Step 1 — Write regression tests.** Test missing/wrong approval issues no writes; stale child/account/target blocks; successful children skipped on resume; uncertain child causes no replay; conflict/rejection stops subsequent children; concurrent batches cannot duplicate operation keys. Interruption after write before receipt yields reconciliation, never automatic repetition.
- [ ] **Step 2 — Observe failure.** Run `uv run pytest tests/test_community_batches.py tests/test_community_batch_cli.py -q`; expected failure is the missing behavior described above, not fixture/import errors.
- [ ] **Step 3 — Implement.** Acquire existing operation/resource locks, verify the full batch digest and child identity before dispatch, then revalidate each child precondition immediately before writing. Default sequential stop-on-problem. Persist durable child progress; reconciliation is read-only and cannot authorize new children. Keep completed effects reported when the batch is partial; do not promise rollback.
- [ ] **Step 4 — Verify.** Run `uv run pytest tests/test_community_batches.py tests/test_community_batch_cli.py -q` and `uv run pytest -q`; expect all tests to pass. Inspect `git diff --check` and changed files for credentials.
- [ ] **Step 5 — Commit.** Stage only the listed implementation/tests/docs and commit with `feat: apply approved community batches without replay`.

Stage release: full durability suite and clean package installation; publish an example complete preview and supervised agent invocation using fictional comments. No live batch execution as part of release.
