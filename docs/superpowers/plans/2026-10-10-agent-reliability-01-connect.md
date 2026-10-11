# Agent connection and recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Connect an explicitly authorized account without interactive stalls or preview-consumed URLs.

**Architecture:** Keep the broker protocol and existing activation/rollback logic. Separate the read-only landing page from the atomic OAuth-start POST; add explicit binding arguments to the existing connect command.

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

### Task 1: Stable noninteractive account binding

**Files:** Modify `socialctl/connections/cli.py`, `tests/test_connections_cli.py`, `AGENTS.md`, `README.md`, `docs/ai-agents.md`, `docs/ai-agents.html`, `docs/connections.md`, `docs/connections.html`.

**Interfaces:** Extend `connect` with `account_id: str | None` (`--account-id`), `yes: bool` (`--yes`), and `replace_independent: bool` (`--replace-independent`). No broker payload/schema change.

- [x] **Step 1 — Write regression tests.** Add `test_noninteractive_requires_exact_account_before_pairing` (zero authorization POSTs on missing flags); `test_connect_yes_binds_only_requested_account` (requested ID is bound even if accounts reorder); mismatch causes no completion; replacement requires the dedicated flag with noninteractive approval. Existing interactive tests must still pass.
- [x] **Step 2 — Observe failure.** Run `uv run pytest tests/test_connections_cli.py -q`; expected failure is the missing behavior described above, not fixture/import errors.
- [x] **Step 3 — Implement.** Validate options and stdin before pairing. `--yes` requires `--account-id`. Select exact returned ID, validate linked Page, print the exact selected binding, skip only the final connection confirmation with explicit `--yes`. `--replace-independent` plus `--yes` is required for noninteractive replacement. Interactive users retain their explicit replacement confirmation. Preserve locks, cleanup and installation rollback. Flush the printed URL and use server expiry/poll interval after validating their types/ranges; retain safe 600-second/2-second legacy defaults when omitted.
- [x] **Step 4 — Verify.** Run `uv run pytest tests/test_connections_cli.py -q` and `uv run pytest -q`; expect all tests to pass. Inspect `git diff --check` and changed files for credentials.
- [x] **Step 5 — Commit.** Stage only the listed implementation/tests/docs and commit with `feat: support explicit noninteractive account binding`.

### Task 2: Preview-resistant OAuth landing page

**Files:** Modify `socialctl/connection_service/app.py`, `tests/test_connection_service.py`, `tests/test_connections_cli.py`, `docs/connections.md`, `docs/connections.html`; create `socialctl/connection_service/landing.py`, `tests/test_connection_landing.py`.

**Interfaces:** `landing_page(authorization_id: str, form_token: str) -> str` renders fixed escaped HTML. GET/HEAD `/connect/{id}` are read-only; POST `/connect/{id}` starts OAuth. A short-lived authenticated form token binds authorization ID, nonce and expiry to a secure HttpOnly SameSite cookie; verify using server encryption keys including retained rotation keys.

- [x] **Step 1 — Write regression tests.** GET twice and HEAD leave stored authorization without state; two separate preview/user browsers can each view the page and the actual user can POST; wrong-cookie/missing/expired/foreign-origin forms fail without consuming state; duplicate POST and concurrent starts produce exactly one provider redirect; callback state/browser-binding and cookie isolation tests remain green.
- [x] **Step 2 — Observe failure.** Run `uv run pytest tests/test_connection_landing.py tests/test_connection_service.py -q`; expected failure is the missing behavior described above, not fixture/import errors.
- [x] **Step 3 — Implement.** Render a Connect form without third-party resources or auto-submit. Reuse no-store, no-referrer and restrictive CSP headers; add noindex. Validate bounded URL-encoded form data without adding multipart dependencies. Validate same-origin Origin when present and the cookie/form token always. Set the existing callback browser cookie only for the successful atomic start; keep PKCE and OAuth state generation in that transaction. Do not store exclusive browser ownership during GET. Error pages explain restart/cancel without exposing token or provider URL. Adapt test browser helpers to GET landing then explicit POST; keep a legacy-server CLI fixture.
- [x] **Step 4 — Verify.** Run `uv run pytest tests/test_connection_landing.py tests/test_connection_service.py tests/test_connections_cli.py -q` and `uv run pytest -q`; expect all tests to pass. Inspect `git diff --check` and changed files for credentials.
- [x] **Step 5 — Commit.** Stage only the listed implementation/tests/docs and commit with `fix: require explicit user action before starting OAuth`.

### Task 3: Recovery and compatible release

**Files:** Modify `tests/test_connections_cli.py`, `tests/test_connection_service.py`, `pyproject.toml`, `uv.lock`, `deploy/connection-service/Dockerfile`, `deploy/connection-service/README.md`, `docs/connections.md`.

**Interfaces:** Existing protocol version 1 and connection metadata remain compatible. Release client 0.2.1; Docker build selects exactly one built socialcli wheel rather than a hard-coded old filename.

- [x] **Step 1 — Write regression tests.** Test denied scopes, closed stdin, keyring write/activation failure and interrupted install preserve prior credentials; concurrent installations reject the second; inconsistent local metadata yields a diagnostic without guessing an account. Exercise old-client/new-server landing and new-client/legacy-server redirect fixtures.
- [x] **Step 2 — Observe failure.** Run `uv run pytest tests/test_connections_cli.py tests/test_connection_service.py -q`; expected failure is the missing behavior described above, not fixture/import errors.
- [x] **Step 3 — Implement.** Preserve provisional expiry/activation and existing cleanup; report cleanup failure without hiding the original failure. Update agent examples to --no-browser --account-id ID --yes only after user authorization. Document SIGKILL/power-loss recovery limits. Bump the release and refresh uv.lock; remove Docker wheel version coupling while rejecting multiple wheels.
- [x] **Step 4 — Verify.** Run `uv run pytest tests/test_connections_cli.py tests/test_connection_service.py -q` and `uv run pytest -q`; expect all tests to pass. Inspect `git diff --check` and changed files for credentials.
- [x] **Step 5 — Commit.** Stage only the listed implementation/tests/docs and commit with `release: verify agent connection recovery and compatibility`.

## Stage delivery

- [x] Build with `uv build --wheel`; install the wheel into an isolated temporary environment and verify `socialcli connect --help` lists the new flags.
- [x] Back up/tag the running server image and preserve database/configuration. Deploy the server first, verify health and GET/HEAD landing behavior with a disposable pairing, then cancel it using its polling capability. No provider login or publication needed for this check.
- [x] Publish the client commit and updated English pages; update the local installed client. Report the exact release/commit and pending live-account validation separately.
