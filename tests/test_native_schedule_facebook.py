from datetime import datetime, timedelta, timezone

import httpx
import pytest

from socialctl.brands import Brand
from socialctl.native_schedule.approval import hash_value, media_hashes, native_digest
from socialctl.native_schedule.facebook import schedule_facebook
from socialctl.native_schedule.models import NativeJob
from socialctl.native_schedule.store import NativeStore


def setup_job(tmp_path, *, format="feed", options=None):
    media = tmp_path / "asset.jpg"
    media.write_bytes(b"image")
    payload = {
        "copy": "Approved copy",
        "content_origin": "standalone",
        "source_video_id": None,
        "visibility": "public",
        "media": [{"path": str(media), "origin": "user"}],
        "first_comment": None,
        "options": {
            "format": format,
            "share_to_facebook_story": False,
            **(options or {}),
        },
    }
    now = datetime.now(timezone.utc)
    job = NativeJob(
        id="facebook-job",
        brand="Test",
        platform="facebook",
        account_id="page-123",
        publish_at=datetime(2035, 1, 1, 14, tzinfo=timezone.utc),
        dispatch_after=now,
        timezone_name="America/New_York",
        route="api",
        created_at=now,
        updated_at=now,
        content_hash=hash_value(payload),
        media_hash=hash_value(media_hashes(payload)),
    )
    brand = Brand(nombre="Test", raiz=tmp_path, cuentas={"facebook": {"page_id": "page-123"}})
    store = NativeStore(tmp_path, brand="Test")
    store.prepare(job, payload=payload)
    job = store.transition(
        job.id, "prepared", "approved", approval_digest=native_digest(job, payload)
    )
    return job, payload, brand, store


@pytest.mark.parametrize(
    ("format", "options"),
    [
        ("feed", {}),
        ("photo", {}),
        ("reel", {"remote_type": "PagePost"}),
        ("feed", {"simulated_response": {"id": "post", "status": 200}}),
        ("feed", {"simulated_response": {"id": "post"}, "fail_after_acceptance": True}),
        ("feed", {"permission": "lost"}),
    ],
)
def test_unconfirmed_contract_always_requires_new_ui_approval_without_network(
    tmp_path, format, options
):
    job, payload, brand, store = setup_job(tmp_path, format=format, options=options)

    def network(request):
        pytest.fail(f"Facebook native gate made a network request: {request.method} {request.url}")

    result = schedule_facebook(
        job,
        payload,
        brand=brand,
        store=store,
        client=httpx.Client(transport=httpx.MockTransport(network)),
    )

    assert result.state == "ui_required"
    assert result.source == "api"
    assert result.calendar_visible is False
    assert "Business Suite" in result.reason
    assert "route=ui" in result.reason
    assert result.remote_id is None
    assert store.get(job.id).state == "approved"
    assert store.get(job.id).route == "api"
    assert store.get_attempts(job.id) == []


def test_gate_requires_exact_explicit_facebook_context(tmp_path):
    job, payload, brand, store = setup_job(tmp_path)
    wrong_brand = Brand(
        nombre="Test", raiz=tmp_path, cuentas={"facebook": {"page_id": "another-page"}}
    )
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: pytest.fail("network"))
    )

    with pytest.raises(ValueError, match="brand/account/store mismatch"):
        schedule_facebook(
            job, payload, brand=wrong_brand, store=store, client=client
        )


def test_gate_rejects_changed_payload_before_ui_handoff(tmp_path):
    job, payload, brand, store = setup_job(tmp_path)
    changed = {**payload, "copy": "Changed after approval"}
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: pytest.fail("network"))
    )

    with pytest.raises(ValueError, match="Stale job or changed stored payload"):
        schedule_facebook(
            job, changed, brand=brand, store=store, client=client
        )


def test_eligible_reel_records_remote_id_but_requires_calendar_evidence(tmp_path, monkeypatch):
    from socialctl.native_schedule import facebook
    from socialctl.native_schedule.store import _replace
    from zoneinfo import ZoneInfo
    job, payload, brand, store = setup_job(tmp_path, format='reel')
    job = _replace(job, publish_at=(datetime.now(ZoneInfo('America/New_York')) + timedelta(days=2)).replace(hour=9, minute=0, second=0))
    # Native Graph schedules use integer seconds, explicitly approved as such.
    job = _replace(job, publish_at=job.publish_at.replace(microsecond=0))
    job = _replace(job, approval_digest=native_digest(job, payload))
    with store._db(write=True) as db:
        store._save(db, job)
    monkeypatch.setattr(facebook, 'obtener_token', lambda *args: 'test-token')
    writes = []
    def network(request):
        if request.method == 'GET' and request.url.path.endswith('/me'):
            return httpx.Response(200, json={'id': 'page-123'})
        if request.method == 'GET':
            return httpx.Response(200, json={'id': 'video-123', 'from': {'id': 'page-123'},
                'description': 'Approved copy', 'published': False,
                'scheduled_publish_time': job.publish_at.isoformat()})
        writes.append(request.url.host)
        if request.url.host == 'rupload.facebook.com':
            assert request.read() == b'image'
            return httpx.Response(200, json={'success': True})
        form = dict(httpx.QueryParams(request.read().decode()))
        if form['upload_phase'] == 'START':
            return httpx.Response(200, json={'video_id': 'video-123',
                'upload_url': 'https://rupload.facebook.com/video-upload/v26.0/video-123'})
        assert form['video_state'] == 'SCHEDULED'
        return httpx.Response(200, json={'success': True})
    client = httpx.Client(transport=httpx.MockTransport(network))
    eligibility = facebook.FacebookEligibility(account_id='page-123', api_enabled=True,
        page_pilot_verified=True, evidence_ref='fictional-pilot')
    result = schedule_facebook(job, payload, brand=brand, store=store, client=client, eligibility=eligibility)
    assert result.state == 'pending_verification'
    assert result.calendar_visible is False
    assert store.get(job.id).state == 'verifying'
    assert store.get(job.id).remote_id == 'video-123'
    assert len(writes) == 3
    again = schedule_facebook(store.get(job.id), payload, brand=brand, store=store, client=client, eligibility=eligibility)
    assert again.state == 'uncertain'
    assert len(writes) == 3
