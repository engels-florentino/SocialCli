"""Constructor-free, offline schema10 billing evidence. Never enrollment authority.

Only source-defined schemas are accepted. No target SQL is executed as DDL, and
no service/store constructor, provider, migration or cleanup is used. SQLite may
update its WAL read-lock bookkeeping; database rows and schema remain read-only.
Protected operational custody is required; identity checks cannot defeat a
privileged host adversary. Keys and decrypted payloads never enter the report.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import struct
import sys

from cryptography.fernet import Fernet, MultiFernet


class InventoryError(ValueError):
    """Fixed sanitized diagnostics only."""


_ERROR = 'Inventory inspection failed.'
# Exact source-derived sqlite_master structure from Store and SaaSStore schema10.
# Deliberately static: never instantiate stores or derive a whitelist from input.
_BASE_SCHEMA = (('index',
  'saas_google_revocations_due',
  'saas_google_revocations',
  'CREATE INDEX saas_google_revocations_due ON saas_google_revocations(state,next_attempt,lease_until)'),
 ('index',
  'saas_media_allocations_owner',
  'saas_media_allocations',
  'CREATE INDEX saas_media_allocations_owner ON saas_media_allocations(user_id,expires)'),
 ('index',
  'saas_record_emails_hash',
  'saas_record_emails',
  'CREATE INDEX saas_record_emails_hash ON saas_record_emails(email_hash,kind,id)'),
 ('index',
  'saas_record_links_grant',
  'saas_record_links',
  'CREATE INDEX saas_record_links_grant ON saas_record_links(grant_id,kind,id)'),
 ('index',
  'saas_record_links_installation',
  'saas_record_links',
  'CREATE INDEX saas_record_links_installation ON saas_record_links(installation_id,kind,id)'),
 ('index',
  'saas_record_owners_user',
  'saas_record_owners',
  'CREATE INDEX saas_record_owners_user ON saas_record_owners(user_id,kind,id)'),
 ('index',
  'saas_reminders_due',
  'saas_reminders',
  'CREATE INDEX saas_reminders_due ON saas_reminders(state,next_attempt,trial_end)'),
 ('index', 'sqlite_autoindex_rates_1', 'rates', None),
 ('index', 'sqlite_autoindex_records_1', 'records', None),
 ('index', 'sqlite_autoindex_saas_billing_1', 'saas_billing', None),
 ('index', 'sqlite_autoindex_saas_billing_2', 'saas_billing', None),
 ('index', 'sqlite_autoindex_saas_billing_3', 'saas_billing', None),
 ('index', 'sqlite_autoindex_saas_billing_events_1', 'saas_billing_events', None),
 ('index', 'sqlite_autoindex_saas_brands_1', 'saas_brands', None),
 ('index', 'sqlite_autoindex_saas_brands_2', 'saas_brands', None),
 ('index', 'sqlite_autoindex_saas_deletion_tombstones_1', 'saas_deletion_tombstones', None),
 ('index', 'sqlite_autoindex_saas_deletions_1', 'saas_deletions', None),
 ('index', 'sqlite_autoindex_saas_entitlements_1', 'saas_entitlements', None),
 ('index', 'sqlite_autoindex_saas_google_revocations_1', 'saas_google_revocations', None),
 ('index', 'sqlite_autoindex_saas_media_allocations_1', 'saas_media_allocations', None),
 ('index', 'sqlite_autoindex_saas_rates_1', 'saas_rates', None),
 ('index', 'sqlite_autoindex_saas_record_emails_1', 'saas_record_emails', None),
 ('index', 'sqlite_autoindex_saas_record_links_1', 'saas_record_links', None),
 ('index', 'sqlite_autoindex_saas_record_owners_1', 'saas_record_owners', None),
 ('index', 'sqlite_autoindex_saas_reminders_1', 'saas_reminders', None),
 ('index', 'sqlite_autoindex_saas_reminders_2', 'saas_reminders', None),
 ('index', 'sqlite_autoindex_saas_slots_1', 'saas_slots', None),
 ('index', 'sqlite_autoindex_saas_slots_2', 'saas_slots', None),
 ('index', 'sqlite_autoindex_saas_trial_markers_1', 'saas_trial_markers', None),
 ('index', 'sqlite_autoindex_saas_users_1', 'saas_users', None),
 ('table',
  'rates',
  'rates',
  'CREATE TABLE rates (identity TEXT PRIMARY KEY, bucket INTEGER NOT NULL, count INTEGER NOT NULL)'),
 ('table',
  'records',
  'records',
  'CREATE TABLE records (kind TEXT NOT NULL, id TEXT NOT NULL, expires REAL NOT NULL, credential_hash TEXT NOT '
  'NULL, payload BLOB NOT NULL, PRIMARY KEY(kind,id))'),
 ('table',
  'saas_billing',
  'saas_billing',
  'CREATE TABLE saas_billing (user_id TEXT PRIMARY KEY,customer_id TEXT UNIQUE,subscription_id TEXT '
  'UNIQUE,customer_key TEXT NOT NULL,customer_started REAL NOT NULL,checkout_id TEXT,attempt TEXT,attempt_started '
  'REAL,lease TEXT,lease_until REAL NOT NULL DEFAULT 0,sync_due REAL NOT NULL DEFAULT 0,cancel_at_period_end '
  "INTEGER NOT NULL DEFAULT 0,review TEXT NOT NULL DEFAULT '')"),
 ('table',
  'saas_billing_events',
  'saas_billing_events',
  'CREATE TABLE saas_billing_events (account_id TEXT NOT NULL,mode TEXT NOT NULL,event_id TEXT NOT NULL,body_hash '
  "TEXT NOT NULL,customer_id TEXT,type TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',received REAL NOT "
  "NULL,next_attempt REAL NOT NULL DEFAULT 0, semantic_hash TEXT NOT NULL DEFAULT '',PRIMARY "
  'KEY(account_id,mode,event_id))'),
 ('table',
  'saas_brands',
  'saas_brands',
  'CREATE TABLE saas_brands (user_id TEXT PRIMARY KEY,id TEXT NOT NULL UNIQUE,label TEXT NOT NULL)'),
 ('table',
  'saas_deletion_tombstones',
  'saas_deletion_tombstones',
  'CREATE TABLE saas_deletion_tombstones (kind TEXT NOT NULL,id TEXT NOT NULL,requested REAL NOT NULL,PRIMARY '
  'KEY(kind,id))'),
 ('table',
  'saas_deletions',
  'saas_deletions',
  'CREATE TABLE saas_deletions (user_id TEXT PRIMARY KEY,requested REAL NOT NULL,state TEXT NOT NULL DEFAULT '
  "'pending',billing_done INTEGER NOT NULL DEFAULT 0,next_attempt REAL NOT NULL DEFAULT 0,lease TEXT,lease_until "
  'REAL NOT NULL DEFAULT 0)'),
 ('table',
  'saas_entitlements',
  'saas_entitlements',
  'CREATE TABLE saas_entitlements (user_id TEXT PRIMARY KEY,trial_start REAL,trial_end REAL,subscription_status '
  "TEXT NOT NULL DEFAULT '',paid_until REAL NOT NULL DEFAULT 0)"),
 ('table',
  'saas_google_revocations',
  'saas_google_revocations',
  'CREATE TABLE saas_google_revocations (id TEXT PRIMARY KEY,user_id TEXT NOT NULL,requested REAL NOT NULL,state '
  "TEXT NOT NULL DEFAULT 'pending',next_attempt REAL NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,lease "
  'TEXT,lease_until REAL NOT NULL DEFAULT 0)'),
 ('table',
  'saas_media_allocations',
  'saas_media_allocations',
  'CREATE TABLE saas_media_allocations (id TEXT PRIMARY KEY,user_id TEXT NOT NULL,size INTEGER NOT NULL,expires '
  'REAL NOT NULL)'),
 ('table',
  'saas_rates',
  'saas_rates',
  'CREATE TABLE saas_rates (key TEXT PRIMARY KEY, bucket INTEGER NOT NULL, count INTEGER NOT NULL, expires REAL '
  'NOT NULL DEFAULT 0)'),
 ('table',
  'saas_record_emails',
  'saas_record_emails',
  'CREATE TABLE saas_record_emails (kind TEXT NOT NULL,id TEXT NOT NULL,email_hash TEXT NOT NULL,PRIMARY '
  'KEY(kind,id,email_hash))'),
 ('table',
  'saas_record_links',
  'saas_record_links',
  'CREATE TABLE saas_record_links (kind TEXT NOT NULL,id TEXT NOT NULL,installation_id TEXT,grant_id TEXT,PRIMARY '
  'KEY(kind,id))'),
 ('table',
  'saas_record_owners',
  'saas_record_owners',
  'CREATE TABLE saas_record_owners (user_id TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL, PRIMARY '
  'KEY(kind,id))'),
 ('table',
  'saas_reminders',
  'saas_reminders',
  'CREATE TABLE saas_reminders (id TEXT PRIMARY KEY,user_id TEXT NOT NULL,trial_end REAL NOT NULL,milestone '
  "INTEGER NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,state TEXT NOT NULL DEFAULT 'pending',next_attempt REAL "
  'NOT NULL DEFAULT 0,lease TEXT,lease_until REAL NOT NULL DEFAULT 0,UNIQUE(user_id,trial_end,milestone))'),
 ('table',
  'saas_schema',
  'saas_schema',
  'CREATE TABLE saas_schema (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)'),
 ('table',
  'saas_slots',
  'saas_slots',
  'CREATE TABLE saas_slots (user_id TEXT NOT NULL,platform TEXT NOT NULL,grant_id TEXT NOT NULL UNIQUE,PRIMARY '
  'KEY(user_id,platform))'),
 ('table',
  'saas_trial_markers',
  'saas_trial_markers',
  'CREATE TABLE saas_trial_markers (marker TEXT PRIMARY KEY,trial_start REAL NOT NULL,trial_end REAL NOT '
  'NULL,retain_until REAL NOT NULL)'),
 ('table',
  'saas_users',
  'saas_users',
  'CREATE TABLE saas_users (id TEXT PRIMARY KEY, created REAL NOT NULL, suspended INTEGER NOT NULL DEFAULT 0, '
  'deleting INTEGER NOT NULL DEFAULT 0, session_version INTEGER NOT NULL DEFAULT 0)'),
 ('trigger',
  'records_links_delete',
  'records',
  'CREATE TRIGGER records_links_delete AFTER DELETE ON records BEGIN DELETE FROM saas_record_links WHERE '
  'kind=OLD.kind AND id=OLD.id; DELETE FROM saas_record_emails WHERE kind=OLD.kind AND id=OLD.id; END'),
 ('trigger',
  'records_meta_webhook_grant_delete',
  'records',
  'CREATE TRIGGER records_meta_webhook_grant_delete AFTER DELETE ON records\n'
  "                WHEN OLD.kind='saas-grant' BEGIN\n"
  "                DELETE FROM records WHERE kind IN ('saas-meta-webhook-binding','saas-meta-webhook-event')\n"
  '                AND (kind,id) IN (SELECT kind,id FROM saas_record_links WHERE grant_id=OLD.id); END'),
 ('trigger',
  'records_owner_delete',
  'records',
  'CREATE TRIGGER records_owner_delete AFTER DELETE ON records BEGIN DELETE FROM saas_record_owners WHERE '
  'kind=OLD.kind AND id=OLD.id; END'))

# Optional source-defined structures: LegacyPolicy and restore_guard respectively.
_LEGACY_SCHEMA = (
    ('table', 'legacy_cutoff', 'legacy_cutoff', 'CREATE TABLE legacy_cutoff (singleton INTEGER PRIMARY KEY CHECK(singleton=1),closed_at REAL NOT NULL,drain_until REAL NOT NULL)'),
    ('table', 'legacy_allowance', 'legacy_allowance', 'CREATE TABLE legacy_allowance (kind TEXT NOT NULL,id TEXT NOT NULL,expires REAL NOT NULL,activated INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(kind,id))'),
    ('index', 'sqlite_autoindex_legacy_allowance_1', 'legacy_allowance', None),
)
_RESTORE_SCHEMA = ('table', 'saas_restore_guard', 'saas_restore_guard', 'CREATE TABLE saas_restore_guard (singleton INTEGER PRIMARY KEY CHECK(singleton=1),state TEXT NOT NULL,prepared REAL NOT NULL,snapshot_created REAL NOT NULL)')
_KNOWN_KINDS = frozenset({
    'authorization', 'connection', 'google-revocation', 'saas-billing-environment',
    'saas-checkout-intent', 'saas-stripe-event', 'saas-user', 'saas-login',
    'saas-session', 'saas-pair', 'saas-installation', 'saas-capability',
    'saas-candidate', 'saas-grant', 'saas-consent', 'saas-delete',
    'saas-meta-operation', 'saas-meta-target', 'saas-meta-webhook-binding',
    'saas-meta-webhook-event', 'saas-migration', 'saas-privacy', 'saas-rotation',
})
_STATES = frozenset({'pending', 'done', 'ignored', 'failed', 'uncertain', 'sending',
                     'sent', 'rejected', 'review', 'processing', 'canceled'})
_SUBSCRIPTIONS = frozenset({'', 'active', 'canceled', 'trialing', 'past_due',
                            'unpaid', 'incomplete', 'incomplete_expired', 'paused'})


def _pack(value: bytes) -> bytes:
    return struct.pack('>Q', len(value)) + value


def _typed(value) -> bytes:
    """Lossless SQL value encoding; integer/real, signed zero, text/blob differ."""
    if value is None:
        return b'n'
    if type(value) is int:
        return b'i' + _pack(str(value).encode('ascii'))
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError()
        return b'r' + struct.pack('>d', value)
    if type(value) is str:
        return b't' + _pack(value.encode('utf-8'))
    if type(value) is bytes:
        return b'b' + _pack(value)
    raise ValueError()


def _row(row) -> bytes:
    return _pack(b''.join(_pack(_typed(value)) for value in row))


def _rows_digest(rows) -> str:
    digest = hashlib.sha256(b'socialcli-inventory-rows-v1\0')
    # Sorting retains duplicates, unlike a map keyed by nullable SQL primary keys.
    for encoded in sorted(_row(row) for row in rows):
        digest.update(encoded)
    return digest.hexdigest()


def _sql_shape(sql):
    if sql is None:
        return None
    # Ignore formatting only outside quoted strings. String literal changes are
    # schema changes, even when they differ only by whitespace.
    pieces = re.split(r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\")", sql)
    return ''.join(piece if i % 2 else ''.join(piece.split()) for i, piece in enumerate(pieces))


def _private_path(path) -> Path:
    path = Path(path)
    text = str(path)
    if (not path.is_absolute() or '..' in path.parts or any(c in text for c in '?#')
            or any(ord(c) < 32 for c in text)):
        raise ValueError()
    # Do not resolve symlinks into apparently safe paths. Parent directories need
    # not be secret, but untrusted writers cannot control any non-sticky ancestor.
    for parent in reversed(path.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError()
        writable = info.st_mode & 0o022
        trusted_sticky = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if writable and not trusted_sticky:
            raise ValueError()
    parent = path.parent.stat()
    if (parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) & 0o077
            or stat.S_IMODE(parent.st_mode) & 0o700 != 0o700):
        raise ValueError()
    return path


def _identity(info) -> dict:
    return {'device': info.st_dev, 'inode': info.st_ino, 'owner': info.st_uid,
            'group': info.st_gid, 'mode': stat.S_IMODE(info.st_mode), 'links': info.st_nlink}


def _file_info(path):
    _private_path(path)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid() or info.st_nlink != 1):
        raise ValueError()
    return info


def _assert_identity(path, expected, fd):
    if _identity(_file_info(path)) != expected or _identity(os.fstat(fd)) != expected:
        raise ValueError()


def _schema(db):
    schema = [tuple(row) for row in db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
    expected = list(_BASE_SCHEMA)
    names = {row[1] for row in schema}
    if names & {'legacy_cutoff', 'legacy_allowance'}:
        expected.extend(_LEGACY_SCHEMA)
    if 'saas_restore_guard' in names:
        expected.append(_RESTORE_SCHEMA)
    def shape(rows):
        return sorted((kind, name, table, _sql_shape(sql)) for kind, name, table, sql in rows)
    if shape(schema) != shape(expected):
        raise ValueError()
    if (db.execute('PRAGMA user_version').fetchone()[0] != 0
            or db.execute('PRAGMA application_id').fetchone()[0] != 0
            or db.execute('SELECT version FROM saas_schema ORDER BY version').fetchall()
                != [(i,) for i in range(1, 11)]):
        raise ValueError()
    if 'saas_restore_guard' in names:
        guard = db.execute('SELECT singleton,state,prepared,snapshot_created FROM saas_restore_guard').fetchall()
        if len(guard) != 1 or guard[0][0] != 1 or guard[0][1] != 'ready':
            raise ValueError()
    if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise ValueError()
    return schema


def _logical_state(db, schema):
    """Pin every table, column, duplicate and ciphertext in this transaction."""
    tables = {}
    schema_parts = list(schema)
    for name in sorted(row[1] for row in schema if row[0] == 'table'):
        columns = sorted(tuple(row) for row in db.execute(f'PRAGMA table_xinfo("{name}")'))
        # The source allowlist fixes names before any interpolated identifier.
        names = sorted(row[1] for row in columns)
        selection = ','.join('"'+name+'"' for name in names)
        rows = db.execute(f'SELECT {selection} FROM "{name}"').fetchall()
        tables[name] = {'columns': names, 'rows': len(rows), 'digest': _rows_digest(rows)}
        schema_parts.extend(('column', name, *column) for column in columns)
        for index in db.execute(f'PRAGMA index_list("{name}")').fetchall():
            schema_parts.append(('index_metadata', name, *index))
            schema_parts.extend(('index_column', index[1], *tuple(column))
                                for column in db.execute(f'PRAGMA index_xinfo("{index[1]}")'))
    schema_parts.append(('encoding', db.execute('PRAGMA encoding').fetchone()[0]))
    schema_root = _rows_digest(schema_parts)
    data_root = _rows_digest((name, json.dumps(value['columns'], separators=(',', ':')),
                             value['rows'], value['digest']) for name, value in tables.items())
    return tables, schema_root, data_root


def _authenticate(db, keys):
    cipher = MultiFernet([Fernet(key) for key in keys])
    records = {}
    for kind, record_id, expires, credential_hash, ciphertext in db.execute('SELECT kind,id,expires,credential_hash,payload FROM records ORDER BY kind,id'):
        envelope = json.loads(cipher.decrypt(ciphertext))
        if (not isinstance(envelope, dict) or envelope['kind'] != kind or envelope['id'] != record_id
                or envelope['expires'] != expires or envelope['credential_hash'] != credential_hash
                or not isinstance(envelope['payload'], dict)):
            raise ValueError()
        records[(kind, record_id)] = envelope['payload']
    return records


def _dict_rows(db, table):
    cursor = db.execute('SELECT * FROM '+table)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor]


def _state(value, allowed=_STATES):
    return value if value in allowed else 'unknown'


def _jobs(db, table):
    rows = _dict_rows(db, table)
    counts = {}
    for row in rows:
        state = _state(row['state'])
        counts[state] = counts.get(state, 0) + 1
    return {'rows': len(rows), 'states': dict(sorted(counts.items())),
            'leased': sum(bool(row['lease']) or row['lease_until'] != 0 for row in rows)}


def _check_references(value):
    """Reject unexpected identities rather than reproduce sensitive strings.

    Billing and vault identifiers are opaque source IDs, never addresses, keys,
    URLs or free-form diagnostics. Raw payload fields are otherwise omitted.
    """
    references = {'user_id', 'account_id', 'customer_id', 'subscription_id',
                  'checkout_id', 'attempt', 'kind', 'id'}
    if isinstance(value, dict):
        for key, item in value.items():
            if key in references and item is not None:
                if (not isinstance(item, str) or not re.fullmatch(r'[A-Za-z0-9_:-]{1,512}', item)
                        or item.startswith(('sk_', 'rk_', 'whsec_'))):
                    raise ValueError()
            _check_references(item)
    elif isinstance(value, list):
        for item in value:
            _check_references(item)


def _inventory(db, records, tables):
    users = {row[0] for row in db.execute('SELECT id FROM saas_users')}
    billing = _dict_rows(db, 'saas_billing')
    events = _dict_rows(db, 'saas_billing_events')
    entitlements = _dict_rows(db, 'saas_entitlements')
    owners = {(kind, record_id): owner for owner, kind, record_id in db.execute('SELECT user_id,kind,id FROM saas_record_owners')}
    attempts = {row['attempt']: row for row in billing if row['attempt']}
    event_keys = {':'.join((row['account_id'], row['mode'], row['event_id'])): row for row in events}
    rows = []
    for row in sorted(billing, key=lambda r: r['user_id'] or ''):
        rows.append({
            'user_id': row['user_id'], 'customer_id': row['customer_id'],
            'subscription_id': row['subscription_id'], 'checkout_id': row['checkout_id'],
            'attempt': row['attempt'], 'customer_key_present': bool(row['customer_key']),
            'customer_started': row['customer_started'], 'attempt_started': row['attempt_started'],
            'leased': bool(row['lease']) or row['lease_until'] != 0, 'lease_until': row['lease_until'],
            'sync_due': row['sync_due'], 'cancel_at_period_end': row['cancel_at_period_end'],
            'review_present': bool(row['review']), 'orphan_user': row['user_id'] not in users,
            'checkout_intent_missing': bool(row['attempt']) and ('saas-checkout-intent', row['attempt']) not in records,
            'customer_outcome': 'pointer_present' if row['customer_id'] else 'uncertain',
            'checkout_outcome': 'pointer_present' if row['checkout_id'] else 'uncertain' if row['attempt'] else 'absent',
            'classification': 'review' if row['review'] else 'in_flight' if row['lease'] or row['lease_until'] else 'uncertain',
            'provenance': 'NOT_ESTABLISHED',
        })
    intents, payloads = [], []
    for (kind, record_id), payload in sorted(records.items()):
        if kind == 'saas-checkout-intent':
            user = payload.get('user_id')
            row = attempts.get(record_id)
            owner = owners.get((kind, record_id))
            intents.append({'kind': kind, 'id': record_id,
                'user_id': user if isinstance(user, str) else None,
                'orphan': row is None, 'orphan_user': user not in users if isinstance(user, str) else True,
                'ownership_discrepancy': bool(row and row['user_id'] != user) or bool(owner and owner != user),
                'deferred_parameters_present': isinstance(payload.get('deferred'), dict),
                'checkout_id': row['checkout_id'] if row else None,
                'outcome': 'uncertain', 'provenance': 'NOT_ESTABLISHED'})
        elif kind == 'saas-stripe-event':
            event = event_keys.get(record_id)
            discrepancies = []
            if event is not None:
                if payload.get('id') != event['event_id']:
                    discrepancies.append('event_id')
                if payload.get('account') not in (None, event['account_id']):
                    discrepancies.append('account_id')
                expected_mode = {'test': False, 'live': True}.get(event['mode'])
                if expected_mode is None or payload.get('livemode') is not expected_mode:
                    discrepancies.append('mode')
                data = payload.get('data')
                obj = data.get('object') if isinstance(data, dict) else None
                obj = obj if isinstance(obj, dict) else {}
                if obj.get('customer') != event['customer_id']:
                    discrepancies.append('customer_id')
                metadata = obj.get('metadata')
                payload_owner = metadata.get('user_id') if isinstance(metadata, dict) else None
                sql_owners = {row['user_id'] for row in billing
                              if row['customer_id'] and row['customer_id'] == event['customer_id']}
                if payload_owner is not None and sql_owners and payload_owner not in sql_owners:
                    discrepancies.append('owner')
            payloads.append({'kind': kind, 'id': record_id, 'orphan': record_id not in event_keys,
                             'local_discrepancies': discrepancies,
                             'outcome': 'uncertain', 'provenance': 'NOT_ESTABLISHED'})
    event_rows = []
    for row in sorted(events, key=lambda r: (r['account_id'], r['mode'], r['event_id'])):
        key = ':'.join((row['account_id'], row['mode'], row['event_id']))
        event_rows.append({'account_id': row['account_id'], 'mode': row['mode'], 'event_id': row['event_id'],
            'customer_id': row['customer_id'], 'state': _state(row['state']),
            'encrypted_payload_present': ('saas-stripe-event', key) in records,
            'customer_pointer_present': any(r['customer_id'] == row['customer_id'] for r in billing) if row['customer_id'] else False,
            'outcome': 'terminal' if row['state'] in {'done', 'ignored'} else 'uncertain',
            'provenance': 'NOT_ESTABLISHED'})
    bounds = [{**row, 'subscription_status': _state(row['subscription_status'], _SUBSCRIPTIONS)}
              for row in sorted(entitlements, key=lambda r: r['user_id'] or '')]
    paid = [row for row in bounds if row['paid_until'] > 0 or row['subscription_status'] != '']
    marker_indexes = sum(db.execute('SELECT count(*) FROM '+table+' WHERE kind=? AND id=?',
                         ('saas-billing-environment', 'active')).fetchone()[0]
                         for table in ('saas_record_owners', 'saas_record_links', 'saas_record_emails'))
    counts = {}
    for kind, _ in records:
        counts[kind] = counts.get(kind, 0) + 1
    # Unknown kinds are authenticated, retained in the whole-state root and
    # explicitly inventoried. They never establish authority or eligibility.
    return {
        'marker': {'state': 'present' if ('saas-billing-environment', 'active') in records else 'absent',
                   'index_rows': marker_indexes},
        'records': {'authenticated': len(records), 'kinds': dict(sorted(counts.items())),
                    'unknown': [{'kind': kind, 'id': record_id} for kind, record_id in sorted(records) if kind not in _KNOWN_KINDS]},
        'billing': {'legacy_guard_present': bool(rows or events or paid or intents or payloads),
                    'rows': rows, 'events': event_rows, 'intents': intents,
                    'event_payloads': payloads, 'paid_state': paid},
        'access': {'bounds': bounds, 'owner_reviewer_policy': 'NOT_ESTABLISHED',
                   'user_rows': len(users), 'trial_marker_rows': tables['saas_trial_markers']['rows']},
        'restore': {'state': 'ready' if 'saas_restore_guard' in tables else 'absent'},
        'coverage': {'all_tables': True, 'all_records_including_expired': True,
                     'deletions': _jobs(db, 'saas_deletions'),
                     'revocations': _jobs(db, 'saas_google_revocations'),
                     'reminders': _jobs(db, 'saas_reminders'),
                     'deletion_tombstone_rows': tables['saas_deletion_tombstones']['rows'],
                     'legacy_cutoff_rows': tables.get('legacy_cutoff', {}).get('rows', 0),
                     'owner_index_rows': tables['saas_record_owners']['rows'],
                     'link_index_rows': tables['saas_record_links']['rows'],
                     'email_index_rows': tables['saas_record_emails']['rows']},
    }


def inspect_database(database: Path, keys: list[bytes]) -> dict:
    """Read only the exact existing private file; return private inventory evidence.

    A WAL database requires existing protected WAL/SHM files so opening it cannot
    create sidecars. Committed WAL pages are included; immutable/nolock are never
    used. A concurrent writer may commit after the read snapshot is pinned, so
    this evidence is not freshness, authority, or an apply-time CAS.
    """
    try:
        database = _private_path(database)
        before = _file_info(database)
        identity = _identity(before)
        fd = os.open(database, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            _assert_identity(database, identity, fd)
            header = os.pread(fd, 100, 0)
            if len(header) < 100 or header[:16] != b'SQLite format 3\0':
                raise ValueError()
            if header[18:20] == b'\x02\x02':
                for suffix in ('-wal', '-shm'):
                    _file_info(Path(str(database)+suffix))
            elif header[18:20] != b'\x01\x01':
                raise ValueError()
            with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True, timeout=5)) as db:
                db.execute('PRAGMA query_only=ON')
                db.execute('PRAGMA trusted_schema=OFF')
                db.execute('BEGIN')
                schema = _schema(db)  # Pins the snapshot before any inventory.
                _assert_identity(database, identity, fd)
                tables, schema_root, data_root = _logical_state(db, schema)
                records = _authenticate(db, keys)
                report = _inventory(db, records, tables)
                _check_references(report)
                _assert_identity(database, identity, fd)
                db.rollback()
            _assert_identity(database, identity, fd)
            root = _rows_digest([(schema_root, data_root, json.dumps(identity, sort_keys=True))])
            return {'format': 'socialcli-billing-inventory-v1', 'status': 'inventory_only',
                    'enrollment_authorized': False, 'schema_version': 10,
                    'target': identity, 'schema_root': schema_root, 'state_root': root,
                    'tables': tables, **report,
                    'not_established': dict.fromkeys((
                        'historical_account_mode_provenance', 'provider_get_validation',
                        'owner_approval', 'reviewer_approval', 'live_freshness',
                        'current_authority', 'external_auth_authority', 'deployment_lineage'), 'NOT_ESTABLISHED')}
        finally:
            os.close(fd)
    except Exception:
        raise InventoryError(_ERROR) from None


def _destination(path):
    path = _private_path(path)
    # A linked checkout has a .git file; a regular repository has a directory.
    if any((parent / '.git').exists() or (parent / '.git').is_symlink() for parent in path.parents):
        raise ValueError()
    if path.exists() or path.is_symlink():
        raise ValueError()
    return path


def write_inventory(database: Path, keys: list[bytes], destination: Path) -> dict:
    """Create a new 0600 report in existing private storage outside Git."""
    created = False
    try:
        destination = _destination(destination)
        report = inspect_database(database, keys)
        encoded = (json.dumps(report, sort_keys=True, indent=2, allow_nan=False)+'\n').encode()
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        created = True
        with os.fdopen(fd, 'wb') as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        directory_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return {'status': 'inventory_only', 'tables': len(report['tables']),
                'rows': sum(table['rows'] for table in report['tables'].values()),
                'records': report['records']['authenticated'], 'state_root': report['state_root']}
    except Exception:
        if created:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass  # A failed partial artifact is private, never success.
        raise InventoryError(_ERROR) from None


def main():
    """Paths and runtime keys are supplied privately, never as arguments."""
    try:
        if len(sys.argv) != 1:
            raise ValueError()
        raw_keys = os.environ.get('SOCIALCLI_ENCRYPTION_KEYS', '')
        key_file = os.environ.get('SOCIALCLI_ENCRYPTION_KEYS_FILE', '')
        if bool(raw_keys) == bool(key_file):
            raise ValueError()
        if key_file:
            path = _private_path(key_file)
            info = _file_info(path)
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, 'r') as stream:
                if _identity(os.fstat(stream.fileno())) != _identity(info):
                    raise ValueError()
                raw_keys = stream.read(65537)
                if len(raw_keys) > 65536:
                    raise ValueError()
                _assert_identity(path, _identity(info), stream.fileno())
        keys = [value.strip().encode('ascii') for value in raw_keys.split(',') if value.strip()]
        summary = write_inventory(Path(os.environ['SOCIALCLI_BILLING_INVENTORY_DATABASE']), keys,
                                  Path(os.environ['SOCIALCLI_BILLING_INVENTORY_DESTINATION']))
        print(json.dumps(summary, sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({'status': 'failed', 'error': _ERROR}))
        return 1
