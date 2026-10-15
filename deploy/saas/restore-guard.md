# Deletion-safe offline restore gate

This candidate workflow is implemented and tested with fictional SQLite fixtures.
It has not been deployed or rehearsed against production Auth. It does not prove
that a restored Supabase database is clean: the final Auth step is an explicit
operator attestation, and output continues to say `identity_cleanup_verified:false`.

## Trust boundary and prerequisites

- Work only on Florentino in a new private directory, with restored services and
  PostgreSQL isolated from public ingress and all outbound network access. Never
  point these commands at a running restore or overwrite the live database.
- Obtain the paired snapshot's creation time from its verified Restic receipt;
  enter that Unix timestamp below. The maximum accepted age is six days.
- Load the required encryption key versions through the existing private runtime
  environment (`SOCIALCLI_ENCRYPTION_KEYS`). Never echo keys or put them in flags.
- Freeze writes on the current authority during the final manifest/export/cleanup/
  cutover window. Do not run both authorities or accept new deletion requests on
  the old authority after the final export.
- A manifest must come from the current authority, must be less than five minutes
  old, and contains only deletion IDs, times and pending revocation job IDs.
  `--current-authority-confirmed` is an operator provenance attestation. A JSON
  source label cannot prove provenance. Never regenerate a manifest from an old
  restored database or change its timestamp. Missing authority means no release.
- New SQLite snapshots contain an `unprepared` restore marker. SaaS startup
  refuses them. Historical snapshots without this marker cannot be distinguished
  automatically from historical live databases: route them through this workflow;
  never mount them directly into a service.

## Commands

These examples use placeholder paths. The operator supplies the verified snapshot
creation epoch and actual private paths. All destination files must be new.

Read the current authoritative database without modifying it:

```sh
python -m socialctl.saas.restore_guard export \
  /current-authority/state.sqlite3 /private/restore/current-deletions.json \
  --current-authority-confirmed
```

Create a separate blocked copy, apply current user/grant tombstones, and invalidate
all restored browser sessions, assistants, capabilities, pending authorizations,
and legacy connection credentials:

```sh
python -m socialctl.saas.restore_guard prepare \
  /private/recovered/state.sqlite3 /private/restore/staged.sqlite3 \
  --manifest /private/restore/current-deletions.json \
  --snapshot-created VERIFIED_SNAPSHOT_UNIX_TIMESTAMP
```

The output state is `identity_cleanup_required`; application startup remains
blocked. User deletion removes owned vault records and customer metadata. Grant
deletion removes the grant and platform slot. Unaffected users' trial eligibility
and still-authorized social grants remain, but assistants must be linked again.
Previously queued revocations are retained only when the current authority still
lists them as pending. Missing current queue credentials prevent release; recover
the authoritative encrypted queue rather than skipping those jobs.

Generate SQL for the isolated restored Auth database:

```sh
python -m socialctl.saas.restore_guard auth-sql \
  /private/restore/current-deletions.json /private/restore/auth-cleanup.sql
```

Apply it only to the isolated restored Auth database, with errors stopping execution:

```sh
psql --dbname ISOLATED_RESTORED_AUTH_DATABASE \
  --set ON_ERROR_STOP=on --file /private/restore/auth-cleanup.sql
```

The SQL transaction deletes all restored Auth refresh tokens and sessions, then
user rows listed in the deletion manifest. Check the restored Auth schema's
foreign-key/cascade behavior and verify absence of deleted identities, plus a fresh
login and rejection of old sessions. Any SQL error, incompatible schema or failed
verification leaves the restore blocked. Neither the Python command nor a successful
SQLite check establishes completion of these PostgreSQL checks.

After verifying Auth cleanup for this same current manifest:

```sh
python -m socialctl.saas.restore_guard release \
  /private/restore/staged.sqlite3 \
  --manifest /private/restore/current-deletions.json \
  --identity-cleanup-confirmed
```

The release command reapplies SQLite deletions and checks pending revocation
credentials before marking this copy ready. It does not start a service or perform
network operations. The operator still must finish the reviewed isolated restore
checks before a separately authorized cutover. If five minutes have elapsed,
export a fresh manifest and repeat Auth SQL generation, execution and verification
against that manifest before release.

## If the current authority is unavailable

Omit `--manifest` during `prepare` to produce a diagnostic quarantine copy. This
removes provider grants as well as sessions, assistants and capabilities and
records `authority_unavailable`. It cannot run as a SaaS service. `release` without
a fresh current-authority manifest is rejected. Do not use a manifest stored in a
backup or treat `--identity-cleanup-confirmed` as an authority override. Recover
current deletion authority and any pending encrypted revocation queue first.

Minimal tombstones are retained for thirty days, beyond the six-day recovery
window. Pending revocation credentials remain encrypted until Google confirms
completion; the restore workflow does not erase the sole remaining credential or
replay a completed historical revocation against a newer connection.
