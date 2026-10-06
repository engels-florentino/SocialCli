"""Explicit operator readback and approved official-UI changes, never auto repair."""
from datetime import datetime, timezone
import json
from uuid import uuid4
from .approval import hash_value, native_digest, verify_approval
from .cadence import reservation_day
from .handoff import _target, record_observation
from .models import NativeJob, NativeObservation, aware
from .store import NativeStore


def action_preview(job_id, kind, *, store: NativeStore, publish_at=None, now=None):
    now=aware(now or datetime.now(timezone.utc))
    job=store.get(job_id)
    if not job or job.state != 'native_scheduled' or not job.remote_id:
        raise ValueError('A verified native scheduled target is required; reconcile first')
    if kind not in {'cancel','reschedule'}:
        raise ValueError('Unsupported action')
    payload=store.get_payload(job_id)
    verify_approval(job,payload)
    if _target(job,payload,store) != job.remote_id:
        raise ValueError('Known target identities conflict')
    target=job
    if kind=='reschedule':
        if publish_at is None or aware(publish_at) <= now:
            raise ValueError('Future destination timestamp required')
        reservation_day(publish_at,job.timezone_name)
        target=NativeJob.model_validate(job.model_dump() | {'publish_at':aware(publish_at)})
        target=NativeJob.model_validate(target.model_dump() | {'approval_digest':native_digest(target,payload)})
    elif publish_at is not None:
        raise ValueError('Cancel has no destination timestamp')
    document={'kind':kind,'job_id':job.id,'remote_ref':job.remote_id,'account_id':job.account_id,
        'platform':job.platform,'route':'official_ui','before':job.model_dump(mode='json'),
        'after':target.model_dump(mode='json'),'payload':payload,
        'consequence':'Unschedule existing object; preserve as draft. Deletion/recreation NOT authorized' if kind=='cancel' else 'Edit existing schedule in place. Deletion/recreation NOT authorized'}
    return {'document':document,'digest':hash_value(document)}


def approve_action(job_id, digest, *, store, kind, publish_at=None, now=None):
    preview=action_preview(job_id,kind,store=store,publish_at=publish_at,now=now)
    if digest != preview['digest']:
        raise ValueError('Action differs from the reviewed preview')
    job=store.get(job_id)
    action={'id':str(uuid4()),'kind':kind,'digest':digest,'status':'approved',
        'job':NativeJob.model_validate_json(json.dumps(preview['document']['after'])).model_dump_json(),
        'preview':preview['document']}
    store.save_action(job,action)
    return action


def mark_action_submit(job_id, *, store, now=None):
    action=store.get_action(job_id)
    if not action:
        raise ValueError('Approved action required')
    target=NativeJob.model_validate_json(action['job'])
    verify_approval(target,store.get_payload(job_id))
    return store.submit_action(job_id,now=now)


def reconcile(job_id: str, *, store: NativeStore, observation: NativeObservation,
              now=None) -> NativeObservation:
    if observation.job_id != job_id:
        raise ValueError('Observation belongs to another job')
    job=store.get(job_id)
    if job is None:
        raise ValueError('Unknown job')
    # Revalidate approval bytes at the trust boundary, including action targets.
    action=store.get_action(job_id)
    if action and action['status']=='submitted':
        verify_approval(NativeJob.model_validate_json(action['job']),store.get_payload(job_id))
        valid=store.confirm_action(observation,now=now)
    else:
        verify_approval(job,store.get_payload(job_id))
        valid=record_observation(job_id,observation,store=store,now=now)
    if not valid:
        store.notice(job_id,'Remote readback conflicts or lacks fresh evidence; no automatic repair',now=now)
    return observation
