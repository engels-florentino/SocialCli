# rc10 validation: concurrent cleanup and stale connection capabilities

Source candidate: 0.3.0rc10. The public TestPyPI release and hosted service remain
rc9; this file is not publication or provider-approval evidence.

## Reproduced causes

- Every brand command runs retention cleanup. Nonblocking managed-store locks
  produced `BlockingIOError` during concurrent commands, reported misleadingly
  as corrupt/unreadable data. Muse confirmed valid inbox JSON and reproduced
  failures against different managed files when running two reads together.
- Browser revocation removed the old assistant's capabilities. After relinking,
  three local platform records returned `404 connection_unavailable`; the healthy
  YouTube result was discarded because `connections` aborted on the first error.
- Explicit `account attach` previously handled revoked installations returning
  401, but not the 404 produced when browser revocation also removed capabilities.

## Corrections and boundaries

Cleanup waits at most two seconds for each managed-store lock. Persistent lock
contention returns `local_data_busy`, preserves files and continues to block
access until retention can complete. JSON reads retain this retry guidance.
Unreadable data still fails closed; expiry and replay-blocking rules are unchanged.

Connection listing retains successful platforms and returns platform-specific
errors and HTTP status. Partial results exit 3, complete failures exit 1, and a
complete listing exits 0. Reading status does not attach or reconnect accounts.
Explicit attach can restore a deleted capability through the current approved
installation, with server-side owner/platform/grant validation and exact local
account identity matching. An account mismatch remains blocked.

## Verification

- Regression tests first failed on the original symptoms, then passed.
- Full suite: **3,091 passed**, one existing Starlette/httpx deprecation warning.
- Static UI asset check and `git diff --check`: passed.
- Wheel and sdist content checks: passed; no private workspace/media packaged.
- Fresh wheel installation outside the checkout on macOS: version/help,
  fictional workspace, encrypted credentials and packaged guides passed.
- Muse copied the reviewed wheel via authorized Mac file access, verified its
  SHA256 and upgraded only SocialCli with `--no-deps` in its existing environment.
  The tested candidate is local, not a download from TestPyPI.
- Muse reported the expected partial listing (YouTube plus three identified
  stale capabilities), successful sequential explicit attachment of the three
  existing owner-approved accounts, then a complete four-platform listing.
- Three rounds of two simultaneous owned-video reads: six exit-0 results with
  valid JSON and empty stderr. No upload, provider-content change, forced purge,
  OAuth or revocation occurred during this regression verification.

Reviewed local artifact SHA256:

| Artifact | SHA256 |
| --- | --- |
| `socialcli-0.3.0rc10-py3-none-any.whl` | `216b593341eac396c985ef1fbfc8b3edb7ac708691f6aafc11b2b3de39445c69` |
| `socialcli-0.3.0rc10.tar.gz` | `10c817c149d4a93f16149f8eae5db516f4bbc1baca4b1d77ac129464abb56e5a` |

These are the local validation artifacts. A later release build must be checked
independently before publication. The new continuous demonstration is separate
from this validation and must be reviewed before sharing with Google.
