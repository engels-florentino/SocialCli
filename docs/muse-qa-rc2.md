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

Full suite after second review: 2,797 tests passed, with one existing Starlette/httpx deprecation warning. Automated regression tests use fictional accounts. The built rc2 wheel installs in an isolated environment and passes the release smoke check without external calls.

A read-only Histopast acceptance attempt on 2026-10-14 could not scan YouTube: the hosted token endpoint returned HTTP 404 connection_unavailable for its saved connection. Local encrypted storage and installation sign-in remain available. No reconnection, permission change or social write was performed. This is a live acceptance blocker, not evidence that all provider reads work.

The rc1 artifact is immutable. rc2 must pass the protected release workflow before the public installation guide is switched. Local source/build verification does not imply TestPyPI publication or platform app approval.


## Second independent review

The second pass reproduced and corrected these additional cases:

- Malformed proxy URL ports could expose raw configuration fragments in stats
  diagnostics. All HTTP client factories now sanitize InvalidURL; local setup
  errors retain fixed guidance in both text and JSON output.
- Interrupted text stats returned 130 despite preserved data, unlike JSON.
  Both modes now distinguish complete, partial and failed observations consistently.
- SaaS transport wrapping hid read deadlines and oversized-response limits.
  It now preserves those reasons. A lost POST credential exchange also retains
  its method context and requires reconciliation instead of an automatic retry.
- An inbox scan with only unverifiable rows could claim partial observations.
  Partial status now requires actual observations or verified coverage. A verified
  empty inbox is still successful; wholly failed reads return error.
- HTML documentation now matches the rc2 exit codes and key-generation guidance.

The follow-up reviewer found no remaining important issue in these changes.
Focused verification: 107 passed; 24 Muse QA regression cases passed independently.
The rebuilt wheel and source archive passed release-content checks; installed-wheel
smoke testing passed without external calls. Live connection acceptance and protected
publication remain separate, as described above.

## Browser sign-in recovery found during live QA

Live owner attempts returned HTTP 401 before provider authorization. The connection
start required an existing SocialCli browser session and rendered a generic error
when that session was absent or expired. The isolated CLI installation remained
authenticated, which does not establish a browser session.

The server now redirects missing/expired browser sessions to email sign-in with
an exact allowlisted local return path. After sign-in, the owner returns to the
pending connection. Expiry, owner identity and CSRF checks remain enforced; the
redirect does not start OAuth or consume its pending state. Three regressions
failed before the fix and passed afterward. Sanitized operational diagnostics
record status or exception type/source locations without request links, headers,
exception messages or credentials. This is a server patch after the immutable
rc2 package; it does not alter the published TestPyPI artifacts.
