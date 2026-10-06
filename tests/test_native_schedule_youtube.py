from datetime import datetime, timezone
import json
import httpx
import pytest

from socialctl.brands import Brand
from socialctl.native_schedule.approval import hash_value, media_hashes, native_digest
from socialctl.native_schedule.models import NativeJob
from socialctl.native_schedule.store import NativeStore
from socialctl.native_schedule.youtube import schedule_youtube, reconcile_youtube, resume_youtube, YouTubeEligibility


def setup(tmp_path, monkeypatch, existing=False, never_published=True):
    monkeypatch.setattr('socialctl.management.youtube.obtener_token', lambda *a: 'token')
    video = tmp_path / 'clip.mp4'; video.write_bytes(b'video')
    brand = Brand(nombre='Test', raiz=tmp_path, cuentas={'youtube': {'channel_id':'channel'}})
    payload = dict(copy='description',content_origin='standalone',source_video_id=None,
        visibility='public',media=[dict(path=str(video),origin='user')],first_comment=None,
        options=dict(format='video',title='Title',categoryId='27',selfDeclaredMadeForKids=False))
    if existing: payload['options'].update(video_id='abcdefghijk',never_published=never_published)
    now = datetime.now(timezone.utc)
    job = NativeJob(id='job',brand='Test',platform='youtube',account_id='channel',
        publish_at=datetime(2035,1,1,14,tzinfo=timezone.utc),dispatch_after=now,
        timezone_name='America/New_York',route='api',created_at=now,updated_at=now,
        content_hash=hash_value(payload),media_hash=hash_value(media_hashes(payload)))
    store=NativeStore(tmp_path,brand='Test'); store.prepare(job,payload=payload)
    job=store.transition(job.id,'prepared','approved',approval_digest=native_digest(job,payload))
    eligibility=YouTubeEligibility(account_id='channel',project_verified=True,studio_pilot_verified=True,
        evidence_ref='operator-reviewed-pilot',api_enabled=True)
    return job,payload,dict(brand=brand,store=store,eligibility=eligibility)


def resource(payload, when='2035-01-01T14:00:00Z', privacy='private'):
    return dict(id='abcdefghijk',etag='etag',snippet=dict(channelId='channel',title='Title',
        description=payload['copy'],categoryId='27'),status=dict(privacyStatus=privacy,
        publishAt=when,selfDeclaredMadeForKids=False,embeddable=True),processingDetails={'processingStatus':'succeeded'})


def test_existing_preserves_status_and_never_uploads(tmp_path,monkeypatch):
    job,payload,ctx=setup(tmp_path,monkeypatch,True); writes=[]
    def handler(req):
        if req.url.path.endswith('/channels'): return httpx.Response(200,json={'items':[{'id':'channel'}]})
        if req.method=='PUT':
            body=json.loads(req.content); writes.append(body)
            assert body['status']['privacyStatus']=='private'
            assert body['status']['selfDeclaredMadeForKids'] is False
            assert body['status']['embeddable'] is True
            return httpx.Response(200,json={'id':'abcdefghijk'})
        assert req.method=='GET'
        return httpx.Response(200,json={'items':[resource(payload)]})
    result=schedule_youtube(job,payload,client=httpx.Client(transport=httpx.MockTransport(handler)),**ctx)
    assert len(writes)==1 and result.state=='pending_verification' and not result.calendar_visible
    assert ctx['store'].get(job.id).state=='verifying'


def test_closed_by_default_no_requests(tmp_path,monkeypatch):
    job,payload,ctx=setup(tmp_path,monkeypatch); ctx.pop('eligibility')
    result=schedule_youtube(job,payload,client=httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail('network'))),**ctx)
    assert result.state=='ui_required' and ctx['store'].get(job.id).state=='approved'


def test_upload_checkpoints_and_lost_response_recovery(tmp_path,monkeypatch):
    job,payload,ctx=setup(tmp_path,monkeypatch); calls=[]
    def handler(req):
        calls.append(req.method)
        if req.url.path.endswith('/channels'): return httpx.Response(200,json={'items':[{'id':'channel'}]})
        if req.method=='POST':
            assert ctx['store'].get(job.id).attempt_id
            assert json.loads(req.content)['status']['publishAt']=='2035-01-01T14:00:00Z'
            return httpx.Response(200,headers={'Location':'https://www.googleapis.com/upload/session'})
        if req.method=='PUT':
            checkpoint=ctx['store'].get_attempts(job.id)[0]['checkpoint']
            assert checkpoint['session_uri']
            if req.headers.get('content-range')=='bytes */5': return httpx.Response(200,json={'id':'abcdefghijk'})
            raise httpx.ReadTimeout('lost')
        assert ctx['store'].get_attempts(job.id)[0]['checkpoint']['video_id']=='abcdefghijk'
        return httpx.Response(200,json={'items':[resource(payload)]})
    client=httpx.Client(transport=httpx.MockTransport(handler))
    assert schedule_youtube(job,payload,client=client,**ctx).state=='uncertain'
    current=ctx['store'].get(job.id)
    assert schedule_youtube(current,payload,client=client,**ctx).state=='uncertain'
    result=reconcile_youtube(current,payload,client=client,brand=ctx['brand'],store=ctx['store'])
    assert result.state=='pending_verification' and calls.count('POST')==1


@pytest.mark.parametrize('privacy,when,expected',[('private','2035-01-02T14:00:00Z','conflict'),('private','2035-01-01T14:00:00Z','pending_verification'),('public',None,'published')])
def test_reconcile_observes_without_overwriting(tmp_path,monkeypatch,privacy,when,expected):
    job,payload,ctx=setup(tmp_path,monkeypatch,True)
    job=ctx['store'].claim(job.id,'approved');ctx['store'].checkpoint(job.id,job.attempt_id,{'video_id':'abcdefghijk'})
    def handler(req):
        assert req.method=='GET'
        if req.url.path.endswith('/channels'): return httpx.Response(200,json={'items':[{'id':'channel'}]})
        return httpx.Response(200,json={'items':[resource(payload,when,privacy)]})
    result=reconcile_youtube(job,payload,client=httpx.Client(transport=httpx.MockTransport(handler)),brand=ctx['brand'],store=ctx['store'])
    assert result.state==expected and not result.calendar_visible
    assert ctx['store'].get(job.id).state!='published'


@pytest.mark.parametrize('case',['project','pilot','account'])
def test_unproven_runtime_eligibility_no_writes(tmp_path,monkeypatch,case):
    from dataclasses import replace
    job,payload,ctx=setup(tmp_path,monkeypatch)
    changes={'project':{'project_verified':False},'pilot':{'studio_pilot_verified':False},'account':{'account_id':'other'}}[case]
    ctx['eligibility']=replace(ctx['eligibility'],**changes)
    result=schedule_youtube(job,payload,client=httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail('network'))),**ctx)
    assert result.state=='ui_required' and not ctx['store'].get_attempts(job.id)


def test_past_timestamp_never_writes(tmp_path,monkeypatch):
    job,payload,ctx=setup(tmp_path,monkeypatch)
    class Later(datetime):
        @classmethod
        def now(cls,tz=None): return datetime(2036,1,1,tzinfo=timezone.utc)
    monkeypatch.setattr('socialctl.native_schedule.youtube.datetime',Later)
    with pytest.raises(ValueError,match='Future'):
        schedule_youtube(job,payload,client=httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail('network'))),**ctx)
    assert not ctx['store'].get_attempts(job.id)


@pytest.mark.parametrize('declared,privacy',[(False,'private'),(True,'public')])
def test_existing_history_guard(tmp_path,monkeypatch,declared,privacy):
    job,payload,ctx=setup(tmp_path,monkeypatch,True,never_published=declared)
    if not declared:
        with pytest.raises(ValueError):
            schedule_youtube(job,payload,client=httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail('network'))),**ctx)
        assert not ctx['store'].get_attempts(job.id)
        return
    def handler(req):
        assert req.method=='GET'
        if req.url.path.endswith('/channels'): return httpx.Response(200,json={'items':[{'id':'channel'}]})
        return httpx.Response(200,json={'items':[resource(payload,privacy=privacy)]})
    with pytest.raises(ValueError,match='not private'):
        schedule_youtube(job,payload,client=httpx.Client(transport=httpx.MockTransport(handler)),**ctx)
    assert not ctx['store'].get_attempts(job.id)


def test_incomplete_session_query_never_resubmits_and_private_ledger(tmp_path,monkeypatch):
    import stat
    job,payload,ctx=setup(tmp_path,monkeypatch)
    job=ctx['store'].claim(job.id,'approved')
    from socialctl.native_schedule.youtube import _checkpoint
    _checkpoint(ctx['store'],job,session_uri='https://www.googleapis.com/upload/session',total_bytes=5)
    assert stat.S_IMODE(ctx['store'].path.stat().st_mode)==0o600
    ctx['store'].transition(job.id,'dispatching','uncertain');job=ctx['store'].get(job.id)
    def handler(req):
        if req.method=='GET': return httpx.Response(200,json={'items':[{'id':'channel'}]})
        assert req.method=='PUT' and req.content==b'' and req.headers['content-range']=='bytes */5'
        return httpx.Response(308,headers={'Range':'bytes=0-2'})
    result=reconcile_youtube(job,payload,client=httpx.Client(transport=httpx.MockTransport(handler)),brand=ctx['brand'],store=ctx['store'])
    assert result.state=='uncertain' and 'session' not in repr(result)


@pytest.mark.parametrize('location',['https://evil.example/upload/session','http://www.googleapis.com/upload/session','https://user@www.googleapis.com/upload/session'])
def test_untrusted_upload_location_never_receives_token_or_bytes(tmp_path,monkeypatch,location):
    job,payload,ctx=setup(tmp_path,monkeypatch)
    def handler(req):
        assert req.url.host=='www.googleapis.com'
        if req.method=='GET': return httpx.Response(200,json={'items':[{'id':'channel'}]})
        assert req.method=='POST'
        return httpx.Response(200,headers={'Location':location})
    result=schedule_youtube(job,payload,client=httpx.Client(transport=httpx.MockTransport(handler)),**ctx)
    assert result.state=='uncertain' and location not in repr(result)
    assert 'session_uri' not in ctx['store'].get_attempts(job.id)[0]['checkpoint']


def test_hashing_past_deadline_retains_session_without_media_put(tmp_path,monkeypatch):
    import socialctl.native_schedule.youtube as adapter
    job,payload,ctx=setup(tmp_path,monkeypatch)
    real_verify=adapter.verify_approval
    deadline_passed=False
    checks=0
    puts=[]
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            return datetime(2036 if deadline_passed else 2034,1,1,tzinfo=timezone.utc)
    def verify(*args,**kwargs):
        nonlocal checks,deadline_passed
        result=real_verify(*args,**kwargs)
        checks+=1
        if checks==2: deadline_passed=True
        return result
    monkeypatch.setattr(adapter,'datetime',Clock)
    monkeypatch.setattr(adapter,'verify_approval',verify)
    def handler(req):
        if req.method=='GET': return httpx.Response(200,json={'items':[{'id':'channel'}]})
        if req.method=='POST': return httpx.Response(200,headers={'Location':'https://www.googleapis.com/upload/session'})
        puts.append(req)
        return httpx.Response(200,json={'id':'abcdefghijk'})
    result=schedule_youtube(job,payload,client=httpx.Client(transport=httpx.MockTransport(handler)),**ctx)
    assert checks==2 and not puts
    assert result.state=='uncertain' and ctx['store'].get(job.id).state=='uncertain'
    checkpoint=ctx['store'].get_attempts(job.id)[0]['checkpoint']
    assert checkpoint['session_uri'] and 'video_id' not in checkpoint


def test_upload_uses_confirmed_chunk_offsets(tmp_path,monkeypatch):
    job,payload,ctx=setup(tmp_path,monkeypatch)
    size=256*1024+5
    (tmp_path/'clip.mp4').write_bytes(b'a'*size)
    from socialctl.native_schedule.approval import hash_value, media_hashes, native_digest
    from socialctl.native_schedule.store import _replace
    payload['media'][0]['path']=str(tmp_path/'clip.mp4')
    updated=_replace(job,media_hash=hash_value(media_hashes(payload)),content_hash=hash_value(payload))
    updated=_replace(updated,approval_digest=native_digest(updated,payload))
    with ctx['store']._db(write=True) as db:
        ctx['store']._save(db,updated)
        db.execute('UPDATE jobs SET payload=? WHERE id=?',(json.dumps(payload),job.id))
    job=updated
    ranges=[]
    def handler(req):
        if req.url.path.endswith('/channels'):
            return httpx.Response(200,json={'items':[{'id':'channel'}]})
        if req.method=='POST':
            return httpx.Response(200,headers={'Location':'https://www.googleapis.com/upload/session'})
        if req.method=='PUT':
            ranges.append(req.headers.get('content-range'))
            if len(ranges)==1:
                assert len(req.read())==256*1024
                return httpx.Response(308,headers={'Range':f'bytes=0-{256*1024-1}'})
            assert req.read()==b'a'*5
            return httpx.Response(200,json={'id':'abcdefghijk'})
        return httpx.Response(200,json={'items':[resource(payload)]})
    result=schedule_youtube(job,payload,client=httpx.Client(transport=httpx.MockTransport(handler)),**ctx)
    assert result.state=='pending_verification'
    assert ranges==[f'bytes 0-{256*1024-1}/{size}',f'bytes {256*1024}-{size-1}/{size}']
    assert ctx['store'].get_attempts(job.id)[0]['checkpoint']['offset']==size


def test_resume_queries_same_session_and_sends_only_missing_bytes(tmp_path,monkeypatch):
    job,payload,ctx=setup(tmp_path,monkeypatch)
    size=256*1024+5
    (tmp_path/'clip.mp4').write_bytes(b'a'*size)
    from socialctl.native_schedule.approval import hash_value, media_hashes, native_digest
    from socialctl.native_schedule.store import _replace
    updated=_replace(job,media_hash=hash_value(media_hashes(payload)),content_hash=hash_value(payload))
    updated=_replace(updated,approval_digest=native_digest(updated,payload))
    with ctx['store']._db(write=True) as db:
        ctx['store']._save(db,updated)
    job=updated
    posts=[]; ranges=[]
    def handler(req):
        if req.url.path.endswith('/channels'):
            return httpx.Response(200,json={'items':[{'id':'channel'}]})
        if req.method=='POST':
            posts.append(req)
            return httpx.Response(200,headers={'Location':'https://www.googleapis.com/upload/session'})
        if req.method=='PUT':
            ranges.append(req.headers['content-range'])
            if len(ranges)==1:
                return httpx.Response(308,headers={'Range':f'bytes=0-{256*1024-1}'})
            if len(ranges)==2:
                raise httpx.ReadTimeout('lost final reply')
            if req.headers['content-range']==f'bytes */{size}':
                return httpx.Response(308,headers={'Range':f'bytes=0-{256*1024-1}'})
            assert req.read()==b'a'*5
            return httpx.Response(200,json={'id':'abcdefghijk'})
        return httpx.Response(200,json={'items':[resource(payload)]})
    client=httpx.Client(transport=httpx.MockTransport(handler))
    assert schedule_youtube(job,payload,client=client,**ctx).state=='uncertain'
    current=ctx['store'].get(job.id)
    result=resume_youtube(current,payload,client=client,**ctx)
    assert result.state=='pending_verification'
    assert len(posts)==1
    assert ranges==[f'bytes 0-{256*1024-1}/{size}',
                    f'bytes {256*1024}-{size-1}/{size}',
                    f'bytes */{size}',
                    f'bytes {256*1024}-{size-1}/{size}']
    assert ctx['store'].get(job.id).state=='verifying'


def test_expired_resumable_session_stays_uncertain_without_new_insert(tmp_path,monkeypatch):
    job,payload,ctx=setup(tmp_path,monkeypatch)
    claimed=ctx['store'].claim(job.id,'approved')
    from socialctl.native_schedule.youtube import _checkpoint
    _checkpoint(ctx['store'],claimed,session_uri='https://www.googleapis.com/upload/session',
                total_bytes=5,expected_parts={'status': {'privacyStatus': 'private'}})
    ctx['store'].transition(job.id,'dispatching','uncertain')
    calls=[]
    def handler(req):
        calls.append(req.method)
        if req.method=='GET':
            return httpx.Response(200,json={'items':[{'id':'channel'}]})
        assert req.method=='PUT' and req.headers['content-range']=='bytes */5'
        return httpx.Response(404)
    result=resume_youtube(ctx['store'].get(job.id),payload,
        client=httpx.Client(transport=httpx.MockTransport(handler)),**ctx)
    assert result.state=='uncertain'
    assert calls==['GET','PUT']
    assert ctx['store'].get(job.id).state=='uncertain'
