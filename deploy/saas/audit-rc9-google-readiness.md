# rc9 Google-readiness checkpoint — October 15, 2026

## Source and verification

Candidate source: 0.3.0rc9. Full local suite: **3,083 passed**, one upstream Starlette/httpx deprecation warning, 70.84 seconds. UI bundles and git whitespace checks passed. New tests cover privacy/assistant bypasses, revocation failures and late-credential races, deletion-safe restore and raw-service quarantine, all enumerated managed YouTube stores, supplied-file/Meta preservation, supervisor installation/status, absolute backup retention and remote-completion proof, and immediate/scheduled uploader certification.

Cross-review found and corrected late OAuth/refresh cleanup races, reconnect races, legacy restore startup bypass, false Drive completion, retention monitoring gaps, legacy future-schedule preservation, nested Meta preservation, shared report-index locking and late native data writes after purge. Pending interrupted OAuth/refresh requests can remain `awaiting_credentials` / `operator_review_required`; do not discard that fence or replay writes.

A fresh macOS/Python 3.13 installed-wheel smoke outside the checkout passed: version/help, fictional workspace creation, encrypted credential round trip, offline guides and packaged email assets. Zero external social calls. Both archives passed the release allowlist check.

Reviewed local wheel SHA256: `2d2465f87d7135eb9fc1a51a27e5bd1f702021948bc258d381b070e32f4b2854`.
Reviewed local sdist SHA256: `5b840e62f4e930b0f69ce6393fe16825d1a5fed6f94f11121f64543acbce58cd`.
These are local artifacts, not proof of publication.

## Live operations completed

- Namecheap TXT added without replacing existing TXT records; public DNS and Search Console verified `socialcli.app` under the project administrator.
- Six-day backup retention preview found zero expired snapshots. Reviewed backup/alert scripts, private Drive configuration and systemd integration installed on florentino.pro. Previous operations files retained privately for rollback.
- Full backup service completed successfully; status returned `ok`, no issues. That completion includes both repository integrity checks and checked Drive mirroring, with explicit remote verification required.
- Backup, backup-alert and independent six-hour retention timers are active. Separate retention-service completion must also be recorded after its current run.
- No backup repository was downloaded to the Mac. No user grant was revoked, reconnected or deleted; no social publication occurred.

## Explicit rollout hold

The application container and public installation/policy pages still run rc8. Do not deploy rc9's Google revocation until the operator Drive authorization is in a different Google project; the current separate Desktop client is in project `socialcli` and same-user revocation could interrupt backups. Project `socialcli-operator-backups` was created; OAuth setup is waiting at Google User Data Policy acceptance. Existing backup authorization remains active.

Google remains External / Testing. The source policies and verification package are prepared, but a real English demo and provider-specific assistant configuration evidence still require completion before submission. No OAuth production switch, Google verification submission, GitHub release or TestPyPI/PyPI publication is claimed by this checkpoint. See [cutover](google-production-cutover.md), [verification package](google-verification-package.md), and [restore guard](restore-guard.md).

Schema10 is additive, but older applications reject it; do not roll back only the image after migration. Restored backups require the current-authority deletion guard and separately verified Auth cleanup. A missing current authority leaves restoration quarantined rather than resurrecting deleted access.
