# Offline legacy billing inventory

This tool implements only read-only evidence collection. A successful report is
`inventory_only` and always has `enrollment_authorized: false`. It cannot enroll,
deploy, repair, release a restore, grant access, establish historical account/mode
provenance or authorize a provider request. Existing markers are reported as
present or absent; matching markers and events never establish eligibility.

Run `uv run python scripts/inspect_legacy_billing.py` from the reviewed source
environment. It accepts no arguments. Supply these through a protected runtime
environment, never shell command literals, process arguments, Git or logs:

- `SOCIALCLI_BILLING_INVENTORY_DATABASE`: exact absolute existing SQLite path.
- `SOCIALCLI_BILLING_INVENTORY_DESTINATION`: explicit new report path outside Git.
- `SOCIALCLI_ENCRYPTION_KEYS`: existing comma-separated primary and rotated
  Fernet keys, as used by backup operators; alternatively use
  `SOCIALCLI_ENCRYPTION_KEYS_FILE` for a private file containing that same format.
  Supply exactly one key interface. The tool never writes keys to evidence.

Database/key files must be regular, owned by the current operator, mode 0600,
and have one hard link. Their immediate parent and the existing report directory
must be operator-owned 0700 directories. Symlink components, relative/traversal
paths, URI/query/fragment syntax and unsafe ancestor write permissions are
rejected. Root-owned sticky temporary ancestors are allowed. No permissions or
directories are repaired or created. Output uses exclusive creation at 0600 and
fsync; existing files are never overwritten. A failed write returns failure even
if an OS error prevents removal of its private partial artifact. Keep failed
artifacts separate from successful evidence. Fixed diagnostics expose no target
path, key, user, provider object or exception text.

The accepted structures are two exact source-proven schema10 variants:
`schema10_deployed_rc15`, without the receiver trigger/index pair, and
`schema10_candidate_receiver`, with both exact receiver objects. Source commits
`14da67a` and `7f13e7c` establish that difference without a schema-version bump.
Half-pairs are rejected; inspection reports the variant and creates neither
object. Both variants require the exact source tables, columns, indexes and
triggers, including optional paired legacy tables and the defined restore guard
only in `ready` state. SQL tokens retain their boundaries, and observed column
and index metadata must match independently frozen source-fixture expectations.
Newer, incomplete, spoofed or extended schemas and quarantined restores fail
closed. Every encrypted record, including
expired and unknown kinds, must authenticate under the supplied key set. No
store, application or billing-service constructor is instantiated.

SQLite opens the existing file with `mode=ro`, `query_only`, and one explicit
read transaction. No migrations, writes, expiry cleanup, environment binding,
journal-mode changes or target chmod occur. All SQL types, duplicate rows,
ciphertext and every table contribute to typed, length-prefixed SHA-256 roots.
Nonfinite SQL values are rejected. File identity is checked before, during and
after the consistent snapshot. The report includes stat identity, per-table
counts/digests, a schema root and a complete state root. An unchanged root is
evidence relative to trusted custody; it is not historical source provenance.

Committed WAL state is read consistently, including while other transactions are
pending. WAL databases require safe pre-existing `-wal` and `-shm` files; absent
sidecars cause failure before SQLite opens, because a read-only SQLite open can
otherwise create them. Never use `immutable=1` or `nolock` on a live database to
bypass this restriction. SQLite may update existing shared-memory read-lock
bookkeeping. The guarantee concerns SQL rows/schema and database payload, not
immutable lock-file bytes. A separately prepared consistent standalone backup
can be inventoried under the existing authorized rehearsal workflow; this tool
does not prepare or copy a database.

Stdout contains one JSON object with only fixed status, aggregate counts and the
state root; failures return exit 1 and a fixed error. Private JSON reports retain
necessary record identifiers, guard categories, orphan/ownership discrepancies,
uncertain outcomes, access bounds and pending-work counts. They omit emails,
credentials, free-form diagnostics, customer idempotency keys, lease tokens and
raw decrypted payloads. Every exported scalar is checked before opening the
artifact: identifiers have constrained formats, event IDs use the source event
format, modes are exactly `test` or `live`, timestamps/bounds are finite numeric
values (null only where defined), and cancellation flags are integers 0 or 1.
Statuses and classifications come from fixed sets. Unexpected strings/blobs in
numeric columns, sensitive reference formats and unknown modes fail closed;
SQLite column affinity is never treated as value validation.
Every underlying column remains covered by the complete root even when omitted
from the human inventory. Terminal event state is only a local observation;
provider outcomes and lineage remain unestablished.

Owner/reviewer policy configuration and Auth authority are external to this SQLite
inventory and remain `NOT_ESTABLISHED`; no external config, brand files or Auth
database is read. Historical lineage, current provider GET evidence, approvals,
freshness/current authority, paired restore custody, rehearsal, previous-image
compatibility, any eventual apply/verify implementation and separate execution
and deployment authorization remain gates. A clean inventory satisfies none of
those gates. Protected operational custody is required: mode/ownership and file
identity checks are not a defense against a privileged host adversary or a
substitute for independent evidence custody. ACLs, service/mount authority and
host/network isolation remain operator responsibilities.
