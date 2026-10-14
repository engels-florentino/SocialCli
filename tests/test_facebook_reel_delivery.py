import httpx
import pytest
from datetime import datetime, timedelta, timezone

from socialctl.native_schedule.facebook_reels import FacebookReelDelivery, read_reel
from tests.test_api_dispatch import api_job


NOW = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)


def test_native_reel_uploads_now_but_finishes_scheduled_not_published(tmp_path):
    store, job = api_job(tmp_path)
    job = job.model_copy(update={'publish_at': NOW + timedelta(days=2)})
    payload = store.get_payload(job.id) | {'delivery_mode': 'native_schedule'}
    phases = []
    def handler(request):
        if request.method == 'GET':
            assert 'scheduled_publish_time' in request.url.params['fields']
            return httpx.Response(200, json={'id': '12345', 'from': {'id': job.account_id},
                'description': payload['copy'], 'published': False,
                'scheduled_publish_time': '2026-10-15T12:00:00+0000', 'status': {}})
        if request.url.host == 'rupload.facebook.com':
            assert request.read() == b'exact approved bytes'
            return httpx.Response(200, json={'success': True})
        form = dict(httpx.QueryParams(request.read().decode()))
        phases.append(form['upload_phase'])
        if form['upload_phase'] == 'START':
            return httpx.Response(200, json={'video_id': '12345',
                'upload_url': 'https://rupload.facebook.com/video-upload/v26.0/12345'})
        assert form['video_state'] == 'SCHEDULED'
        assert form['scheduled_publish_time'] == '1792065600'
        return httpx.Response(200, json={'success': True})
    checkpoints = []
    result = FacebookReelDelivery(httpx.Client(transport=httpx.MockTransport(handler)),
        'test-token', clock=lambda: NOW).deliver(job, payload, checkpoints.append)
    assert result == '12345'
    assert phases == ['START', 'FINISH']
    assert checkpoints[-1]['phase'] == 'schedule_verified'


@pytest.mark.parametrize('minutes', [-1, 0, 10, 29 * 24 * 60])
def test_native_reel_invalid_time_never_starts_an_upload(tmp_path, minutes):
    store, job = api_job(tmp_path)
    job = job.model_copy(update={'publish_at': NOW + timedelta(minutes=minutes)})
    payload = store.get_payload(job.id) | {'delivery_mode': 'native_schedule'}
    client = httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail('unexpected request')))
    with pytest.raises(ValueError, match='schedule window'):
        FacebookReelDelivery(client, 'test-token', clock=lambda: NOW).deliver(job, payload, lambda _: None)


@pytest.mark.parametrize('published,stamp', [(True, '2026-10-15T12:00:00Z'),
    (False, '2026-10-16T12:00:00Z'), (False, None), (False, '2026-10-15T12:00:00')])
def test_schedule_readback_never_accepts_wrong_time_or_public_video(published, stamp):
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={
        'id': '12345', 'from': {'id': 'page'}, 'description': 'copy',
        'published': published, 'scheduled_publish_time': stamp})))
    with pytest.raises(ValueError, match='schedule'):
        read_reel('12345', 'page', client, 'test-token', expected_description='copy',
                  expected_publish_at=NOW + timedelta(days=2))


def test_slow_upload_never_falls_back_to_immediate_publication(tmp_path):
    store, job = api_job(tmp_path)
    job = job.model_copy(update={'publish_at': NOW + timedelta(minutes=11)})
    payload = store.get_payload(job.id) | {'delivery_mode': 'native_schedule'}
    clock = [NOW]
    calls = []
    def network(request):
        calls.append(request.url.host)
        if request.url.host == 'rupload.facebook.com':
            clock[0] = NOW + timedelta(minutes=2)
            return httpx.Response(200, json={'success': True})
        form = dict(httpx.QueryParams(request.read().decode()))
        assert form['upload_phase'] == 'START', 'Expired upload must never FINISH as PUBLISHED'
        return httpx.Response(200, json={'video_id': '12345',
            'upload_url': 'https://rupload.facebook.com/video-upload/v26.0/12345'})
    with pytest.raises(ValueError, match='schedule window'):
        FacebookReelDelivery(httpx.Client(transport=httpx.MockTransport(network)),
            'test-token', clock=lambda: clock[0]).deliver(job, payload, lambda _: None)
    assert len(calls) == 2


def test_reel_uses_start_binary_upload_and_finish_on_page(tmp_path):
    store, job = api_job(tmp_path)
    payload = store.get_payload(job.id)
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith('/video_reels'):
            form = dict(httpx.QueryParams(request.read().decode()))
            if form['upload_phase'] == 'START':
                return httpx.Response(200, json={
                    'video_id': '12345',
                    'upload_url': 'https://rupload.facebook.com/video-upload/v26.0/12345',
                })
            assert form['upload_phase'] == 'FINISH'
            assert form['video_id'] == '12345'
            assert form['video_state'] == 'PUBLISHED'
            assert form['description'] == payload['copy']
            return httpx.Response(200, json={'success': True})
        assert request.url.host == 'rupload.facebook.com'
        assert request.headers['authorization'] == 'OAuth test-token'
        assert request.headers['offset'] == '0'
        assert request.headers['file_size'] == str(len(b'exact approved bytes'))
        assert request.read() == b'exact approved bytes'
        return httpx.Response(200, json={'success': True})

    checkpoints = []
    client = httpx.Client(transport=httpx.MockTransport(handler))
    remote_id = FacebookReelDelivery(client, 'test-token').deliver(
        job, payload, checkpoints.append)
    assert remote_id == '12345'
    assert [value['phase'] for value in checkpoints] == [
        'start_requested', 'started', 'upload_requested', 'uploaded', 'finish_requested']
    assert len(calls) == 3


def test_untrusted_upload_url_receives_no_token_or_video(tmp_path):
    store, job = api_job(tmp_path)
    payload = store.get_payload(job.id)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={
            'video_id': '12345', 'upload_url': 'https://evil.example/upload/12345'})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match='upload URL'):
        FacebookReelDelivery(client, 'test-token').deliver(job, payload, lambda _: None)
    assert len(calls) == 1


def test_reel_readback_requires_page_ownership():
    client = httpx.Client(transport=httpx.MockTransport(lambda request:
        httpx.Response(200, json={'id': '12345', 'from': {'id': 'other-page'},
                                  'description': 'copy'})))
    with pytest.raises(ValueError, match='another Page'):
        read_reel('12345', 'our-page', client, 'test-token', expected_description='copy')


def test_reel_readback_requires_approved_description():
    client = httpx.Client(transport=httpx.MockTransport(lambda request:
        httpx.Response(200, json={'id': '12345', 'from': {'id': 'our-page'},
                                  'description': 'different', 'status': {}})))
    with pytest.raises(ValueError, match='description'):
        read_reel('12345', 'our-page', client, 'test-token', expected_description='copy')
