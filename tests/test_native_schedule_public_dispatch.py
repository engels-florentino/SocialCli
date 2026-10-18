"""Public workers must use the guarded native submission path, not an empty adapter map."""
import json
from hashlib import sha256
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest

from socialctl.native_schedule.store import NativeStore
from tests.test_native_schedule_cli import fixture, invoke, digest


def prepared(tmp_path, *, platform='facebook', first_comment=None, existing=False):
    brand, data = fixture(tmp_path)
    now = datetime.now(timezone.utc)
    data.update(route='api', dispatch_after=(now - timedelta(minutes=1)).isoformat(),
        publish_at=(now.astimezone(ZoneInfo('America/New_York')) + timedelta(days=2)).replace(
            hour=9, minute=0, second=0, microsecond=0).isoformat())
    # Success/uncertainty fixtures approve no first comment; comment obligations
    # are covered explicitly by the public pending-follow-up regressions below.
    data['payload']['first_comment'] = first_comment
    account = 'page-1' if platform == 'facebook' else 'channel'
    data.update(platform=platform, account_id=account)
    if platform == 'youtube':
        (brand/'accounts.yml').write_text('youtube:\n  channel_id: channel\n')
        data['payload']['options'] = dict(format='video', title='Title', categoryId='27', selfDeclaredMadeForKids=False)
        if existing: data['payload']['options'].update(video_id='abcdefghijk', never_published=True)
    elif first_comment:
        data['payload'].update(content_origin='youtube_long', source_video_id='M3zbWgbb4lo')
    (brand / 'native.json').write_text(json.dumps(data))
    inventory = json.loads((brand / 'native-calendar.json').read_text())
    inventory.update(coverage_start=(now-timedelta(days=1)).isoformat(), coverage_end=(now+timedelta(days=7)).isoformat(),
        accounts=[dict(platform=platform, account_id=account, complete=True)])
    (brand / 'native-calendar.json').write_text(json.dumps(inventory))
    (brand / 'pilot.txt').write_text('Fictional account-specific pilot evidence')
    (brand / 'eligibility').mkdir()
    proof = dict(version=1, platform=platform, account_id=account, api_enabled=True, evidence_ref='pilot.txt')
    proof.update({'page_pilot_verified': True} if platform == 'facebook' else
        {'project_verified': True, 'studio_pilot_verified': True})
    (brand / f'eligibility/{platform}.json').write_text(json.dumps(proof))
    approval = digest(invoke(tmp_path, 'prepare', 'native.json', '--dry-run').output)
    assert invoke(tmp_path, 'prepare', 'native.json', '--persist', '--approval-digest', approval).exit_code == 0
    assert invoke(tmp_path, 'approve', 'clip', '--approval-digest', approval).exit_code == 0
    return brand


@pytest.mark.parametrize('args', [
    ('dispatch', 'clip', '--eligibility', 'eligibility/facebook.json'),
    ('deliver', '--eligibility-dir', 'eligibility'),
])
def test_public_dispatch_records_uncertainty_and_never_repeats_upload(tmp_path, monkeypatch, args):
    from socialctl.native_schedule import facebook
    brand = prepared(tmp_path)
    monkeypatch.setattr(facebook, 'obtener_token', lambda *a: 'fictional')
    writes = []

    def request(client, method, url, **kwargs):
        req = httpx.Request(method, url)
        if method == 'GET':
            return httpx.Response(200, json={'id': 'page-1'}, request=req)
        writes.append(url)
        if 'rupload.facebook.com' in url:
            return httpx.Response(200, json={'success': True}, request=req)
        if kwargs['data']['upload_phase'] == 'START':
            return httpx.Response(200, json={'video_id': 'video-123',
                'upload_url': 'https://rupload.facebook.com/video-upload/v26.0/video-123'}, request=req)
        raise httpx.ReadTimeout('Fictional lost response', request=req)

    monkeypatch.setattr(httpx.Client, 'request', request)
    first = invoke(tmp_path, *args)
    store = NativeStore(brand, brand='Histopast')
    assert store.get('clip').state == 'uncertain', first.output
    assert first.exit_code != 0
    assert len(writes) == 3
    assert len(store.get_attempts('clip')) == 1
    invoke(tmp_path, *args)
    assert len(writes) == 3
    assert len(store.get_attempts('clip')) == 1


def test_public_worker_without_eligibility_never_sends(tmp_path, monkeypatch):
    brand = prepared(tmp_path)
    monkeypatch.setattr(httpx.Client, 'request', lambda *a, **k: pytest.fail('network without pilot evidence'))
    result = invoke(tmp_path, 'deliver')
    store = NativeStore(brand, brand='Histopast')
    assert result.exit_code != 0, result.output
    assert store.get_attempts('clip') == []
    assert store.get('clip').state == 'blocked'


@pytest.mark.parametrize('foreign', [False, True])
@pytest.mark.parametrize('granted', [False, True])
def test_instagram_public_due_worker_binds_account_and_uploads_original(tmp_path, monkeypatch, foreign, granted):
    from socialctl.native_schedule import cli
    from tests.test_api_dispatch import api_job
    from socialctl.brands import crear_brand
    from socialctl.models import Platform
    brand = crear_brand(tmp_path, 'Example')
    brand.cuentas = {'facebook': {'page_id': '123'}, 'instagram': {'ig_user_id': '456'}}
    brand.guardar_secreto(Platform.INSTAGRAM, {'access_token': 'fictional',
        **({'granted_scopes':['instagram_basic','instagram_content_publish','pages_read_engagement']} if granted else {})})
    original_store, job = api_job(tmp_path / 'fixture', platform='instagram')
    from socialctl.native_schedule.store import _replace
    from socialctl.native_schedule.approval import native_digest
    job = _replace(job, brand='Example', account_id='456', state='prepared', approval_digest=None)
    payload = original_store.get_payload(job.id)
    store = NativeStore(brand.raiz, brand='Example')
    store.prepare(job, payload=payload, now=job.created_at)
    job = store.transition(job.id, 'prepared', 'approved',
        approval_digest=native_digest(job, payload), now=job.created_at)
    (brand.raiz / 'pilot.txt').write_text('Fictional Instagram account/format pilot')
    (brand.raiz / 'pilot.json').write_text(json.dumps(dict(version=1, platform='instagram',
        account_id='456', delivery_mode='at_time', api_enabled=True, account_pilot_verified=True,
        formats=['reel'], allowed_media_hosts=[], evidence_ref='pilot.txt')))
    class Clock:
        @staticmethod
        def now(zone): return job.publish_at
    monkeypatch.setattr(cli, 'datetime', Clock)
    posts = []
    def request(client, method, url, **kwargs):
        req = httpx.Request(method, url)
        if method == 'GET' and url.endswith('/me'):
            return httpx.Response(200, json={'id': '123', 'instagram_business_account': {'id': '999' if foreign else '456'}}, request=req)
        if method == 'GET':
            return httpx.Response(200, json={'status_code': 'FINISHED'}, request=req)
        posts.append(url)
        if 'rupload.facebook.com' in url:
            assert b''.join(kwargs['content']) == b'exact approved bytes'
            return httpx.Response(200, json={'success': True}, request=req)
        if url.endswith('/media_publish'):
            return httpx.Response(200, json={'id': '789'}, request=req)
        return httpx.Response(200, json={'id': 'container-1',
            'uri': 'https://rupload.facebook.com/ig-api-upload/v26.0/container-1'}, request=req)
    monkeypatch.setattr(httpx.Client, 'request', request)
    # MetaClient uses streaming reads; intercept its provider transport too.
    monkeypatch.setattr('socialctl.management.meta_client.MetaClient.identity',
        lambda self: (_ for _ in ()).throw(ValueError('linked account mismatch')) if foreign else
        {'actor_id': '123', 'token_mode': 'facebook_page', 'page_id': '123', 'account_id': '456'})
    result = cli.dispatch_local(brand, store, job_id=job.id, eligibility='pilot.json')
    denied=foreign or not granted
    assert store.get(job.id).state == ('blocked' if denied else 'verifying')
    assert len(posts) == (0 if denied else 3)
    assert result == ([] if denied else [job.id])
    cli.dispatch_local(brand, store, job_id=job.id, eligibility='pilot.json')
    assert len(posts) == (0 if denied else 3)


@pytest.mark.parametrize('actual',[None,[],['instagram_basic'],['instagram_basic','instagram_content_publish','pages_read_engagement']])
def test_instagram_broker_delivery_requires_returned_grants(tmp_path,monkeypatch,actual):
    from socialctl.brands import crear_brand
    from socialctl.models import Platform
    from socialctl.connections import client
    brand=crear_brand(tmp_path,'Example')
    brand.cuentas={'facebook':{'page_id':'123'},'instagram':{'ig_user_id':'456'}}
    brand.guardar_secreto(Platform.INSTAGRAM,{'version':1,'auth_mode':'broker','protocol':'saas-v1',
        'service_url':'https://social.example','connection_id':'fictional-connection','account_id':'456'})
    required={'instagram_basic','instagram_content_publish','pages_read_engagement'}
    class Broker:
        def request(self,*args,**kwargs):
            assert args[1].endswith('/token?refresh=false')
            return {'platform':'instagram','account':{'id':'456'},'access_token':'private-token',
                    'expires_at':2000000000,'granted_scopes':actual}
    monkeypatch.setattr(client,'broker_for',lambda *a:Broker())
    monkeypatch.setattr(client.keychain,'get',lambda *a:'fictional-connection-secret')
    if actual==sorted(required) or actual is not None and set(actual)==required:
        assert client.broker_access_token(brand,Platform.INSTAGRAM,None,refresh=False,required_scopes=required)=='private-token'
    else:
        with pytest.raises(ValueError,match='granted'):
            client.broker_access_token(brand,Platform.INSTAGRAM,None,refresh=False,required_scopes=required)


def test_losing_native_worker_does_not_change_another_workers_claim(tmp_path):
    from socialctl.native_schedule.dispatcher import dispatch_ready
    from tests.test_native_schedule_handoff import approved, NOW
    from socialctl.native_schedule.store import _replace
    from socialctl.native_schedule.approval import native_digest
    store, job, payload, _ = approved(tmp_path)
    job = _replace(job, route='api')
    job = _replace(job, approval_digest=native_digest(job, payload))
    with store._db(write=True) as db:
        store._save(db, job)
    def competing_submit(stale_job, payload):
        assert store.claim(job.id, 'approved', now=NOW) is not None
        raise ValueError('Another worker acquired the claim before this submitter')
    dispatch_ready(NOW, store.brand, store=store, native_submit=competing_submit)
    assert store.get(job.id).state == 'dispatching'
    assert len(store.get_attempts(job.id)) == 1


@pytest.mark.parametrize('args', [
    ['dispatch', 'clip', '--eligibility', 'eligibility/facebook.json'],
    ['dispatch', 'clip', '--yes', '--eligibility', 'pilot.json'],
    ['deliver', '--eligibility-dir', 'eligibility'],
])
def test_remote_grammar_preserves_bounded_eligibility_arguments(tmp_path, monkeypatch, args):
    from socialctl import schedule_remote
    sent = []
    monkeypatch.setattr(schedule_remote, '_send', lambda root, brand, argv: sent.append(argv))
    schedule_remote.send_native(tmp_path, 'Example', args + ['--json'])
    assert sent == [['native-schedule', *args, '--json']]


@pytest.mark.parametrize('path', ['../pilot.json', '/tmp/pilot.json', '.secrets/pilot.json',
    'eligible/../../pilot.json', 'eligible;echo x', 'eligibility//pilot.json'])
def test_remote_eligibility_rejects_unsafe_paths(tmp_path, monkeypatch, path):
    from socialctl import schedule_remote
    monkeypatch.setattr(schedule_remote, '_send', lambda *a: pytest.fail('unsafe remote send'))
    with pytest.raises(ValueError):
        schedule_remote.send_native(tmp_path, 'Example', ['dispatch', 'clip', '--eligibility', path])


def instagram_manifest(tmp_path, monkeypatch, *, fmt='carousel', first_comment=None, derived=False):
    """Ordinary manifest preview, persist, approve and dispatch with a fictional clock."""
    from socialctl.native_schedule import cli
    from socialctl.brands import cargar_brand
    from socialctl.models import Platform
    brand, data = fixture(tmp_path)
    (brand/'accounts.yml').write_text('facebook:\n  page_id: "123"\ninstagram:\n  ig_user_id: "456"\n')
    cargar_brand(tmp_path, 'Histopast').guardar_secreto(Platform.INSTAGRAM, {
        'access_token': 'fictional',
        'granted_scopes': ['instagram_basic', 'instagram_content_publish', 'pages_read_engagement']})
    instant = datetime(2035, 1, 1, 14, tzinfo=timezone.utc)
    moment = [instant-timedelta(hours=1)]
    class Clock(datetime):
        @classmethod
        def now(cls, zone): return moment[0].astimezone(zone)
    monkeypatch.setattr(cli, 'datetime', Clock)
    monkeypatch.setattr('socialctl.native_schedule.store.datetime', Clock)
    data.update(platform='instagram', account_id='456', route='api',
        publish_at=instant.isoformat(), dispatch_after=(instant-timedelta(hours=1)).isoformat(),
        schedule={'mode': 'exact', 'timezone': 'UTC'})
    data['payload'].update(delivery_mode='at_time', options={'format': fmt}, first_comment=first_comment)
    if derived:
        data['payload'].update(content_origin='youtube_long', source_video_id='M3zbWgbb4lo')
    if fmt == 'carousel':
        data['payload']['media'] = [
            {'path': name, 'origin': 'user-provided', 'public_url': 'https://cdn.example/'+name}
            for name in ('one.jpg', 'two.mp4', 'three.jpg')]
        for item, content in zip(data['payload']['media'], (b'first approved image', b'exact approved video', b'last approved image')):
            (brand/item['path']).write_bytes(content)
    (brand/'native.json').write_text(json.dumps(data))
    (brand/'native-calendar.json').write_text(json.dumps(dict(observed_at=moment[0].isoformat(),
        coverage_start='2035-01-01T00:00:00Z', coverage_end='2035-01-02T00:00:00Z',
        accounts=[dict(platform='instagram', account_id='456', complete=True)], objects=[])))
    (brand/'pilot.txt').write_text('Fictional account/format evidence; no production approval')
    (brand/'pilot.json').write_text(json.dumps(dict(version=1, platform='instagram', account_id='456',
        delivery_mode='at_time', api_enabled=True, account_pilot_verified=True, formats=[fmt],
        allowed_media_hosts=['cdn.example'], evidence_ref='pilot.txt')))
    preview = invoke(tmp_path, 'prepare', 'native.json', '--dry-run')
    assert preview.exit_code == 0, preview.output
    document, _ = json.JSONDecoder().raw_decode(preview.output)
    assert [item['path'] for item in document['payload']['media']] == [str(brand/item['path']) for item in data['payload']['media']]
    assert document['media'] == [dict(path=str(brand/item['path']),
        sha256=sha256((brand/item['path']).read_bytes()).hexdigest()) for item in data['payload']['media']]
    approval = digest(preview.output)
    assert invoke(tmp_path, 'prepare', 'native.json', '--persist', '--approval-digest', approval).exit_code == 0
    assert invoke(tmp_path, 'approve', 'clip', '--approval-digest', approval).exit_code == 0
    moment[0] = instant
    monkeypatch.setattr('socialctl.management.meta_client.MetaClient.identity', lambda self:
        {'actor_id': '123', 'token_mode': 'facebook_page', 'page_id': '123', 'account_id': '456'})
    return brand, NativeStore(brand, brand='Histopast'), moment


@pytest.mark.parametrize('derived', [False, True])
def test_public_instagram_first_comment_pending_before_any_mutation(tmp_path, monkeypatch, derived):
    comment = '🎥 Video completo en https://www.youtube.com/watch?v=M3zbWgbb4lo' if derived else 'Approved first comment'
    brand, store, moment = instagram_manifest(tmp_path, monkeypatch, fmt='reel', first_comment=comment, derived=derived)
    writes = []
    def request(client, method, url, **kwargs):
        if method == 'POST': writes.append(url)
        return httpx.Response(200, json={'id': 'container-1', 'uri': 'https://rupload.facebook.com/ig-api-upload/v26.0/container-1',
            'success': True, 'status_code': 'FINISHED'}, request=httpx.Request(method, url))
    monkeypatch.setattr(httpx.Client, 'request', request)
    result = invoke(tmp_path, 'dispatch', 'clip', '--eligibility', 'pilot.json')
    assert store.get('clip').state == 'blocked', result.output
    assert writes == []
    assert store.get_attempts('clip') == []
    assert any('first-comment delivery pending' in notice['message'] for notice in store.get_notices('clip'))


@pytest.mark.parametrize('variant', ['success', 'failed_child', 'changed_hosted_bytes', 'expired_url',
    'ambiguous_publish', 'post_preview_media_change', 'future', 'expired_window', 'deadline_during_processing'])
def test_public_instagram_carousel_preserves_preview_order_bytes_and_failures(tmp_path, monkeypatch, variant):
    brand, store, moment = instagram_manifest(tmp_path, monkeypatch)
    if variant == 'post_preview_media_change': (brand/'two.mp4').write_bytes(b'changed after approval')
    if variant == 'future': moment[0] -= timedelta(seconds=1)
    if variant == 'expired_window': moment[0] += timedelta(minutes=5)
    calls, writes, children, hosted = [], [], [], []
    def request(client, method, url, **kwargs):
        calls.append((method, url))
        req = httpx.Request(method, url)
        if method == 'GET':
            if 'cdn.example' in url:
                hosted.append(url)
                if variant == 'expired_url': return httpx.Response(403, request=req)
                content = (brand/url.rsplit('/', 1)[1]).read_bytes()
                if variant == 'changed_hosted_bytes': content = b'changed hosted original'
                return httpx.Response(200, content=content, request=req)
            if variant == 'deadline_during_processing': moment[0] += timedelta(minutes=5)
            return httpx.Response(200, json={'status_code': 'FINISHED'}, request=req)
        writes.append((url, kwargs.get('data')))
        if 'rupload.facebook.com' in url:
            assert b''.join(kwargs['content']) == b'exact approved video'
            return httpx.Response(200, json={'success': True}, request=req)
        data = kwargs['data']
        if url.endswith('/media_publish'):
            assert data['creation_id'] == 'parent'
            if variant == 'ambiguous_publish': raise httpx.ReadTimeout('fictional lost publication response', request=req)
            return httpx.Response(200, json={'id': 'published'}, request=req)
        assert url.endswith('/456/media')
        if data.get('media_type') == 'CAROUSEL':
            assert data['children'] == 'child-1,child-2,child-3'
            assert len(children) == 3
            return httpx.Response(200, json={'id': 'parent'}, request=req)
        children.append(data)
        if variant == 'failed_child' and len(children) == 2:
            return httpx.Response(400, json={'error': 'fictional failed child'}, request=req)
        identifier = 'child-'+str(len(children))
        if data.get('upload_type') == 'resumable':
            assert len(children) == 2 and data['media_type'] == 'VIDEO'
            return httpx.Response(200, json={'id': identifier,
                'uri': 'https://rupload.facebook.com/ig-api-upload/v26.0/'+identifier}, request=req)
        assert data['image_url'] == ['https://cdn.example/one.jpg', 'https://cdn.example/three.jpg'][len(hosted)-1]
        return httpx.Response(200, json={'id': identifier}, request=req)
    monkeypatch.setattr(httpx.Client, 'request', request)
    mock_request = request
    def send(client, request, **kwargs):
        return mock_request(client, request.method, str(request.url))
    monkeypatch.setattr(httpx.Client, 'send', send)
    first = invoke(tmp_path, 'dispatch', 'clip', '--eligibility', 'pilot.json')
    job = store.get('clip')
    if variant == 'success':
        assert job.state == 'verifying', (first.output, calls, writes, store.get_attempts('clip'))
        assert len(writes) == 6 and len(children) == 3
        assert hosted == ['https://cdn.example/one.jpg', 'https://cdn.example/three.jpg']
    elif variant in {'post_preview_media_change', 'future', 'expired_window'}:
        assert not calls and not store.get_attempts('clip'), first.output
        assert job.state == ('blocked' if variant == 'expired_window' else 'approved')
    else:
        assert job.state == 'uncertain', first.output
        assert not any(url.endswith('/media_publish') for url, _ in writes) or variant == 'ambiguous_publish'
        if variant == 'failed_child':
            assert len(children) == 2 and len(writes) == 2
            assert not any(data and data.get('media_type') == 'CAROUSEL' for _, data in writes)
        if variant in {'changed_hosted_bytes', 'expired_url'}:
            assert writes == [] and hosted == ['https://cdn.example/one.jpg']
        if variant == 'ambiguous_publish':
            assert len(writes) == 6 and sum(url.endswith('/media_publish') for url, _ in writes) == 1
            assert store.get_attempts('clip')[0]['checkpoint']['phase'] == 'publish_requested'
        if variant == 'deadline_during_processing':
            assert len(children) == 3 and len(writes) == 5
            assert store.get_attempts('clip')[0]['checkpoint']['phase'] == 'publish_requested'
    count = len(writes)
    invoke(tmp_path, 'dispatch', 'clip', '--eligibility', 'pilot.json')
    assert len(writes) == count


@pytest.mark.parametrize('command', ['submit-api', 'dispatch', 'deliver'])
@pytest.mark.parametrize('platform,existing', [('facebook', False), ('youtube', False), ('youtube', True)])
@pytest.mark.parametrize('with_comment', [False, True])
def test_public_native_first_comment_pending_preserves_approval_without_claim(tmp_path, monkeypatch, command, platform, existing, with_comment):
    from tests.test_native_schedule_youtube import resource
    from socialctl.native_schedule import facebook
    comment = ('🎥 Video completo en https://www.youtube.com/watch?v=M3zbWgbb4lo' if platform == 'facebook'
               else 'Approved first comment') if with_comment else None
    brand = prepared(tmp_path, platform=platform, first_comment=comment, existing=existing)
    store = NativeStore(brand, brand='Histopast')
    approved = store.get('clip')
    payload = store.get_payload('clip')
    assert payload['first_comment'] == comment
    monkeypatch.setattr(facebook, 'obtener_token', lambda *a: 'fictional')
    monkeypatch.setattr('socialctl.management.youtube.obtener_token', lambda *a: 'fictional')
    writes = []
    def send(client, request, **kwargs):
        if request.method != 'GET': writes.append(request.method)
        if 'googleapis.com' in request.url.host:
            if request.url.path.endswith('/channels'):
                return httpx.Response(200, json={'items': [{'id': 'channel'}]}, request=request)
            if request.method == 'POST':
                return httpx.Response(200, headers={'Location': 'https://www.googleapis.com/upload/session'}, request=request)
            if request.method == 'PUT': return httpx.Response(200, json={'id': 'abcdefghijk'}, request=request)
            item = resource(payload, when=approved.publish_at.isoformat())
            return httpx.Response(200, json={'items': [item]}, request=request)
        if request.method == 'GET':
            item = ({'id': 'page-1'} if request.url.path.endswith('/me') else
                {'id': 'video-123', 'from': {'id': 'page-1'}, 'description': payload['copy'],
                 'published': False, 'scheduled_publish_time': approved.publish_at.isoformat()})
            return httpx.Response(200, json=item, request=request)
        if 'rupload.facebook.com' in request.url.host:
            assert request.read() == b'video'
            return httpx.Response(200, json={'success': True}, request=request)
        form = dict(httpx.QueryParams(request.read().decode()))
        if form['upload_phase'] == 'START':
            return httpx.Response(200, json={'video_id': 'video-123',
                'upload_url': 'https://rupload.facebook.com/video-upload/v26.0/video-123'}, request=request)
        return httpx.Response(200, json={'success': True}, request=request)
    monkeypatch.setattr(httpx.Client, 'send', send)
    args = (['deliver', '--eligibility-dir', 'eligibility'] if command == 'deliver' else
        [command, 'clip', '--eligibility', f'eligibility/{platform}.json'])
    result = invoke(tmp_path, *args)
    assert store.get_payload('clip') == payload
    assert store.get('clip').approval_digest == approved.approval_digest
    if with_comment:
        assert result.exit_code == 1, result.output
        assert writes == [] and store.get_attempts('clip') == [], result.output
        if command == 'submit-api':
            assert json.loads(result.stdout)['state'] == 'ui_required'
            assert 'first-comment delivery pending durable follow-up' in result.output
            assert store.get('clip').state == 'approved'
        else:
            assert store.get('clip').state == 'blocked'
            assert any('first-comment delivery pending durable follow-up' in note['message']
                for note in store.get_notices('clip'))
    else:
        assert result.exit_code == 0, result.output
        assert store.get('clip').state == 'verifying'
        assert len(store.get_attempts('clip')) == 1 and writes


@pytest.mark.parametrize('command', ['submit-api', 'dispatch', 'deliver'])
@pytest.mark.parametrize('platform', ['facebook', 'youtube'])
def test_public_native_historical_comment_attempt_remains_uncertain_without_replay(tmp_path, monkeypatch, command, platform):
    comment = ('🎥 Video completo en https://www.youtube.com/watch?v=M3zbWgbb4lo' if platform == 'facebook'
               else 'Historical approved first comment')
    brand = prepared(tmp_path, platform=platform, first_comment=comment)
    store = NativeStore(brand, brand='Histopast')
    payload = store.get_payload('clip')
    claimed = store.claim('clip', 'approved')
    store.checkpoint('clip', claimed.attempt_id, {'video_id': 'video-123' if platform == 'facebook' else 'abcdefghijk'})
    store.transition('clip', 'dispatching', 'uncertain')
    attempts = store.get_attempts('clip')
    monkeypatch.setattr(httpx.Client, 'send', lambda *a, **k: pytest.fail('historical attempt must not resend'))
    args = (['deliver', '--eligibility-dir', 'eligibility'] if command == 'deliver' else
        [command, 'clip', '--eligibility', f'eligibility/{platform}.json'])
    result = invoke(tmp_path, *args)
    assert result.exit_code == 1, result.output
    assert store.get('clip').state == 'uncertain'
    assert store.get_payload('clip') == payload
    assert store.get_attempts('clip') == attempts
    assert 'first-comment delivery pending' not in result.output
