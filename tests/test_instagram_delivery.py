from pathlib import Path

import httpx
import pytest

from socialctl.native_schedule.instagram_delivery import InstagramDelivery, read_media
from tests.test_api_dispatch import api_job


def test_reel_uses_resumable_local_video_and_publishes(tmp_path):
    store, job = api_job(tmp_path, platform='instagram')
    payload = store.get_payload(job.id)
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == 'POST' and request.url.path == '/v26.0/account/media':
            form = dict(httpx.QueryParams(request.read().decode()))
            assert form['media_type'] == 'REELS'
            assert form['upload_type'] == 'resumable'
            return httpx.Response(200, json={
                'id': 'container-1',
                'uri': 'https://rupload.facebook.com/ig-api-upload/v26.0/container-1'})
        if request.url.host == 'rupload.facebook.com':
            assert request.headers['authorization'] == 'OAuth test-token'
            assert request.read() == b'exact approved bytes'
            return httpx.Response(200, json={'success': True})
        if request.method == 'GET' and request.url.path.endswith('/container-1'):
            return httpx.Response(200, json={'status_code': 'FINISHED'})
        if request.method == 'POST' and request.url.path.endswith('/media_publish'):
            form = dict(httpx.QueryParams(request.read().decode()))
            assert form['creation_id'] == 'container-1'
            return httpx.Response(200, json={'id': 'media-1'})
        raise AssertionError(request.url)

    checkpoints = []
    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert InstagramDelivery(client, 'test-token').deliver(
        job, payload, checkpoints.append) == 'media-1'
    assert [stage['phase'] for stage in checkpoints] == [
        'container_requested', 'container_created', 'upload_requested',
        'uploaded', 'container_ready', 'publish_requested']
    assert len(calls) == 4


def test_failed_carousel_child_never_creates_parent(tmp_path):
    store, job = api_job(tmp_path, platform='instagram')
    payload = store.get_payload(job.id)
    payload['options']['format'] = 'carousel'
    payload['media'] = [
        {'path': str(tmp_path / 'one.jpg'), 'origin': 'user_supplied',
         'public_url': 'https://cdn.example/one.jpg'},
        {'path': str(tmp_path / 'two.jpg'), 'origin': 'user_supplied',
         'public_url': 'https://cdn.example/two.jpg'},
    ]
    for item in payload['media']:
        Path(item['path']).write_bytes(b'jpg')
    posts = []

    def handler(request):
        if request.method == 'POST' and request.url.path.endswith('/media'):
            posts.append(request)
            if len(posts) == 2:
                return httpx.Response(400, json={'error': 'bad child'})
            return httpx.Response(200, json={'id': 'child-1'})
        if request.method == 'GET':
            if request.url.host == 'cdn.example':
                return httpx.Response(200, content=b'jpg')
            return httpx.Response(200, json={'status_code': 'FINISHED'})
        raise AssertionError(request.url)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        InstagramDelivery(client, 'test-token',
                          allowed_media_hosts=frozenset({'cdn.example'})).deliver(
                              job, payload, lambda _: None)
    assert len(posts) == 2
    assert all('CAROUSEL' not in request.read().decode() for request in posts)


def test_hosted_image_must_match_original_before_container_creation(tmp_path):
    store, job = api_job(tmp_path, platform='instagram')
    payload = store.get_payload(job.id)
    payload['options']['format'] = 'image'
    payload['media'][0]['public_url'] = 'https://cdn.example/image.jpg'
    posts = []

    def handler(request):
        if request.method == 'POST':
            posts.append(request)
        return httpx.Response(200, content=b'different bytes')

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match='differs'):
        InstagramDelivery(client, 'test-token',
                          allowed_media_hosts=frozenset({'cdn.example'})).deliver(
                              job, payload, lambda _: None)
    assert posts == []


def test_hosted_image_requires_explicit_approved_host(tmp_path):
    store, job = api_job(tmp_path, platform='instagram')
    payload = store.get_payload(job.id)
    payload['options']['format'] = 'image'
    payload['media'][0]['public_url'] = 'https://cdn.example/image.jpg'
    client = httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail('network')))
    with pytest.raises(ValueError, match='host'):
        InstagramDelivery(client, 'test-token').deliver(job, payload, lambda _: None)


def test_story_rejects_caption_that_api_cannot_publish(tmp_path):
    store, job = api_job(tmp_path, platform='instagram')
    payload = store.get_payload(job.id)
    payload['options']['format'] = 'story'
    client = httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail('network')))
    with pytest.raises(ValueError, match='caption'):
        InstagramDelivery(client, 'test-token').deliver(job, payload, lambda _: None)


def test_story_without_caption_uses_resumable_upload(tmp_path):
    store, job = api_job(tmp_path, platform='instagram')
    payload = store.get_payload(job.id)
    payload['options']['format'] = 'story'
    payload['copy'] = ''

    def handler(request):
        if request.url.path.endswith('/account/media'):
            form = dict(httpx.QueryParams(request.read().decode()))
            assert form['media_type'] == 'STORIES'
            assert 'caption' not in form
            return httpx.Response(200, json={
                'id': 'container-1',
                'uri': 'https://rupload.facebook.com/ig-api-upload/v26.0/container-1'})
        if request.url.host == 'rupload.facebook.com':
            return httpx.Response(200, json={'success': True})
        if request.method == 'GET':
            return httpx.Response(200, json={'status_code': 'FINISHED'})
        return httpx.Response(200, json={'id': 'media-1'})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert InstagramDelivery(client, 'test-token').deliver(
        job, payload, lambda _: None) == 'media-1'


def test_untrusted_instagram_upload_uri_receives_no_bytes(tmp_path):
    store, job = api_job(tmp_path, platform='instagram')
    payload = store.get_payload(job.id)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={
            'id': 'container-1', 'uri': 'https://evil.example/upload/container-1'})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match='upload URI'):
        InstagramDelivery(client, 'test-token').deliver(job, payload, lambda _: None)
    assert len(calls) == 1


def test_read_media_requires_exact_owner_and_caption():
    def handler(request):
        assert request.method == 'GET'
        assert request.url.path == '/v26.0/media-1'
        assert request.url.params['fields'] == 'id,owner,caption,media_type,permalink,timestamp'
        return httpx.Response(200, json={
            'id': 'media-1', 'owner': {'id': 'account'},
            'caption': 'Approved copy', 'media_type': 'VIDEO',
            'permalink': 'https://www.instagram.com/reel/example/',
            'timestamp': '2026-09-22T13:00:00+0000'})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    item = read_media('media-1', 'account', client, 'test-token',
                      expected_caption='Approved copy', expected_format='reel')
    assert item['id'] == 'media-1'
    with pytest.raises(ValueError, match='caption'):
        read_media('media-1', 'account', client, 'test-token',
                   expected_caption='Different', expected_format='reel')


def test_read_media_rejects_other_owner():
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200,
        json={'id': 'media-1', 'owner': {'id': 'other'}, 'caption': 'Approved copy',
              'media_type': 'VIDEO', 'permalink': 'https://www.instagram.com/p/example/'})))
    with pytest.raises(ValueError, match='account'):
        read_media('media-1', 'account', client, 'test-token',
                   expected_caption='Approved copy', expected_format='reel')
