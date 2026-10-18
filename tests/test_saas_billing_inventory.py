"""Fictional offline evidence: authentication, whole-state preservation, custody."""
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys

from cryptography.fernet import Fernet
import pytest

from socialctl.connection_service.store import Store
from socialctl.saas.store import SaaSStore


def fixture(tmp_path, *, wal=False):
    keys = [Fernet.generate_key()]
    vault = Store(tmp_path / 'private' / 'state.sqlite3', keys)
    SaaSStore(vault, keys)
    if not wal:
        with sqlite3.connect(vault.path) as db:
            db.execute('PRAGMA journal_mode=DELETE')
    return vault, keys


def api():
    from socialctl.saas import billing_inventory as inventory
    return inventory


def frozen(path):
    return {p.name: (p.read_bytes(), stat.S_IMODE(p.stat().st_mode))
            for p in path.parent.iterdir() if p.is_file()}


def test_empty_inventory_is_constructor_free_read_only_and_never_enrollment(tmp_path, monkeypatch):
    vault, keys = fixture(tmp_path)
    before = frozen(vault.path)
    parent_mode = vault.path.parent.stat().st_mode
    def forbidden(*a, **kw):
        raise AssertionError('constructor used')
    monkeypatch.setattr(Store, '__init__', forbidden)
    monkeypatch.setattr(SaaSStore, '__init__', forbidden)
    from socialctl.saas.billing import BillingService
    monkeypatch.setattr(BillingService, '__init__', forbidden)
    report = api().inspect_database(vault.path, keys)
    assert report['status'] == 'inventory_only'
    assert report['enrollment_authorized'] is False
    assert report['marker']['state'] == 'absent'
    assert set(report['not_established'].values()) == {'NOT_ESTABLISHED'}
    assert report['tables']['saas_schema']['rows'] == 10
    assert report == api().inspect_database(vault.path, keys)
    assert frozen(vault.path) == before
    assert vault.path.parent.stat().st_mode == parent_mode


@pytest.mark.parametrize('unsafe', ['missing', 'symlink', 'parent_symlink', 'file_mode',
                                   'parent_mode', 'uri', 'directory', 'hardlink'])
def test_unsafe_targets_fail_without_creation_or_permission_repair(tmp_path, unsafe):
    vault, keys = fixture(tmp_path)
    path = vault.path
    if unsafe == 'missing':
        path = path.parent / 'missing.sqlite3'
    elif unsafe == 'symlink':
        path = path.parent / 'link'; path.symlink_to(vault.path)
    elif unsafe == 'parent_symlink':
        link = tmp_path / 'link'; link.symlink_to(vault.path.parent)
        path = link / vault.path.name
    elif unsafe == 'file_mode':
        path.chmod(0o640)
    elif unsafe == 'parent_mode':
        path.parent.chmod(0o750)
    elif unsafe == 'uri':
        path = path.parent / 'state.sqlite3?mode=rw'
    elif unsafe == 'directory':
        path = vault.path.parent
    else:
        os.link(path, path.parent / 'alias')
    before = frozen(vault.path)
    with pytest.raises(api().InventoryError, match='Inventory inspection failed'):
        api().inspect_database(path, keys)
    assert frozen(vault.path) == before


@pytest.mark.parametrize('alter', [
    'DROP TABLE saas_reminders',
    'ALTER TABLE rates ADD COLUMN unreviewed TEXT',
    'CREATE TABLE unexpected (value)',
    'CREATE TRIGGER unexpected AFTER INSERT ON rates BEGIN DELETE FROM records; END',
    'UPDATE saas_schema SET version=11 WHERE version=10',
    "UPDATE sqlite_master SET sql=replace(sql,'count INTEGER NOT NULL','count TEXT NOT NULL') WHERE name='rates'",
])
def test_schema10_spoofs_new_schemas_and_extra_triggers_fail_closed(tmp_path, alter):
    vault, keys = fixture(tmp_path)
    with sqlite3.connect(vault.path) as db:
        if 'sqlite_master' in alter:
            db.execute('PRAGMA writable_schema=ON')
        db.execute(alter)
    before = frozen(vault.path)
    with pytest.raises(api().InventoryError):
        api().inspect_database(vault.path, keys)
    assert frozen(vault.path) == before


@pytest.mark.parametrize('state', ['unprepared', 'authority_unavailable', 'identity_cleanup_required', 'ready'])
def test_only_exact_defined_ready_restore_guard_is_accepted(tmp_path, state):
    vault, keys = fixture(tmp_path)
    with sqlite3.connect(vault.path) as db:
        db.execute('CREATE TABLE saas_restore_guard (singleton INTEGER PRIMARY KEY CHECK(singleton=1),state TEXT NOT NULL,prepared REAL NOT NULL,snapshot_created REAL NOT NULL)')
        db.execute('INSERT INTO saas_restore_guard VALUES(1,?,100,90)', (state,))
    before = frozen(vault.path)
    if state == 'ready':
        assert api().inspect_database(vault.path, keys)['restore']['state'] == 'ready'
    else:
        with pytest.raises(api().InventoryError):
            api().inspect_database(vault.path, keys)
    assert frozen(vault.path) == before


def test_optional_legacy_tables_are_hashed_and_summarized(tmp_path):
    from socialctl.connection_service.legacy import LegacyPolicy
    vault, keys = fixture(tmp_path)
    LegacyPolicy(vault, lambda: 1000, close=True)
    report = api().inspect_database(vault.path, keys)
    assert report['coverage']['legacy_cutoff_rows'] == 1
    assert 'legacy_allowance' in report['tables']


def test_rotated_keys_authenticate_all_expired_and_unknown_records_without_payload_dump(tmp_path):
    vault, keys = fixture(tmp_path)
    with vault.transaction() as db:
        vault.put(db, 'unknown-future-kind', 'expired-record', {'email': 'private@example.test',
                  'token': 'private-token'}, expires=1)
    keys.insert(0, Fernet.generate_key())
    report = api().inspect_database(vault.path, keys)
    assert report['records']['authenticated'] == 1
    assert report['records']['unknown'] == [{'kind': 'unknown-future-kind', 'id': 'expired-record'}]
    assert 'private@example.test' not in json.dumps(report)
    assert 'private-token' not in json.dumps(report)
    with pytest.raises(api().InventoryError):
        api().inspect_database(vault.path, keys[:1])


@pytest.mark.parametrize('column,value', [('payload', b'invalid-envelope'), ('kind', 'tampered'),
                                        ('id', 'tampered'), ('expires', 42),
                                        ('credential_hash', 'tampered')])
def test_envelope_authentication_covers_each_authenticated_sql_field(tmp_path, column, value):
    vault, keys = fixture(tmp_path)
    with vault.transaction() as db:
        vault.put(db, 'saas-checkout-intent', 'old', {'user_id': 'owner', 'deferred': {}}, expires=1)
    with sqlite3.connect(vault.path) as db:
        db.execute('UPDATE records SET '+column+'=?', (value,))
    before = frozen(vault.path)
    with pytest.raises(api().InventoryError):
        api().inspect_database(vault.path, keys)
    assert frozen(vault.path) == before


def test_typed_hashing_retains_types_duplicates_null_blob_and_order(tmp_path):
    inventory = api()
    values = [None, 1, 1.0, '1', b'1', '', b'', -0.0, 0.0]
    assert len({inventory._typed(v) for v in values}) == len(values)
    rows = [(None, 1, 1.0, '1', b'1'), (None, 1, 1.0, '1', b'1')]
    assert inventory._rows_digest(rows) != inventory._rows_digest(rows[:1])
    assert inventory._rows_digest(rows + [(3,)]) == inventory._rows_digest([(3,)] + rows)
    for value in [float('nan'), float('inf'), -float('inf')]:
        with pytest.raises(ValueError):
            inventory._typed(value)


@pytest.mark.parametrize('change', ['row', 'rate', 'orphan', 'ciphertext', 'null_primary_duplicates', 'nonfinite'])
def test_complete_root_covers_every_row_and_ciphertext(tmp_path, change):
    vault, keys = fixture(tmp_path)
    with vault.transaction() as db:
        vault.put(db, 'saas-user', 'owner', {'email': 'private@example.test'}, expires=1)
    before = api().inspect_database(vault.path, keys)
    with vault.transaction() as db:
        if change == 'row':
            db.execute("INSERT INTO saas_deletions(user_id,requested) VALUES('owner',1)")
        elif change == 'rate':
            db.execute("INSERT INTO rates VALUES('rate',1,2)")
        elif change == 'orphan':
            vault.put(db, 'saas-checkout-intent', 'orphan', {'user_id': 'absent', 'deferred': {}}, expires=1)
        elif change == 'ciphertext':
            vault.put(db, 'saas-user', 'owner', {'email': 'private@example.test'}, expires=1)
        elif change == 'null_primary_duplicates':
            db.executemany('INSERT INTO rates VALUES(NULL,1,2)', [(), ()])
        else:
            db.execute("INSERT INTO rates VALUES('rate',?,2)", (float('inf'),))
    if change == 'nonfinite':
        with pytest.raises(api().InventoryError):
            api().inspect_database(vault.path, keys)
        return
    after = api().inspect_database(vault.path, keys)
    assert after['state_root'] != before['state_root']
    assert after['schema_root'] == before['schema_root']
    if change == 'null_primary_duplicates':
        assert after['tables']['rates']['rows'] == 2


@pytest.mark.parametrize('legacy', ['customer_intent', 'customer', 'checkout_intent', 'checkout_session',
                                   'paid_access', 'subscription_status', 'same_account_event',
                                   'orphan_checkout_intent', 'orphan_stripe_event'])
def test_every_billing_startup_legacy_category_is_inventoried_with_uncertainty(tmp_path, legacy):
    vault, keys = fixture(tmp_path)
    with vault.transaction() as db:
        if legacy == 'orphan_checkout_intent':
            vault.put(db, 'saas-checkout-intent', 'attempt', {'user_id': 'missing', 'deferred': {}}, expires=1)
        elif legacy == 'orphan_stripe_event':
            vault.put(db, 'saas-stripe-event', 'acct:test:evt', {'data': {'object': {'email': 'private@example.test'}}}, expires=1)
        elif legacy in {'paid_access', 'subscription_status'}:
            db.execute('INSERT INTO saas_entitlements(user_id,subscription_status,paid_until) VALUES(?,?,?)',
                       ('missing', 'active' if legacy == 'subscription_status' else '', 9 if legacy == 'paid_access' else 0))
        elif legacy == 'same_account_event':
            db.execute("INSERT INTO saas_billing_events(account_id,mode,event_id,body_hash,type,received) VALUES('acct','test','evt','hash','customer.updated',1)")
        else:
            db.execute("INSERT INTO saas_billing(user_id,customer_key,customer_started,customer_id,attempt,checkout_id) VALUES('missing','key',1,?,?,?)",
                       (None if legacy == 'customer_intent' else 'cus_original',
                        'attempt' if legacy.startswith('checkout') else None,
                        'cs_original' if legacy == 'checkout_session' else None))
    report = api().inspect_database(vault.path, keys)
    assert report['billing']['legacy_guard_present'] is True
    assert sum(len(report['billing'][name]) for name in ('rows', 'events', 'intents', 'event_payloads', 'paid_state')) >= 1
    assert report['enrollment_authorized'] is False
    if legacy == 'orphan_checkout_intent':
        assert report['billing']['intents'][0]['orphan'] is True
        assert report['billing']['intents'][0]['outcome'] == 'uncertain'
    if legacy == 'orphan_stripe_event':
        assert report['billing']['event_payloads'][0]['orphan'] is True
    assert 'private@example.test' not in json.dumps(report)


def test_matching_marker_does_not_establish_enrollment_and_stale_indexes_are_visible(tmp_path):
    vault, keys = fixture(tmp_path)
    with vault.transaction() as db:
        vault.put(db, 'saas-billing-environment', 'active', {'account_id': 'acct', 'mode': 'test'}, expires=253402300799)
        db.execute("INSERT INTO saas_record_owners VALUES('stale','saas-billing-environment','active')")
    report = api().inspect_database(vault.path, keys)
    assert report['marker'] == {'state': 'present', 'index_rows': 1}
    assert report['enrollment_authorized'] is False


def test_pending_work_and_access_bounds_are_covered_without_converting_access(tmp_path):
    vault, keys = fixture(tmp_path)
    with vault.transaction() as db:
        db.execute("INSERT INTO saas_users VALUES('owner',1,0,1,4)")
        db.execute("INSERT INTO saas_entitlements VALUES('owner',1,99,'active',88)")
        db.execute("INSERT INTO saas_deletions(user_id,requested,lease,lease_until) VALUES('owner',5,'lease',50)")
        db.execute("INSERT INTO saas_google_revocations(id,user_id,requested,state) VALUES('revoke','owner',5,'uncertain')")
        db.execute("INSERT INTO saas_reminders(id,user_id,trial_end,milestone,state) VALUES('remind','owner',99,1,'uncertain')")
    before = frozen(vault.path)
    report = api().inspect_database(vault.path, keys)
    assert report['coverage']['deletions']['rows'] == 1
    assert report['coverage']['deletions']['leased'] == 1
    assert report['coverage']['revocations']['states']['uncertain'] == 1
    assert report['coverage']['reminders']['states']['uncertain'] == 1
    assert report['access']['bounds'][0] == {'user_id': 'owner', 'trial_start': 1.0,
        'trial_end': 99.0, 'subscription_status': 'active', 'paid_until': 88.0}
    assert report['access']['owner_reviewer_policy'] == 'NOT_ESTABLISHED'
    assert frozen(vault.path) == before


def test_live_wal_reads_committed_snapshot_including_all_busy_tables(tmp_path, monkeypatch):
    vault, keys = fixture(tmp_path, wal=True)
    writer = sqlite3.connect(vault.path)
    try:
        writer.execute("INSERT INTO rates VALUES('committed',1,2)"); writer.commit()
        writer.execute("INSERT INTO rates VALUES('uncommitted',1,3)")
        before = api().inspect_database(vault.path, keys)
        assert before['tables']['rates']['rows'] == 1
        writer.rollback()
        inventory = api()
        original = inventory._logical_state
        def concurrent(db, schema):
            # The reader has already pinned its snapshot by reading schema.
            writer.execute("INSERT INTO rates VALUES('later',1,4)"); writer.commit()
            return original(db, schema)
        monkeypatch.setattr(inventory, '_logical_state', concurrent)
        during = inventory.inspect_database(vault.path, keys)
        assert during['state_root'] == before['state_root']
        monkeypatch.setattr(inventory, '_logical_state', original)
        assert inventory.inspect_database(vault.path, keys)['state_root'] != before['state_root']
    finally:
        writer.rollback(); writer.close()


def test_wal_without_sidecars_fails_closed_without_creating_them(tmp_path):
    vault, keys = fixture(tmp_path, wal=True)
    before = frozen(vault.path)
    with pytest.raises(api().InventoryError):
        api().inspect_database(vault.path, keys)
    assert frozen(vault.path) == before


def test_path_replacement_is_rejected_after_snapshot(tmp_path, monkeypatch):
    vault, keys = fixture(tmp_path)
    inventory = api()
    original = inventory._logical_state
    def replace(db, schema):
        report = original(db, schema)
        other = vault.path.with_name('replacement')
        other.write_bytes(vault.path.read_bytes()); other.chmod(0o600)
        os.replace(other, vault.path)
        return report
    monkeypatch.setattr(inventory, '_logical_state', replace)
    with pytest.raises(inventory.InventoryError):
        inventory.inspect_database(vault.path, keys)


def test_private_new_artifact_outside_git_and_no_clobber(tmp_path):
    vault, keys = fixture(tmp_path)
    destination = tmp_path / 'evidence' / 'inventory.json'
    destination.parent.mkdir(mode=0o700)
    summary = api().write_inventory(vault.path, keys, destination)
    assert set(summary) == {'status', 'tables', 'rows', 'records', 'state_root'}
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert json.loads(destination.read_text())['state_root'] == summary['state_root']
    before = destination.read_bytes()
    with pytest.raises(api().InventoryError):
        api().write_inventory(vault.path, keys, destination)
    assert destination.read_bytes() == before
    destination.parent.chmod(0o750)
    with pytest.raises(api().InventoryError):
        api().write_inventory(vault.path, keys, destination.with_name('unsafe'))
    repository = tmp_path / 'repository'; repository.mkdir(mode=0o700)
    (repository / '.git').mkdir()
    with pytest.raises(api().InventoryError):
        api().write_inventory(vault.path, keys, repository / 'forbidden')


def test_cli_uses_private_environment_and_fixed_sanitized_diagnostics(tmp_path):
    vault, keys = fixture(tmp_path)
    destination = tmp_path / 'evidence' / 'report.json'; destination.parent.mkdir(mode=0o700)
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'inspect_legacy_billing.py'
    env = dict(os.environ, SOCIALCLI_BILLING_INVENTORY_DATABASE=str(vault.path),
               SOCIALCLI_BILLING_INVENTORY_DESTINATION=str(destination),
               SOCIALCLI_ENCRYPTION_KEYS=keys[0].decode())
    result = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['status'] == 'inventory_only'
    assert str(tmp_path) not in result.stdout + result.stderr
    assert keys[0].decode() not in result.stdout + result.stderr
    assert result.stderr == ''
    env['SOCIALCLI_ENCRYPTION_KEYS'] = 'private-invalid-key'
    result = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert result.stdout == '{"status": "failed", "error": "Inventory inspection failed."}\n'
    assert result.stderr == ''


def test_cli_private_key_file_and_no_raw_argument_echo(tmp_path):
    vault, keys = fixture(tmp_path)
    key_file = vault.path.parent / 'runtime-keys'; key_file.write_bytes(keys[0]); key_file.chmod(0o600)
    destination = tmp_path / 'evidence' / 'report.json'; destination.parent.mkdir(mode=0o700)
    env = dict(os.environ, SOCIALCLI_BILLING_INVENTORY_DATABASE=str(vault.path),
               SOCIALCLI_BILLING_INVENTORY_DESTINATION=str(destination),
               SOCIALCLI_ENCRYPTION_KEYS_FILE=str(key_file))
    env.pop('SOCIALCLI_ENCRYPTION_KEYS', None)
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'inspect_legacy_billing.py'
    result = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True)
    assert result.returncode == 0
    result = subprocess.run([sys.executable, str(script), 'private-argument'], env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert 'private-argument' not in result.stdout + result.stderr


@pytest.mark.parametrize('sensitive', ['private@example.test', 'sk_test_private', 'whsec_private'])
def test_sensitive_values_in_reference_fields_fail_closed_without_artifact(tmp_path, sensitive):
    vault, keys = fixture(tmp_path)
    with vault.transaction() as db:
        vault.put(db, 'saas-checkout-intent', 'attempt', {'user_id': sensitive, 'deferred': {}}, expires=1)
    destination = tmp_path / 'evidence' / 'report.json'; destination.parent.mkdir(mode=0o700)
    with pytest.raises(api().InventoryError):
        api().write_inventory(vault.path, keys, destination)
    assert list(destination.parent.iterdir()) == []


def test_failed_artifact_write_is_sanitized_even_when_cleanup_fails(tmp_path, monkeypatch):
    vault, keys = fixture(tmp_path)
    destination = tmp_path / 'evidence' / 'report.json'; destination.parent.mkdir(mode=0o700)
    inventory = api()
    def fail(*args, **kwargs):
        raise OSError('private-detail')
    monkeypatch.setattr(inventory.os, 'fsync', fail)
    monkeypatch.setattr(Path, 'unlink', fail)
    with pytest.raises(inventory.InventoryError, match=r'^Inventory inspection failed\.$'):
        inventory.write_inventory(vault.path, keys, destination)


def test_internal_pointer_and_ownership_discrepancies_remain_explicit(tmp_path):
    vault, keys = fixture(tmp_path)
    with vault.transaction() as db:
        db.execute("INSERT INTO saas_billing(user_id,customer_key,customer_started,customer_id,attempt) VALUES('sql-owner','key',1,'cus_original','missing-intent')")
        db.execute("INSERT INTO saas_billing_events(account_id,mode,event_id,body_hash,customer_id,type,received) VALUES('acct','test','evt_original','hash','cus_original','invoice.paid',1)")
        vault.put(db, 'saas-stripe-event', 'acct:test:evt_original', {
            'id': 'evt_different', 'account': 'acct_other', 'livemode': True,
            'data': {'object': {'customer': 'cus_other', 'metadata': {'user_id': 'different-owner'}}}}, expires=1)
        vault.put(db, 'saas-checkout-intent', 'attempt', {'user_id': 'payload-owner', 'deferred': {}}, expires=1)
        db.execute("UPDATE saas_record_owners SET user_id='index-owner' WHERE kind='saas-checkout-intent'")
    report = api().inspect_database(vault.path, keys)
    assert report['billing']['rows'][0]['checkout_intent_missing'] is True
    assert report['billing']['intents'][0]['ownership_discrepancy'] is True
    assert set(report['billing']['event_payloads'][0]['local_discrepancies']) == {
        'event_id', 'account_id', 'mode', 'customer_id', 'owner'}
    assert report['billing']['event_payloads'][0]['outcome'] == 'uncertain'


def test_script_initialization_failure_has_no_traceback_or_paths():
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'inspect_legacy_billing.py'
    result = subprocess.run([sys.executable, '-S', str(script)], capture_output=True, text=True,
                            env={'PATH': os.environ['PATH']})
    assert result.returncode == 1
    assert result.stdout == '{"status": "failed", "error": "Inventory inspection failed."}\n'
    assert result.stderr == ''
