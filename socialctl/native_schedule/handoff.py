"""Local handoff manifests for trusted operators using official scheduling UIs.

This module never opens a browser or calls a platform API.  It binds the exact
approved payload and current media bytes to one durable UI attempt; evidence is
accepted only after that delivery attempt has started.
"""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .approval import hash_value, media_hashes, verify_approval
from .models import NativeJob, NativeObservation, aware
from .store import NativeStore


SCHEMA = 'socialctl.native-schedule.handoff.v1'
_DELIVERY_STATES = {'dispatching', 'awaiting_ui', 'verifying', 'uncertain', 'native_scheduled'}


def _now(value: datetime | None) -> datetime:
    return aware(value or datetime.now(timezone.utc))


def _stored(job: NativeJob, payload: dict, store: NativeStore) -> NativeJob:
    current = store.get(job.id)
    stored_payload = store.get_payload(job.id)
    if current is None or stored_payload is None:
        raise ValueError('Job and payload must exist in the explicit store')
    if current.brand != store.brand or job.brand != store.brand:
        raise ValueError('Job does not belong to the explicit store brand')
    # A caller may pass an earlier lifecycle snapshot, but immutable intent must
    # match the authoritative row exactly.
    immutable = ('id', 'brand', 'platform', 'account_id', 'media_hash',
                 'content_hash', 'publish_at', 'timezone_name', 'route')
    if any(getattr(job, field) != getattr(current, field) for field in immutable):
        raise ValueError('Job does not match the stored approved intent')
    if payload != stored_payload:
        raise ValueError('Payload does not match the stored approved intent')
    return current


def _target(job: NativeJob, payload: dict, store: NativeStore) -> str | None:
    candidates = []
    if job.remote_id:
        candidates.append(job.remote_id)
    video_id = payload.get('options', {}).get('video_id')
    if video_id:
        candidates.append(video_id)
    for attempt in store.get_attempts(job.id):
        checkpoint_id = attempt['checkpoint'].get('video_id')
        if checkpoint_id:
            candidates.append(checkpoint_id)
    if len(set(candidates)) > 1:
        raise ValueError('Existing remote target identities conflict')
    return candidates[0] if candidates else None


def _submit_started(job: NativeJob, store: NativeStore) -> bool:
    return any(attempt['checkpoint'].get('ui_submit_started_at')
               for attempt in store.get_attempts(job.id))


def _check_meta_destinations(job: NativeJob, payload: dict) -> None:
    if job.platform not in {'facebook', 'instagram'}:
        return
    options = payload.get('options', {})
    if options.get('share_to_facebook_story') is not False:
        raise ValueError('Share to Facebook Story must be explicitly OFF for feed/Reel delivery')
    destinations = options.get('destinations')
    if destinations not in (None, [], [job.platform]):
        raise ValueError('Additional Meta destinations require separately approved jobs')


def prepare_handoff(job: NativeJob, payload: dict, *, store: NativeStore,
                    now: datetime | None = None, require_new: bool = False) -> dict:
    """Own or resume a UI attempt and return the exact operator manifest."""
    now = _now(now)
    current = _stored(job, payload, store)
    if require_new and current.state not in {'approved','ready','waiting_window'}:
        raise ValueError('Another worker already owns this delivery')
    if current.route != 'ui' or current.approval_digest is None:
        raise ValueError('An explicitly approved UI route is required')
    _check_meta_destinations(current, payload)

    target = _target(current, payload, store)
    reconciling = (current.state in {'uncertain', 'verifying', 'native_scheduled'}
                   or _submit_started(current, store))

    # This is intentionally before claim: media is re-read immediately before
    # handoff and a mismatch cannot acquire an attempt.
    if not reconciling:
        verify_approval(current, payload)
    elif hash_value(payload) != current.content_hash:
        raise ValueError('Stored reconciliation payload changed')
    if current.state in {'approved', 'ready', 'waiting_window'}:
        claimed = store.claim(current.id, current.state, now=now)
        if claimed is None:
            raise ValueError('Approved UI job could not be claimed')
        current = store.transition(current.id, 'dispatching', 'awaiting_ui', now=now)
        if current is None:
            raise ValueError('UI attempt ownership was lost')
        store.checkpoint(current.id, current.attempt_id,
                         {'handoff_prepared_at': now.isoformat()}, now=now)
    elif current.state not in {'awaiting_ui', 'uncertain', 'verifying', 'native_scheduled'}:
        raise ValueError('Job is not in an approved UI delivery stage')

    files = []
    media_integrity = 'freshly rehashed'
    try:
        hashes = media_hashes(payload)
        if reconciling and hash_value(hashes) != current.media_hash:
            media_integrity = 'source differs from approved aggregate; upload prohibited'
        for item, digest in zip(payload.get('media', []), hashes, strict=True):
            path = Path(item['path'])
            files.append({'path': str(path), 'origin': item['origin'],
                          'sha256': digest['sha256'], 'bytes': path.stat().st_size})
    except ValueError:
        if not reconciling:
            raise
        media_integrity = 'approved aggregate retained; source unavailable'
        files = [{'path': item['path'], 'origin': item['origin'],
                  'sha256': None, 'bytes': None} for item in payload.get('media', [])]

    if target:
        delivery = {'action': 'verify_or_edit_existing', 'remote_id': target,
                    'upload_allowed': False}
    elif reconciling:
        delivery = {'action': 'search_existing_before_retry', 'upload_allowed': False}
    else:
        delivery = {'action': 'upload_approved_file', 'upload_allowed': True}

    local = current.publish_at.astimezone(ZoneInfo(current.timezone_name))
    manifest = {
        'schema': SCHEMA,
        'state': 'awaiting_ui' if current.state == 'awaiting_ui' else current.state,
        'job': {'id': current.id, 'brand': current.brand, 'platform': current.platform,
                'account_id': current.account_id},
        'payload': deepcopy(payload),
        'schedule': {'local': local.isoformat(), 'utc': current.publish_at.isoformat(),
                     'timezone': current.timezone_name},
        'approval_digest': current.approval_digest,
        'content_hash': current.content_hash,
        'media_hash': current.media_hash,
        'media': files,
        'media_integrity': media_integrity,
        'delivery': delivery,
        'operator_policy': {
            'official_ui_only': True,
            'verify_current_window_in_ui': True,
            'fresh_account_calendar_day_and_destinations_before_final_click': True,
            'mark_submit_started_before_final_confirmation': True,
            'trusted_operator_attestation_required': True,
        },
    }
    # Defensive check that the returned package still describes exact content.
    if hash_value(manifest['payload']) != current.content_hash:
        raise ValueError('Handoff payload changed while packaging')
    return manifest


def mark_ui_submit_started(job_id: str, *, store: NativeStore,
                           now: datetime | None = None) -> NativeJob:
    """Persist the final-submit crash boundary before the operator confirms."""
    now = _now(now)
    job = store.get(job_id)
    if job is None or job.state != 'awaiting_ui' or not job.attempt_id:
        raise ValueError('An owned awaiting_ui attempt is required')
    if job.publish_at <= now:
        raise ValueError('Deadline expired; immediate publication prohibited')
    verify_approval(job,store.get_payload(job_id))
    store.checkpoint(job.id, job.attempt_id,
                     {'ui_submit_started_at': now.isoformat()}, now=now)
    uncertain = store.transition(job.id, 'awaiting_ui', 'uncertain', now=now)
    if uncertain is None:
        raise ValueError('UI submit marker lost attempt ownership')
    return uncertain


def record_observation(job_id: str, observation: NativeObservation, *,
                       store: NativeStore, now: datetime | None = None) -> bool:
    """Import trusted operator evidence; never interpret screenshots/artifacts."""
    job = store.get(job_id)
    if job is None:
        raise ValueError('Unknown job')
    if observation.job_id != job_id:
        raise ValueError('Observation belongs to a different job')
    if (job.state not in _DELIVERY_STATES or not job.attempt_id
            or job.approval_digest is None):
        raise ValueError('Observation requires an approved delivery-stage job')
    # NativeStore performs identity/date/freshness/artifact checksum checks and
    # retains invalid observations for audit without promoting success.
    return store.record_observation(observation, now=now)
