# Muse QA follow-up — 0.3.0rc2

Scope: address eight read/onboarding findings without social publication or automatic reconnection.

| Finding | Resolution |
| --- | --- |
| YouTube inbox scopes | Published rc1 already exposes management/analytics flags. Help and onboarding now identify comment consent explicitly. Known insufficient grants stop before the expensive scan, with owner-authorized remediation. Legacy grant representations are normalized. |
| Local errors reported as server rejection | Local protocol/configuration failures use local_configuration; HTTPX construction failures get safe diagnostics; transport failures remain separate. |
| Partial exit ambiguous | Aggregate inbox/stats/report use 3 for partial, 1 for error, 0 for success; both text and JSON modes. Other legacy read commands retain their exit contracts. |
| Progress spam | Request progress is emitted at most once per five seconds. |
| Read time allocation | Default aggregate budget is 300 seconds; inbox gains repeated --only platform selection. Audience precedes costly YouTube enrichment. Verified observations remain preserved. Missing Meta fields still produce honest partial coverage. |
| Authorization label | Native scheduling has an explicit native_schedule_authorization_verified field. Legacy alias retained with explicit native_scheduling_only scope. |
| Fernet format | credentials generate-key creates a private external key without outputting it or overwriting existing files; configure help explains encoding. |
| Setup discovery | Root and credentials help show brand, credential storage, login, connect order; packaged guide includes runnable setup. |

Independent review found and resolved text stats exit consistency and legacy scope normalization.
An existing generated CSS/source mismatch was repaired without changing the landing design.

## Verification boundaries

Full suite: 2,785 tests passed, with one existing Starlette/httpx deprecation warning. Automated regression tests use fictional accounts. The built rc2 wheel installs in an isolated environment and passes the release smoke check without external calls.

A read-only Histopast acceptance attempt on 2026-10-14 could not scan YouTube: the hosted token endpoint returned HTTP 404 connection_unavailable for its saved connection. Local encrypted storage and installation sign-in remain available. No reconnection, permission change or social write was performed. This is a live acceptance blocker, not evidence that all provider reads work.

The rc1 artifact is immutable. rc2 must pass the protected release workflow before the public installation guide is switched. Local source/build verification does not imply TestPyPI publication or platform app approval.
