# Schema 9 ownership and cleanup indexes

Schema 9 adds indexed installation/grant links and keyed email digests for exact
account cleanup. Account deletion and credential rotation no longer decrypt other
users' pairs, login attempts or capabilities. Unclaimed records retain their TTL.
The migration decrypts the relevant existing records once, inside one transaction;
allow startup time for that backfill and keep every required decryption key present.
Malformed ciphertext fails the migration instead of silently omitting associations.

Before deployment, take and verify an encrypted-database backup with the existing
backup workflow. Start the updated application, confirm schema 9 and a successful
operations status/maintenance run, then run the normal smoke checks. No provider
reconnection or publication is part of this migration.

Schema 8 binaries reject a schema 9 database. Rollback requires restoring the
verified pre-migration database and matching key configuration while traffic is
stopped, with explicit handling of writes acknowledged after that backup. Do not
lower the schema version manually or run mixed schema 8/9 application workers.

Email indexes use domain-separated HMACs under the configured vault keys. During
vault key rotation, retain the old and new keys and rewrite encrypted records
through `Store.put` (preserving kind, ID, payload, expiry and credential hash), or
explicitly run `Store.index_owner` for each decrypted user/login record as part of
the same re-encryption transaction. This refreshes the keyed indexes as well as the
ciphertext. Verify all records decrypt with the new key and matching login attempts
are still selected for account deletion before retiring the old key. Rotating only
ciphertext with `MultiFernet.rotate` does not refresh the email index and is not a
complete key rotation for schema 9.
