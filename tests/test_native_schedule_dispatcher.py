from datetime import timedelta
import pytest
from tests.test_native_schedule_handoff import approved, observation, NOW, WHEN
from socialctl.native_schedule.dispatcher import dispatch_ready
from socialctl.native_schedule.reconcile import action_preview, approve_action, mark_action_submit, reconcile


def test_dispatch_now_not_publication_time(tmp_path):
    store, job, _, _ = approved(tmp_path)
    assert dispatch_ready(NOW, 'Histopast', store=store) == ['job']
    assert store.get(job.id).state == 'awaiting_ui'
    assert dispatch_ready(NOW, 'Histopast', store=store) == []
    assert len(store.get_attempts(job.id)) == 1
    assert store.get(job.id).publish_at == WHEN


def test_window_and_expired(tmp_path):
    store, job, _, _ = approved(tmp_path)
    from socialctl.native_schedule.store import _replace
    with store._db(write=True) as db:
        revised=_replace(job,dispatch_after=NOW+timedelta(hours=1))
        from socialctl.native_schedule.approval import native_digest
        store._save(db,_replace(revised,approval_digest=native_digest(revised,store.get_payload(job.id))))
    assert dispatch_ready(NOW, 'Histopast', store=store) == []
    assert store.get(job.id).state == 'waiting_window'
    assert dispatch_ready(WHEN, 'Histopast', store=store) == []
    assert store.get(job.id).state == 'blocked'


def test_reschedule_holds_both_slots_until_readback(tmp_path):
    store, job, _, _ = approved(tmp_path)
    dispatch_ready(NOW,'Histopast',store=store)
    obs = observation(job,tmp_path/'evidence')
    assert reconcile(job.id,store=store,observation=obs,now=NOW).state == 'native_scheduled'
    new_time = WHEN+timedelta(days=1)
    preview = action_preview(job.id,'reschedule',store=store,publish_at=new_time,now=NOW)
    approve_action(job.id,preview['digest'],store=store,publish_at=new_time,kind='reschedule',now=NOW)
    assert len(store.reserved_days(job.platform,job.account_id)) == 2
    mark_action_submit(job.id,store=store,now=NOW)
    assert store.get(job.id).publish_at == WHEN
    with pytest.raises(ValueError):
        mark_action_submit(job.id,store=store,now=NOW)
    action=store.get_action(job.id)
    revised=action['job']
    from socialctl.native_schedule.models import NativeJob
    target=NativeJob.model_validate_json(revised)
    fresh=observation(target,tmp_path/'new-evidence',publish_at=new_time,observed_at=NOW+timedelta(seconds=1))
    reconcile(job.id,store=store,observation=fresh,now=NOW+timedelta(seconds=1))
    assert store.get(job.id).publish_at == new_time
    assert store.get(job.id).state == 'native_scheduled'
    assert store.reserved_days(job.platform,job.account_id) == {'2026-09-22'}


def test_two_workers_one_attempt(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store,job,_,_=approved(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:dispatch_ready(NOW,'Histopast',store=store),range(2)))
    assert sum(map(len,results)) == 1
    assert len(store.get_attempts(job.id)) == 1
    assert store.get(job.id).state == 'awaiting_ui'


def test_cooldown_does_not_change_editorial_time_or_digest(tmp_path):
    store,job,_,_=approved(tmp_path)
    store.defer_before_write(job.id,NOW+timedelta(minutes=10),reason='rate_limit',now=NOW)
    assert dispatch_ready(NOW,'Histopast',store=store) == []
    assert store.get(job.id).publish_at == WHEN
    assert store.get(job.id).approval_digest == job.approval_digest
    dispatch_ready(NOW+timedelta(minutes=11),'Histopast',store=store)
    with pytest.raises(ValueError):
        store.defer_before_write(job.id,NOW+timedelta(minutes=12),reason='rate_limit',now=NOW)


def test_conflict_and_missing_do_not_retry(tmp_path):
    store,job,_,_=approved(tmp_path)
    dispatch_ready(NOW,'Histopast',store=store)
    for state in ['missing','conflict']:
        reconcile(job.id,store=store,observation=observation(job,tmp_path/state,state=state),now=NOW)
        assert store.get(job.id).state == 'uncertain'
        assert dispatch_ready(NOW,'Histopast',store=store) == []
    assert store.get_notices(job.id)


def test_crash_claim_recovery_never_resubmits(tmp_path):
    store,job,_,_=approved(tmp_path)
    store.claim(job.id,'approved',now=NOW)
    assert dispatch_ready(NOW,'Histopast',store=store) == []
    assert store.recover_dispatching(now=NOW) == [job.id]
    assert dispatch_ready(NOW,'Histopast',store=store) == []


def test_cancel_requires_marker_and_fresh_readback(tmp_path):
    store,job,_,_=approved(tmp_path)
    dispatch_ready(NOW,'Histopast',store=store)
    reconcile(job.id,store=store,observation=observation(job,tmp_path/'calendar'),now=NOW)
    preview=action_preview(job.id,'cancel',store=store,now=NOW)
    with pytest.raises(ValueError):
        approve_action(job.id,'0'*64,store=store,kind='cancel',now=NOW)
    approve_action(job.id,preview['digest'],store=store,kind='cancel',now=NOW)
    assert store.get(job.id).state == 'native_scheduled'
    mark_action_submit(job.id,store=store,now=NOW)
    reconcile(job.id,store=store,observation=observation(job,tmp_path/'stale',state='cancelled'),now=NOW)
    assert store.get(job.id).state == 'uncertain'
    fresh=NOW+timedelta(seconds=1)
    reconcile(job.id,store=store,observation=observation(job,tmp_path/'cancelled',state='cancelled',observed_at=fresh),now=fresh)
    assert store.get(job.id).state == 'cancelled'


def test_published_requires_visibility_and_processing_not_clock(tmp_path):
    store,job,_,_=approved(tmp_path)
    dispatch_ready(NOW,'Histopast',store=store)
    reconcile(job.id,store=store,observation=observation(job,tmp_path/'calendar'),now=NOW)
    assert dispatch_ready(WHEN,'Histopast',store=store) == []
    assert store.get(job.id).state == 'native_scheduled'
    reconcile(job.id,store=store,observation=observation(job,tmp_path/'pending',state='published',public_visible=True,observed_at=WHEN),now=WHEN)
    assert store.get(job.id).state == 'uncertain'
    later=WHEN+timedelta(seconds=1)
    reconcile(job.id,store=store,observation=observation(job,tmp_path/'public',state='published',public_visible=True,processing_complete=True,observed_at=later),now=later)
    assert store.get(job.id).state == 'published'


def test_partial_multiplatform_failure_does_not_abort_other_jobs(tmp_path):
    store,job,_,media=approved(tmp_path,platform='facebook')
    # Same ledger, independent platform reservation and approval.
    _,other,_,_=approved(tmp_path,platform='instagram',job_id='second')
    media.write_bytes(b'changed')
    assert dispatch_ready(NOW,'Histopast',store=store) == [other.id]
    assert store.get(job.id).state == 'approved'
    assert store.get(other.id).state == 'awaiting_ui'


def test_native_comment_status_is_separate(tmp_path):
    from socialctl.publication_status import describe_native_job
    store,job,payload,_=approved(tmp_path)
    assert 'pending manual/server' in describe_native_job(job,payload | {'first_comment':'CTA'})


@pytest.mark.parametrize('stamp',['2026-11-01T01:30:00-04:00','2026-11-01T01:30:00-05:00','2026-03-08T02:30:00-05:00'])
def test_noneditorial_dst_times_rejected(stamp):
    from datetime import datetime
    from socialctl.native_schedule.cadence import reservation_day
    with pytest.raises(ValueError):
        reservation_day(datetime.fromisoformat(stamp),'America/New_York')


def test_cli_delivery_roundtrip(tmp_path):
    import json
    from tests.test_native_schedule_cli import fixture, invoke, digest
    brand,_=fixture(tmp_path)
    d=digest(invoke(tmp_path,'prepare','native.json','--dry-run').output)
    assert invoke(tmp_path,'prepare','native.json','--persist','--approval-digest',d).exit_code == 0
    assert invoke(tmp_path,'approve','clip','--approval-digest',d).exit_code == 0
    # Fixture's dispatch is in the future; CLI returns a real waiting state.
    result=invoke(tmp_path,'dispatch','clip')
    assert result.exit_code == 0 and 'waiting_window' in result.output
    assert invoke(tmp_path,'handoff','clip').exit_code == 1
    assert invoke(tmp_path,'submit-marker','clip').exit_code == 1
    assert invoke(tmp_path,'reconcile','clip').exit_code == 0


def test_action_conflict_advances_barrier_and_keeps_reservations(tmp_path):
    store,job,_,_=approved(tmp_path)
    dispatch_ready(NOW,'Histopast',store=store)
    reconcile(job.id,store=store,observation=observation(job,tmp_path/'calendar'),now=NOW)
    new_time=WHEN+timedelta(days=1)
    preview=action_preview(job.id,'reschedule',store=store,publish_at=new_time,now=NOW)
    approve_action(job.id,preview['digest'],store=store,kind='reschedule',publish_at=new_time,now=NOW)
    mark_action_submit(job.id,store=store,now=NOW)
    from socialctl.native_schedule.models import NativeJob
    target=NativeJob.model_validate_json(store.get_action(job.id)['job'])
    conflict_time=NOW+timedelta(minutes=2)
    reconcile(job.id,store=store,observation=observation(target,tmp_path/'conflict',state='conflict',observed_at=conflict_time),now=conflict_time)
    stale_time=NOW+timedelta(minutes=1)
    reconcile(job.id,store=store,observation=observation(target,tmp_path/'older',observed_at=stale_time),now=conflict_time)
    assert store.get(job.id).state=='uncertain'
    assert len(store.reserved_days(job.platform,job.account_id))==2
    assert store.get(job.id).publish_at==WHEN


@pytest.mark.parametrize('invalid_binding',[None,'checksum','account'])
def test_action_target_mismatch_is_audited_and_only_bound_evidence_advances_barrier(tmp_path,invalid_binding):
    from socialctl.native_schedule.models import NativeJob
    store,job,_,_=approved(tmp_path)
    dispatch_ready(NOW,'Histopast',store=store)
    reconcile(job.id,store=store,observation=observation(job,tmp_path/'calendar'),now=NOW)
    new_time=WHEN+timedelta(days=1)
    preview=action_preview(job.id,'reschedule',store=store,publish_at=new_time,now=NOW)
    approve_action(job.id,preview['digest'],store=store,kind='reschedule',publish_at=new_time,now=NOW)
    mark_action_submit(job.id,store=store,now=NOW)
    target=NativeJob.model_validate_json(store.get_action(job.id)['job'])
    t1,t2,t3=[NOW+timedelta(minutes=n) for n in (1,2,3)]
    changes={'remote_ref':'unexpected-reference','observed_at':t2}
    if invalid_binding=='checksum': changes['evidence_sha256']='0'*64
    if invalid_binding=='account': changes['account_id']='unrelated-account'
    contradictory=observation(target,tmp_path/'target-conflict',**changes)
    reconcile(job.id,store=store,observation=contradictory,now=t2)
    assert store.get_observations(job.id)[-1] == contradictory
    assert store.get_action(job.id)['status']=='submitted'
    assert len(store.reserved_days(job.platform,job.account_id))==2
    assert store.get(job.id).reconcile_after==(t2 if invalid_binding is None else NOW)
    older=observation(target,tmp_path/'older-target',observed_at=t1)
    reconcile(job.id,store=store,observation=older,now=t2)
    if invalid_binding:
        assert store.get_action(job.id)['status']=='confirmed'
        assert store.get(job.id).observation_watermark==t1
        return
    assert store.get_action(job.id)['status']=='submitted'
    assert store.get(job.id).state=='uncertain'
    assert store.get(job.id).publish_at==WHEN
    assert len(store.reserved_days(job.platform,job.account_id))==2
    assert store.get_observations(job.id)[-2:] == [contradictory,older]
    reconcile(job.id,store=store,observation=observation(target,tmp_path/'fresh-target',observed_at=t3),now=t3)
    assert store.get_action(job.id)['status']=='confirmed'
    assert store.get(job.id).publish_at==new_time
    assert store.reserved_days(job.platform,job.account_id)=={'2026-09-22'}


def test_action_confirmation_requires_consensus_with_checkpoint_reference(tmp_path):
    from socialctl.native_schedule.models import NativeJob
    store,job,_,_=approved(tmp_path)
    dispatch_ready(NOW,'Histopast',store=store)
    reconcile(job.id,store=store,observation=observation(job,tmp_path/'calendar'),now=NOW)
    new_time=WHEN+timedelta(days=1)
    preview=action_preview(job.id,'reschedule',store=store,publish_at=new_time,now=NOW)
    approve_action(job.id,preview['digest'],store=store,kind='reschedule',publish_at=new_time,now=NOW)
    mark_action_submit(job.id,store=store,now=NOW)
    current=store.get(job.id)
    store.checkpoint(job.id,current.attempt_id,{'video_id':'conflicting-checkpoint-reference'},now=NOW)
    target=NativeJob.model_validate_json(store.get_action(job.id)['job'])
    later=NOW+timedelta(minutes=1)
    readback=observation(target,tmp_path/'readback',observed_at=later)
    reconcile(job.id,store=store,observation=readback,now=later)
    assert store.get_observations(job.id)[-1]==readback
    assert store.get_action(job.id)['status']=='submitted'
    assert store.get(job.id).reconcile_after==later
    assert len(store.reserved_days(job.platform,job.account_id))==2
