# Offline legacy TEST enrollment

This is an operator tool for a separately authorized maintenance window. Adding
this source does **not** establish any real database's eligibility, authorize an
enrollment, approve a runtime policy, release a restore, or authorize deployment.
Independent implementation and fact-schema review remain required before use.
`BillingService.bind_environment` remains the startup authority and is unchanged.
There is no consumer CLI flag or automatic enrollment path.

The only permitted database mutation is one ordinary uniqueness-enforced
`INSERT` into `records` for `('saas-billing-environment','active')`, with payload
`{"account_id":"<reviewed account>","mode":"test"}`, expiry `253402300799`, and
empty credential hash. The payload uses the existing authenticated envelope.
No existing row, ciphertext, entitlement, uncertainty, permission, index, or
schema is changed. Matching or foreign existing markers and stale marker indexes
block apply. An existing receipt also blocks apply.

## Runtime custody and trust

Run the exact reviewed source tree inside its reviewed image, with process-level
network denial enforced by the host/container policy. This script has no network
client imports or calls; it does not configure a sandbox or prove that the host
actually denied networking. Freeze ingress, webhook acceptance, workers,
schedulers, deletion, revocation and reminders; drain in-flight activity. Preserve
arrivals externally or reject for provider retry. A declared freeze with any
active/uncertain writer fails. Nonempty writer leases fail without cleanup.

Use an existing, operator-owned 0700 custody directory **outside Git**. Policy,
registry, input artifacts and keys files must be regular operator-owned 0600
files with one hard link. Unsafe permissions, symlinks, hard links, replacements,
unsafe ancestors, absent files, and existing output destinations are rejected;
there is no permission repair. Outputs are exclusively created, flushed and
fsynced, including their parent directory. Failed output creation can leave a
private incomplete file; it never yields a success status or authorizes replay.

The policy and registry are separately reviewed protected runtime inputs, not
manifest-selected trust roots. Their custody authenticates operator-created
approval records; the tool does **not** authenticate a human signer or historical
provider creator. Two distinct, policy-pinned principals must occupy the owner
and independent-reviewer roles. Merely writing an `approve` record, copying an
artifact hash, or setting an environment variable is not operational authority.
Privileged custody replacement is outside this trust boundary. File identity
checks cannot defeat an adversary with authority to rewrite the whole package.

The protected runtime environment supplies:

- `SOCIALCLI_BILLING_ENROLLMENT_POLICY`: absolute existing policy file.
- `SOCIALCLI_BILLING_ENROLLMENT_IMAGE_DIGEST`: externally attested running image
  digest, checked against the protected image assertion and policy. The process
  cannot independently attest its container identity.
- `SOCIALCLI_BILLING_ENROLLMENT_DESTINATION`: new artifact path in custody.
- Exactly one of `SOCIALCLI_ENCRYPTION_KEYS` or
  `SOCIALCLI_ENCRYPTION_KEYS_FILE`, using the existing comma-separated rotated
  key format. Never pass keys in argv, manifests, diagnostics or ordinary logs.

Only these operator commands exist:

```text
uv run python scripts/enroll_legacy_test_billing.py prepare
uv run python scripts/enroll_legacy_test_billing.py apply
uv run python scripts/enroll_legacy_test_billing.py verify
uv run python scripts/enroll_legacy_test_billing.py recover
```

These examples describe the interface; they are not permission to execute it.
Use an already provisioned, reviewed runtime without dependency/network fetching.
The reviewed source tree must contain the script at its fixed repository path.
Initialization/import failures produce the same sanitized failure as other
failures. stdout contains only fixed statuses, counts and hashes. Exit 0 means
the named operation completed, 1 means refusal/failure, and 2 means uncertainty.

## Closed artifact contracts and the implementation extension

`socialctl.saas.billing_enrollment_schema` defines strict closed Pydantic models.
`json_schema(Model)` exports Draft 2020-12 for Evidence, Manifest, Approval,
Receipt, Verification, Policy, Registry and the typed fact reports. Every field
is required and extra fields, unknown versions, duplicate JSON keys, BOMs,
multiple JSON documents, nonfinite values and wrong scalar types are rejected.
Literal booleans/integers do not accept each other's JSON representations.
SHA-256 hashes cover original UTF-8 bytes, not reserialized models.

Evidence/Manifest/Approval/Receipt/Verification retain the reviewed v1 field
sets. IDs are constrained private identifiers; bundle, approval and enrollment
nonces use their fixed prefix plus 32 lowercase hexadecimal digits. A transition
`limitation` is a constrained reference/reason code; explanatory prose remains
in the reviewed original receipt, avoiding emails or payloads in tool artifacts.
No hosted URLs, credentials, emails, raw lease/idempotency keys or decrypted
payload dumps belong here.

The reviewed high-level contracts did not specify the contents of evidence
reports. The parent explicitly authorized this source-only extension: closed
fact assertions, a protected registry, and runtime policy. **This extension
requires independent review before operational use.** It does not accept an
arbitrary file with a nearby boolean or a caller-chosen SQL/expression selector.

Policy pins the target (host/service/mount/path/device/inode/account/test), two
principals and roles, custody/registry paths, source/tool/image/configuration and
collector digests, application ID, and fixed age limits (manifest and authority
300 seconds, provider 900 seconds). Manifest data cannot override these pins.
The registry maps unique IDs to local JSON basenames, roles and exact hashes.
Every registered typed artifact is parsed, including unused entries. The source
assertion names exactly the enrollment module, its schema module, shared
inventory module and operator script; each running file is hashed and compared.

A `FactReport` contains its v1 schema identifier, pinned collector digest and
one closed `facts` object. The registry role fixes the accepted facts model:

| Role | Required semantic content |
| --- | --- |
| `initial_empty` | Original source receipt digest, endpoint, account/test, time, zero encrypted/billing/event/paid-state counts. |
| `deployment` | Original source receipt digest, exact transition ID/kind/from/to/account/test/time and continuous/unresolved/contradictory finding. Only continuous is eligible. |
| `provider` | Object references with request ID/response digest, requested and returned IDs, object type, account/test, user/application, customer/subscription/session/invoice/price relationships, exact nested `event_object_id`, observation time and retained-terminal references; exhaustive complete collections for customer subscriptions/sessions/invoices and Checkout line items. |
| `configuration` | Reviewed account/test, application, configured price and portal IDs, observation time. |
| `documentary_review` | Original/current endpoints, exact ordered transitions, exhaustive lineage/row/record/uncertainty references, evidence artifact hashes, and no unresolved/conflicting findings. |
| `owner_instruction` | Exact manual-grant user and entitlement row hashes, original owner conversation receipt digest and time. |
| `authority`, `auth_authority` | Exact target, distinct current endpoints, observation/expiry times and no competing authority. |
| `freeze` | Exact target/freeze ID/times, all seven drained writer categories, retained/rejected arrivals and deny-all network prerequisite. |
| `backup` | Exact target and SQLite/Auth authority endpoints, paired snapshot digests and final verification time. |
| `rehearsal` | Reviewed source/image/previous-image digests, exact authoritative source target/authority/root, distinct prepared-copy target/identity/root, controlled preparation map/receipt/times, structured one-binding delta and bound hashes, matching TEST startup accepted, foreign/live rejected before provider calls, isolated network/ready restore, exact uncertainty IDs. |
| `compatibility` | Previous image digest, exact rehearsal artifact hash and copy target, identical before/after post-enrollment roots and binding ciphertext hash. |
| `source`, `image` | Exact source-file hashes and externally attested image/source association. |

Source receipt/response digests refer to the originals examined by the private
reviewer and retained in existing custody. These are documentary assertions;
the tool validates the registered assertion bytes and their complete mapping,
not arbitrary historical receipt formats or provider signatures. Reviewers must
check those original receipts and response relationships before approving the
Evidence digest. Missing originals or unsupported mappings remain blockers.
The approved collector fingerprint is a trust pin, not a signature.

Only `facts` and `objects.<reference_id>` selectors are interpreted. No SQL,
Python, JSONPath evaluation, filesystem traversal or URL fetching exists.
Provider requested/returned IDs, fixed ID prefixes, configured price/portal,
ownership, relationships and complete collection membership are cross-checked.
Unavailable terminal objects require retained deployment references; absence
alone is not provenance. Observation age uses the **oldest** required object or
collection timestamp, and every observation must be inside the final freeze.

Historical references must be exact members of the validated transition receipt
set. Every declared deployment assertion must participate in that chain; a second
assertion with the same transition ID cannot substitute foreign endpoints or
account provenance. An ID match alone does not establish receipt membership.

Every retained event, including an orphan without a SQL event row, must match its
specific provider event observation and that observation's `event_object_id`.
The nested object identity, object type/test flag when present, required
relationships and available nested relationships/ownership/application metadata
must agree. Missing required relationships are a refusal, not an uncertainty
that can be waved through. Unsupported retained payload shapes remain blocked.

The prepared-copy assertion is tied to the **current complete authoritative
source root**, target and authority endpoint. The copy has a separate path,
service and file identity. Its root is recomputed with its own identity through
the shared inventory encoders; live and copy roots are never equated by inode.
Preparation differences are derived in memory from the existing source-defined
`prepare_restore` plus `release_restore` recipe and the source snapshot's current
tombstones/pending revocations. The verifier performs no restore or mutation:

- Remove only the fixed restore access kinds and their actual index/cascade rows;
  preserve every other ciphertext unless current source deletion/revocation
  authority specifically removes its record.
- Increment surviving users' session versions twice, once for each existing
  preparation/release authority application; clear existing legacy allowances.
- Apply exact source tombstones and pending-revocation reconciliation, including
  only their related deletions, and remove brands left without slots.
- On the disposable copy, the existing Store constructor installs the exact
  frozen receiver trigger and grant-link index before cleanup. A deployed-rc15
  source therefore produces candidate-receiver copy schema and cascade behavior;
  an existing candidate source retains them. No other schema change is accepted
  beyond the fixed ready restore guard and its asserted preparation/snapshot times.

The precise before/after table roots and fixed reason labels must equal that
calculation. A reason label cannot authorize an arbitrary entitlement change,
credential replacement or unrelated deletion. Original source rows remain
unchanged throughout enrollment. The prepared source/copy association and its
original preparation receipt remain documentary assertions under independent
custody review; this is not cryptographic evidence that a rehearsal ran.

`baseline_sha256` must equal the prepared baseline. `exact_delta_sha256` hashes a
closed structured assertion containing full post/excluding-binding roots, exact
TEST binding and ciphertext hash. Its only allowed enrollment delta is one
records row; all other table/schema roots remain unchanged. Compatibility binds
the exact rehearsal bytes, copy target, identical enrolled before/after roots,
and the same binding ciphertext. These additions are part of the independently
reviewed fact-schema extension; the original five v1 artifact field sets are
unchanged. No new operational collection or broad recovery drill is implied.

## Prepare and final approvals

The protected registry must contain the complete typed package: Evidence,
original inventory bytes, documentary assertions, paired current authority,
freeze/backup, source/image/configuration/policy, rehearsal/compatibility and two
detached documentary approvals. Each documentary approval binds exact Evidence
and policy bytes. The script never creates or repairs these inputs or approvals.

`prepare` opens the existing database read-only, authenticates all records,
checks the frozen schema10 deployed/candidate variants and restore readiness,
and requires marker/index absence. It reuses inventory's typed encoder and
full-root algorithm; existing inventory report format and bytes are unchanged.
Exact typed row and primary-key references cover all billing/event/paid-state
rows and all intents, orphan event payloads and unknown encrypted kinds.
Manual grants require null provider identity and exact owner instructions;
provider lineages require historical origins plus the same account and TEST.
Uncertain outcomes, expired/orphan records and unknown kinds must be explicitly
mapped and preserved, not silently settled. Counts cannot replace row coverage.

The initial-empty endpoint must connect through every ordered reviewed
transition to current authority. Current settings, matching events and
unresolved provenance do not qualify. The documentary review seals the entire
mapping and evidence digest set. `prepare` writes a new immutable Manifest with a
fresh nonce, full baseline, target, pins and a maximum 300-second window.

Then the existing owner and independent reviewer separately approve the exact
Manifest bytes under protected custody. Register that Manifest and both final
approvals without modifying any prior artifact. Earlier documentary evidence
can remain unchanged; fresh final artifacts can be supplied through the protected
registry and pinned Manifest references. Never extend an expired Manifest or
revive stale authority/observations; collect/review a new bounded package.

## Apply, verification and crash boundary

`apply` opens existing read-write/no-create SQLite, uses a bounded five-second
busy wait, FULL synchronous durability and `BEGIN IMMEDIATE`. Within that same
transaction it rechecks every protected input and pin, all records/schema,
identity, approvals/time/freshness, and the full logical state CAS. A changed
rate row, grant, deletion, lease, trial, manual bound or orphan ciphertext aborts.
It inserts the exact binding once, re-authenticates and proves the full root
excluding **only that row** equals the baseline, rechecks inputs and time, then
commits. No replacement/upsert, index update, constructor, migration, cleanup or
provider request occurs.

With writers frozen, a new read-only connection checks the exact full delta and
writes a Receipt. `committed_at` is the observed successful COMMIT acknowledgement
in UTC seconds, within the original window. A commit attempt with uncertain
acknowledgement, late acknowledgement, failed readback or failed receipt write
returns `uncertain`; it never retries the INSERT. Precommit failures roll back.

`verify` requires the immutable Manifest/Evidence/approvals and established
Receipt. It validates approvals at the original acknowledged operation time,
authenticates all records, and compares full post and original-excluding-binding
roots plus binding ciphertext. Verification can occur after the original apply
window expires; that does not authorize another apply. It writes a new closed
Verification. A later ordinary database write produces `baseline_mismatch`
without repair or invalidating the retained historical receipt.

`recover` is read-only. With an established Receipt it performs the same exact
verification. Without one it can report `uncertain` with exact-delta hashes and
one added row, after validating the immutable preapproved intent and a possible
original approval window. **It cannot prove an attributable commit timestamp or
approval-valid-at-operation from SQLite state alone, and cannot create a success
Receipt.** It does not write an uncertainty artifact using a success schema,
revive expiry, claim already-applied success from a marker, or automatically
replay. An external durable commit attestation would need a separately reviewed
contract; this implementation does not invent one.

## Remaining operational gates

Keep the separate authorization gates for private documentary continuity/mapping
review, protected principal/policy/collector provisioning, final freeze/current
SQLite and Auth authority, paired backup, fresh provider observations, and
candidate/previous-image rehearsal on a prepared disposable copy. Reuse existing
approved restore/monitoring evidence; never release quarantine in this tool.
No real enrollment, provider collection, release or deployment is performed by
these source tests. Restart workers only after separately authorized startup and
review of every retained uncertainty. Roll back code/configuration to the
rehearsed compatible image; do not restore an older database to remove binding.
