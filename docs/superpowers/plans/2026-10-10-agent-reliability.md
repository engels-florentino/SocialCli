# Agent reliability delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the approved reliability specification in independently verified stages.

**Architecture:** Six component plans reuse existing connection, inbox, ChangeSet and batch foundations. Execute in the sequence below; ship an independently tested component before moving on.

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

## Component plans and dependencies

| Order | Plan | Depends on | Delivered behavior |
|---|---|---|---|
| 1 | [Connection and recovery](2026-10-10-agent-reliability-01-connect.md) | Existing broker | Exact noninteractive binding; preview-resistant OAuth; compatible rollout |
| 2 | [Bounded reads and JSON](2026-10-10-agent-reliability-02-reads.md) | 1 for installed-client baseline | Deadline, progress, sanitized diagnostics and machine-readable reports |
| 3 | [Community reliability](2026-10-10-agent-reliability-03-community.md) | 2 read budget | Partial YouTube reads; exact Meta reconciliation; likes diagnostics |
| 4 | [Headless credentials](2026-10-10-agent-reliability-04-credentials.md) | 1 recovery | Explicit encrypted-file backend, migration, rotation and headless setup |
| 5 | [Community batches](2026-10-10-agent-reliability-05-batches.md) | 3 reliable child actions | One exact approval; durable stop/resume/reconcile without replay |
| 6 | [Inbox and links](2026-10-10-agent-reliability-06-inbox-links.md) | 2, 3; 5 for optional batch application | Coverage-aware inbox; exact clip source mapping within approved previews |

## Scope coverage self-review

- Connection flags, preview protection, cancellation, concurrency, previous credentials and compatibility: plan 1 tasks 1–3.
- Whole-operation deadline, retries, JSON, exit contracts, snapshots, redaction and read provenance: plan 2 tasks 1–2.
- YouTube endpoint contracts, partial data and exact reply binding; Meta IDs, eventual consistency, permissions and likes: plan 3 tasks 1–2.
- Explicit storage, external key, atomicity, locking, corruption, migration, rotation, disconnect and backup: plan 4 tasks 1–2.
- Immutable previews, one batch approval, child freshness, cross-batch idempotency, partial execution and reconciliation: plan 5 tasks 1–2.
- Timezones, relative dates, coverage, edits, cursor recovery, unsupported platforms and exact source links: plan 6 tasks 1–2.
- English documentation, clean installation, full suite, patch versions, client-server rollout and rollback: stage delivery in every plan and final release acceptance in plan 6.

## Execution and review

Recommended execution method: native implementation in this session, component by component. Maintain one application folder as requested. Review task diffs and test evidence before each commit; do not create agents unless the chosen execution/review workflow explicitly authorizes them. Each stage requires its own verification; previous passing tests do not prove subsequent work.

Plan status: all six components implemented and reviewed. Full suite: 2,114 passed. Client 0.2.3 built and verified in clean default/headless environments. Connection service remains compatible at the deployed 0.2.1 server implementation. Final GitHub/client/docs rollout and public health checks follow the implementation commit.

Release ruling: shared CLI, comments and documentation changes for components 3–6 are released together as 0.2.3 after their focused reviews and one combined full suite. Component 2 was independently committed/built as 0.2.2; its public rollout is included in 0.2.3. This avoids packaging artificial intermediate working-tree states; cost: a larger rollback unit.
