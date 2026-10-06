"""Read-only API reconciliation for an already claimed approved occurrence."""

from collections.abc import Callable

from .approval import verify_approval
from .models import NativeJob, NativeObservation
from .store import NativeStore


def reconcile_api(job_id: str, *, store: NativeStore,
                  reader: Callable[[NativeJob, dict], dict]) -> NativeObservation | None:
    """Confirm only a complete, identity-bound provider readback.

    A partial result is never evidence of absence. The reader must compare
    actual remote copy/options with the approved payload before `matches=True`.
    """
    job = store.get(job_id)
    if job is None or job.brand != store.brand or job.route != 'api':
        raise ValueError('Owned API job required')
    payload = store.get_payload(job_id)
    verify_approval(job, payload)
    result = reader(job, payload)
    if not isinstance(result, dict) or not result.get('complete'):
        store.notice(job_id, 'API readback incomplete; absence not proven')
        return None
    if not result.get('matches'):
        store.notice(job_id, 'API readback differs from approved content or account')
        return None
    observation = result.get('observation')
    if (not isinstance(observation, NativeObservation) or observation.job_id != job.id
            or observation.source != 'api' or observation.account_id != job.account_id
            or observation.remote_ref != job.remote_id or
            observation.content_hash != job.content_hash or
            observation.approval_digest != job.approval_digest):
        store.notice(job_id, 'API readback lacks exact remote identity')
        return None
    if not store.record_observation(observation, now=observation.observed_at):
        store.notice(job_id, 'API observation did not prove publication')
        return None
    return observation
