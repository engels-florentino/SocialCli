# Backup and isolated recovery

The SaaS metadata and encrypted connection vault share **one SQLite database**.
Copy them together using the online backup API. Never copy the live database file
alone: committed rows may still be in its WAL. Auth is a separate PostgreSQL
store and needs its own custom-format dump. A SQLite snapshot and an Auth dump
are not a distributed transaction.

## Create and validate a vault snapshot

Run in the reviewed candidate environment with its private
`SOCIALCLI_ENCRYPTION_KEYS` already supplied by the service manager. Do not put
keys in command arguments, shell history or the snapshot directory. Retain every
key still needed to decrypt older records and eligibility markers. A database
without the matching keys is not a usable recovery set.

Create a dedicated 0700 destination directory outside the served/static paths,
then use a new filename for each snapshot:

```sh
python -m socialctl.saas.backup snapshot /data/connections.sqlite3 /backups/vault-20261013T180000Z.sqlite3 --timeout 60
python -m socialctl.saas.backup verify /backups/vault-20261013T180000Z.sqlite3
```

The snapshot command opens the source read-only, includes committed WAL data,
checks SQLite integrity and every encrypted record's authenticated row binding,
and publishes a 0600 file only after validation. Existing destinations are never
overwritten. The timeout bounds the online copy's lock/retry phase; validation
and disk synchronization also take time. Exit 1 is failure, never a successful
backup receipt. Output contains counts, schema version, cutoff presence and SHA256,
not identities, payloads or keys. Store that report privately with the backup.
The checksum detects an accidental change against the saved report; it is not a
signature against a malicious operator who can replace both.

A crash may leave a hidden `.socialcli-snapshot-*` temporary file. It is not a
completed snapshot and must not be selected for restore. Only verified named
artifacts count. Do not run `verify` on a changing live database: its file hash is
intended for the isolated, self-contained snapshot. The backup tool never sends
mail, refreshes tokens, migrates schemas or processes pending jobs.

## Recovery rehearsal

1. Preserve the original snapshot and its report. Verify its hash, all encryption
   keys, release identifier and schema version. Make a working copy into a new
   0700 directory; never replace the current live database to test a backup.
2. Use the matching release and a private environment with no public ports,
   maintenance timer or outbound network. Keep the original encrypted data;
   do not print decrypted records. Supply recovery keys independently.
3. Run `verify`, then test startup and schema compatibility. Use fictional HTTP
   providers for application smoke tests. Verify owner isolation, trial dates,
   paid-period boundaries and the persistent legacy cutoff. Never test a restored
   real refresh token against a provider or replay pending email/Checkout jobs.
4. Restore Auth separately into a new volume and internal network. Supply its
   JWT, administrator, runtime and SMTP secrets separately. Disable actual SMTP;
   use `/admin/generate_link` for a fictional identity, verify the generated code,
   fetch the identity and delete it. Confirm all original table counts/digests
   against one exported snapshot, without revealing identity data.
5. Delete only the explicitly created rehearsal containers and working copies.
   Keep the recovery set according to the retention policy; record results and
   the oldest successfully rehearsed recovery point.

The automated SQLite tests exercise WAL with an uncommitted concurrent writer,
wrong/missing/rotated keys, tampering, corrupt files, lock timeout, no overwrite,
CLI diagnostics and isolated application restoration. The restored fixture keeps
its original trial deadline, denies a foreign capability, and retains the legacy
cutoff even when the environment flag is absent. These tests use fictional data.
Actual Auth restore evidence is in [the identity guide](../auth/README.md).

## Before returning a historical restore to service

A backup does not reverse operations that already happened at Stripe, Auth,
Google, Meta or TikTok. A historical image may contain deleted identities,
revoked installations, consumed refresh tokens, delivered emails or completed
payment work. Keep it offline until those differences are resolved. Never clear
uncertain-operation markers to force a retry.

Preserve the edge legacy block on both domains throughout recovery. Keep public
traffic and maintenance disabled; take a new copy of the damaged/current state
before any replacement. Reconcile deletion and revocation history, canonical
Stripe state and identity ownership. Quarantine restored sessions, installation
capabilities and provider grants whose current validity cannot be established;
require fresh owner approval where necessary. Suppress reminders and operations
that may already have been delivered. Do not restart a historical worker and hope
idempotency keys remain valid. If recent revocations/deletions cannot be recovered,
use maintenance mode rather than expose the old access state.

Code rollback must use a schema-compatible, cutoff-aware release. Never restore
an old database just to downgrade code. For a coordinated planned restore, stop
writes and maintenance before taking the SQLite and Auth copies and keep traffic
closed until both are consistent and smoke tests pass.

## Operational rollout still required

The utility and rehearsals do not establish backup scheduling, off-host storage,
retention, alert delivery or a recovery SLA. Before public rollout, configure a
private scheduled backup of both stores, independently secured key recovery,
an encrypted off-host destination, retention and a failed/stale-backup alert.
Rehearse from that actual destination. Do not delete old key versions while any
retained recovery point still depends on them. No backup timer is enabled by this
source change.

Reference: [Python SQLite online backup API](https://docs.python.org/3.11/library/sqlite3.html#sqlite3.Connection.backup).
