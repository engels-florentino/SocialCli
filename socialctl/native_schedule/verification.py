"""Verify a trusted operator attestation; does not interpret image contents."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from .models import NativeJob, NativeObservation, aware


def evidence_matches(job: NativeJob, observation: NativeObservation, *, now: datetime) -> bool:
    """Artifact and identity validity, independent of the reported remote outcome."""
    now = aware(now)
    if (observation.job_id != job.id or observation.platform != job.platform
        or observation.account_id != job.account_id or observation.content_hash != job.content_hash
        or observation.approval_digest != job.approval_digest
        or observation.observed_at > now or observation.observed_at < job.created_at):
        return False
    try:
        data = Path(observation.evidence_path).read_bytes()
        return bool(data) and sha256(data).hexdigest() == observation.evidence_sha256
    except OSError:
        return False


def verify(job: NativeJob, observation: NativeObservation, *, now: datetime | None = None) -> bool:
    """Historical attestation validity; ledger separately enforces freshness."""
    return (
        evidence_matches(job, observation, now=now or datetime.now(timezone.utc))
        and observation.publish_at == job.publish_at
        and observation.state == 'native_scheduled' and observation.calendar_visible
        and observation.source == 'ui'
        and (job.remote_id is None or job.remote_id == observation.remote_ref)
    )
