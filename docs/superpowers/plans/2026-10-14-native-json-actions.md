# Native scheduling JSON actions implementation plan

> Use superpowers:executing-plans inline, TDD and one independent final review.

**Goal:** Consistent structured native scheduling lifecycle results and errors, local
and SSH-authoritative, preserving exact customer times, evidence and approval gates.
**Architecture:** Opt-in report context captures typed output and read-only job
snapshots. All existing native CLI operations accept --json, retain existing output
on stderr and produce one versioned stdout document. Remote validates the same
bounded envelope and preserves exit code; it never retries a failed command.
**Tech stack:** Existing Python/Typer/Pydantic/SQLite/SSH wrapper; no new dependencies.
**Spec:** Critiques3/6 and scheduling requirements in2026-10-13-socialcli-agent-readiness.md.

## Contract

- JSON mode never implies approval or uploads media to SocialCli servers.
- Distinguish prepared/approved/waiting/UI handoff/verifying/uncertain/scheduled/
  published/cancelled states with fixed next-action hints, not message parsing.
- Keep full approval document and exact digest, requested/final dates and timezone.
- Top-level command status never asserts all jobs are public; each job state and
  evidence remain authoritative. Pending/unverified result needs verification.
- Preserve IDs/evidence when a remote submission fails or a later local read fails.
  Actual submission boundaries mark uncertainty on exceptions; no automatic resend.
- Error text comes from fixed catalogue; existing reject_secrets guards remain.
- Read-only status/dry-run do not create the SQLite database.
- Remote --json passes strict command grammar, checks version/command/brand/exit,
  shape, size and credential safety, and emits one document without nesting reports.
- A remote transport/non-JSON/invalid reply is an unverified outcome, never an
  invitation to repeat a mutation. Older executors remain usable in legacy mode.
- Preserve legacy CLI behavior and exits without --json.
- Tests offline, fictional workspace/media bytes only; no provider writes/deployment.
- Other management commands and real provider acceptance remain subsequent work.

## Task1: Native envelope and remote protocol

- [x] RED: exact preview/prepare/approve/status, missing brand, unsupported/unverified
  API submission, interruption/snapshot failure, remote forwarding/rejection/exits.
- [x] Implement report, minimal CLI hooks, bounded remote envelope. GREEN.
- [x] Guide/tracker updates, full suite/build/installed smoke and one review/fix pass.
- [x] Local commit; retain SaaS rollout gates.

## Review focus

Remote mutation lost reply must not retry; no implicit native approval; dry-run
creates no ledger; final state never claims publication from scheduling evidence;
unsafe/mismatched remote output never displayed; partial observations preserved.

## Review and scope decisions

One independent review found an Important peer-output validation gap. Four tests
reproduced acceptance of foreign/malformed approval or result data, then passed
after adding variant schemas and brand/job/hash binding. Real local envelopes
round-trip through the validator for lifecycle, cancellation/rescheduling and
migration operations. Review fixes stay in this one pass.

Scope decision: complete native scheduling before other management families;
those commands still require their existing diagnostics until a later increment.
Deferred minors: nested action handoffs retain their existing action/instruction
but do not have a dedicated next-action hint; the1MB SSH acceptance limit does
not bound subprocess output buffering (the existing180s timeout remains).

No provider writes, server deployment or public release is part of this increment.

Final verification:2631 tests passed in53.34s; one existing upstream Starlette
deprecation warning. Artifact allowlist and isolated installed macOS smoke passed.
Wheel SHA256:76ea3d151681ff50bbd98d3ecca064e10972a2897f0d6d7ab81a889eebfa2755.
Sdist SHA256:0088d95cdb4ff51372cdaa182916469f1b8f9376e21d6442704f0b5db76dbe66.
