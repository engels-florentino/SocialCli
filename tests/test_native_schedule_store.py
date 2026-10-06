from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from socialctl.native_schedule.store import NativeStore
from socialctl.native_schedule.cadence import plan_slots, reservation_day
from tests.test_native_schedule_verification import make_job, make_observation, NOW, WHEN, HASH


def start_ui(store):
    store.transition('job-1', 'prepared', 'approved', approval_digest=HASH, now=NOW)
    job = store.claim('job-1', 'approved', now=NOW)
    return store.transition(job.id, 'dispatching', 'awaiting_ui', now=NOW)


def test_read_only_does_not_create(tmp_path):
    store = NativeStore(tmp_path / 'absent', brand='Histopast')
    assert store.list_jobs() == []
    assert store.get('none') is None
    assert not (tmp_path / 'absent').exists()


def test_claim_concurrent_and_crash_recovery(tmp_path):
    store = NativeStore(tmp_path, brand='Histopast')
    store.prepare(make_job(state='prepared'), payload={'caption': 'approved text'}, now=NOW)
    store.transition('job-1', 'prepared', 'approved', approval_digest=HASH, now=NOW)
    def claim(_):
        return NativeStore(tmp_path, brand='Histopast').claim('job-1', 'approved', now=NOW)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(claim, range(2)))
    assert sum(result is not None for result in results) == 1
    job = store.get('job-1')
    store.checkpoint(job.id, job.attempt_id, {'upload_id': 'upload-1'}, now=NOW)
    reopened = NativeStore(tmp_path, brand='Histopast')
    assert reopened.get_payload(job.id) == {'caption': 'approved text'}
    assert reopened.get_attempts(job.id)[0]['checkpoint'] == {'upload_id': 'upload-1'}
    assert reopened.recover_dispatching(now=NOW) == ['job-1']
    assert reopened.get(job.id).state == 'uncertain'
    assert reopened.claim(job.id, 'uncertain', now=NOW) is None
    with pytest.raises(ValueError):
        reopened.transition(job.id, 'uncertain', 'ready', now=NOW)


def test_atomic_daily_reservations_and_legacy_unique(tmp_path):
    def prepare(number):
        try:
            NativeStore(tmp_path, brand='Histopast').prepare(
                make_job(id=str(number), state='prepared'), payload={}, now=NOW)
            return True
        except sqlite3.IntegrityError:
            return False
    with ThreadPoolExecutor(2) as pool:
        assert sum(pool.map(prepare, range(2))) == 1
    store = NativeStore(tmp_path, brand='Histopast')
    store.prepare(make_job(id='legacy', legacy_entry_id='old', state='prepared',
        publish_at=WHEN+timedelta(days=1)), payload={}, now=NOW)
    with pytest.raises(sqlite3.IntegrityError):
        store.prepare(make_job(id='duplicate', legacy_entry_id='old', state='prepared',
            publish_at=WHEN+timedelta(days=2)), payload={}, now=NOW)


def test_past_cadence_brand_and_dispatch_gate(tmp_path):
    store = NativeStore(tmp_path, brand='Histopast')
    for job in [make_job(state='prepared', publish_at=WHEN+timedelta(hours=1)),
                make_job(state='prepared', brand='Other')]:
        with pytest.raises(ValueError):
            store.prepare(job, payload={}, now=NOW)
    with pytest.raises(ValueError):
        store.prepare(make_job(state='prepared'), payload={}, now=WHEN)
    store.prepare(make_job(state='prepared'), payload={}, now=NOW)
    store.transition('job-1', 'prepared', 'approved', approval_digest=HASH, now=NOW)
    assert store.claim('job-1', 'approved', now=WHEN) is None
    with pytest.raises(ValueError):
        NativeStore(tmp_path, brand='Other').list_jobs()


def test_observation_transaction_and_cannot_fake_state(tmp_path):
    store = NativeStore(tmp_path, brand='Histopast')
    store.prepare(make_job(state='prepared'), payload={}, now=NOW)
    with pytest.raises(ValueError):
        store.transition('job-1', 'prepared', 'native_scheduled', now=NOW)
    bad = make_observation(tmp_path, account_id='other')
    assert not store.record_observation(bad, now=NOW)
    assert store.get('job-1').state == 'prepared'
    assert not store.record_observation(make_observation(tmp_path), now=NOW)
    assert store.get('job-1').state == 'prepared'
    start_ui(store)
    assert store.record_observation(make_observation(tmp_path), now=NOW)
    assert store.get('job-1').state == 'native_scheduled'
    assert len(store.get_observations('job-1')) == 3


def test_stable_overflow_and_dst():
    result = plan_slots([WHEN]*3, occupied={'2026-09-22'}, now=NOW)
    assert [reservation_day(x, 'America/New_York') for x in result] == [
        '2026-09-21', '2026-09-23', '2026-09-24']
    dates = [datetime(2026,10,31,0,tzinfo=timezone.utc), datetime(2026,11,1,0,tzinfo=timezone.utc)]
    slots = plan_slots(dates, now=NOW)
    assert all(reservation_day(x, 'America/New_York') for x in slots)
    assert slots[0].hour == 13 and slots[1].hour == 14


def test_failed_claim_rolls_back_attempt_and_state(tmp_path):
    store = NativeStore(tmp_path, brand='Histopast')
    store.prepare(make_job(state='prepared'), payload={}, now=NOW)
    store.transition('job-1', 'prepared', 'approved', approval_digest=HASH, now=NOW)
    original = store._save
    def crash(db, job):
        raise RuntimeError('crash before commit')
    store._save = crash
    with pytest.raises(RuntimeError):
        store.claim('job-1', 'approved', now=NOW)
    store._save = original
    assert store.get('job-1').state == 'approved'
    assert store.get_attempts('job-1') == []
    assert store.claim('job-1', 'approved', now=NOW) is not None


def test_old_evidence_cannot_clear_later_uncertainty(tmp_path):
    store = NativeStore(tmp_path, brand='Histopast')
    store.prepare(make_job(state='prepared'), payload={}, now=NOW)
    start_ui(store)
    old = make_observation(tmp_path)
    assert store.record_observation(old, now=NOW)
    later = NOW + timedelta(minutes=2)
    store.transition('job-1', 'native_scheduled', 'uncertain', now=later)
    assert not store.record_observation(old, now=later)
    assert store.get('job-1').state == 'uncertain'
    # Historical verification remains independent of promotion freshness.
    from socialctl.native_schedule.verification import verify
    assert verify(store.get('job-1'), old, now=later)
    fresh = make_observation(tmp_path, observed_at=later + timedelta(seconds=1))
    assert store.record_observation(fresh, now=later + timedelta(seconds=1))


def test_newer_conflict_watermark_survives_reopen_and_replay(tmp_path):
    store = NativeStore(tmp_path, brand='Histopast')
    store.prepare(make_job(state='prepared'), payload={}, now=NOW)
    start_ui(store)
    old = make_observation(tmp_path)
    assert store.record_observation(old, now=NOW)
    later = NOW + timedelta(minutes=2)
    conflict = make_observation(tmp_path, observed_at=later, state='draft')
    assert not store.record_observation(conflict, now=later)
    store = NativeStore(tmp_path, brand='Histopast')
    assert store.get('job-1').state == 'uncertain'
    assert not store.record_observation(old, now=later)
    # Equal-time evidence cannot supersede a conflict either.
    assert not store.record_observation(make_observation(tmp_path, observed_at=later), now=later)
    fresh_time = later + timedelta(seconds=1)
    assert store.record_observation(make_observation(tmp_path, observed_at=fresh_time), now=fresh_time)
    assert not store.record_observation(conflict, now=fresh_time)
    assert store.get('job-1').state == 'native_scheduled'
    assert len(store.get_observations('job-1')) == 6
