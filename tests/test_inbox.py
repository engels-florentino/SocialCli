from datetime import datetime,timezone
import pytest


def test_relative_since_uses_local_calendar_and_dst():
    from socialctl.inbox import resolve_since
    now=datetime(2026,3,9,12,tzinfo=timezone.utc)
    assert resolve_since('ayer','America/New_York',now)==datetime(2026,3,8,5,tzinfo=timezone.utc)
    assert resolve_since('yesterday','America/New_York',now)==resolve_since('2026-03-08','America/New_York',now)
    assert resolve_since('2026-03-08T01:00:00-05:00',None,now)==datetime(2026,3,8,6,tzinfo=timezone.utc)
    for zone in (None,'not/a/timezone'):
        with pytest.raises(ValueError):resolve_since('ayer',zone,now)


def test_unified_observations_keep_original_and_provider_times(tmp_path):
    from socialctl.inbox import UnifiedInboxStore
    store=UnifiedInboxStore(tmp_path)
    row={'platform':'youtube','account_id':'actor','comment_id':'comment','media_id':'video','parent_id':None,'author_id':'viewer','text':'Original','created_time':'2026-10-01T12:00:00+00:00','provider_updated_at':'2026-10-01T12:00:00+00:00'}
    store.observe([row],observed_at='2026-10-02T12:00:00+00:00')
    store.observe([{**row,'text':'Edited','provider_updated_at':'2026-10-03T12:00:00+00:00'}],observed_at='2026-10-03T13:00:00+00:00')
    rows=store.load()['items']
    assert len(rows)==1
    result=next(iter(rows.values()))
    assert result['original_text']=='Original' and result['text']=='Edited'
    assert result['created_time']==row['created_time']
    assert result['provider_updated_at']=='2026-10-03T12:00:00+00:00' and result['history']


def test_youtube_reply_failure_retains_top_comment():
    from socialctl.inbox import _youtube_rows
    from tests.test_youtube_community import thread,comment
    class Client:
        configured_channel_id='owner'
        def list_threads(self,video):
            row=comment(author='viewer');row['snippet']['textDisplay']='A valid top comment'
            row['snippet']['publishedAt']='2026-10-10T12:00:00Z'
            return {'items':[thread(top=row)],'complete':True}
        def list_replies(self,*args):raise RuntimeError('Reply page unavailable')
    rows,complete,errors=_youtube_rows(Client(),'video-1')
    assert len(rows)==1 and rows[0]['text']=='A valid top comment'
    assert not complete and errors


def test_meta_direct_reply_has_separate_parent_cursor(tmp_path):
    from tests.test_meta_comments import service
    from tests.test_meta_comment_inbox import remote_row
    from socialctl.management.meta_comment_inbox import CommentInboxStore,sync_inbox
    _,_,graph,client,_=service(tmp_path)
    inbox=CommentInboxStore(client.brand.raiz)
    top=remote_row(graph,'800','Top')
    reply=remote_row(graph,'801','Reply');reply['parent']={'id':'800'}
    graph.pages=lambda request:{'data':[reply] if request.url.path.endswith('/800/comments') else [top]}
    result=sync_inbox(client,inbox,media_id='789')
    assert result['parent_ids']==['800']
    sync_inbox(client,inbox,media_id='789',parent_id='800')
    assert {c.parent_id for c in inbox.load().cursors}=={None,'800'}
    assert next(r for r in inbox.load().comments if r.comment_id=='801').parent_id=='800'


def test_meta_later_page_error_retains_valid_rows(tmp_path):
    from tests.test_meta_comments import service
    from tests.test_meta_comment_inbox import remote_row
    _,_,graph,client,_=service(tmp_path)
    first=remote_row(graph,'800','First')
    def pages(request):
        if request.url.params.get('after'):raise httpx.ReadTimeout('page stalled')
        return {'data':[first],'paging':{'next':'https://ignored.invalid','cursors':{'after':'next'}}}
    import httpx
    graph.pages=pages
    result=client.list_comments('789')
    assert result['data']==[first] and result['complete'] is False
    assert result['error']


@pytest.mark.parametrize('reply_failure',[False,True,'interrupt'])
def test_unified_meta_reads_replies_and_preserves_partial_progress(tmp_path,monkeypatch,reply_failure):
    import httpx
    from tests.test_meta_comments import service
    from tests.test_meta_comment_inbox import remote_row
    from socialctl import inbox as mod
    from socialctl.management.meta_client import MetaClient
    from socialctl.read_budget import ReadBudget
    _,brand,graph,client,_=service(tmp_path)
    top=remote_row(graph,'800','Top',created='2026-10-10T10:00:00Z')
    reply=remote_row(graph,'801','Reply',created='2026-10-10T11:00:00Z');reply['parent']={'id':'800'}
    def pages(request):
        if request.url.path.endswith('/800/comments'):
            if reply_failure=='interrupt':raise KeyboardInterrupt()
            if reply_failure:raise httpx.ReadTimeout('later replies failed')
            return {'data':[reply]}
        return {'data':[top]}
    graph.pages=pages
    monkeypatch.setattr(mod,'read_client',lambda *a,**k:httpx.Client(transport=httpx.MockTransport(graph)))
    monkeypatch.setattr(MetaClient,'content_list',lambda *a,**k:{'data':[{'id':'789'}],'complete':True})
    result=mod.sync_brand_inbox(brand,since=datetime(2026,10,10,tzinfo=timezone.utc),budget=ReadBudget(10))
    items=result.data['items']
    assert {i['comment_id'] for i in items}==({'800'} if reply_failure else {'800','801'})
    coverage=next(c for c in result.coverage if c['platform']=='facebook')
    assert coverage['complete'] is (not reply_failure)
    assert bool(result.data['watermarks']) is (not reply_failure)
    if not reply_failure:
        assert next(i for i in items if i['comment_id']=='801')['parent_id']=='800'


def test_youtube_interrupt_retains_valid_top_comment():
    from socialctl.inbox import _youtube_rows,InboxInterrupted
    from tests.test_youtube_community import thread,comment
    class Client:
        configured_channel_id='owner'
        def list_threads(self,video):
            row=comment(author='viewer');row['snippet']['publishedAt']='2026-10-10T12:00:00Z'
            return {'items':[thread(top=row)],'complete':True}
        def list_replies(self,*args):raise KeyboardInterrupt()
    with pytest.raises(InboxInterrupted) as caught:_youtube_rows(Client(),'video-1')
    assert caught.value.rows[0]['comment_id']=='UgxTop'


@pytest.mark.parametrize('second_page',['malformed','rejected'])
def test_meta_bad_later_page_keeps_already_valid_comments(tmp_path,second_page):
    import httpx
    from tests.test_meta_comments import service
    from tests.test_meta_comment_inbox import remote_row
    from socialctl.management.meta_comment_inbox import CommentInboxStore,sync_inbox
    _,brand,graph,client,_=service(tmp_path)
    first=remote_row(graph,'800','First')
    def pages(request):
        if request.url.params.get('after'):
            if second_page=='rejected':
                return {'error':{'code':100,'message':'Invalid paging cursor'}}
            return {'data':'malformed'}
        return {'data':[first],'paging':{'next':'https://ignored.invalid','cursors':{'after':'next'}}}
    graph.pages=pages
    inbox=CommentInboxStore(brand.raiz)
    result=sync_inbox(client,inbox,media_id='789')
    assert not result['complete']
    assert [r.comment_id for r in inbox.load().comments]==['800']


def test_unified_meta_can_resume_same_explicit_interval(tmp_path):
    from tests.test_meta_comments import service
    from tests.test_meta_comment_inbox import remote_row
    from socialctl.management.meta_comment_inbox import CommentInboxStore,sync_inbox
    _,brand,graph,client,_=service(tmp_path)
    first=remote_row(graph,'800','First');second=remote_row(graph,'801','Second')
    seen=[]
    def pages(request):
        cursor=request.url.params.get('after');seen.append(cursor)
        if cursor:return {'data':[second]}
        return {'data':[first],'paging':{'next':'https://ignored.invalid','cursors':{'after':'next'}}}
    graph.pages=pages;inbox=CommentInboxStore(brand.raiz)
    since='2026-09-01T00:00:00Z'
    sync_inbox(client,inbox,media_id='789',since=since,max_pages=1,resume=True)
    result=sync_inbox(client,inbox,media_id='789',since=since,max_pages=1,resume=True)
    assert seen==[None,'next'] and result['complete']
    assert len(inbox.load().comments)==2


def test_meta_interruption_after_first_page_persists_partial_comments(tmp_path,monkeypatch):
    from tests.test_meta_comments import service
    from tests.test_meta_comment_inbox import remote_row
    from socialctl.management.meta_comment_inbox import CommentInboxStore,sync_inbox
    _,brand,graph,client,_=service(tmp_path)
    first=remote_row(graph,'800','First')
    graph.pages=lambda request:{'data':[first],'paging':{'next':'https://ignored.invalid','cursors':{'after':'next'}}}
    original=client._request
    def request(method,path,**kwargs):
        if (kwargs.get('params') or {}).get('after'):raise KeyboardInterrupt()
        return original(method,path,**kwargs)
    monkeypatch.setattr(client,'_request',request)
    store=CommentInboxStore(brand.raiz)
    result=sync_inbox(client,store,media_id='789')
    assert result['interrupted'] and not result['complete']
    assert store.load().comments[0].comment_id=='800'


def test_youtube_interrupted_pagination_preserves_verified_threads(tmp_path):
    import httpx
    from tests.test_youtube_community import service,thread
    from socialctl.inbox import _youtube_rows,InboxInterrupted
    _,api,client,_=service(tmp_path)
    def read(request):
        if request.url.path.endswith('/commentThreads'):
            if request.url.params.get('pageToken'):raise KeyboardInterrupt()
            return httpx.Response(200,json={'items':[thread()], 'nextPageToken':'next', 'pageInfo':{'totalResults':2,'resultsPerPage':50}})
    api.on_read=read
    # Use a viewer comment so it belongs in the unified inbox.
    api.threads[0]['snippet']['topLevelComment']['snippet']['authorChannelId']={'value':'viewer'}
    def read(request):
        if request.url.path.endswith('/commentThreads'):
            if request.url.params.get('pageToken'):raise KeyboardInterrupt()
            return httpx.Response(200,json={'items':api.threads, 'nextPageToken':'next', 'pageInfo':{'totalResults':2,'resultsPerPage':50}})
    api.on_read=read
    with pytest.raises(InboxInterrupted) as caught:_youtube_rows(client,'video-1')
    assert caught.value.rows[0]['comment_id']=='UgxTop'
