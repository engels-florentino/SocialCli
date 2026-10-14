"""Customer-selected instants must coexist without weakening legacy reservations."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from socialctl.native_schedule.models import NativeJob
from socialctl.native_schedule.store import NativeStore
from socialctl.native_schedule.approval import approval_document, hash_value, native_digest

NOW = datetime(2034, 12, 31, 12, tzinfo=timezone.utc)
WHEN = datetime(2035, 1, 3, 18, 30, tzinfo=timezone.utc)


def job(*, id='first', when=WHEN, mode='exact', zone='Europe/Madrid', account='page'):
    return NativeJob(id=id, brand='Example', platform='facebook', account_id=account,
        publish_at=when, dispatch_after=NOW, timezone_name=zone, schedule_mode=mode,
        media_hash='a'*64, content_hash='b'*64, route='ui', created_at=NOW, updated_at=NOW)


def inventory(when=WHEN, ref='external'):
    return {'observed_at': NOW.isoformat(), 'coverage_start':'2035-01-01T00:00:00Z',
        'coverage_end':'2035-01-08T00:00:00Z',
        'accounts':[{'platform':'facebook','account_id':'page','complete':True}],
        'objects':[{'platform':'facebook','account_id':'page','remote_ref':ref,'publish_at':when.isoformat()}]}


def test_exact_multiple_times_same_date_but_no_duplicate_instant(tmp_path):
    store = NativeStore(tmp_path, brand='Example')
    store.prepare(job(), payload={}, now=NOW)
    store.prepare(job(id='later', when=WHEN+timedelta(hours=1)), payload={}, now=NOW)
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        store.prepare(job(id='duplicate', zone='America/New_York'), payload={}, now=NOW)
    assert len(store.list_jobs()) == 2
    assert store.reserved_days('facebook','page') == {'2035-01-03'}
    assert store.get('first').publish_at == datetime(2035,1,3,18,30,tzinfo=timezone.utc)


def test_exact_same_instant_admission_is_atomic(tmp_path):
    def insert(index):
        try:
            NativeStore(tmp_path, brand='Example').prepare(
                job(id=str(index), when=WHEN.astimezone(timezone(timedelta(hours=1 if index else -5))),
                    zone='Europe/Madrid' if index else 'America/New_York'), payload={}, now=NOW)
            return True
        except (ValueError, sqlite3.IntegrityError):
            return False
    with ThreadPoolExecutor(2) as pool:
        assert sum(pool.map(insert, range(2))) == 1


@pytest.mark.parametrize('legacy_first', [True, False])
def test_legacy_day_and_exact_instant_conflict_in_either_direction(tmp_path, legacy_first):
    store = NativeStore(tmp_path, brand='Example')
    old = job(id='old', when=datetime(2035,1,3,14,tzinfo=timezone.utc), mode='legacy_daily', zone='America/New_York')
    new = job()
    first, second = (old,new) if legacy_first else (new,old)
    store.prepare(first, payload={}, now=NOW)
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        store.prepare(second, payload={}, now=NOW)
    store.prepare(job(id='other-account', account='other'), payload={}, now=NOW)
    assert len(store.list_jobs()) == 2


def test_exact_external_calendar_conflicts_only_at_same_instant_and_blocks_claim(tmp_path):
    store = NativeStore(tmp_path, brand='Example')
    store.record_inventory(inventory(WHEN-timedelta(hours=1)))
    store.prepare(job(), payload={}, now=NOW)
    store.transition('first','prepared','approved',approval_digest='c'*64,now=NOW)
    store.record_inventory(inventory())
    with pytest.raises(ValueError, match='external'):
        store.claim('first','approved',now=NOW)
    assert store.get_attempts('first') == []
    with pytest.raises(ValueError, match='external'):
        store.prepare(job(id='duplicate'),payload={},now=NOW)
    assert store.get('first').state == 'approved'


def test_schema_upgrade_retains_legacy_bytes_and_cannot_downgrade(tmp_path):
    store = NativeStore(tmp_path, brand='Example')
    old = job(id='old',when=datetime(2035,1,2,14,tzinfo=timezone.utc),mode='legacy_daily',zone='America/New_York')
    store.prepare(old,payload={},now=NOW)
    # Simulate a persisted v2 job written before schedule_mode existed.
    with sqlite3.connect(store.path) as db:
        legacy = json.loads(db.execute('SELECT data FROM jobs').fetchone()[0]);legacy.pop('schedule_mode',None)
        encoded = json.dumps(legacy)
        db.execute('UPDATE jobs SET data=?',(encoded,))
        assert db.execute('PRAGMA user_version').fetchone()[0] == 2
    before = store.path.read_bytes()
    assert store.get('old').schedule_mode == 'legacy_daily'
    assert store.path.read_bytes() == before
    store.prepare(job(),payload={},now=NOW)
    store.record_inventory(inventory(WHEN+timedelta(days=1)))
    with sqlite3.connect(store.path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 3
        assert db.execute('SELECT data FROM jobs WHERE id="old"').fetchone()[0] == encoded
    before = store.path.read_bytes()
    assert len(store.list_jobs()) == 2
    assert store.path.read_bytes() == before


def test_exact_mode_bound_in_digest_legacy_document_stays_identical(tmp_path):
    media=tmp_path/'original.mp4';media.write_bytes(b'fictional original')
    payload={'copy':'Caption','content_origin':'standalone','source_video_id':None,
        'visibility':'public','first_comment':None,'options':{'format':'reel'},
        'media':[{'path':str(media),'origin':'user'}]}
    old=job(when=datetime(2035,1,3,14,tzinfo=timezone.utc),mode='legacy_daily',zone='America/New_York')
    document=approval_document(old,payload)
    assert 'schedule_mode' not in document['intent']
    exact=NativeJob.model_validate(old.model_dump() | {'schedule_mode':'exact'})
    assert native_digest(exact,payload) != native_digest(old,payload)
    # Independent previous-version serialization: no policy field in immutable intent.
    fields=('brand','platform','account_id','legacy_entry_id','publish_at','timezone_name',
            'dispatch_after','route','media_hash','content_hash')
    assert document['intent'] == {key:old.model_dump(mode='json')[key] for key in fields}


def scheduled(tmp_path, *, mode='exact'):
    from hashlib import sha256
    from socialctl.native_schedule.approval import media_hashes
    from socialctl.native_schedule.models import NativeObservation
    from socialctl.native_schedule.store import _replace
    media=tmp_path/'clip.mp4';media.write_bytes(b'fictional original')
    payload={'copy':'Caption','content_origin':'standalone','source_video_id':None,
        'visibility':'public','first_comment':None,'options':{'format':'reel'},
        'media':[{'path':str(media),'origin':'user'}]}
    j=job(mode=mode,zone='America/New_York',when=datetime(2035,1,3,14,tzinfo=timezone.utc))
    j=_replace(j,content_hash=hash_value(payload),media_hash=hash_value(media_hashes(payload)))
    store=NativeStore(tmp_path,brand='Example');store.prepare(j,payload=payload,now=NOW)
    store.transition(j.id,'prepared','approved',approval_digest=native_digest(j,payload),now=NOW)
    j=store.claim(j.id,'approved',now=NOW)
    j=store.mark_api_submitted(j.id,j.attempt_id,'remote',now=NOW)
    evidence=tmp_path/'evidence.txt';evidence.write_bytes(b'fictional account calendar')
    obs=NativeObservation(job_id=j.id,platform=j.platform,account_id=j.account_id,
        content_hash=j.content_hash,approval_digest=j.approval_digest,remote_ref='remote',
        publish_at=j.publish_at,observed_at=NOW,source='ui',calendar_visible=True,
        state='native_scheduled',evidence_path=str(evidence),evidence_sha256=sha256(evidence.read_bytes()).hexdigest())
    assert store.record_observation(obs,now=NOW)
    if mode == 'exact':
        inv=inventory();inv['objects']=[]
        (tmp_path/'native-calendar.json').write_text(json.dumps(inv))
    return store,store.get(j.id),payload,obs


def test_exact_reschedule_holds_both_instants_until_confirmed(tmp_path):
    from socialctl.native_schedule.reconcile import action_preview, approve_action, mark_action_submit, reconcile
    from socialctl.native_schedule.models import NativeObservation
    store,j,payload,obs=scheduled(tmp_path)
    target=datetime.fromisoformat('2035-01-03T17:45:00-05:00')
    preview=action_preview(j.id,'reschedule',store=store,publish_at=target,now=NOW)
    approve_action(j.id,preview['digest'],store=store,kind='reschedule',publish_at=target,now=NOW)
    for when in (j.publish_at,target):
        with pytest.raises((ValueError,sqlite3.IntegrityError)):
            store.prepare(job(id='competing',when=when),payload={},now=NOW)
    store.prepare(job(id='middle',when=datetime(2035,1,3,18,tzinfo=timezone.utc)),payload={},now=NOW)
    mark_action_submit(j.id,store=store,now=NOW)
    target_job=NativeJob.model_validate_json(store.get_action(j.id)['job'])
    readback=NativeObservation.model_validate(obs.model_dump()|{'publish_at':target,'approval_digest':target_job.approval_digest,'observed_at':NOW+timedelta(seconds=1)})
    reconcile(j.id,store=store,observation=readback,now=NOW+timedelta(seconds=1))
    assert store.get(j.id).state=='native_scheduled'
    assert store.get(j.id).publish_at==datetime(2035,1,3,22,45,tzinfo=timezone.utc)
    store.prepare(job(id='old-slot',when=j.publish_at),payload={},now=NOW)
    assert store.get_action(j.id)['status']=='confirmed'


def test_preexisting_pending_action_without_policy_field_still_submits(tmp_path):
    from socialctl.native_schedule.reconcile import action_preview, approve_action, mark_action_submit
    store,j,_,_=scheduled(tmp_path,mode='legacy_daily')
    preview=action_preview(j.id,'cancel',store=store,now=NOW)
    approve_action(j.id,preview['digest'],store=store,kind='cancel',now=NOW)
    with sqlite3.connect(store.path) as db:
        action=json.loads(db.execute('SELECT data FROM actions').fetchone()[0])
        action['preview']['before'].pop('schedule_mode',None)
        old_target=json.loads(action['job']);old_target.pop('schedule_mode',None);action['job']=json.dumps(old_target)
        db.execute('UPDATE actions SET data=?',(json.dumps(action),))
    mark_action_submit(j.id,store=store,now=NOW)
    assert store.get_action(j.id)['status']=='submitted'


@pytest.mark.parametrize('problem',['missing','outside_coverage','stale','foreign','occupied'])
def test_exact_reschedule_preview_requires_current_complete_destination_calendar(tmp_path,problem):
    from socialctl.native_schedule.reconcile import action_preview
    store,j,_,_=scheduled(tmp_path)
    path=tmp_path/'native-calendar.json';inv=json.loads(path.read_text())
    if problem=='outside_coverage':inv['coverage_end']='2035-01-03T00:00:00Z'
    if problem=='stale':inv['observed_at']=(NOW-timedelta(days=2)).isoformat()
    if problem=='foreign':inv['accounts'][0]['account_id']='foreign'
    if problem=='occupied':inv['objects']=[dict(platform='facebook',account_id='page',remote_ref='other',publish_at='2035-01-03T22:45:00Z')]
    path.write_text(json.dumps(inv))
    if problem=='missing':path.unlink()
    before=store.path.read_bytes()
    with pytest.raises(ValueError):
        action_preview(j.id,'reschedule',store=store,publish_at=datetime.fromisoformat('2035-01-03T17:45:00-05:00'),now=NOW)
    assert store.path.read_bytes()==before
    assert store.get_action(j.id) is None


@pytest.mark.parametrize('problem',['stale','occupied','outside_coverage'])
def test_exact_reschedule_rechecks_calendar_after_approval_before_submit(tmp_path,problem):
    from socialctl.native_schedule.reconcile import action_preview,approve_action,mark_action_submit
    store,j,_,_=scheduled(tmp_path)
    target=datetime.fromisoformat('2035-01-03T17:45:00-05:00')
    preview=action_preview(j.id,'reschedule',store=store,publish_at=target,now=NOW)
    approve_action(j.id,preview['digest'],store=store,kind='reschedule',publish_at=target,now=NOW)
    path=tmp_path/'native-calendar.json';inv=json.loads(path.read_text())
    if problem=='stale':inv['observed_at']=(NOW-timedelta(days=2)).isoformat()
    if problem=='outside_coverage':inv['coverage_end']='2035-01-03T00:00:00Z'
    if problem=='occupied':inv['objects']=[dict(platform='facebook',account_id='page',remote_ref='other',publish_at=target.isoformat())]
    path.write_text(json.dumps(inv))
    with pytest.raises(ValueError):mark_action_submit(j.id,store=store,now=NOW)
    assert store.get_action(j.id)['status']=='approved'
    assert store.get(j.id).state=='native_scheduled'
    with pytest.raises((ValueError,sqlite3.IntegrityError)):
        store.prepare(job(id='competing',when=target),payload={},now=NOW)


def test_legacy_schema_two_jobs_and_action_snapshots_omit_unknown_field(tmp_path):
    from socialctl.native_schedule.reconcile import action_preview,approve_action
    store,j,_,_=scheduled(tmp_path,mode='legacy_daily')
    preview=action_preview(j.id,'cancel',store=store,now=NOW)
    approve_action(j.id,preview['digest'],store=store,kind='cancel',now=NOW)
    with sqlite3.connect(store.path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==2
        data=json.loads(db.execute('SELECT data FROM jobs').fetchone()[0])
        action=json.loads(db.execute('SELECT data FROM actions').fetchone()[0])
    assert 'schedule_mode' not in data
    assert 'schedule_mode' not in action['preview']['before']
    assert 'schedule_mode' not in action['preview']['after']
    assert 'schedule_mode' not in json.loads(action['job'])


@pytest.mark.parametrize('mode',['exact','legacy_daily'])
def test_pending_reschedule_hold_conflicts_with_other_policy(tmp_path,mode):
    from socialctl.native_schedule.reconcile import action_preview,approve_action
    store,j,_,_=scheduled(tmp_path,mode=mode)
    target=datetime.fromisoformat('2035-01-04T17:45:00-05:00' if mode=='exact' else '2035-01-04T09:00:00-05:00')
    preview=action_preview(j.id,'reschedule',store=store,publish_at=target,now=NOW)
    approve_action(j.id,preview['digest'],store=store,kind='reschedule',publish_at=target,now=NOW)
    other=job(id='competing',mode='legacy_daily' if mode=='exact' else 'exact',zone='America/New_York',
        when=datetime.fromisoformat('2035-01-04T09:00:00-05:00' if mode=='exact' else '2035-01-04T20:00:00-05:00'))
    with pytest.raises(ValueError,match='pending remote action'):
        store.prepare(other,payload={},now=NOW)
    assert store.get('competing') is None
