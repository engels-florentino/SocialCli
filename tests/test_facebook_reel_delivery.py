import httpx
import pytest

from socialctl.native_schedule.facebook_reels import FacebookReelDelivery, read_reel
from tests.test_api_dispatch import api_job


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
