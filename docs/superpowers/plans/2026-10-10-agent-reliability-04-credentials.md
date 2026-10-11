# Headless credential storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Support explicit encrypted-file capability storage without weakening the default OS keyring.

**Architecture:** Introduce a small capability-store interface behind the existing keychain functions. Store versioned authenticated encrypted capabilities locally; an external protected key file supplies the master key.

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

### Task 1: Encrypted backend and explicit configuration

**Files:** Create `socialctl/connections/credential_store.py`, `socialctl/connections/file_store.py`, `tests/test_credential_store.py`; modify `socialctl/connections/keychain.py`, `socialctl/connections/cli.py`, `pyproject.toml`, `uv.lock`.

**Interfaces:** `CapabilityStore` has ensure_available/get/save/delete using the existing brand/platform/metadata arguments. `store_for(brand) -> CapabilityStore` selects default keyring or explicitly configured encrypted-file backend. Per-workspace configuration records backend/path/key-file path, never the key.

- [x] **Step 1 — Write regression tests.** Test default keyring unchanged, no silent fallback, roundtrip, wrong/missing key, authenticated binding mismatch, corruption, unknown version, insecure permissions, symlink and path replacement. Two writers preserve records; interrupted atomic replacement preserves old data.
- [x] **Step 2 — Observe failure.** Run `uv run pytest tests/test_credential_store.py tests/test_connections_cli.py -q`; expected failure is the missing behavior described above, not fixture/import errors.
- [x] **Step 3 — Implement.** Use cryptography authenticated encryption through a headless-credentials optional extra. Bind plaintext envelope to service origin, stable brand identity, platform/account and version before encrypting. Require protected external key file and restrictive encrypted-file permissions. Use same-directory atomic replacement and existing locking patterns; Linux/macOS headless supported, document platform differences. Validate chosen backend before OAuth; use a neutral secure credential store error rather than assuming keyring.
- [x] **Step 4 — Verify.** Run `uv run pytest tests/test_credential_store.py tests/test_connections_cli.py -q` and `uv run pytest -q`; expect all tests to pass. Inspect `git diff --check` and changed files for credentials.
- [x] **Step 5 — Commit.** Stage only the listed implementation/tests/docs and commit with `feat: add opt-in encrypted capability storage`.

### Task 2: Migration, rotation and operational documentation

**Files:** Create `socialctl/connections/credential_cli.py`, `tests/test_credential_cli.py`; modify `socialctl/connections/cli.py`, `docs/connections.md`, `docs/connections.html`, `docs/ai-agents.md`, `SETUP.md`.

**Interfaces:** Add brand-scoped `credentials configure --backend encrypted-file --store-file PATH --key-file PATH`, `credentials status`, `credentials migrate --to BACKEND`, and `credentials rotate --new-key-file PATH`. Rotation operates on encrypted-file backend only. Commands never accept inline key values.

- [x] **Step 1 — Write regression tests.** Test migration verifies destination before source removal, failure preserves original, rotation keeps old data/key usable on failure, new key decrypts all records on success, disconnect respects selected backend. Status and all errors contain no capability/key/provider token.
- [x] **Step 2 — Observe failure.** Run `uv run pytest tests/test_credential_cli.py tests/test_credential_store.py -q`; expected failure is the missing behavior described above, not fixture/import errors.
- [x] **Step 3 — Implement.** Provide explicit setup/migration confirmations. Configuration records only paths/backend, validates that key and store resolve to different files outside the repository, and rejects existing store/key aliases. Keep configuration update atomic with destination validation; report recovery instructions for interruption windows. Rotation rewrites atomically with preserved old backup until verified. Document backup/key separation, unrecoverable key loss and dependency installation.
- [x] **Step 4 — Verify.** Run `uv run pytest tests/test_credential_cli.py tests/test_credential_store.py -q` and `uv run pytest -q`; expect all tests to pass. Inspect `git diff --check` and changed files for credentials.
- [x] **Step 5 — Commit.** Stage only the listed implementation/tests/docs and commit with `feat: support credential migration and key rotation`.

Stage release: install default and headless extras in clean environments; run credential-store probes with fictional capabilities, never real secrets. Verify installed docs/help and patch version/lockfile.
