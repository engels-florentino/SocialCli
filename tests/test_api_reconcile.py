from datetime import timedelta
from hashlib import sha256

from socialctl.native_schedule.models import NativeObservation
from socialctl.native_schedule.api_reconcile import reconcile_api
from socialctl.native_schedule.dispatcher import dispatch_ready
from tests.test_api_dispatch import CAPABILITY, Success, api_job
from tests.test_native_schedule_handoff import WHEN


def published_observation(job, path):
    path.write_bytes(b"provider response with remote identity and content")
    return NativeObservation(
        job_id=job.id, platform=job.platform, account_id=job.account_id,
        content_hash=job.content_hash, approval_digest=job.approval_digest,
        remote_ref=job.remote_id, publish_at=job.publish_at,
        observed_at=WHEN + timedelta(minutes=1), source="api", calendar_visible=False,
        state="published", public_visible=True, processing_complete=True,
        actual_published_at=WHEN, evidence_path=str(path),
        evidence_sha256=sha256(path.read_bytes()).hexdigest(),
    )


def submitted(tmp_path):
    store, job = api_job(tmp_path)
    dispatch_ready(WHEN, store.brand, store=store,
                   adapters={"facebook": Success()}, capabilities=CAPABILITY)
    return store, store.get(job.id)


def test_partial_api_readback_preserves_verification_state(tmp_path):
    store, job = submitted(tmp_path)
    observation = published_observation(job, tmp_path / "readback.json")
    assert reconcile_api(job.id, store=store,
                         reader=lambda *_: {"complete": False, "matches": True,
                                            "observation": observation}) is None
    assert store.get(job.id).state == "verifying"


def test_matching_api_readback_confirms_publication(tmp_path):
    store, job = submitted(tmp_path)
    observation = published_observation(job, tmp_path / "readback.json")
    result = reconcile_api(job.id, store=store,
                           reader=lambda *_: {"complete": True, "matches": True,
                                              "observation": observation})
    assert result == observation
    assert store.get(job.id).state == "published"


def test_mismatched_copy_or_remote_id_never_confirms(tmp_path):
    store, job = submitted(tmp_path)
    observation = published_observation(job, tmp_path / "readback.json")
    assert reconcile_api(job.id, store=store,
                         reader=lambda *_: {"complete": True, "matches": False,
                                            "observation": observation}) is None
    assert store.get(job.id).state == "verifying"
