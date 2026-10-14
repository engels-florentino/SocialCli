# Installation platform access implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline with TDD and one independent final review.

**Goal:** Let the owner select each new assistant installation's social platforms,
and enforce that boundary at the server credential service.
**Architecture:** Schema7 migrates already-approved installations to explicit legacy
all-platform access. New browser pairing records an explicit selected allowlist.
Every credential lifecycle path rechecks that current policy, including refresh
completion. Unknown/missing policies deny. Existing social tokens are not revoked
by a local installation change. This is the platform-boundary component of critique7,
not a replacement for the outstanding per-operation credential architecture.
**Tech Stack:** Existing FastAPI, encrypted SQLite records, shared HTML UI; no new dependencies.
**Spec:** docs/architecture/agent-permission-boundaries.md

## Global constraints

No customer media transfer/storage, real account actions or deployment. Preserve
owner/brand binding, approval, CSRF and entitlement checks. Existing approvals keep
their prior scope via an explicit migration; never silently broaden new selections.
Keep current shared UI. Existing tokens may outlive broker revocation; disclose it.
New schema prevents older application versions reopening restricted records.

## Review focus

- OAuth started before revocation/restriction cannot complete credential delivery.
- Existing-grant attach and legacy migration cannot bypass platform selection.
- Missing/unknown/corrupt policy fails closed without a default-all fallback.
- Concurrent refresh and installation revocation must not export a token afterward.
- UI names selections explicitly and does not imply per-action or provider revocation.

## Task1: Explicit policy and server checks

Files: socialctl/saas/permissions.py, store.py, pairing.py, connections.py,
migration.py, operations.py; tests/test_saas_platform_access.py.
- [x] RED: selected-only pairing, denied start/attach/migration, fail-closed policy,
  revoked during refresh, and old-installation migration/old-version cutoff.
- [x] Implement policy parsing/checks and schema7 explicit migration.
- [x] Enforce policy at start, candidate, authorization, activation, capability and
  post-refresh recheck; preserve status/revoke access as recovery paths.
- [x] GREEN targeted tests and affected migration/backup/readiness suites.

## Task2: Human/machine guidance and final verification

Files: shared pairing/lifecycle HTML, canonical packaged agent guide, tracker.
- [x] Pairing checkboxes use distinct names accepted by bounded form reader;
  owner may choose no networks, unknown form selections rejected.
- [x] Status/account panel show approved networks and provider-token limitations.
- [x] Complete suite, generated UI, wheel/sdist and installed smoke.
- [x] Independent review once; one Important fix pass with RED/GREEN; commit.


## Verification and review decisions

Independent final review: no Critical or Important defects;23 policy/rotation
tests passed. A separate fictional probe revoked the installation during provider
refresh and confirmed401 without exporting the renewed token. No re-review needed.
Full suite: **2703 passed in55.32s**, one pre-existing Starlette warning. Installed
wheel:16 new policy tests pass with all socialctl imports asserted from the installed
package; installed smoke passes workspace, encrypted credentials and packaged guide.
Generated shared UI passes; static fictional pairing screen inspected in Safari,
with shared colors, typography, controls and accessible checkbox labels. No real
pairing or provider authorization was opened for this visual check.

Wheel SHA256:b0511592cd49c0ec90b9e3e4a9f1cf2ee0c36b1590cc9a8850339442b557b007
Sdist SHA256:e8943fee486bc0a4d2706073e77c99d81c12718948fba3d66e23ed8e4fb5e564

Ruling: this is the installation/platform connection-broker component, not complete
critique7. Cost: broad exported tokens still permit other provider-authorized actions.
Ruling: provider credentials may cover linked accounts; human/machine output states
that selections do not attenuate provider scopes. Cost: independent provider HTTP
calls are not confined by the broker policy; typed per-operation services remain open.
Ruling on review exclusions: live-provider behavior and provider revocation remain
unproven. Cost: pilots must verify actual grants and revocation before related claims.
Ruling on review exclusions: schema7 stops older code at startup, not already-running
workers. Stop all older workers before applying the migration; do not roll code back
to schema6 against this database. Cost: mixed-version operation can bypass new policy.
Ruling on review exclusions: parent full-suite, packaging and installed acceptance
are now verified above; latest Linux full-suite remains checkpoint15aaa05 until this
increment is separately tested. Cost: no new Linux/deployment claim from these results.
Final: minor (deferred): retain the reviewer's exact installation-revocation-during-
refresh probe as a permanent regression; current regression covers policy restriction
during refresh and reviewer separately verified revocation. No implementation defect found.

This increment is not deployed or published. Existing candidate remains1fbaf1d.
