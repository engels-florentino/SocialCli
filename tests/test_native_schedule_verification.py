from datetime import datetime, timezone
from hashlib import sha256

import pytest
from pydantic import ValidationError

from socialctl.native_schedule.models import NativeJob, NativeObservation
from socialctl.native_schedule.verification import verify

NOW = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
WHEN = datetime(2026, 9, 21, 13, tzinfo=timezone.utc)
HASH = 'a' * 64


def make_job(**changes):
    values = dict(id='job-1', brand='Histopast', platform='youtube', account_id='test',
        media_hash=HASH, content_hash=HASH, approval_digest=HASH, publish_at=WHEN,
        timezone_name='America/New_York', dispatch_after=NOW, route='ui', state='ready',
        created_at=NOW, updated_at=NOW) | changes
    if values['state'] == 'prepared' and 'approval_digest' not in changes:
        values['approval_digest'] = None
    return NativeJob(**values)


def make_observation(tmp_path, **changes):
    evidence = tmp_path / 'calendar.png'
    evidence.write_bytes(b'operator calendar evidence')
    return NativeObservation(**(dict(job_id='job-1', platform='youtube', account_id='test',
        content_hash=HASH, approval_digest=HASH, remote_ref='remote-1', publish_at=WHEN,
        observed_at=NOW, source='ui', calendar_visible=True, state='native_scheduled',
        evidence_path=str(evidence), evidence_sha256=sha256(evidence.read_bytes()).hexdigest()) | changes))


def test_verified_and_historical(tmp_path):
    assert verify(make_job(), make_observation(tmp_path), now=NOW)
    assert verify(make_job(), make_observation(tmp_path), now=datetime(2030, 1, 1, tzinfo=timezone.utc))


@pytest.mark.parametrize('changes', [dict(state='draft'), dict(account_id='other'),
    dict(calendar_visible=False), dict(job_id='other'), dict(content_hash='b'*64),
    dict(platform='facebook'), dict(approval_digest='b'*64), dict(remote_ref='wrong'),
    dict(publish_at=datetime(2026, 9, 22, 13, tzinfo=timezone.utc))])
def test_mismatch(tmp_path, changes):
    job = make_job(remote_id='remote-1')
    assert not verify(job, make_observation(tmp_path, **changes), now=NOW)


def test_artifact_integrity(tmp_path):
    observation = make_observation(tmp_path)
    (tmp_path / 'calendar.png').write_bytes(b'changed')
    assert not verify(make_job(), observation, now=NOW)


def test_future_observation_and_api_cannot_verify(tmp_path):
    assert not verify(make_job(), make_observation(tmp_path, observed_at=WHEN), now=NOW)
    with pytest.raises(ValidationError):
        make_observation(tmp_path, source='api')


def test_strict_frozen_and_nonexistent_datetime():
    from zoneinfo import ZoneInfo
    with pytest.raises(ValidationError):
        make_job(media_hash='bad')
    with pytest.raises(ValidationError):
        make_job().state = 'published'
    with pytest.raises(ValidationError):
        make_job(publish_at=datetime(2027, 3, 14, 2, 30, tzinfo=ZoneInfo('America/New_York')))
    with pytest.raises(ValidationError):
        make_job(publish_at=datetime(2027, 1, 1))
