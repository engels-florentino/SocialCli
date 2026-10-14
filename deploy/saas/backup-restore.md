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

## Scheduled candidate backups

`backup_host.py` runs on the Docker host as root. Its mode-0600 JSON configuration
specifies a private `root`, `service_container`, `auth_container`, `repository`,
`password_file`, `recovery_repository`, `recovery_password_file`, and a
`secret_files` mapping of simple destination filenames to private source paths.
Initialize both Restic repositories with different passwords before running it.
Never put actual configuration, passwords or recovery files in Git.

The worker snapshots and authenticates the SQLite vault, obtains a custom-format
Auth dump, validates its archive catalog, and writes file hashes. State and recovery
configuration go into separate encrypted Restic repositories. A completed receipt
requires both snapshot IDs and repository checks; incomplete Restic exit codes are
failures. A failed run preserves the previous successful snapshot IDs. A host lock
prevents overlapping workers. Dedicated plaintext staging is removed after each
run and on the next run after a crash. Private recovery directories and the Docker
host remain trusted; repository encryption does not protect a compromised host.

On 13 October 2026, the included candidate-only service and hourly timer were
installed and enabled on Florentino. A real run under the systemd restrictions
exited zero. This covers the isolated candidate vault and private SocialCli Auth,
not the public 0.2.6 connection vault. The separate candidate maintenance timer now runs every five minutes; reminder
delivery remains disabled.

Both encrypted repositories passed `restic check --read-data`. An exact completed
snapshot pair was restored into disposable containers with networking disabled:
file hashes matched, vault authentication/schema 6 passed, and PostgreSQL restored
28 tables, 2 users and 75 migrations. Recovery configuration matched its sources.
The fresh PostgreSQL rehearsal needs `socialcli_auth` and the `postgres NOLOGIN`
compatibility role from the Auth initialization before `pg_restore --exit-on-error`.
Only disposable restored data and containers were removed afterward.

The user chose Google Drive for off-host copies. The available credential is a
service account and the read-only probe found no accessible Shared Drives. Personal
Drive OAuth or access to a Workspace Shared Drive remains pending. No encrypted
repository has been uploaded. Service accounts cannot own Drive files; see
[Google's upload guidance](https://developers.google.com/workspace/drive/api/guides/folder).

Operator storage preference: Google Drive is the only off-host backup destination;
do not download repositories, database dumps or recovery passwords to the Mac.
Generate and encrypt backups on Florentino, then transfer directly from that
server once Drive authorization is available. This preference does not establish
that an upload has occurred or authorize deleting the existing server recovery
sets before remote recovery is verified.

## Private freshness check

`backup_host.py PRIVATE_CONFIG --status` reads the private local receipt without
starting Docker, Restic, a backup, or a network request. Exit 0 means the last
local snapshot pair is recent; exit 1 requests operator attention. The report
contains only fixed labels: `backup_unknown` for a missing/invalid receipt,
`backup_unverified` when no completed pair exists, `backup_failed` after a failed
run, `backup_stalled` when a running receipt is older than ten minutes, and
`backup_stale` when the last completed pair is older than two hours. A new run
does not hide an overdue or missing successful backup. Future timestamps and
malformed snapshot IDs fail closed.

The candidate backup-check timer runs this read-only command independently every
five minutes, even when the hourly backup timer stops. Its service has networking
disabled and a read-only filesystem. Failures appear in private systemd status
and journal; this is not delivered email or off-host monitoring, and cannot detect
a host outage while the host itself is down. A healthy result never establishes
Drive upload or remote recoverability.

## Operational rollout still required

Local scheduled backups do not establish off-host recovery or a recovery SLA.
Before public rollout, finish independently secured off-host password recovery,
encrypted remote storage, retention and failed/stale-backup alerts. Rehearse from
that actual destination. Automatic pruning is intentionally not enabled before
that recovery point is verified; monitor local disk usage meanwhile. Do not delete
old key versions while any retained recovery point depends on them. The receipt's
`complete` status means a local backup, not a successful off-host copy.

Reference: [Python SQLite online backup API](https://docs.python.org/3.11/library/sqlite3.html#sqlite3.Connection.backup).

## Google Drive authorization and encrypted copy — 14 October 2026

The owner authorized the separate Desktop OAuth client of project `socialcli`
with only `drive.file`. Drive API was already enabled; this scope was added to
the consent declaration without changing the SaaS YouTube scope requests.
An initial exchange failed because the operator obscured a client_secret field
that rclone declares IsPassword=false. The field was corrected without exposing
its value; retry with the same approved permission saved a refresh token.

Private runtime configuration now lives in
`/home/blades3/projects/socialcli-saas/private/drive/rclone.conf` (root0600,
parent0700). No credentials or backup contents were downloaded to the Mac.

Both repositories were copied directly from Florentino to
`socialcli_backup:SocialCli-Backups/candidate/{data,recovery}`.
Initial rclone checks found zero differences:82 data files and65 recovery files.
Independent remote `restic check --read-data --no-cache` succeeded for both:
20 snapshots each,40 data packs and23 recovery packs, no errors. This proves
remote encrypted integrity; it is not a new application-level restore rehearsal.

The reviewed existing hourly backup service now runs the four non-deleting
copy/check steps in `systemd/drive-copy.conf` after a successful local backup.
The dedicated token directory is writable for atomic OAuth refresh updates;
other private directories remain protected. A complete real systemd run with
these post-steps returned Result=success, ExecMainStatus=0; the timer is active.
No pruning, remote deletion or public0.2.6 backup scope was introduced.

Remaining limits: the Google project remains External/Testing, so grants can
expire after seven days. Production status/verification remain deferred.
Independent off-host recovery of the two Restic passwords, alert delivery and
an application-level restore from Drive remain open. The existing freshness
receipt still measures local completion only; a post-copy failure marks the
systemd service failed but does not change that receipt to remote failure.
