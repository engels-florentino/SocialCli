"""Fictional public worker and durable scheduled comment regressions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest
from typer.testing import CliRunner

from socialctl.brands import crear_brand
from socialctl.models import Platform
from socialctl.native_schedule.approval import hash_value, media_hashes, native_digest
from socialctl.native_schedule.models import NativeJob
from socialctl.native_schedule.store import NativeStore

NOW = datetime(2035, 1, 1, 14, tzinfo=timezone.utc)
TEXT = 'Exact approved text 🎥\nhttps://www.youtube.com/watch?v=abcdefghijk'


def setup_job(tmp_path, monkeypatch, *, platform='instagram', comment=TEXT):
    from socialctl.native_schedule import cli
    brand = crear_brand(tmp_path, 'Example')
    brand.cuentas = {'facebook': {'page_id': '123'}, 'instagram': {'ig_user_id': '456'},
                    'youtube': {'channel_id': 'channel'}}
    import yaml
    (brand.raiz / "accounts.yml").write_text(yaml.safe_dump(brand.cuentas))
    brand.guardar_secreto(Platform.INSTAGRAM, {'access_token': 'fictional'})
    video = brand.raiz / 'supplied.mp4'
    video.write_bytes(b'fictional supplied original')
    payload = dict(calendar='fictional exact inventory', visibility='public', copy='Approved copy',
        content_origin='standalone', source_video_id=None, first_comment=comment,
        delivery_mode='at_time', options={'format': 'reel'},
        media=[{'path': str(video), 'origin': 'user-supplied'}])
    account = {'instagram':'456', 'facebook':'123', 'youtube':'channel'}[platform]
    if platform == 'youtube': payload['options'] = dict(format='video', title='Approved title', categoryId='27', selfDeclaredMadeForKids=False)
    job = NativeJob(id='fictional-job', brand='Example', platform=platform, account_id=account,
        media_hash=hash_value(media_hashes(payload)), content_hash=hash_value(payload),
        publish_at=NOW, timezone_name='UTC', schedule_mode='exact', dispatch_after=NOW-timedelta(hours=1),
        route='api', created_at=NOW-timedelta(hours=2), updated_at=NOW-timedelta(hours=2))
    store = NativeStore(brand.raiz, brand='Example')
    store.prepare(job, payload=payload, now=job.created_at)
    store.transition(job.id, 'prepared', 'approved', approval_digest=native_digest(job, payload), now=job.created_at)
    moment = [NOW]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return moment[0].astimezone(tz)
    monkeypatch.setattr(cli, 'datetime', Clock)
    monkeypatch.setattr('socialctl.native_schedule.store.datetime', Clock)
    monkeypatch.setattr('socialctl.management.meta_comments.obtener_token', lambda *a: 'fictional')
    uploads = []
    class Adapter:
        def deliver(self, job, payload, checkpoint):
            uploads.append(job.id)
            checkpoint({'phase':'publish_requested'})
            return '789'
    monkeypatch.setattr('socialctl.native_schedule.instagram_runtime.delivery_context', lambda *a, **k:
        (Adapter(), dict(route='api', verified_account='456', verified_format='reel', grant_verified=True, remote_verified=True)))
    return brand, store, moment, uploads


class Meta:
    def __init__(self):
        self.writes = []
        self.public = False
        self.partial = False
        self.foreign = False
        self.lost = False
        self.existing = False
        self.text = TEXT
    def send(self, client, request, **kwargs):
        path = request.url.path.rsplit('/', 1)[-1]
        if request.method == 'POST':
            self.writes.append(dict(httpx.QueryParams(request.read().decode())))
            self.existing = True
            if self.lost: raise httpx.ReadTimeout('fictional lost response', request=request)
            data = {'id':'900'}
        elif path == 'me': data = {'id':'123', 'instagram_business_account':{'id':'456'}}
        elif path == '789':
            data = dict(id='789', owner={'id':'999' if self.foreign else '456'}, caption='Approved copy',
                media_type='VIDEO', timestamp=NOW.isoformat())
            if self.public: data['permalink'] = 'https://www.instagram.com/reel/fictional/'
        elif path == 'comments':
            data = {'data':[{'id':'900', 'text':self.text, 'from':{'id':'456'}}] if self.existing else []}
            if self.partial: data['paging'] = {'next':'untrusted-pagination'}
        else: raise AssertionError(str(request.url))
        return httpx.Response(200, json=data, request=request)


def install(monkeypatch, meta):
    monkeypatch.setattr(httpx.Client, 'send', lambda client, request, **kwargs: meta.send(client, request, **kwargs))


def invoke(tmp_path, *args):
    from socialctl.cli import app
    return CliRunner().invoke(app, ['native-schedule', *args, '--brand','Example','--root',str(tmp_path)])


def journal(brand, store):
    from socialctl.native_schedule.first_comment import first_comment_status
    return first_comment_status(brand, store.get('fictional-job'), store.get_payload('fictional-job'))


def test_public_worker_waits_for_exact_published_readback_then_preserves_text(tmp_path, monkeypatch):
    brand, store, moment, uploads = setup_job(tmp_path, monkeypatch)
    meta = Meta(); install(monkeypatch, meta)
    result = invoke(tmp_path, 'deliver')
    assert result.exit_code == 0, result.output
    assert uploads == ['fictional-job'] and not meta.writes
    assert store.get('fictional-job').state == 'verifying'
    assert journal(brand, store)['status'] == 'pending'
    meta.public = True
    result = invoke(tmp_path, 'deliver')
    assert result.exit_code == 0, result.output
    assert store.get('fictional-job').state == 'published'
    assert meta.writes == [{'message':TEXT}]
    assert journal(brand, store)['status'] == 'verified'
    assert 'verified' in invoke(tmp_path, 'status').output
    invoke(tmp_path, 'deliver')
    assert len(meta.writes) == 1 and len(uploads) == 1
    assert len(store.get_attempts('fictional-job')) == 1


@pytest.mark.parametrize('variant', ['future','private','foreign','partial'])
def test_worker_rejects_incomplete_or_wrong_media_or_comments(tmp_path, monkeypatch, variant):
    brand, store, moment, uploads = setup_job(tmp_path, monkeypatch)
    meta = Meta(); meta.public = variant != 'private'; meta.foreign = variant == 'foreign'; meta.partial = variant == 'partial'
    install(monkeypatch, meta)
    if variant == 'future': moment[0] -= timedelta(seconds=1)
    invoke(tmp_path, 'deliver')
    assert meta.writes == []
    assert journal(brand, store)['status'] != 'verified'


def test_restart_competing_workers_share_one_comment_intent(tmp_path, monkeypatch):
    from socialctl.native_schedule.cli import dispatch_local
    brand, store, moment, uploads = setup_job(tmp_path, monkeypatch)
    meta = Meta(); install(monkeypatch, meta)
    dispatch_local(brand, store)
    meta.public = True
    def run(_): return dispatch_local(brand, NativeStore(brand.raiz, brand='Example'))
    with ThreadPoolExecutor(max_workers=2) as pool: list(pool.map(run, range(2)))
    assert len(meta.writes) == 1
    assert len(list((brand.raiz/'.socialctl/publications').glob('*.json'))) == 1
    assert len(list((brand.raiz/'.socialctl/comments').glob('*.json'))) == 1
    assert journal(brand, store)['status'] == 'verified'


def test_lost_response_is_durable_uncertainty_and_explicit_recovery_only_observes(tmp_path, monkeypatch):
    brand, store, moment, uploads = setup_job(tmp_path, monkeypatch)
    meta = Meta(); meta.public = True; meta.lost = True; install(monkeypatch, meta)
    invoke(tmp_path, 'deliver')
    assert len(meta.writes) == 1
    assert journal(brand, store)['status'] == 'uncertain'
    result = invoke(tmp_path, 'reconcile', 'fictional-job')
    assert len(meta.writes) == 1
    assert 'uncertain' in result.output
    invoke(tmp_path, 'deliver')
    assert len(meta.writes) == 1
    assert journal(brand, store)['status'] == 'uncertain'
    from socialctl.management.meta_comments import CommentStore
    change = CommentStore(brand.raiz).load(journal(brand, store)['change_id'])
    assert change.candidates == ['900'] and change.remote_id is None


def test_existing_exact_own_comment_is_observed_without_adopting_creation(tmp_path, monkeypatch):
    brand, store, moment, uploads = setup_job(tmp_path, monkeypatch)
    meta = Meta(); meta.public = True; meta.existing = True; install(monkeypatch, meta)
    invoke(tmp_path, 'deliver')
    assert not meta.writes
    assert journal(brand, store)['status'] == 'observed_existing'
    invoke(tmp_path, 'deliver')
    assert not meta.writes


def test_read_only_reconcile_cannot_start_pending_comment(tmp_path, monkeypatch):
    brand, store, moment, uploads = setup_job(tmp_path, monkeypatch)
    meta = Meta(); install(monkeypatch, meta)
    invoke(tmp_path, 'deliver')
    meta.public = True
    invoke(tmp_path, 'reconcile', 'fictional-job')
    assert not meta.writes and journal(brand, store)['status'] == 'pending'


def test_retry_first_cannot_comment_after_media_becomes_private(tmp_path, monkeypatch):
    from socialctl.publication_steps import retry_first_comment
    from socialctl.management.meta_comments import CommentError
    brand, store, moment, uploads = setup_job(tmp_path, monkeypatch)
    meta = Meta(); meta.public = True; meta.partial = True; install(monkeypatch, meta)
    invoke(tmp_path, 'deliver')
    attempt = journal(brand, store)['publication_id']
    meta.public = False; meta.partial = False
    with pytest.raises((CommentError, ValueError)):
        retry_first_comment(brand, httpx.Client(), attempt)
    assert not meta.writes


def submitted(store, remote_id):
    job = store.claim('fictional-job', 'approved', now=NOW)
    store.checkpoint(job.id, job.attempt_id, {'video_id':remote_id}, now=NOW)
    store.mark_api_submitted(job.id, job.attempt_id, remote_id, now=NOW)


class YouTube:
    def __init__(self):
        self.public = True
        self.processed = True
        self.partial = False
        self.foreign = False
        self.writes = []
        self.existing = False
        self.lost = False
    def comment(self):
        return dict(id='comment-1', kind='youtube#comment', etag='comment-etag', snippet=dict(
            channelId='channel', videoId='abcdefghijk', authorChannelId={'value':'channel'}, textOriginal=TEXT))
    def thread(self):
        return dict(id='thread-1', kind='youtube#commentThread', etag='thread-etag', snippet=dict(
            videoId='abcdefghijk', channelId='channel', topLevelComment=self.comment()))
    def send(self, client, request, **kwargs):
        name = request.url.path.rsplit('/',1)[-1]
        if name == 'channels': data = dict(items=[{'id':'channel'}], pageInfo=dict(totalResults=1,resultsPerPage=5))
        elif name == 'videos':
            data = {'items':[dict(id='abcdefghijk', etag='video-etag', snippet=dict(channelId='foreign' if self.foreign else 'channel',
                title='Approved title', description='Approved copy', categoryId='27'),
                status=dict(privacyStatus='public' if self.public else 'private', selfDeclaredMadeForKids=False,
                    uploadStatus='processed' if self.processed else 'uploaded', publishAt=NOW.isoformat()),
                processingDetails={'processingStatus':'succeeded' if self.processed else 'processing'})]}
            if self.partial: data['nextPageToken'] = 'another-page'
        elif name == 'commentThreads':
            if request.method == 'POST':
                self.writes.append(json.loads(request.read()))
                self.existing = True
                if self.lost: raise httpx.ReadTimeout('fictional lost response', request=request)
                data = self.thread()
            else:
                rows = [self.thread()] if self.existing and request.url.params.get('moderationStatus','published') == 'published' else []
                data = dict(items=rows, pageInfo=dict(totalResults=len(rows),resultsPerPage=len(rows)))
        else: raise AssertionError(str(request.url))
        return httpx.Response(200, json=data, request=request)


@pytest.mark.parametrize('variant', ['success','private','processing','partial','foreign','lost'])
def test_youtube_worker_requires_exact_public_processed_video(tmp_path, monkeypatch, variant):
    brand, store, moment, uploads = setup_job(tmp_path, monkeypatch, platform='youtube')
    submitted(store, 'abcdefghijk')
    monkeypatch.setattr('socialctl.management.youtube_resources.obtener_token', lambda *a: 'fictional')
    # Resource clients pin the same fictional credential for reads and writes.
    brand.guardar_secreto(Platform.YOUTUBE, {'access_token':'fictional'})
    meta = YouTube()
    meta.public = variant != 'private'; meta.processed = variant != 'processing'
    meta.partial = variant == 'partial'; meta.foreign = variant == 'foreign'; meta.lost = variant == 'lost'
    install(monkeypatch, meta)
    result = invoke(tmp_path, 'deliver')
    if variant in {'success','lost'}:
        assert len(meta.writes) == 1, result.output
        assert meta.writes[0]['snippet']['topLevelComment']['snippet']['textOriginal'] == TEXT
        assert journal(brand, store)['status'] == ('verified' if variant == 'success' else 'uncertain')
        invoke(tmp_path, 'deliver')
        invoke(tmp_path, 'reconcile', 'fictional-job')
        assert len(meta.writes) == 1
    else:
        assert not meta.writes
        assert journal(brand, store)['status'] == 'pending'


@pytest.mark.parametrize('variant', ['success','private','processing','foreign'])
def test_facebook_worker_requires_exact_public_processed_reel(tmp_path, monkeypatch, variant):
    brand, store, moment, uploads = setup_job(tmp_path, monkeypatch, platform='facebook')
    submitted(store, '789')
    meta = Meta(); meta.public = True
    brand.guardar_secreto(Platform.FACEBOOK, {'access_token':'fictional'})
    def send(client, request, **kwargs):
        if request.url.path.endswith('/789'):
            return httpx.Response(200, json=dict(id='789', **{'from':{'id':'999' if variant == 'foreign' else '123'}},
                description='Approved copy', published=variant != 'private', status={'video_status':'processing' if variant == 'processing' else 'ready'}),request=request)
        if request.url.path.endswith('/900'):
            return httpx.Response(200,json=dict(id='900',message=TEXT, **{'from':{'id':'123'}}, object={'id':'789'}),request=request)
        return meta.send(client,request,**kwargs)
    monkeypatch.setattr(httpx.Client,'send',send)
    result = invoke(tmp_path, 'deliver')
    assert len(meta.writes) == (1 if variant == 'success' else 0), result.output
    assert journal(brand,store)['status'] == ('verified' if variant == 'success' else 'pending')


def test_story_comment_is_rejected_before_container_creation(tmp_path, monkeypatch):
    from socialctl.native_schedule.instagram_delivery import InstagramDelivery
    brand, store, moment, uploads = setup_job(tmp_path, monkeypatch)
    payload = store.get_payload('fictional-job')
    payload['options']['format'] = 'story'
    client = httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail('unsupported combination wrote')))
    with pytest.raises(ValueError,match='Story first comments'):
        InstagramDelivery(client,'fictional').deliver(store.get('fictional-job'),payload,lambda value: None)


@pytest.mark.parametrize('command,args', [('dispatch',('fictional-job',)), ('deliver',()), ('reconcile',('fictional-job',))])
def test_public_followup_json_preserves_remote_envelope_contract(tmp_path, monkeypatch, command, args):
    from socialctl.native_schedule.report import validate_remote
    brand, store, moment, uploads = setup_job(tmp_path,monkeypatch)
    submitted(store,'789')
    meta = Meta(); meta.public = True; install(monkeypatch,meta)
    result = invoke(tmp_path,command,*args,'--json')
    report = validate_remote(result.stdout,command=command,brand='Example',exit_code=result.exit_code,
        request_args=(command,*args))
    assert report['status'] == 'ok'
    assert len(meta.writes) == (0 if command == 'reconcile' else 1)


def test_made_for_kids_comment_combination_stops_native_submission_before_network(tmp_path, monkeypatch):
    from tests.test_native_schedule_youtube import setup
    from socialctl.native_schedule.youtube import schedule_youtube
    from socialctl.native_schedule.approval import native_digest
    from socialctl.native_schedule.store import _replace
    job,payload,context = setup(tmp_path,monkeypatch,first_comment=TEXT)
    payload['options']['selfDeclaredMadeForKids'] = True
    job = _replace(job, content_hash=hash_value(payload))
    job = _replace(job, approval_digest=native_digest(job,payload))
    with context['store']._db(write=True) as db:
        context['store']._save(db,job)
        db.execute('UPDATE jobs SET payload=? WHERE id=?',(json.dumps(payload),job.id))
    client = httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail('unfulfillable first comment reached network')))
    result = schedule_youtube(job,payload,client=client,**context)
    assert result.state == 'ui_required' and 'made for kids' in result.reason
    assert context['store'].get_attempts(job.id) == []
