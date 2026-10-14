# Exact native scheduling implementation plan

> Use superpowers:executing-plans inline, TDD, and one final independent review.

**Goal:** Keep the customer's requested publication instant and timezone while preserving legacy approvals and reservations.
**Architecture:** Explicit per-manifest exact mode; backward-compatible NativeJob field and immutable digest; version-gated SQLite slot keys with mixed-policy collision checks.
**Tech stack:** Python datetime/zoneinfo, Pydantic, SQLite, Typer. No new dependencies.
**Spec:** ../specs/2026-10-13-exact-native-scheduling.md

## Global constraints

No media on SaaS server, no live publication or inferred brand, no media conversion.
No silent time changes. Existing approvals, uncertain attempts and remote IDs survive.
Context7 Python docs checked for zoneinfo offset/UTC conversion and DST fold behavior.

## Review focus

- Old serialized jobs/actions retain digest and submit/reconcile semantics.
- Exact/legacy reservations collide symmetrically, including pending action holds.
- Two workers cannot admit the same UTC instant using different timezone strings.
- DST gaps, folds and date-boundary timezone differences never change requested time.
- Version 3 persists across writes, old writers reject, read-only access leaves bytes intact.

## Task 1: Exact intent and reservations

Files: native_schedule/models.py, cadence.py, approval.py, store.py, handoff.py;
new tests/test_native_schedule_exact.py.
Produces: `schedule_mode`, `reservation_key(job)`, `slot_day(key)`,
`slots_conflict(first, second)`, `exact_time(value, timezone_name)`, and
`NativeStore.assert_available(job)` read-only admission check.
- [x] Write failing tests for exact same-day coexistence, same-instant concurrency,
  symmetric legacy collision, external reservation conflict, version/read-only and legacy digest preservation.
- [x] Run new tests and observe unsupported exact mode/reservations fail.
- [x] Implement exact keys `at:<UTC ISO>` and transactional collision checks; keep legacy date keys and digest representation unchanged.
- [x] Run new and existing native store/inventory tests; expect pass.

## Task 2: CLI, timezone validation and reschedule lifecycle

Files: native_schedule/cli.py, inventory.py, reconcile.py, store.py;
new tests/test_native_schedule_exact_cli.py.
Consumes Task 1 helpers. Exact manifest takes optional schedule object;
reschedule uses the stored mode and timezone, requiring a matching local offset.
- [x] Write failing tests for exact preview/persist, timezone/DST gaps and folds,
  same-day conflicts, zone-specific inventory coverage, exact action holds and old pending actions.
- [x] Implement exact/no-overflow CLI and policy-aware rescheduling/confirmation.
- [x] Run tests; expect exact requested local/UTC values, legacy compatibility and no network writes.

## Task 3: Distribution and review

Files: packaged guide and generated docs/agents.md, NATIVE-SCHEDULING.md,
delivery_options.py, agent-readiness tracker.
- [x] Document opt-in schema, legacy reservation compatibility and provider pilot limits;
  execute the installed examples and preserve consistent shared UI copy.
- [x] Run full pytest, generated asset check, diff check, artifact validation and installed smoke outside checkout.
- [x] Request one fresh review; fix Critical/Important with regression tests and rerun affected/full checks as needed.
- [x] Record evidence and commit locally; keep launch gates explicit.


## Verification and review outcome

Full suite: **2,497 passed**, one upstream Starlette deprecation warning, 51.21s.
Generated assets, diff check, wheel/sdist validation and isolated installed macOS
smoke passed. The installed package also exercised two exact times on one day with
fictional media; no network writes. Actual models/store from previous commit
f5d40cf were loaded for a compatibility probe: they read and update legacy schema-2
rows, and reject schema 3 after exact admission.

Independent reviewer found no Critical issues and two Important issues: fresh
calendar coverage missing from exact reschedules, and default legacy mode leaking
into schema-2 serialization. Both reproduced as failing tests and are fixed.
Reschedule preview/approval/submit now validate current destination-account inventory,
full local-day coverage and occupancy; old reservations persist. Default legacy mode
is omitted from all serialized model documents, including action snapshots. Tests
also cover mixed-policy pending holds and concurrent offset representations.

Declined-to-judge items remain external release gates: real provider acceptance,
permissions/pilots, official UI behavior and production load/crash conditions.
Linux supervision and other SaaS/product work continue under the master tracker.
No remote deployment or provider publication was performed.
