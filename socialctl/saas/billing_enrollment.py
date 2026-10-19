"""Offline operator-only one-row TEST enrollment; no store/service constructors.

The protected runtime policy/registry are trust roots. Assertions are documentary
facts, not a substitute for independent review or process-level network denial.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from cryptography.fernet import Fernet

from . import billing_inventory as inv
from . import billing_enrollment_schema as s

BINDING_KEY = ('saas-billing-environment', 'active')
ERROR = 'Enrollment operation failed.'
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
# Observation boundaries, never exception messages or inferred root causes.
_STAGE_CODES = {
    'cli_arguments': 'invalid_arguments',
    'runtime_load': 'runtime_load_failed',
    'cli_preparation': 'operation_preparation_failed',
    'prepare': 'preparation_failed',
    'apply_precommit': 'precommit_rejected',
    'apply_commit': 'commit_outcome_unknown',
    'apply_postcommit': 'completion_unverified',
    'apply_readback': 'completion_unverified',
    'apply_receipt': 'completion_unverified',
    'apply_cleanup': 'completion_unverified',
    'verify': 'verification_failed',
    'recover': 'recovery_failed',
}


def diagnostic(stage, *, baseline_mismatch=False):
    return {'stage': stage, 'error_code': ('baseline_mismatch' if baseline_mismatch
                                          else _STAGE_CODES[stage])}


class EnrollmentError(ValueError):
    """Fixed diagnostics only; never include source payloads or exceptions."""

    def __init__(self, message=ERROR, *, stage=None):
        super().__init__(ERROR)
        self.stage = stage


class BaselineMismatch(EnrollmentError):
    pass


def require(condition):
    if not condition:
        raise EnrollmentError(ERROR)


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(model):
    return (json.dumps(model.model_dump(by_alias=True), sort_keys=True,
                       separators=(',', ':'), allow_nan=False) + '\n').encode()


def strict_json(raw):
    require(not raw.startswith(b'\xef\xbb\xbf'))
    return json.loads(raw.decode('utf-8'), object_pairs_hook=s._unique_object,
                      parse_constant=s._nonfinite)


def read_private(path):
    """No symlink/hardlink/unsafe permission or replacement; no repair."""
    path = Path(path)
    identity = inv._identity(inv._file_info(path))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        inv._assert_identity(path, identity, stream.fileno())
        before = os.fstat(stream.fileno())
        raw = stream.read(MAX_ARTIFACT_BYTES + 1)
        require(len(raw) <= MAX_ARTIFACT_BYTES)
        after = os.fstat(stream.fileno())
        require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns))
        inv._assert_identity(path, identity, stream.fileno())
    return raw


def directory_identity_of(info):
    return {key: value for key, value in inv._identity(info).items() if key != 'links'}


def write_private(path, raw):
    """Exclusive durable creation under existing custody, outside Git."""
    path = inv._destination(path)
    directory_identity = directory_identity_of(path.parent.stat())
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        require(directory_identity_of(os.fstat(directory_fd)) == directory_identity)
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory_fd)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
            inv._assert_identity(path, inv._identity(os.fstat(stream.fileno())), stream.fileno())
        require(directory_identity_of(path.parent.stat()) == directory_identity)
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return sha256(raw)


def root_from_inventory(report):
    return s.Root(algorithm='inventory-typed-root-v1', schema_variant=report['schema_variant'],
                  schema_sha256=report['schema_root'], state_sha256=report['state_root'],
                  tables=[s.TableRoot(name=name, count=value['rows'], sha256=value['digest'])
                          for name, value in sorted(report['tables'].items())])


def transaction_root(db, schema, identity, *, exclude_binding=False):
    tables, schema_root, data_root = inv._logical_state(db, schema, exclude_binding=exclude_binding)
    return root_from_inventory({'tables': tables, 'schema_root': schema_root,
        'state_root': inv._state_root(schema_root, data_root, identity),
        'schema_variant': inv._schema_variant(schema)})


def row_references(db, schema):
    """Private values plus exact typed row/primary-key references, never exported."""
    result = {}
    for table in sorted(item[1] for item in schema if item[0] == 'table'):
        metadata = db.execute(f'PRAGMA table_xinfo("{table}")').fetchall()
        names = sorted(item[1] for item in metadata)
        primary = [item[1] for item in sorted(metadata, key=lambda item: item[5]) if item[5]]
        require(bool(primary))
        selection = ','.join('"' + name + '"' for name in names)
        rows = []
        for row in db.execute(f'SELECT {selection} FROM "{table}"'):
            values = dict(zip(names, row))
            reference = s.Row(table=table, key_sha256=inv._rows_digest([tuple(values[k] for k in primary)]),
                              row_sha256=inv._rows_digest([row]))
            rows.append({'ref': reference, 'values': values})
        require(len({item['ref'].key_sha256 for item in rows}) == len(rows))
        result[table] = rows
    return result


@dataclass
class Snapshot:
    db: sqlite3.Connection
    schema: list
    identity: dict
    records: dict
    rows: dict
    root: s.Root
    report: dict
    path: Path
    fd: int

    def check_identity(self):
        inv._assert_identity(self.path, self.identity, self.fd)


def snapshot(db, keys, path, identity, fd):
    schema = inv._schema(db)
    tables, _, _ = inv._logical_state(db, schema)
    records = inv._authenticate(db, keys, json_loader=strict_json)
    report = inv._inventory(db, records, tables)
    inv._check_report_contract(report)
    result = Snapshot(db, schema, identity, records, row_references(db, schema),
                      transaction_root(db, schema, identity), report, path, fd)
    result.check_identity()
    return result


@contextmanager
def database(path, keys, *, write=False):
    path = inv._private_path(path)
    identity = inv._identity(inv._file_info(path))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    db = None
    try:
        inv._assert_identity(path, identity, fd)
        header = os.pread(fd, 100, 0)
        require(len(header) == 100 and header[:16] == b'SQLite format 3\0')
        if header[18:20] == b'\x02\x02':
            for suffix in ('-wal', '-shm'):
                inv._file_info(Path(str(path) + suffix))
        else:
            require(header[18:20] == b'\x01\x01')
            journal = Path(str(path) + '-journal')
            if journal.exists() or journal.is_symlink():
                inv._file_info(journal)
        db = sqlite3.connect(path.as_uri() + ('?mode=rw' if write else '?mode=ro'),
                             uri=True, timeout=5, isolation_level=None)
        db.execute('PRAGMA trusted_schema=OFF')
        if write:
            db.execute('PRAGMA synchronous=FULL')
            require(db.execute('PRAGMA synchronous').fetchone() == (2,))
        else:
            db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        yield snapshot(db, keys, path, identity, fd)
        inv._assert_identity(path, identity, fd)
    except BaselineMismatch:
        raise
    except Exception:
        raise EnrollmentError(ERROR) from None
    finally:
        if db is not None:
            db.rollback()
            db.close()
        os.close(fd)

FACT_TYPES = {
    'initial_empty': s.InitialEmptyFacts, 'deployment': s.ContinuityFacts,
    'provider': s.ProviderFacts, 'documentary_review': s.DocumentaryFacts,
    'authority': s.AuthorityFacts, 'auth_authority': s.AuthorityFacts,
    'freeze': s.FreezeFacts, 'configuration': s.ConfigurationFacts,
    'owner_instruction': s.OwnerInstructionFacts, 'backup': s.BackupFacts,
    'rehearsal': s.RehearsalFacts, 'compatibility': s.CompatibilityFacts,
    'source': s.SourceFacts, 'image': s.ImageFacts,
}


def unique(values):
    require(len(values) == len(set(values)))
    return set(values)


def ref_key(reference):
    return (reference.table, reference.key_sha256, reference.row_sha256)


def record_key(reference):
    return (reference.kind, reference.id)


def unique_refs(references):
    unique([(ref.artifact_id, ref.selector) for ref in references])


@dataclass
class Runtime:
    policy_path: Path
    policy: s.Policy
    policy_digest: str
    registry: s.Registry
    raw: dict
    entries: dict
    image_digest: str

    def one(self, role):
        matches = [item for item in self.entries.values() if item.role == role]
        require(len(matches) == 1)
        return matches[0]

    def model(self, entry, model):
        return s.parse_artifact(self.raw[entry.id], model)

    def facts(self, entry):
        require(entry.role in FACT_TYPES)
        report = self.model(entry, s.FactReport)
        require(type(report.facts) is FACT_TYPES[entry.role])
        require(report.collector_sha256 == self.policy.collector_sha256)
        return report.facts

    def reference(self, ref, roles):
        require(ref.artifact_id in self.entries)
        entry = self.entries[ref.artifact_id]
        require(entry.role in roles)
        facts = self.facts(entry)
        if isinstance(facts, s.ProviderFacts):
            if ref.selector == 'facts':
                return facts
            parts = ref.selector.split('.')
            require(len(parts) == 2 and parts[0] == 'objects')
            matches = [obj for obj in facts.objects if obj.reference_id == parts[1]]
            require(len(matches) == 1)
            return matches[0]
        require(ref.selector == 'facts')
        return facts

    def fresh(self):
        current = load_runtime(self.policy_path, image_digest=self.image_digest)
        require(current.policy_digest == self.policy_digest)
        require(current.registry == self.registry and current.raw == self.raw)
        return current


def source_paths():
    here = Path(__file__).parent
    return {'billing_enrollment.py': here / 'billing_enrollment.py',
            'billing_enrollment_schema.py': here / 'billing_enrollment_schema.py',
            'billing_inventory.py': here / 'billing_inventory.py',
            'enroll_legacy_test_billing.py': here.parents[1] / 'scripts' / 'enroll_legacy_test_billing.py'}


def load_runtime(policy_path, *, image_digest):
    policy_path = Path(policy_path)
    policy_raw = read_private(policy_path)
    policy = s.parse_artifact(policy_raw, s.Policy)
    custody = Path(policy.custody_directory)
    require(policy_path.parent == custody)
    registry_path = Path(policy.registry_path)
    require(registry_path.parent == custody and registry_path != policy_path)
    # Reuse protected path rules and explicitly forbid repository custody.
    for parent in [custody, *custody.parents]:
        require(not (parent / '.git').exists() and not (parent / '.git').is_symlink())
    registry = s.parse_artifact(read_private(registry_path), s.Registry)
    unique([entry.id for entry in registry.entries])
    unique([entry.filename for entry in registry.entries])
    require(len(policy.principals) == 2)
    require(unique([p.id for p in policy.principals]) and
            {p.role for p in policy.principals} == {'owner', 'independent_reviewer'})
    raw = {}
    for entry in registry.entries:
        raw[entry.id] = read_private(custody / entry.filename)
        require(sha256(raw[entry.id]) == entry.sha256)
    runtime = Runtime(policy_path, policy, sha256(policy_raw), registry, raw,
                      {entry.id: entry for entry in registry.entries}, image_digest)
    require(runtime.one('policy').sha256 == runtime.policy_digest)
    models = {'evidence': s.Evidence, 'manifest': s.Manifest, 'approval': s.Approval, 'receipt': s.Receipt}
    for entry in runtime.entries.values():
        if entry.role in FACT_TYPES:
            runtime.facts(entry)
        elif entry.role in models:
            runtime.model(entry, models[entry.role])
        elif entry.role == 'policy':
            require(runtime.model(entry, s.Policy) == policy)
        else:
            strict_json(runtime.raw[entry.id])
    for role, expected in [('source', policy.source_sha256), ('image', policy.image_sha256),
                           ('configuration', policy.config_sha256)]:
        require(runtime.one(role).sha256 == expected)
    source = runtime.facts(runtime.one('source'))
    paths = source_paths()
    require(unique([f.name for f in source.files]) == set(paths))
    for file in source.files:
        require(sha256(paths[file.name].read_bytes()) == file.sha256)
    require(sha256(paths['enroll_legacy_test_billing.py'].read_bytes()) == policy.tool_sha256)
    image = runtime.facts(runtime.one('image'))
    require(image.source_sha256 == policy.source_sha256 and image.image_digest == image_digest)
    config = runtime.facts(runtime.one('configuration'))
    require((config.account_id, config.mode, config.app_id) ==
            (policy.target.account_id, 'test', policy.app_id))
    return runtime


def validate_approvals(runtime, scope, subject_digest, at):
    principals = {p.role: p.id for p in runtime.policy.principals}
    found = []
    for entry in runtime.entries.values():
        if entry.role != 'approval':
            continue
        approval = runtime.model(entry, s.Approval)
        if approval.scope != scope:
            continue
        require(approval.subject_sha256 == subject_digest)
        require(approval.policy_sha256 == runtime.policy_digest)
        require(approval.principal_id == principals[approval.role])
        require(approval.created_at <= at < approval.expires_at)
        found.append((entry.sha256, approval))
    require(len(found) == 2)
    unique([a.approval_id for _, a in found])
    require({a.role for _, a in found} == {'owner', 'independent_reviewer'})
    unique([a.principal_id for _, a in found])
    return [digest for digest, _ in sorted(found, key=lambda item: item[1].role)]


def target_matches(snapshot, target):
    require(str(snapshot.path) == target.database_path)
    require(snapshot.identity['device'] == target.device and snapshot.identity['inode'] == target.inode)
    snapshot.check_identity()


def final_facts(runtime, now):
    """No network; only pinned closed reports. Freshness is oldest observation."""
    target = runtime.policy.target
    authority = runtime.facts(runtime.one('authority'))
    auth = runtime.facts(runtime.one('auth_authority'))
    freeze = runtime.facts(runtime.one('freeze'))
    require(authority.endpoint_id != auth.endpoint_id)
    require(runtime.facts(runtime.one('configuration')).observed_at <= now)
    for item in (authority, auth):
        require(item.target == target and not item.competing_authority_ids)
        require(item.observed_at <= now < item.expires_at)
        require(now - item.observed_at <= runtime.policy.authority_max_age)
    require(freeze.target == target and freeze.started_at <= freeze.observed_at <= now)
    require(unique([writer.name for writer in freeze.writers]) ==
            {'ingress', 'webhooks', 'workers', 'schedulers', 'deletion', 'revocation', 'reminders'})
    require(all(writer.state == 'drained' for writer in freeze.writers))
    require(freeze.started_at <= authority.observed_at and freeze.started_at <= auth.observed_at)
    backup = runtime.facts(runtime.one('backup'))
    require(backup.target == target and backup.authority_id == authority.endpoint_id
            and backup.auth_authority_id == auth.endpoint_id)
    require(freeze.started_at <= backup.verified_at <= now)
    rehearsal = runtime.facts(runtime.one('rehearsal'))
    compatibility = runtime.facts(runtime.one('compatibility'))
    require(rehearsal.source_sha256 == runtime.policy.source_sha256
            and rehearsal.image_sha256 == runtime.policy.image_sha256)
    require(compatibility.previous_image_sha256 == rehearsal.previous_image_sha256)
    provider = runtime.facts(runtime.one('provider'))
    observed = [obj.observed_at for obj in provider.objects]
    observed += [collection.observed_at for collection in provider.collections]
    require(bool(observed))
    oldest = min(observed)
    require(freeze.started_at <= oldest <= max(observed) <= now)
    require(now - oldest <= runtime.policy.provider_max_age)
    return authority, auth, freeze, oldest


def required_coverage(snapshot):
    """Every legacy row/record, including orphan payloads and unknown kinds."""
    rows = {}
    for table in ('saas_billing', 'saas_billing_events', 'saas_entitlements'):
        for item in snapshot.rows[table]:
            values = item['values']
            if table == 'saas_entitlements' and not (values['paid_until'] > 0 or values['subscription_status'] != ''):
                continue
            rows[ref_key(item['ref'])] = item
    records = {key: value for key, value in snapshot.records.items()
               if key[0] in {'saas-checkout-intent', 'saas-stripe-event'} or key[0] not in inv._KNOWN_KINDS}
    return rows, records


def validate_provider(runtime, evidence):
    provider = runtime.facts(runtime.one('provider'))
    require(bool(provider.objects))
    unique([obj.reference_id for obj in provider.objects])
    unique([obj.returned_id for obj in provider.objects])
    ids = {obj.returned_id: obj for obj in provider.objects}
    prefixes = {'account': 'acct_', 'customer': 'cus_', 'subscription': 'sub_',
                'checkout': 'cs_', 'invoice': 'in_', 'event': 'evt_', 'price': 'price_',
                'portal': 'bpc_', 'line_item': 'li_'}
    account = runtime.policy.target.account_id
    for obj in provider.objects:
        require(obj.requested_id == obj.returned_id and obj.returned_id.startswith(prefixes[obj.object_type]))
        require(obj.account_id == account and obj.app_id == runtime.policy.app_id)
        if obj.object_type == 'account':
            require(obj.returned_id == account)
        unique_refs(obj.retained_refs)
        if obj.outcome == 'retained_terminal':
            require(bool(obj.retained_refs))
            for ref in obj.retained_refs:
                require(ref.artifact_id in {a.id for a in evidence.artifacts})
                runtime.reference(ref, {'deployment'})
        else:
            require(not obj.retained_refs)
        for field, expected_type in [('customer_id', 'customer'), ('subscription_id', 'subscription'),
                                     ('checkout_id', 'checkout'), ('invoice_id', 'invoice'), ('price_id', 'price')]:
            pointer = getattr(obj, field)
            if pointer is not None:
                require(pointer in ids and ids[pointer].object_type == expected_type)
                related = ids[pointer]
                require(expected_type == 'price' or obj.user_id == related.user_id)
                if obj.customer_id is not None and related.customer_id is not None:
                    require(obj.customer_id == related.customer_id)
                if obj.subscription_id is not None and related.subscription_id is not None:
                    require(obj.subscription_id == related.subscription_id)
        if obj.object_type in {'subscription', 'checkout', 'invoice'}:
            require(obj.customer_id is not None and obj.user_id is not None)
    for obj in provider.objects:
        if obj.object_type == 'event':
            require(obj.event_object_id in ids)
            nested = ids[obj.event_object_id]
            require(nested.object_type in {'customer', 'subscription', 'checkout', 'invoice'})
            require(nested.user_id == obj.user_id)
            for field in ('customer_id', 'subscription_id', 'checkout_id', 'invoice_id'):
                value = getattr(nested, field)
                if value is not None:
                    require(getattr(obj, field) == value)
            own_field = {'customer':'customer_id', 'subscription':'subscription_id',
                         'checkout':'checkout_id', 'invoice':'invoice_id'}[nested.object_type]
            require(getattr(obj, own_field) == nested.returned_id)
        else:
            require(obj.event_object_id is None)
    require(account in ids and ids[account].object_type == 'account')
    config = runtime.facts(runtime.one('configuration'))
    require(config.price_id in ids and ids[config.price_id].object_type == 'price')
    require(config.portal_id in ids and ids[config.portal_id].object_type == 'portal')
    for obj in provider.objects:
        if obj.object_type in {'subscription', 'checkout', 'invoice', 'line_item'}:
            require(obj.price_id == config.price_id)
        if obj.object_type not in {'account', 'price', 'portal'}:
            require(obj.user_id is not None)
    unique([(c.collection, c.parent_id) for c in provider.collections])
    collections = {(c.collection, c.parent_id): c for c in provider.collections}
    for collection in provider.collections:
        require(collection.pagination == 'complete')
        unique(collection.object_ids)
        require(collection.parent_id in ids)
        for object_id in collection.object_ids:
            require(object_id in ids)
        field, kind = {'subscriptions': ('customer_id', 'subscription'),
                       'sessions': ('customer_id', 'checkout'),
                       'invoices': ('customer_id', 'invoice'),
                       'line_items': ('checkout_id', 'line_item')}[collection.collection]
        expected = {obj.returned_id for obj in provider.objects
                    if obj.object_type == kind and getattr(obj, field) == collection.parent_id}
        require(set(collection.object_ids) == expected)
    for obj in provider.objects:
        required = ('subscriptions', 'sessions', 'invoices') if obj.object_type == 'customer' else (
            ('line_items',) if obj.object_type == 'checkout' else ())
        require(all((kind, obj.returned_id) in collections for kind in required))
    return ids



def validate_retained_event(payload, event, provider_ids, lineage, app_id):
    """All retained payloads, including orphans, must agree with exact observations."""
    require(event.object_type == 'event' and event.user_id == lineage.user_id)
    data = payload.get('data')
    require(isinstance(data, dict) and isinstance(data.get('object'), dict))
    obj = data['object']
    require(obj.get('id') == event.event_object_id)
    observed = provider_ids[event.event_object_id]
    if 'object' in obj:
        expected_type = {'customer':'customer', 'subscription':'subscription',
                         'checkout':'checkout.session', 'invoice':'invoice'}[observed.object_type]
        require(obj['object'] == expected_type)
    if 'livemode' in obj:
        require(obj['livemode'] is False)
    for field, reference in [('customer', 'customer_id'), ('subscription', 'subscription_id'),
                             ('checkout_session', 'checkout_id'), ('invoice', 'invoice_id')]:
        expected = getattr(observed, reference)
        # A relationship required by the matching object observation cannot be
        # omitted. A nested object's own ID is checked separately above.
        if expected is not None or field in obj:
            require(obj.get(field) == expected == getattr(event, reference))
    relationship_fields = {'customer':'customer_id', 'subscription':'subscription_id',
                           'checkout_session':'checkout_id', 'invoice':'invoice_id'}
    def check_nested_relationships(value):
        if isinstance(value, dict):
            for field, child in value.items():
                if field == 'metadata':
                    continue
                if field in relationship_fields:
                    expected = getattr(event, relationship_fields[field])
                    actual = child.get('id') if isinstance(child, dict) else child
                    require(actual == expected)
                check_nested_relationships(child)
        elif isinstance(value, list):
            for child in value:
                check_nested_relationships(child)
    check_nested_relationships(obj)
    def check_metadata(value):
        if isinstance(value, dict):
            if 'metadata' in value:
                metadata = value['metadata']
                require(isinstance(metadata, dict))
                for field, expected in [('user_id', lineage.user_id), ('app', app_id), ('app_id', app_id)]:
                    if field in metadata:
                        require(metadata[field] == expected)
            for child in value.values():
                check_metadata(child)
        elif isinstance(value, list):
            for child in value:
                check_metadata(child)
    check_metadata(obj)
    metadata = obj.get('metadata')
    # Ownership-bearing objects in the existing billing contract require these
    # metadata fields; invoice events may carry ownership through exact pointers.
    if observed.object_type in {'customer', 'subscription', 'checkout'}:
        require(isinstance(metadata, dict) and metadata.get('user_id') == lineage.user_id
                and metadata.get('app') == app_id)

def prepared_copy_tables(snapshot, copy):
    """Derive the fixed existing prepare+release restore effects in memory.

    No target mutation or arbitrary after-values: both authority applications
    use this exact source's current tombstones and pending revocations. Existing
    ciphertext is only retained or removed by the fixed restore rules.
    """
    tables = {name: [dict(item['values']) for item in rows] for name, rows in snapshot.rows.items()}
    tables['records'] = [row for row in tables['records'] if (row['kind'], row['id']) != BINDING_KEY]
    # The existing restore recipe constructs Store on the disposable copy
    # before cleanup, installing precisely the two frozen receiver objects.
    schema = list(snapshot.schema)
    upgrade_receiver = snapshot.root.schema_variant == 'schema10_deployed_rc15'
    if upgrade_receiver:
        schema.extend(row for row in inv._BASE_SCHEMA if row[1] in inv._RECEIVER_OBJECTS)
    prepared_variant = inv._schema_variant(schema)
    require(prepared_variant == 'schema10_candidate_receiver')
    cleanup_kinds = {'saas-session', 'saas-login', 'saas-pair', 'saas-installation', 'saas-capability',
                     'saas-candidate', 'authorization', 'connection',
                     'saas-meta-webhook-binding', 'saas-meta-webhook-event'}
    pending = {row['id'] for row in tables['saas_google_revocations'] if row['state'] == 'pending'}
    tombstones = list(tables['saas_deletion_tombstones'])
    def remove_records(keys):
        keys = set(keys) & {(row['kind'], row['id']) for row in tables['records']}
        if prepared_variant == 'schema10_candidate_receiver':
            grants = {record_id for kind, record_id in keys if kind == 'saas-grant'}
            keys |= ({(row['kind'], row['id']) for row in tables['saas_record_links']
                      if row['grant_id'] in grants and row['kind'] in
                      {'saas-meta-webhook-binding', 'saas-meta-webhook-event'}} &
                     {(row['kind'], row['id']) for row in tables['records']})
        tables['records'] = [row for row in tables['records'] if (row['kind'], row['id']) not in keys]
        for name in ('saas_record_owners', 'saas_record_links', 'saas_record_emails'):
            tables[name] = [row for row in tables[name] if (row['kind'], row['id']) not in keys]
    # prepare_restore and release_restore each apply the current-authority
    # cleanup. Only session_version is non-idempotent across the two passes.
    for _ in range(2):
        remove_records((row['kind'], row['id']) for row in tables['records'] if row['kind'] in cleanup_kinds)
        for row in tables['saas_users']:
            row['session_version'] += 1
        if 'legacy_allowance' in tables:
            tables['legacy_allowance'] = []
        for row in list(tables['saas_google_revocations']):
            if row['id'] not in pending:
                remove_records({('google-revocation', row['id'])})
                tables['saas_google_revocations'].remove(row)
            else:
                row.update(state='pending', lease=None, lease_until=0.0, next_attempt=0.0)
        for tombstone in tombstones:
            kind, record_id = tombstone['kind'], tombstone['id']
            require(kind in {'grant', 'user'})
            if kind == 'grant':
                remove_records({('saas-grant', record_id)})
                tables['saas_slots'] = [row for row in tables['saas_slots'] if row['grant_id'] != record_id]
            else:
                customers = {row['customer_id'] for row in tables['saas_billing']
                             if row['user_id'] == record_id and row['customer_id']}
                events = [row for row in tables['saas_billing_events'] if row['customer_id'] in customers]
                remove_records(('saas-stripe-event', ':'.join((row['account_id'], row['mode'], row['event_id'])))
                               for row in events)
                tables['saas_billing_events'] = [row for row in tables['saas_billing_events'] if row not in events]
                owned = {(row['kind'], row['id']) for row in tables['saas_record_owners'] if row['user_id'] == record_id}
                remove_records(owned | {('saas-user', record_id)})
                for name in ('saas_users', 'saas_entitlements', 'saas_billing', 'saas_slots', 'saas_brands',
                             'saas_reminders', 'saas_deletions', 'saas_media_allocations'):
                    field = 'id' if name == 'saas_users' else 'user_id'
                    tables[name] = [row for row in tables[name] if row[field] != record_id]
        slot_users = {row['user_id'] for row in tables['saas_slots']}
        tables['saas_brands'] = [row for row in tables['saas_brands'] if row['user_id'] in slot_users]
    require(all(any(row['kind'] == 'google-revocation' and row['id'] == record_id
                    for row in tables['records']) for record_id in pending))
    tables['saas_restore_guard'] = [{'singleton': 1, 'state': 'ready',
        'prepared': float(copy.prepared_at), 'snapshot_created': float(copy.snapshot_created_at)}]
    if 'saas_restore_guard' not in snapshot.rows:
        schema.append(inv._RESTORE_SCHEMA)
    metadata = {}
    for name in tables:
        if name == 'saas_restore_guard' and name not in snapshot.rows:
            columns = [(0, 'singleton', 'INTEGER', 0, None, 1, 0),
                       (1, 'state', 'TEXT', 1, None, 0, 0),
                       (2, 'prepared', 'REAL', 1, None, 0, 0),
                       (3, 'snapshot_created', 'REAL', 1, None, 0, 0)]
            require(inv._rows_digest([('column', *row) for row in columns]) == inv._SOURCE_METADATA_ROOTS[name])
            metadata[name] = {'columns': columns, 'indexes': []}
        else:
            columns = [tuple(row) for row in snapshot.db.execute(f'PRAGMA table_xinfo("{name}")')]
            indexes = [(tuple(index), [tuple(row) for row in snapshot.db.execute(f'PRAGMA index_xinfo("{index[1]}")')])
                       for index in snapshot.db.execute(f'PRAGMA index_list("{name}")').fetchall()]
            if name == 'saas_record_links' and upgrade_receiver:
                # SQLite lists the newly created index first. All source column
                # ordinals and index metadata have already passed frozen review.
                cids = {column[1]: column[0] for column in columns}
                grant_columns = [(seq, cids[column], column, 0, 'BINARY', 1)
                                 for seq, column in enumerate(('grant_id', 'kind', 'id'))]
                grant_columns.append((3, -1, None, 0, 'BINARY', 0))
                indexes = [((0, 'saas_record_links_grant', 0, 'c', 0), grant_columns)] + [
                    ((index[0] + 1, *index[1:]), values) for index, values in indexes]
                frozen_metadata = [('column', *column) for column in columns]
                for index, values in indexes:
                    frozen_metadata.append(('index', *index[1:]))
                    frozen_metadata.extend(('index_column', index[1], *value) for value in values)
                require(inv._rows_digest(frozen_metadata) == inv._SOURCE_METADATA_ROOTS[name])
            metadata[name] = {'columns': columns, 'indexes': indexes}
    logical = {}
    for name, rows in tables.items():
        columns = sorted(column[1] for column in metadata[name]['columns'])
        logical[name] = {'columns': columns, 'rows': len(rows),
                         'digest': inv._rows_digest([tuple(row[column] for column in columns) for row in rows])}
    schema_root = inv._schema_root(schema, metadata, snapshot.db.execute('PRAGMA encoding').fetchone()[0])
    return logical, schema_root, prepared_variant


def preparation_reason(table):
    return {'records':'access_cleanup', 'saas_record_owners':'access_cleanup',
            'saas_record_links':'access_cleanup', 'saas_record_emails':'access_cleanup',
            'saas_users':'session_invalidation', 'saas_google_revocations':'revocation_authority',
            'legacy_allowance':'legacy_cleanup', 'saas_restore_guard':'restore_guard'}.get(table, 'deletion_authority')


def validate_rehearsal(runtime, snapshot, baseline, now):
    rehearsal = runtime.facts(runtime.one('rehearsal'))
    compatibility = runtime.facts(runtime.one('compatibility'))
    copy = rehearsal.prepared_copy
    require(copy.source_target == runtime.policy.target and copy.source_baseline == baseline)
    require(copy.source_authority_id == runtime.facts(runtime.one('authority')).endpoint_id)
    require(copy.target.account_id == runtime.policy.target.account_id)
    require(copy.target.host_authority_id == runtime.policy.target.host_authority_id)
    require(copy.target.service_id != runtime.policy.target.service_id)
    require(copy.target.database_path != runtime.policy.target.database_path)
    require((copy.target.device, copy.target.inode) != (runtime.policy.target.device, runtime.policy.target.inode))
    require((copy.identity.device, copy.identity.inode) == (copy.target.device, copy.target.inode))
    require(copy.snapshot_created_at <= copy.prepared_at <= now)
    require(copy.prepared_at - copy.snapshot_created_at <= 6 * 86400)
    logical, schema_root, prepared_variant = prepared_copy_tables(snapshot, copy)
    expected = root_from_inventory({'tables': logical, 'schema_root': schema_root,
        'state_root': inv._state_root(schema_root, inv._data_root(logical), copy.identity.model_dump()),
        'schema_variant': prepared_variant})
    require(copy.baseline == expected and rehearsal.baseline_sha256 == expected.state_sha256)
    before = {table.name: table for table in baseline.tables}
    after = {table.name: table for table in expected.tables}
    expected_changes = {name for name in after if before.get(name) != after[name]}
    require(unique([change.table for change in copy.changes]) == expected_changes)
    for change in copy.changes:
        require(change.before == before.get(change.table) and change.after == after[change.table])
        require(change.reason == preparation_reason(change.table))
    delta = rehearsal.enrollment_delta
    require(rehearsal.exact_delta_sha256 == sha256(encode(delta)))
    require(delta.post_excluding_binding == expected)
    require(delta.binding == s.Binding(kind=BINDING_KEY[0], id=BINDING_KEY[1],
        payload=s.BindingPayload(account_id=runtime.policy.target.account_id, mode='test'),
        expires=253402300799, credential_hash=''))
    require(delta.post.schema_variant == expected.schema_variant and delta.post.schema_sha256 == expected.schema_sha256)
    post = {table.name: table for table in delta.post.tables}
    require(unique([table.name for table in delta.post.tables]) == set(after))
    for name in after:
        require(post[name].count == after[name].count + (name == 'records'))
        if name != 'records':
            require(post[name] == after[name])
        else:
            require(post[name].sha256 != after[name].sha256)
    post_logical = {name: {**logical[name], 'rows': table.count, 'digest': table.sha256} for name, table in post.items()}
    require(delta.post.state_sha256 == inv._state_root(schema_root, inv._data_root(post_logical), copy.identity.model_dump()))
    require(compatibility.rehearsal_sha256 == runtime.one('rehearsal').sha256)
    require(compatibility.copy_target == copy.target and compatibility.before == delta.post == compatibility.after)
    require(compatibility.binding_ciphertext_sha256 == delta.binding_ciphertext_sha256)


def validate_evidence(runtime, snapshot, evidence, now):
    require(evidence.created_at <= now)
    unique([a.id for a in evidence.artifacts])
    declared = {a.id for a in evidence.artifacts}
    for artifact in evidence.artifacts:
        require(artifact.id in runtime.entries)
        entry = runtime.entries[artifact.id]
        require(entry.role == artifact.role and entry.sha256 == artifact.sha256)
    require(evidence.documentary_review_ref.artifact_id in declared)
    doc = runtime.reference(evidence.documentary_review_ref, {'documentary_review'})
    require(not doc.findings and doc.account_id == runtime.policy.target.account_id)
    expected_digests = [a.sha256 for a in evidence.artifacts
                        if a.id != evidence.documentary_review_ref.artifact_id]
    require(unique(doc.evidence_sha256) == unique(expected_digests))
    require(bool(evidence.transitions))
    transition_ids = unique([t.id for t in evidence.transitions])
    require(doc.transition_ids == [t.id for t in evidence.transitions])
    authority = runtime.facts(runtime.one('authority'))
    require(doc.current_authority_id == authority.endpoint_id)
    first = runtime.facts(runtime.one('initial_empty'))
    require(first.endpoint_id == doc.original_empty_id and first.account_id == doc.account_id)
    require(first.observed_at <= now)
    validated_history = set()
    endpoint = first.endpoint_id
    last_observed = first.observed_at
    for index, transition in enumerate(evidence.transitions):
        require(transition.from_id == endpoint and transition.to_id != endpoint)
        require(transition.account_id == doc.account_id and bool(transition.receipts))
        require((index == 0) == (transition.kind == 'initial'))
        receipts = []
        unique_refs(transition.receipts)
        for ref in transition.receipts:
            require(ref.artifact_id in declared)
            receipt = runtime.reference(ref, {'initial_empty', 'deployment'})
            receipts.append(receipt)
            if isinstance(receipt, s.ContinuityFacts):
                validated_history.add((ref.artifact_id, ref.selector))
        continuity = [r for r in receipts if isinstance(r, s.ContinuityFacts)]
        require(bool(continuity))
        if index == 0:
            require(first in receipts)
        for receipt in continuity:
            require(receipt.transition_id == transition.id and receipt.finding == 'continuous')
            require((receipt.from_id, receipt.to_id, receipt.kind, receipt.account_id, receipt.mode) ==
                    (transition.from_id, transition.to_id, transition.kind, transition.account_id, transition.mode))
            require(last_observed <= receipt.observed_at <= now)
        last_observed = max(r.observed_at for r in continuity)
        endpoint = transition.to_id
    unique([evidence.transitions[0].from_id, *[t.to_id for t in evidence.transitions]])
    require(endpoint == authority.endpoint_id)
    require({a.id for a in evidence.artifacts if a.role == 'deployment'} ==
            {artifact_id for artifact_id, _ in validated_history})
    provider_ids = validate_provider(runtime, evidence)
    required_rows, required_records = required_coverage(snapshot)
    lineage_ids = unique([lineage.id for lineage in evidence.lineages])
    require(unique(doc.lineage_ids) == lineage_ids)
    all_rows, all_records = {}, {}
    uncertain_required_rows = set()
    uncertain_required_records = set()
    for lineage in evidence.lineages:
        require(lineage.origin_transition_id in transition_ids and bool(lineage.historical_refs))
        require(bool(lineage.rows or lineage.record_refs))
        for refs in (lineage.historical_refs, lineage.provider_refs, lineage.owner_instruction_refs):
            unique_refs(refs)
        for ref in lineage.historical_refs:
            require((ref.artifact_id, ref.selector) in validated_history)
            require(ref.artifact_id in declared)
            historical = runtime.reference(ref, {'deployment'})
            require(historical.transition_id in transition_ids and historical.finding == 'continuous')
        require(any(runtime.reference(ref, {'deployment'}).transition_id == lineage.origin_transition_id
                    for ref in lineage.historical_refs))
        provider_objects = []
        if lineage.category == 'paid_manual':
            require(lineage.account_id is None and lineage.mode is None and not lineage.provider_refs)
            require(bool(lineage.owner_instruction_refs) and bool(lineage.rows))
        else:
            require(lineage.account_id == doc.account_id and lineage.mode == 'test')
            require(bool(lineage.provider_refs) and not lineage.owner_instruction_refs)
            for ref in lineage.provider_refs:
                require(ref.artifact_id in declared)
                obj = runtime.reference(ref, {'provider'})
                require(isinstance(obj, s.ProviderObject))
                require(obj.user_id == lineage.user_id or obj.object_type in {'account', 'price', 'portal'})
                provider_objects.append(obj)
        linked_ids = {obj.returned_id for obj in provider_objects}
        for reference in lineage.rows:
            key = ref_key(reference)
            require(key in required_rows and key not in all_rows)
            all_rows[key] = lineage.id
            values = required_rows[key]['values']
            category = {'saas_billing': {'billing'}, 'saas_billing_events': {'event'},
                        'saas_entitlements': {'paid_provider', 'paid_manual'}}[reference.table]
            require(lineage.category in category)
            if 'user_id' in values:
                require(values['user_id'] == lineage.user_id)
            if reference.table == 'saas_billing':
                require(not values['lease'] and values['lease_until'] == 0)
                for field in ('customer_id', 'subscription_id', 'checkout_id'):
                    if values[field]:
                        require(values[field] in linked_ids)
                if values['subscription_id']:
                    require(provider_ids[values['subscription_id']].customer_id == values['customer_id'])
                if values['checkout_id']:
                    obj = provider_ids[values['checkout_id']]
                    require(obj.customer_id == values['customer_id'])
                    if values['subscription_id']:
                        require(obj.subscription_id == values['subscription_id'])
                uncertain_required_rows.add(key)  # Preserve inventory's conservative classification.
            elif reference.table == 'saas_billing_events':
                require(values['account_id'] == doc.account_id and values['mode'] == 'test')
                require(values['event_id'] in linked_ids)
                obj = provider_ids[values['event_id']]
                require(obj.object_type == 'event' and obj.customer_id == values['customer_id'])
                if values['state'] not in {'done', 'ignored'}:
                    uncertain_required_rows.add(key)
            elif lineage.category == 'paid_provider':
                billing = [row['values'] for row in snapshot.rows['saas_billing']
                           if row['values']['user_id'] == lineage.user_id]
                require(len(billing) == 1 and billing[0]['subscription_id'] in linked_ids)
        for reference in lineage.record_refs:
            key = record_key(reference)
            require(key in required_records and key not in all_records)
            all_records[key] = lineage.id
            payload = required_records[key]
            if key[0] == 'saas-checkout-intent':
                require(lineage.category == 'intent' and payload.get('user_id') == lineage.user_id)
            elif key[0] == 'saas-stripe-event':
                require(lineage.category == 'event' and payload.get('livemode') is False)
                require(payload.get('account') in (None, doc.account_id))
                require(payload.get('id') in linked_ids)
                validate_retained_event(payload, provider_ids[payload['id']], provider_ids, lineage, runtime.policy.app_id)
            owners = [row['values']['user_id'] for row in snapshot.rows['saas_record_owners']
                      if (row['values']['kind'], row['values']['id']) == key]
            require(not owners or owners == [lineage.user_id])
            uncertain_required_records.add(key)
        if lineage.category == 'paid_manual':
            covered = set()
            for ref in lineage.owner_instruction_refs:
                require(ref.artifact_id in declared)
                grant = runtime.reference(ref, {'owner_instruction'})
                require(grant.user_id == lineage.user_id and grant.observed_at <= now)
                covered |= unique([ref_key(row) for row in grant.rows])
            require(covered == {ref_key(row) for row in lineage.rows})
    require(set(all_rows) == set(required_rows) and set(all_records) == set(required_records))
    require(unique([ref_key(row) for row in doc.rows]) == set(all_rows))
    require(unique([record_key(ref) for ref in doc.record_refs]) == set(all_records))
    uncertainty_ids = unique([u.id for u in evidence.uncertainties])
    require(unique(doc.uncertainty_ids) == uncertainty_ids)
    require(unique(runtime.facts(runtime.one('rehearsal')).uncertainty_ids) == uncertainty_ids)
    uncertain_rows, uncertain_records = set(), set()
    for uncertainty in evidence.uncertainties:
        require(uncertainty.row_refs or uncertainty.record_refs)
        require(uncertainty.reason_code in {'operation_outcome', 'orphan_record', 'unknown_kind',
                                           'billing_classification', 'nonterminal_event'})
        provenance = unique(uncertainty.provenance_lineage_ids)
        require(bool(provenance) and provenance <= lineage_ids)
        for reference in uncertainty.row_refs:
            key = ref_key(reference)
            require(key in all_rows and all_rows[key] in provenance and key not in uncertain_rows)
            uncertain_rows.add(key)
        for reference in uncertainty.record_refs:
            key = record_key(reference)
            require(key in all_records and all_records[key] in provenance and key not in uncertain_records)
            uncertain_records.add(key)
    require(uncertain_required_rows <= uncertain_rows and uncertain_required_records <= uncertain_records)
    for intent in snapshot.report['billing']['intents']:
        require(not intent['ownership_discrepancy'])
    for payload in snapshot.report['billing']['event_payloads']:
        require(not payload['local_discrepancies'])
    for jobs in ('deletions', 'revocations', 'reminders'):
        require(snapshot.report['coverage'][jobs]['leased'] == 0)


class Clock:
    def __init__(self):
        self.wall = time.time()
        self.monotonic = time.monotonic()
        self.last = self.wall

    def now(self):
        wall = time.time()
        require(wall >= self.last and abs((wall - self.wall) - (time.monotonic() - self.monotonic)) <= 1)
        self.last = wall
        return int(wall)


def make_ref(runtime, role):
    return s.Ref(artifact_id=runtime.one(role).id, selector='facts')


def validate_snapshot(runtime, snapshot, evidence, now, *, baseline=None):
    target_matches(snapshot, runtime.policy.target)
    require(snapshot.report['restore']['state'] in {'ready', 'absent'})
    if baseline is None:
        require(snapshot.report['marker'] == {'state': 'absent', 'index_rows': 0})
    else:
        require(snapshot.report['marker'] == {'state': 'present', 'index_rows': 0})
    inventory_raw = runtime.raw[runtime.one('inventory').id]
    inventory_report = strict_json(inventory_raw)
    require(inventory_report.get('format') == 'socialcli-billing-inventory-v1')
    expected_root = root_from_inventory(inventory_report)
    actual_root = snapshot.root if baseline is None else baseline
    if expected_root != actual_root:
        raise BaselineMismatch(ERROR)
    require(inventory_report['target'] == snapshot.identity)
    tables, schema_root, data_root = inv._logical_state(snapshot.db, snapshot.schema,
                                                      exclude_binding=baseline is not None)
    original_records = {key: value for key, value in snapshot.records.items() if key != BINDING_KEY}
    original_report = inv._inventory(snapshot.db, original_records, tables)
    expected_report = inv._inspection_report(snapshot.schema, snapshot.identity, tables,
                                             schema_root, data_root, original_report)
    require(json.dumps(inventory_report, sort_keys=True, allow_nan=False) ==
            json.dumps(expected_report, sort_keys=True, allow_nan=False))
    validate_rehearsal(runtime, snapshot, actual_root, now)
    validate_evidence(runtime, snapshot, evidence, now)
    for entry in runtime.entries.values():
        if entry.role == 'approval':
            approval = runtime.model(entry, s.Approval)
            if approval.scope == 'documentary_review':
                require(approval.created_at >= evidence.created_at)
    return validate_approvals(runtime, 'documentary_review', runtime.one('evidence').sha256, now)


def validate_manifest(runtime, manifest, snapshot, evidence, now, *, enrolled=False):
    require(manifest.created_at <= now < manifest.expires_at)
    require(0 < manifest.expires_at - manifest.created_at <= runtime.policy.manifest_max_age)
    require(manifest.target == runtime.policy.target)
    require(manifest.binding.payload.account_id == manifest.target.account_id)
    authority, auth, freeze, provider_oldest = final_facts(runtime, now)
    for name, role in [('authority_ref', 'authority'), ('auth_authority_ref', 'auth_authority'),
                       ('freeze_ref', 'freeze'), ('provider_ref', 'provider'), ('backup_ref', 'backup'),
                       ('rehearsal_ref', 'rehearsal'), ('compatibility_ref', 'compatibility')]:
        require(getattr(manifest, name) == make_ref(runtime, role))
    require(manifest.authority_observed_at == authority.observed_at)
    require(manifest.freeze_started_at == freeze.started_at and manifest.provider_observed_at == provider_oldest)
    require(manifest.evidence_sha256 == runtime.one('evidence').sha256)
    require(manifest.source_sha256 == runtime.policy.source_sha256
            and manifest.tool_sha256 == runtime.policy.tool_sha256
            and manifest.image_sha256 == runtime.policy.image_sha256
            and manifest.config_sha256 == runtime.policy.config_sha256
            and manifest.policy_sha256 == runtime.policy_digest
            and manifest.inventory_sha256 == runtime.one('inventory').sha256)
    baseline = transaction_root(snapshot.db, snapshot.schema, snapshot.identity, exclude_binding=True) if enrolled else None
    documentary = validate_snapshot(runtime, snapshot, evidence, now, baseline=baseline)
    require(documentary == manifest.documentary_approval_sha256)
    if (baseline or snapshot.root) != manifest.baseline:
        raise BaselineMismatch(ERROR)
    final = validate_approvals(runtime, 'enrollment_manifest', runtime.one('manifest').sha256, now)
    for entry in runtime.entries.values():
        if entry.role == 'approval':
            approval = runtime.model(entry, s.Approval)
            minimum = manifest.created_at if approval.scope == 'enrollment_manifest' else evidence.created_at
            require(approval.created_at >= minimum)
    return documentary + final


def prepare(runtime, keys, destination):
    """Read-only preparation. Never creates evidence, approvals, or a binding."""
    clock = Clock()
    runtime = runtime.fresh()
    evidence = runtime.model(runtime.one('evidence'), s.Evidence)
    with database(Path(runtime.policy.target.database_path), keys) as current:
        now = clock.now()
        documentary = validate_snapshot(runtime, current, evidence, now)
        authority, auth, freeze, provider_oldest = final_facts(runtime, now)
        require(evidence.created_at <= now)
        manifest = s.Manifest.model_validate({
            'schema': 'socialcli-legacy-enrollment-manifest/v1',
            'nonce': 'enrollment_' + uuid.uuid4().hex, 'created_at': now, 'expires_at': now + 300,
            'target': runtime.policy.target.model_dump(), 'baseline': current.root.model_dump(),
            'evidence_sha256': runtime.one('evidence').sha256,
            'documentary_approval_sha256': documentary,
            'source_sha256': runtime.policy.source_sha256, 'tool_sha256': runtime.policy.tool_sha256,
            'image_sha256': runtime.policy.image_sha256, 'config_sha256': runtime.policy.config_sha256,
            'policy_sha256': runtime.policy_digest, 'inventory_sha256': runtime.one('inventory').sha256,
            'authority_ref': make_ref(runtime, 'authority').model_dump(),
            'authority_observed_at': authority.observed_at,
            'auth_authority_ref': make_ref(runtime, 'auth_authority').model_dump(),
            'backup_ref': make_ref(runtime, 'backup').model_dump(),
            'freeze_ref': make_ref(runtime, 'freeze').model_dump(),
            'freeze_started_at': freeze.started_at,
            'provider_ref': make_ref(runtime, 'provider').model_dump(),
            'provider_observed_at': provider_oldest,
            'rehearsal_ref': make_ref(runtime, 'rehearsal').model_dump(),
            'compatibility_ref': make_ref(runtime, 'compatibility').model_dump(),
            'binding': {'kind': BINDING_KEY[0], 'id': BINDING_KEY[1],
                        'payload': {'account_id': runtime.policy.target.account_id, 'mode': 'test'},
                        'expires': 253402300799, 'credential_hash': ''},
        })
        runtime.fresh()
        current.check_identity()
        final_now = clock.now()
        require(final_now < manifest.expires_at)
        final_facts(runtime, final_now)
        validate_approvals(runtime, 'documentary_review', manifest.evidence_sha256, final_now)
        digest = write_private(destination, encode(manifest))
    return {'status': 'prepared', 'manifest_sha256': digest}


def exact_delta(snapshot, manifest):
    payload = snapshot.records.get(BINDING_KEY)
    require(payload == manifest.binding.payload.model_dump())
    rows = snapshot.db.execute('SELECT expires,credential_hash,payload FROM records WHERE kind=? AND id=?', BINDING_KEY).fetchall()
    require(len(rows) == 1 and rows[0][0] == 253402300799 and rows[0][1] == '')
    require(snapshot.report['marker'] == {'state': 'present', 'index_rows': 0})
    excluding = transaction_root(snapshot.db, snapshot.schema, snapshot.identity, exclude_binding=True)
    if excluding != manifest.baseline:
        raise BaselineMismatch(ERROR)
    before = {item.name: item for item in manifest.baseline.tables}
    after = {item.name: item for item in snapshot.root.tables}
    require(set(before) == set(after) and snapshot.root.schema_sha256 == manifest.baseline.schema_sha256)
    for name in before:
        require(after[name].count == before[name].count + (1 if name == 'records' else 0))
        if name != 'records':
            require(after[name] == before[name])
    return excluding, sha256(rows[0][2])



def final_time_check(runtime, manifest, now):
    """No hashing/I/O here: recheck every temporal authority immediately at COMMIT."""
    require(manifest.created_at <= now < manifest.expires_at)
    final_facts(runtime, now)
    validate_approvals(runtime, 'documentary_review', manifest.evidence_sha256, now)
    validate_approvals(runtime, 'enrollment_manifest', runtime.one('manifest').sha256, now)

def apply(runtime, keys, destination):
    """One uniqueness-enforced INSERT. Acknowledgement failure never replays it."""
    clock = Clock()
    commit_attempted = False
    stage = 'apply_precommit'
    try:
        inv._destination(destination)
        runtime = runtime.fresh()
        require(not any(entry.role == 'receipt' for entry in runtime.entries.values()))
        manifest_entry = runtime.one('manifest')
        manifest = runtime.model(manifest_entry, s.Manifest)
        evidence = runtime.model(runtime.one('evidence'), s.Evidence)
        # The first clock read is pre-lock; all decisive checks are repeated
        # after BEGIN IMMEDIATE and after authentication/whole-state hashing.
        require(manifest.created_at <= clock.now() < manifest.expires_at)
        with database(Path(runtime.policy.target.database_path), keys, write=True) as current:
            runtime.fresh()
            approvals = validate_manifest(runtime, manifest, current, evidence, clock.now())
            require(current.root == manifest.baseline)
            envelope = {'kind': BINDING_KEY[0], 'id': BINDING_KEY[1],
                        'expires': 253402300799, 'credential_hash': '',
                        'payload': manifest.binding.payload.model_dump()}
            ciphertext = Fernet(keys[0]).encrypt(json.dumps(envelope, separators=(',', ':'), allow_nan=False).encode())
            current.db.execute('INSERT INTO records(kind,id,expires,credential_hash,payload) VALUES(?,?,?,?,?)',
                               (*BINDING_KEY, 253402300799, '', ciphertext))
            after = snapshot(current.db, keys, current.path, current.identity, current.fd)
            exact_delta(after, manifest)
            runtime.fresh()
            validate_manifest(runtime, manifest, after, evidence, clock.now(), enrolled=True)
            current.check_identity()
            final_time_check(runtime, manifest, clock.now())
            stage = 'apply_commit'
            commit_attempted = True
            current.db.execute('COMMIT')
            stage = 'apply_postcommit'
            committed_at = clock.now()  # Observed successful COMMIT acknowledgement, not reconstructed history.
            final_time_check(runtime, manifest, committed_at)
            # Keep the committed connection alive until independent readback
            # finishes. Closing the last WAL writer can remove its sidecars;
            # the read-only custody guard must not recreate or bypass them.
            stage = 'apply_readback'
            with database(Path(runtime.policy.target.database_path), keys) as readback:
                runtime.fresh()
                validate_manifest(runtime, manifest, readback, evidence, committed_at, enrolled=True)
                excluding, digest = exact_delta(readback, manifest)
                require(digest == sha256(ciphertext))
                readback.check_identity()
                stage = 'apply_receipt'
                receipt = s.Receipt.model_validate({
                    'schema': 'socialcli-legacy-enrollment-receipt/v1', 'nonce': manifest.nonce,
                    'manifest_sha256': manifest_entry.sha256, 'evidence_sha256': manifest.evidence_sha256,
                    'approval_sha256': approvals, 'target': manifest.target.model_dump(),
                    'committed_at': committed_at, 'baseline': manifest.baseline.model_dump(),
                    'post': readback.root.model_dump(), 'post_excluding_binding': excluding.model_dump(),
                    'binding_ciphertext_sha256': digest, 'result': 'one_binding_insert_verified',
                })
                readback.check_identity()
                receipt_digest = write_private(destination, encode(receipt))
                stage = 'apply_cleanup'
        return {'status': 'one_binding_insert_verified', 'receipt_sha256': receipt_digest,
                'rows_added': 1, 'post_sha256': receipt.post.state_sha256}
    except BaseException as error:
        # Even a catchable interruption during COMMIT cannot establish rollback.
        # Process termination/power loss may still prevent any output at all.
        if commit_attempted:
            return {'status': 'uncertain', 'diagnostic': diagnostic(stage)}
        if not isinstance(error, Exception):
            raise
        kind = BaselineMismatch if isinstance(error, BaselineMismatch) else EnrollmentError
        raise kind(stage=stage) from None


def verify(runtime, keys, destination):
    """Fresh read-only exact delta, using original valid-at-operation approvals."""
    runtime = runtime.fresh()
    manifest_entry = runtime.one('manifest')
    manifest = runtime.model(manifest_entry, s.Manifest)
    evidence = runtime.model(runtime.one('evidence'), s.Evidence)
    receipt_entry = runtime.one('receipt')
    receipt = runtime.model(receipt_entry, s.Receipt)
    require(receipt.manifest_sha256 == manifest_entry.sha256 and receipt.nonce == manifest.nonce)
    require(receipt.evidence_sha256 == manifest.evidence_sha256 and receipt.target == manifest.target)
    require(receipt.baseline == manifest.baseline and receipt.post_excluding_binding == manifest.baseline)
    clock = Clock()
    require(receipt.committed_at <= clock.now())
    with database(Path(runtime.policy.target.database_path), keys) as current:
        approvals = validate_manifest(runtime, manifest, current, evidence, receipt.committed_at, enrolled=True)
        require(approvals == receipt.approval_sha256)
        excluding, digest = exact_delta(current, manifest)
        require(current.root == receipt.post and digest == receipt.binding_ciphertext_sha256)
        runtime.fresh()
        current.check_identity()
        result = s.Verification.model_validate({
            'schema': 'socialcli-legacy-enrollment-verification/v1',
            'manifest_sha256': manifest_entry.sha256, 'receipt_sha256': receipt_entry.sha256,
            'verified_at': clock.now(), 'result': 'exact_delta_verified',
            'post': current.root.model_dump(), 'post_excluding_binding': excluding.model_dump(),
            'binding_ciphertext_sha256': digest,
        })
        digest = write_private(destination, encode(result))
    return {'status': 'exact_delta_verified', 'verification_sha256': digest}


def recover(runtime, keys, destination):
    """No INSERT and no invented commit time. Missing receipt remains uncertain."""
    if any(entry.role == 'receipt' for entry in runtime.entries.values()):
        return verify(runtime, keys, destination)
    runtime = runtime.fresh()
    manifest_entry = runtime.one('manifest')
    manifest = runtime.model(manifest_entry, s.Manifest)
    evidence = runtime.model(runtime.one('evidence'), s.Evidence)
    # This proves only that the intent had an admissible approved window. It
    # cannot prove when an unacknowledged SQLite COMMIT occurred.
    approval_times = [runtime.model(entry, s.Approval).created_at
                      for entry in runtime.entries.values() if entry.role == 'approval']
    candidate = max([manifest.created_at, *approval_times])
    with database(Path(runtime.policy.target.database_path), keys) as current:
        validate_manifest(runtime, manifest, current, evidence, candidate, enrolled=True)
        excluding, digest = exact_delta(current, manifest)
        runtime.fresh()
        current.check_identity()
        return {'status': 'uncertain', 'delta': 'exact_delta_verified',
                'diagnostic': {'stage': 'recover', 'error_code': 'receipt_missing'},
                'manifest_sha256': manifest_entry.sha256, 'post_sha256': current.root.state_sha256,
                'post_excluding_binding_sha256': excluding.state_sha256,
                'binding_ciphertext_sha256': digest, 'rows_added': 1}


def runtime_keys():
    raw = os.environ.get('SOCIALCLI_ENCRYPTION_KEYS', '')
    key_file = os.environ.get('SOCIALCLI_ENCRYPTION_KEYS_FILE', '')
    require(bool(raw) != bool(key_file))
    if key_file:
        raw = read_private(Path(key_file)).decode('ascii')
    require(len(raw) <= 65536)
    keys = [item.strip().encode('ascii') for item in raw.split(',') if item.strip()]
    require(bool(keys))
    for key in keys:
        Fernet(key)
    return keys


def main():
    """Only four commands, protected environment paths, and sanitized statuses."""
    import sys
    stage = 'cli_arguments'
    try:
        require(len(sys.argv) == 2 and sys.argv[1] in {'prepare', 'apply', 'verify', 'recover'})
        stage = 'runtime_load'
        runtime = load_runtime(Path(os.environ['SOCIALCLI_BILLING_ENROLLMENT_POLICY']),
                               image_digest=os.environ['SOCIALCLI_BILLING_ENROLLMENT_IMAGE_DIGEST'])
        stage = 'cli_preparation'
        destination = Path(os.environ['SOCIALCLI_BILLING_ENROLLMENT_DESTINATION'])
        require(destination.parent == Path(runtime.policy.custody_directory))
        operation = {'prepare': prepare, 'apply': apply, 'verify': verify, 'recover': recover}[sys.argv[1]]
        keys = runtime_keys()
        stage = sys.argv[1] if sys.argv[1] != 'apply' else 'apply_precommit'
        summary = operation(runtime, keys, destination)
        print(json.dumps(summary, sort_keys=True))
        return 2 if summary['status'] == 'uncertain' else 0
    except Exception as error:
        if isinstance(error, EnrollmentError) and error.stage in _STAGE_CODES:
            stage = error.stage
        mismatch = isinstance(error, BaselineMismatch)
        summary = {'status': 'baseline_mismatch' if mismatch else 'failed',
                   'diagnostic': diagnostic(stage, baseline_mismatch=mismatch)}
        if not mismatch:
            summary['error'] = ERROR
        print(json.dumps(summary, sort_keys=True))
        return 1
