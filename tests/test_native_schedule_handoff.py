from datetime import datetime, timezone
from hashlib import sha256

import pytest

from socialctl.native_schedule.approval import hash_value, media_hashes, native_digest
from socialctl.native_schedule.handoff import (
    mark_ui_submit_started,
    prepare_handoff,
    record_observation,
)
from socialctl.native_schedule.models import NativeJob, NativeObservation
from socialctl.native_schedule.store import NativeStore


NOW = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
WHEN = datetime(2026, 9, 21, 13, tzinfo=timezone.utc)  # 09:00 New York


def approved(tmp_path, *, platform='facebook', job_id='job', account='account', existing=None,
             share_story=False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    media = tmp_path / f'{job_id}.mp4'
    media.write_bytes(b'exact approved bytes')
    payload = {
        'copy': 'Exact caption\nwith punctuation.',
        'content_origin': 'standalone',
        'source_video_id': None,
        'visibility': 'public',
        'media': [{'path': str(media), 'origin': 'user_supplied'}],
        'first_comment': None,
        'options': {'format': 'reel', 'share_to_facebook_story': share_story},
        'calendar': f'operator checked {platform} account',
    }
    if existing:
        payload['options']['video_id'] = existing
        if platform == 'youtube':
            payload['options']['never_published'] = True
            payload['options']['selfDeclaredMadeForKids'] = False
            payload['options']['title'] = 'Exact title'
    job = NativeJob(id=job_id, brand='Histopast', platform=platform, account_id=account,
        media_hash=hash_value(media_hashes(payload)), content_hash=hash_value(payload),
        publish_at=WHEN, timezone_name='America/New_York', dispatch_after=NOW,
        route='ui', created_at=NOW, updated_at=NOW)
    store = NativeStore(tmp_path, brand='Histopast')
    store.prepare(job, payload=payload, now=NOW)
    job = store.transition(job.id, 'prepared', 'approved',
        approval_digest=native_digest(job, payload), now=NOW)
    return store, job, payload, media


def observation(job, artifact, **changes):
    artifact.write_bytes(b'trusted operator calendar export')
    values = dict(job_id=job.id, platform=job.platform, account_id=job.account_id,
        content_hash=job.content_hash, approval_digest=job.approval_digest,
        remote_ref='stable-remote-reference', publish_at=job.publish_at,
        observed_at=NOW, source='ui', calendar_visible=True,
        state='native_scheduled', evidence_path=str(artifact),
        evidence_sha256=sha256(artifact.read_bytes()).hexdigest())
    return NativeObservation(**(values | changes))


def test_handoff_preserves_complete_payload_bytes_hash_and_time(tmp_path, monkeypatch):
    store, job, payload, media = approved(tmp_path)
    monkeypatch.setattr('httpx.Client.request', lambda *a, **k: pytest.fail('no API calls'))
    result = prepare_handoff(job, payload, store=store, now=NOW)

    assert result['state'] == 'awaiting_ui'
    assert result['job'] == {'id': job.id, 'brand': job.brand, 'platform': 'facebook',
        'account_id': job.account_id}
    assert result['payload'] == payload
    assert result['payload']['copy'] == payload['copy']
    assert result['schedule'] == {'local': '2026-09-21T09:00:00-04:00',
        'utc': '2026-09-21T13:00:00+00:00', 'timezone': 'America/New_York'}
    assert result['approval_digest'] == job.approval_digest
    assert result['media'] == [{'path': str(media), 'origin': 'user_supplied',
        'sha256': sha256(media.read_bytes()).hexdigest(), 'bytes': media.stat().st_size}]
    assert result['delivery']['action'] == 'upload_approved_file'
    current = store.get(job.id)
    assert current.state == 'awaiting_ui' and current.attempt_id


@pytest.mark.parametrize('platform', ['facebook', 'instagram'])
def test_meta_story_destination_must_be_explicitly_off(tmp_path, platform):
    store, job, payload, _ = approved(tmp_path, platform=platform, share_story=True)
    with pytest.raises(ValueError, match='Story'):
        prepare_handoff(job, payload, store=store, now=NOW)
    assert store.get(job.id).state == 'approved'


def test_existing_target_is_verify_or_edit_and_never_upload(tmp_path):
    store, job, payload, _ = approved(tmp_path, platform='youtube', existing='abcdefghijk')
    result = prepare_handoff(job, payload, store=store, now=NOW)
    assert result['delivery'] == {'action': 'verify_or_edit_existing',
        'remote_id': 'abcdefghijk', 'upload_allowed': False}


def test_checkpoint_target_also_prevents_second_upload(tmp_path):
    store, job, payload, _ = approved(tmp_path, platform='youtube')
    claimed = store.claim(job.id, 'approved', now=NOW)
    store.checkpoint(job.id, claimed.attempt_id, {'video_id': 'abcdefghijk'}, now=NOW)
    store.transition(job.id, 'dispatching', 'awaiting_ui', now=NOW)
    result = prepare_handoff(store.get(job.id), payload, store=store, now=NOW)
    assert result['delivery']['action'] == 'verify_or_edit_existing'
    assert result['delivery']['upload_allowed'] is False


def test_submit_start_crash_regeneration_requires_search_before_retry(tmp_path):
    store, job, payload, media = approved(tmp_path, platform='tiktok')
    prepare_handoff(job, payload, store=store, now=NOW)
    marked = mark_ui_submit_started(job.id, store=store, now=NOW)
    assert marked.state == 'uncertain'
    checkpoint = store.get_attempts(job.id)[0]['checkpoint']
    assert checkpoint['ui_submit_started_at'] == NOW.isoformat()
    media.unlink()  # reconciliation must not require another upload/source file
    regenerated = prepare_handoff(store.get(job.id), payload, store=store, now=NOW)
    assert regenerated['delivery'] == {
        'action': 'search_existing_before_retry', 'upload_allowed': False}
    assert regenerated['media'][0]['sha256'] is None
    assert regenerated['media_integrity'] == 'approved aggregate retained; source unavailable'


@pytest.mark.parametrize('source_state', ['missing', 'changed'])
def test_recovered_verifying_is_search_only_without_known_target(tmp_path, source_state):
    store, job, payload, media = approved(tmp_path, platform='tiktok')
    store.claim(job.id, 'approved', now=NOW)
    assert store.recover_dispatching(now=NOW) == [job.id]
    store.transition(job.id, 'uncertain', 'verifying', now=NOW)
    if source_state == 'missing':
        media.unlink()
    else:
        media.write_bytes(b'different historical source')

    regenerated = prepare_handoff(store.get(job.id), payload, store=store, now=NOW)
    assert regenerated['delivery'] == {
        'action': 'search_existing_before_retry', 'upload_allowed': False}
    assert regenerated['media_integrity'] != 'freshly rehashed'


def test_handoff_rehashes_media_and_requires_exact_store_context(tmp_path):
    store, job, payload, media = approved(tmp_path)
    media.write_bytes(b'tampered')
    with pytest.raises(ValueError, match='digest|changed|mismatch'):
        prepare_handoff(job, payload, store=store, now=NOW)
    assert store.get(job.id).state == 'approved'
    other = NativeStore(tmp_path / 'other', brand='Histopast')
    with pytest.raises(ValueError, match='store'):
        prepare_handoff(job, payload, store=other, now=NOW)


def test_observation_requires_started_approved_delivery_and_exact_evidence(tmp_path):
    store, job, payload, _ = approved(tmp_path)
    obs = observation(job, tmp_path / 'calendar.png')
    with pytest.raises(ValueError, match='delivery'):
        record_observation(job.id, obs, store=store, now=NOW)
    prepare_handoff(job, payload, store=store, now=NOW)
    assert record_observation(job.id, obs, store=store, now=NOW)
    assert store.get(job.id).state == 'native_scheduled'


@pytest.mark.parametrize('target_source', ['payload', 'checkpoint'])
def test_observation_must_match_existing_target(tmp_path, target_source):
    existing = 'abcdefghijk' if target_source == 'payload' else None
    store, job, payload, _ = approved(tmp_path, platform='youtube', existing=existing)
    prepare_handoff(job, payload, store=store, now=NOW)
    current = store.get(job.id)
    if target_source == 'checkpoint':
        store.checkpoint(job.id, current.attempt_id, {'video_id': 'abcdefghijk'}, now=NOW)
    wrong = observation(job, tmp_path / 'calendar.png', remote_ref='different-video')
    assert not record_observation(job.id, wrong, store=store, now=NOW)
    assert store.get(job.id).state == 'uncertain'


def test_inconsistent_known_targets_cannot_promote_observation(tmp_path):
    store, job, payload, _ = approved(
        tmp_path, platform='youtube', existing='abcdefghijk')
    prepare_handoff(job, payload, store=store, now=NOW)
    current = store.get(job.id)
    store.checkpoint(job.id, current.attempt_id, {'video_id': 'other-video'}, now=NOW)
    apparently_matching = observation(
        job, tmp_path / 'calendar.png', remote_ref='abcdefghijk')
    # Shared store boundary must protect direct controller callers too.
    assert not store.record_observation(apparently_matching, now=NOW)
    assert store.get(job.id).state == 'uncertain'


@pytest.mark.parametrize('change,expected_state', [
    ({'account_id': 'wrong'}, 'awaiting_ui'),
    ({'publish_at': datetime(2026, 9, 22, 13, tzinfo=timezone.utc)}, 'uncertain'),
])
def test_observation_rejects_wrong_account_or_date(tmp_path, change, expected_state):
    store, job, payload, _ = approved(tmp_path)
    prepare_handoff(job, payload, store=store, now=NOW)
    obs = observation(job, tmp_path / 'calendar.png', **change)
    assert not record_observation(job.id, obs, store=store, now=NOW)
    assert store.get(job.id).state == expected_state


def test_separate_meta_results_survive_partial_success(tmp_path):
    fb_store, fb, fb_payload, _ = approved(tmp_path / 'fb', platform='facebook', job_id='fb')
    ig_store, ig, ig_payload, _ = approved(tmp_path / 'ig', platform='instagram', job_id='ig')
    prepare_handoff(fb, fb_payload, store=fb_store, now=NOW)
    prepare_handoff(ig, ig_payload, store=ig_store, now=NOW)
    assert record_observation(fb.id, observation(fb, tmp_path / 'fb.png'), store=fb_store, now=NOW)
    bad = observation(ig, tmp_path / 'ig.png', account_id='wrong')
    assert not record_observation(ig.id, bad, store=ig_store, now=NOW)
    assert fb_store.get(fb.id).state == 'native_scheduled'
    assert ig_store.get(ig.id).state == 'awaiting_ui'


def test_prepare_rejects_prefilled_approval_digest(tmp_path):
    store, job, payload, _ = approved(tmp_path / 'source')
    fresh = job.model_copy(update={'id': 'bypass', 'state': 'prepared',
        'approval_digest': 'a' * 64, 'attempt_id': None})
    with pytest.raises(ValueError, match='approval'):
        NativeStore(tmp_path / 'target', brand='Histopast').prepare(fresh, payload=payload, now=NOW)
