"""Cross-boundary regressions: observed remote calendars remain authoritative."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from threading import Event

import pytest

from socialctl.brands import cargar_brand
from socialctl.native_schedule import migration as m
from socialctl.native_schedule.cli import proposal
from socialctl.native_schedule.models import NativeJob
from socialctl.native_schedule.store import NativeStore
from tests.test_native_schedule_migration import setup, save, NOW
from tests.test_native_schedule_verification import make_job, make_observation, HASH, NOW as STORE_NOW, WHEN


def inventory(*, when=WHEN, ref='external'):
    return dict(observed_at=STORE_NOW.isoformat(), coverage_start=STORE_NOW.isoformat(),
        coverage_end=(when+timedelta(days=30)).isoformat(),
        accounts=[dict(platform='youtube',account_id='test',complete=True)],
        objects=[dict(platform='youtube',account_id='test',remote_ref=ref,publish_at=when.isoformat())])


def migration_with_remote(tmp_path, monkeypatch):
    root,old,entries,manifest=setup(tmp_path,monkeypatch)
    manifest['inventory']['objects']=[dict(platform='facebook',account_id='page',remote_ref='existing',publish_at='2030-11-02T10:00:00-04:00')]
    save(root,manifest)
    preview=m.prepare_migration('Histopast',root=tmp_path)
    m.apply_migration(preview['digest'],brand='Histopast',root=tmp_path)
    return root,manifest,preview,NativeStore(root,brand='Histopast')


def test_migration_then_ordinary_prepare_preserves_remote_day(tmp_path,monkeypatch):
    root,manifest,preview,store=migration_with_remote(tmp_path,monkeypatch)
    assert store.reserved_days('facebook','page')=={'2030-11-02','2030-11-03','2030-11-04'}
    # A later empty inventory must not release a previously observed remote row.
    fresh=manifest['inventory'] | {'objects':[], 'observed_at':datetime.now(timezone.utc).isoformat()}
    (root/'native-calendar.json').write_text(json.dumps(fresh))
    candidate=dict(id='ordinary',platform='facebook',account_id='page',publish_at='2030-11-02T09:00:00-04:00',dispatch_after=NOW.isoformat(),route='ui',payload=manifest['candidates'][0]['payload'])
    (root/'native.json').write_text(json.dumps(candidate))
    job,payload=proposal(cargar_brand(tmp_path,'Histopast'),'native.json')
    assert job.publish_at.isoformat()=='2030-11-05T14:00:00+00:00'
    store.prepare(job,payload=payload,now=NOW,inventory=fresh)
    assert '2030-11-02' in NativeStore(root,brand='Histopast').reserved_days('facebook','page')
    # Bypassing preview still cannot persist an overlapping job.
    blocked=NativeJob.model_validate(job.model_dump() | {'id':'overlap','publish_at':datetime(2030,11,2,13,tzinfo=timezone.utc)})
    with pytest.raises(ValueError,match='external calendar'):
        store.prepare(blocked,payload=payload,now=NOW)


def test_migration_then_reschedule_rejects_external_destination(tmp_path,monkeypatch):
    root,manifest,preview,store=migration_with_remote(tmp_path,monkeypatch)
    job=next(j for j in store.list_jobs() if j.platform=='facebook')
    store.transition(job.id,'prepared','approved',approval_digest=HASH,now=NOW)
    store.claim(job.id,'approved',now=NOW)
    job=store.get(job.id)
    observation=make_observation(tmp_path,job_id=job.id,platform=job.platform,account_id=job.account_id,
        content_hash=job.content_hash,approval_digest=HASH,publish_at=job.publish_at,observed_at=NOW)
    assert store.record_observation(observation,now=NOW)
    job=store.get(job.id)
    target=NativeJob.model_validate(job.model_dump() | {'publish_at':datetime(2030,11,2,13,tzinfo=timezone.utc)})
    with pytest.raises(ValueError,match='external calendar'):
        store.save_action(job,dict(kind='reschedule',job=target.model_dump_json(),status='approved'))
    assert store.get_action(job.id) is None
    assert store.get(job.id)==job


@pytest.mark.parametrize('boundary',['reservations','job:0','commit'])
def test_external_reservations_survive_migration_crash_and_recovery(tmp_path,monkeypatch,boundary):
    root,old,entries,manifest=setup(tmp_path,monkeypatch)
    manifest['inventory']['objects']=[dict(platform='facebook',account_id='page',remote_ref='external',publish_at='2030-11-02T10:00:00-04:00')]
    save(root,manifest); preview=m.prepare_migration('Histopast',root=tmp_path)
    def fail(point):
        if point==boundary: raise RuntimeError('crash')
    monkeypatch.setattr(m,'_checkpoint',fail)
    with pytest.raises(RuntimeError): m.apply_migration(preview['digest'],brand='Histopast',root=tmp_path)
    store=NativeStore(root,brand='Histopast')
    assert '2030-11-02' in store.reserved_days('facebook','page')
    monkeypatch.setattr(m,'_checkpoint',lambda _:None)
    m.recover_migration(preview['digest'],brand='Histopast',root=tmp_path)
    assert '2030-11-02' in store.reserved_days('facebook','page')
    assert len(store.list_jobs())==3


def test_repeat_moved_known_reference_and_empty_inventory_never_release(tmp_path):
    store=NativeStore(tmp_path,brand='Histopast')
    store.prepare(make_job(state='prepared'),payload={},now=STORE_NOW)
    store.transition('job-1','prepared','approved',approval_digest=HASH,now=STORE_NOW)
    store.claim('job-1','approved',now=STORE_NOW)
    assert store.record_observation(make_observation(tmp_path),now=STORE_NOW)
    before=store.get('job-1')
    for data in [inventory(ref='remote-1'),inventory(ref='remote-1'),inventory(ref='remote-1',when=WHEN+timedelta(days=1)),inventory() | {'objects':[]}]:
        store.record_inventory(data)
    assert store.reserved_days('youtube','test')=={'2026-09-21','2026-09-22'}
    assert store.get('job-1')==before # Inventory is not a job observation or approval.
    assert len(store.list_jobs())==1
    with store._db() as db:
        assert db.execute('SELECT count(*) FROM external_reservations').fetchone()[0]==2
        assert db.execute('SELECT count(*) FROM calendar_inventory').fetchone()[0]==3


def test_inventory_batch_is_atomic_and_failed_admission_retains_evidence(tmp_path):
    store=NativeStore(tmp_path,brand='Histopast')
    bad=inventory();bad['objects'].append(bad['objects'][0] | {'account_id':'wrong'})
    with pytest.raises(ValueError):store.record_inventory(bad)
    assert store.reserved_days('youtube','test')==set()
    with pytest.raises(ValueError,match='external calendar'):
        store.prepare(make_job(state='prepared'),payload={},now=STORE_NOW,inventory=inventory())
    assert store.list_jobs()==[]
    assert store.reserved_days('youtube','test')=={'2026-09-21'}


@pytest.mark.parametrize('operation',['prepare','claim','reschedule','submit','ui-marker'])
def test_inventory_write_serializes_with_admission(tmp_path,monkeypatch,operation):
    store=NativeStore(tmp_path,brand='Histopast')
    day=WHEN
    if operation!='prepare':
        store.prepare(make_job(state='prepared'),payload={},now=STORE_NOW)
        if operation in {'claim','ui-marker'}:
            store.transition('job-1','prepared','approved',approval_digest=HASH,now=STORE_NOW)
        if operation=='ui-marker':
            store.claim('job-1','approved',now=STORE_NOW)
            store.transition('job-1','dispatching','awaiting_ui',now=STORE_NOW)
        if operation in {'reschedule','submit'}:
            day=WHEN+timedelta(days=1)
    job=store.get('job-1')
    target=make_job(state='prepared',publish_at=day)
    action=dict(kind='reschedule',job=target.model_dump_json(),status='approved',preview={'before':job.model_dump(mode='json')}) if job else None
    if operation=='submit':store.save_action(job,action)
    entered=Event();release=Event();contender=Event()
    original=NativeStore._record_inventory
    def held(db,data):
        original(db,data);entered.set()
        assert release.wait(5)
    monkeypatch.setattr(NativeStore,'_record_inventory',staticmethod(held))
    def admit():
        contender.set()
        with pytest.raises(ValueError,match='external calendar'):
            if operation=='prepare':store.prepare(target,payload={},now=STORE_NOW)
            elif operation=='claim':store.claim(job.id,'approved',now=STORE_NOW)
            elif operation=='reschedule':store.save_action(job,action)
            elif operation=='submit':store.submit_action(job.id,now=STORE_NOW)
            else:store.checkpoint(job.id,job.attempt_id,{'ui_submit_started_at':STORE_NOW.isoformat()},now=STORE_NOW)
    with ThreadPoolExecutor(2) as pool:
        writer=pool.submit(store.record_inventory,inventory(when=day))
        assert entered.wait(5)
        reader=pool.submit(admit);assert contender.wait(5)
        assert not reader.done()
        release.set();writer.result();reader.result()
    assert store.get('job-1')==job
    if operation=='submit':assert store.get_action(job.id)['status']=='approved'
    if operation=='ui-marker':assert not store.get_attempts(job.id)[0]['checkpoint']


@pytest.mark.parametrize('ref,confirmed',[('remote-1',True),('other-content',False)])
def test_import_during_pending_reschedule_requires_consistent_readback(tmp_path,ref,confirmed):
    store=NativeStore(tmp_path,brand='Histopast')
    store.prepare(make_job(state='prepared'),payload={},now=STORE_NOW)
    store.transition('job-1','prepared','approved',approval_digest=HASH,now=STORE_NOW)
    store.claim('job-1','approved',now=STORE_NOW)
    assert store.record_observation(make_observation(tmp_path),now=STORE_NOW)
    job=store.get('job-1')
    target=NativeJob.model_validate(job.model_dump() | {'publish_at':WHEN+timedelta(days=1)})
    action=dict(kind='reschedule',job=target.model_dump_json(),status='approved',preview={'before':job.model_dump(mode='json')})
    store.save_action(job,action)
    store.submit_action(job.id,now=STORE_NOW)
    store.record_inventory(inventory(when=target.publish_at,ref=ref))
    observed=STORE_NOW+timedelta(seconds=1)
    assert store.confirm_action(make_observation(tmp_path,publish_at=target.publish_at,observed_at=observed),now=observed) is confirmed
    assert store.get(job.id).state==('native_scheduled' if confirmed else 'uncertain')
    assert store.get_action(job.id)['status']==('confirmed' if confirmed else 'submitted')
    if not confirmed:
        assert store.reserved_days('youtube','test')=={'2026-09-21','2026-09-22'}
        with store._db() as db:
            assert db.execute('SELECT count(*) FROM holds').fetchone()[0]==1
            assert db.execute('SELECT verified FROM observations ORDER BY id DESC LIMIT 1').fetchone()[0]==0


@pytest.mark.parametrize('ref,confirmed',[('remote-1',True),('other-content',False)])
def test_inventory_after_claim_cannot_confirm_conflicting_calendar(tmp_path,ref,confirmed):
    store=NativeStore(tmp_path,brand='Histopast')
    store.prepare(make_job(state='prepared'),payload={},now=STORE_NOW)
    store.transition('job-1','prepared','approved',approval_digest=HASH,now=STORE_NOW)
    store.claim('job-1','approved',now=STORE_NOW)
    store.record_inventory(inventory(ref=ref))
    assert store.record_observation(make_observation(tmp_path),now=STORE_NOW) is confirmed
    assert store.get('job-1').state==('native_scheduled' if confirmed else 'uncertain')


def test_evidence_write_failure_rolls_back_batch_and_exposes_no_migrated_jobs(tmp_path,monkeypatch):
    root,old,entries,manifest=setup(tmp_path,monkeypatch)
    manifest['inventory']['objects']=[dict(platform='facebook',account_id='page',remote_ref='external',publish_at='2030-11-02T10:00:00-04:00')]
    save(root,manifest);preview=m.prepare_migration('Histopast',root=tmp_path)
    original=NativeStore._record_inventory
    def crash(db,data):
        original(db,data)
        raise OSError('disk failure before commit')
    monkeypatch.setattr(NativeStore,'_record_inventory',staticmethod(crash))
    with pytest.raises(OSError):m.apply_migration(preview['digest'],brand='Histopast',root=tmp_path)
    # Database transaction is absent/rolled back; recovery retains durable intent.
    import sqlite3
    with sqlite3.connect(root/'native-schedules.sqlite3') as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='jobs'").fetchone()
    assert (old.root/'legacy-tombstones.json').exists()
    monkeypatch.setattr(NativeStore,'_record_inventory',staticmethod(original))
    m.recover_migration(preview['digest'],brand='Histopast',root=tmp_path)
    store=NativeStore(root,brand='Histopast')
    assert '2030-11-02' in store.reserved_days('facebook','page')
    assert len(store.list_jobs())==3


def test_later_migration_empty_inventory_keeps_previous_calendar_reservations(tmp_path,monkeypatch):
    root,old,entries,manifest=setup(tmp_path,monkeypatch)
    candidates=manifest['candidates']
    manifest['candidates']=candidates[:1]
    manifest['inventory']['objects']=[dict(platform='facebook',account_id='page',remote_ref='external',publish_at='2030-11-02T10:00:00-04:00')]
    save(root,manifest);first=m.prepare_migration('Histopast',root=tmp_path)
    m.apply_migration(first['digest'],brand='Histopast',root=tmp_path)
    manifest['candidates']=candidates[1:];manifest['inventory']['objects']=[]
    save(root,manifest);second=m.prepare_migration('Histopast',root=tmp_path)
    facebook=next(row for row in second['transfers'] if row['job']['platform']=='facebook')
    assert facebook['job']['publish_at']=='2030-11-04T14:00:00Z'
    m.apply_migration(second['digest'],brand='Histopast',root=tmp_path)
    assert '2030-11-02' in NativeStore(root,brand='Histopast').reserved_days('facebook','page')


def test_committed_recovery_backfills_all_historical_inventory_batches(tmp_path,monkeypatch):
    root,old,entries,manifest=setup(tmp_path,monkeypatch)
    candidates=manifest['candidates']
    manifest['candidates']=candidates[:1]
    manifest['inventory']['objects']=[dict(platform='facebook',account_id='page',remote_ref='external',publish_at='2030-11-02T10:00:00-04:00')]
    save(root,manifest);first=m.prepare_migration('Histopast',root=tmp_path)
    m.apply_migration(first['digest'],brand='Histopast',root=tmp_path)
    manifest['candidates']=candidates[1:];manifest['inventory']['objects']=[]
    save(root,manifest);second=m.prepare_migration('Histopast',root=tmp_path)
    m.apply_migration(second['digest'],brand='Histopast',root=tmp_path)
    store=NativeStore(root,brand='Histopast')
    original_jobs=store.list_jobs()
    # Simulate a previously committed ledger from before reservation support.
    with store._db(write=True) as db:
        db.execute('PRAGMA user_version=1')
        db.execute('DROP TABLE external_reservations')
        db.execute('DROP TABLE calendar_inventory')
    m.recover_migration(second['digest'],brand='Histopast',root=tmp_path)
    assert '2030-11-02' in store.reserved_days('facebook','page')
    assert store.list_jobs()==original_jobs


def test_old_ledger_readonly_then_schema_upgrade_blocks_old_writers(tmp_path):
    store=NativeStore(tmp_path,brand='Histopast')
    store.prepare(make_job(state='prepared'),payload={},now=STORE_NOW)
    with store._db(write=True) as db:
        db.execute('PRAGMA user_version=1')
        db.execute('DROP TABLE external_reservations')
        db.execute('DROP TABLE calendar_inventory')
    before=store.path.read_bytes()
    assert store.reserved_days('youtube','test')=={'2026-09-21'}
    assert store.path.read_bytes()==before
    store.record_inventory(inventory(when=WHEN+timedelta(days=1)))
    with store._db() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==2
    assert store.reserved_days('youtube','test')=={'2026-09-21','2026-09-22'}


def test_missing_version_two_reservation_table_fails_closed(tmp_path):
    store=NativeStore(tmp_path,brand='Histopast')
    store.record_inventory(inventory())
    with store._db(write=True) as db:
        db.execute('DROP TABLE external_reservations')
    with pytest.raises(ValueError,match='reservation ledger missing'):
        store.reserved_days('youtube','test')
    with pytest.raises(ValueError,match='reservation ledger missing'):
        store.prepare(make_job(state='prepared'),payload={},now=STORE_NOW)
