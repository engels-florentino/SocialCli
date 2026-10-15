"""Platform reply evidence survives a fresh local installation; no live writes."""
import httpx
import pytest

from socialctl.engagement import review_inbox, selected_replies, prepare_replies
from socialctl.inbox import UnifiedInboxStore, _youtube_rows
from socialctl.read_budget import ReadBudget
from tests.test_youtube_community import service, comment, thread


def youtube_scan(tmp_path, *, replies=None):
    brand, api, client, _ = service(tmp_path)
    top = comment(author='viewer', text='Question')
    top['snippet']['publishedAt'] = '2026-10-10T12:00:00Z'
    api.threads = [thread(top=top)]
    if replies is not None:
        api.replies = replies
    return brand, api, client


def save_scan(brand, client):
    rows, complete, errors = _youtube_rows(client, 'video-1')
    UnifiedInboxStore(brand.raiz).observe(rows, observed_at='2026-10-15T00:00:00Z')
    return review_inbox(brand)['items'], complete, errors


def test_fresh_workspace_detects_owner_reply_without_local_journal(tmp_path):
    brand, api, client = youtube_scan(tmp_path)
    rows, _, _ = save_scan(brand, client)
    assert rows[0]['reply_state'] == 'replied'
    assert rows[0]['reply_evidence']['reply_ids'] == ['UgxTop.9_reply-2']
    assert rows[0]['proposals'] == []
    with pytest.raises(Exception, match='existing replied'):
        selected_replies(brand, {'version':1, 'replies':[dict(platform='youtube', account_id='channel-a', comment_id='UgxTop', text='Duplicate')]})
    assert not api.writes


@pytest.mark.parametrize('case', ['partial', 'hidden_author', 'other_account'])
def test_reply_absence_requires_complete_authored_listing(tmp_path, case):
    reply = comment('external-reply', author='another-account', parent='UgxTop')
    if case == 'hidden_author':
        reply['snippet'].pop('authorChannelId')
    brand, api, client = youtube_scan(tmp_path, replies=[reply])
    if case == 'partial':
        api.on_read = lambda r: httpx.Response(503) if r.url.path.endswith('/comments') else None
    rows, _, _ = save_scan(brand, client)
    top = next(r for r in rows if r['comment_id'] == 'UgxTop')
    assert top['reply_evidence']['state'] == ('no_owner_reply_found' if case == 'other_account' else 'unknown')
    assert top['reply_state'] != 'replied'


def test_later_page_owner_reply_retained_even_when_next_page_fails(tmp_path):
    brand, api, client = youtube_scan(tmp_path, replies=[])
    other = comment('other', author='viewer-2', parent='UgxTop')
    own = comment('owner-reply', parent='UgxTop')
    def pages(request):
        if not request.url.path.endswith('/comments'): return None
        cursor = request.url.params.get('pageToken')
        if cursor == 'last': return httpx.Response(503)
        return httpx.Response(200, json={'items':[own if cursor else other],
            'pageInfo':{'totalResults':3, 'resultsPerPage':1}, 'nextPageToken':'last' if cursor else 'second'})
    api.on_read = pages
    rows, complete, _ = save_scan(brand, client)
    top = next(r for r in rows if r['comment_id'] == 'UgxTop')
    assert not complete and top['reply_state'] == 'replied'
    assert top['reply_evidence']['reply_ids'] == ['owner-reply']
    sibling = next(r for r in rows if r['comment_id'] == 'other')
    assert sibling['reply_state'] != 'replied'


def test_stale_unreplied_observation_rechecks_before_drafting(tmp_path, monkeypatch):
    brand, api, client = youtube_scan(tmp_path, replies=[])
    save_scan(brand, client)
    api.replies = [comment('new-owner-reply', parent='UgxTop')]
    monkeypatch.setattr('socialctl.engagement.http_client', lambda budget: httpx.Client(transport=httpx.MockTransport(api.handler)))
    prepared = []
    with pytest.raises(Exception, match='owner reply'):
        prepare_replies(brand, {'version':1, 'replies':[dict(platform='youtube', account_id='channel-a', comment_id='UgxTop', text='Duplicate')]}, budget=ReadBudget(10), prepared=prepared)
    assert prepared == [] and not api.writes


@pytest.mark.parametrize('platform', ['facebook', 'instagram'])
def test_meta_parent_reconciliation_ignores_since_for_owner_reply(tmp_path, platform):
    from tests.test_meta_comments import service as meta_service
    from tests.test_meta_comment_inbox import remote_row
    from socialctl.management.meta_comment_inbox import CommentInboxStore, sync_inbox, prepare_inbox_draft
    _, brand, graph, client, changes = meta_service(tmp_path, platform)
    parent = remote_row(graph, '800', 'Question', created='2026-10-14T00:00:00Z')
    own = remote_row(graph, '900', 'Old answer', author=client.account_id, created='2026-10-01T00:00:00Z')
    own['parent'] = {'id':'800'}
    if platform == 'instagram': own['parent_id'] = '800'
    graph.pages = lambda r: {'data':[parent] if r.url.path.endswith('/789/comments') else [own]}
    inbox = CommentInboxStore(brand.raiz)
    sync_inbox(client, inbox, media_id='789', since='2026-10-10T00:00:00Z')
    sync_inbox(client, inbox, media_id='789', parent_id='800', since='2026-10-10T00:00:00Z')
    record = next(r for r in inbox.load().comments if r.comment_id == '800')
    assert record.reply_evidence['state'] == 'owner_reply_found'
    assert record.reply_evidence['reply_ids'] == ['900']
    with pytest.raises(Exception, match='owner reply'):
        prepare_inbox_draft(client, changes, inbox, media_id='789', comment_id='800', text='Duplicate')
    assert not graph.writes


@pytest.mark.parametrize('case', ['hidden_author', 'partial'])
def test_draft_refuses_unknown_reply_coverage_without_creating_child(tmp_path, monkeypatch, case):
    brand, api, client = youtube_scan(tmp_path, replies=[])
    save_scan(brand, client)
    if case == 'partial':
        api.on_read = lambda r: httpx.Response(503) if r.url.path.endswith('/comments') else None
    else:
        hidden = comment('hidden', author='viewer-2', parent='UgxTop')
        hidden['snippet'].pop('authorChannelId')
        api.replies = [hidden]
    monkeypatch.setattr('socialctl.engagement.http_client', lambda budget: httpx.Client(transport=httpx.MockTransport(api.handler)))
    prepared = []
    with pytest.raises(Exception, match='coverage is incomplete'):
        prepare_replies(brand, {'version':1, 'replies':[dict(platform='youtube', account_id='channel-a', comment_id='UgxTop', text='Reply')]}, budget=ReadBudget(10), prepared=prepared)
    assert prepared == [] and not api.writes


def test_partial_refresh_cannot_erase_previously_verified_reply(tmp_path):
    brand, api, client = youtube_scan(tmp_path)
    save_scan(brand, client)
    api.on_read = lambda r: httpx.Response(503) if r.url.path.endswith('/comments') else None
    rows, _, _ = save_scan(brand, client)
    assert rows[0]['reply_state'] == 'replied'
    assert rows[0]['reply_evidence']['reply_ids'] == ['UgxTop.9_reply-2']


@pytest.mark.parametrize('field,value', [('account_id','other'), ('media_id','other-video'), ('parent_id','other-parent'), ('thread_id','other-thread')])
def test_saved_reply_evidence_cannot_cross_account_media_parent_or_thread(tmp_path, field, value):
    import json
    brand, api, client = youtube_scan(tmp_path)
    save_scan(brand, client)
    store = UnifiedInboxStore(brand.raiz)
    data = store.load()
    next(iter(data['items'].values()))['reply_evidence'][field] = value
    store.path.write_text(json.dumps(data))
    with pytest.raises(Exception, match='evidence differs'):
        review_inbox(brand)


def test_fresh_installation_recovers_ten_replied_parents_among_198_comments(tmp_path):
    brand, api, client = youtube_scan(tmp_path, replies=[])
    api.threads = []
    for index in range(198):
        top = comment(f'parent-{index}', author='viewer')
        top['snippet']['publishedAt'] = '2026-10-10T12:00:00Z'
        item = thread(f'thread-{index}', top)
        item['snippet']['totalReplyCount'] = int(index < 10)
        api.threads.append(item)
        if index < 10: api.replies.append(comment(f'reply-{index}', parent=top['id']))
    def pages(request):
        if not request.url.path.endswith('/commentThreads') or 'id' in request.url.params: return None
        start = int(request.url.params.get('pageToken', '0'))
        payload = {'items':api.threads[start:start+50], 'pageInfo':{'totalResults':198,'resultsPerPage':50}}
        if start+50 < 198: payload['nextPageToken'] = str(start+50)
        return httpx.Response(200,json=payload)
    api.on_read = pages
    rows, complete, errors = save_scan(brand, client)
    assert complete and not errors and len(rows) == 198
    assert sum(r['reply_state'] == 'replied' for r in rows) == 10
    assert sum(r['platform_reply_state'] == 'no_owner_reply_found' for r in rows) == 188
    assert not api.writes


def test_ten_child_batch_reconciles_delayed_receipts_without_replay(tmp_path, monkeypatch):
    import json
    from socialctl.management.community_batches import apply_community_batch
    brand, api, client = youtube_scan(tmp_path, replies=[])
    api.threads = []
    for index in range(10):
        top = comment(f'parent-{index}', author='viewer')
        top['snippet']['publishedAt'] = '2026-10-10T12:00:00Z'
        api.threads.append(thread(f'thread-{index}', top))
    save_scan(brand, client)
    delayed = set()
    def on_write(request):
        payload = json.loads(request.content)
        parent = payload['snippet']['parentId']
        reply = comment('created-'+parent, parent=parent, text=payload['snippet']['textOriginal'])
        api.replies.append(reply)
        delayed.add(parent)
        return httpx.Response(200,json=reply)
    def on_read(request):
        parent = request.url.params.get('parentId')
        if request.url.path.endswith('/comments') and parent in delayed:
            delayed.remove(parent)
            return httpx.Response(200,json={'items':[], 'pageInfo':{'totalResults':0,'resultsPerPage':50}})
    api.on_write, api.on_read = on_write, on_read
    transport = lambda *args: httpx.Client(transport=httpx.MockTransport(api.handler))
    monkeypatch.setattr('socialctl.engagement.http_client', transport)
    monkeypatch.setattr('socialctl.management.community_batches.http_client', transport)
    selections = [dict(platform='youtube', account_id='channel-a', comment_id=f'parent-{i}', text=f'Exact reply {i}') for i in range(10)]
    batch = prepare_replies(brand, {'version':1,'replies':selections}, budget=ReadBudget(30), prepared=[])
    result = apply_community_batch(brand, batch.id, batch.fingerprint)
    assert result.status == 'verified' and len(api.writes) == 10
    assert all(r['reply_state'] == 'replied' for r in review_inbox(brand)['items'])
    again = apply_community_batch(brand, batch.id, batch.fingerprint)
    assert again.status == 'verified' and len(api.writes) == 10


def test_meta_resumed_tail_never_proves_absence_of_owner_replies(tmp_path):
    from tests.test_meta_comments import service as meta_service
    from tests.test_meta_comment_inbox import remote_row
    from socialctl.management.meta_comment_inbox import CommentInboxStore, sync_inbox
    _, brand, graph, client, _ = meta_service(tmp_path)
    parent = remote_row(graph, '800', 'Question')
    other = remote_row(graph, '901', 'External reply')
    other['parent'] = {'id':'800'}
    graph.pages = lambda r: {'data':[parent]}
    inbox = CommentInboxStore(brand.raiz)
    sync_inbox(client, inbox, media_id='789')
    graph.pages = lambda r: ({'data':[]} if r.url.params.get('after') else
        {'data':[other], 'paging':{'cursors':{'after':'next'}, 'next':'https://graph.facebook.com/next'}}) if r.url.path.endswith('/800/comments') else {'data':[parent]}
    sync_inbox(client, inbox, media_id='789', parent_id='800', max_pages=1)
    result = sync_inbox(client, inbox, media_id='789', parent_id='800', resume=True)
    assert result['complete'] is True
    record = next(r for r in inbox.load().comments if r.comment_id == '800')
    assert record.reply_evidence['state'] == 'unknown'


def test_meta_unified_scan_exposes_own_reply_evidence_from_later_page(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from tests.test_meta_comments import service as meta_service
    from tests.test_meta_comment_inbox import remote_row
    from socialctl.inbox import sync_brand_inbox
    from socialctl.management.meta_client import MetaClient
    from socialctl.models import Platform
    _, brand, graph, client, _ = meta_service(tmp_path)
    parent = remote_row(graph, '800', 'Question', created='2026-10-14T00:00:00Z')
    other = remote_row(graph, '901', 'External reply')
    own = remote_row(graph, '902', 'Our prior answer', author='123')
    other['parent'] = own['parent'] = {'id':'800'}
    def pages(request):
        if request.url.path.endswith('/789/comments'): return {'data':[parent]}
        if request.url.params.get('after'): return {'data':[own]}
        return {'data':[other], 'paging':{'cursors':{'after':'next'}, 'next':'https://graph.facebook.com/next'}}
    graph.pages = pages
    monkeypatch.setattr(MetaClient, 'content_list', lambda *a, **kw: {'data':[{'id':'789'}], 'complete':True})
    monkeypatch.setattr('socialctl.inbox.read_client', lambda *a, **kw: httpx.Client(transport=httpx.MockTransport(graph)))
    result = sync_brand_inbox(brand, since=datetime(2026,10,10,tzinfo=timezone.utc), budget=ReadBudget(10), only=[Platform.FACEBOOK])
    rows = review_inbox(brand)['items']
    assert len(rows) == 1 and rows[0]['reply_state'] == 'replied'
    assert rows[0]['reply_evidence']['reply_ids'] == ['902']
    assert not graph.writes


def test_meta_fresh_top_read_invalidates_previous_negative_reply_coverage(tmp_path):
    from tests.test_meta_comments import service as meta_service
    from tests.test_meta_comment_inbox import remote_row
    from socialctl.management.meta_comment_inbox import CommentInboxStore, sync_inbox
    _, brand, graph, client, _ = meta_service(tmp_path)
    parent = remote_row(graph, '800', 'Question')
    graph.pages = lambda r: {'data':[parent] if r.url.path.endswith('/789/comments') else []}
    inbox = CommentInboxStore(brand.raiz)
    sync_inbox(client, inbox, media_id='789')
    sync_inbox(client, inbox, media_id='789', parent_id='800')
    assert inbox.load().comments[0].reply_evidence['state'] == 'no_owner_reply_found'
    # A new scan may stop before reaching this parent's replies.
    sync_inbox(client, inbox, media_id='789')
    assert inbox.load().comments[0].reply_evidence['state'] == 'unknown'


def test_platform_reply_does_not_resolve_uncertain_local_write(tmp_path):
    from socialctl.management.community_changes import CommunityStore, prepare_community
    brand, api, client = youtube_scan(tmp_path)
    store = CommunityStore(brand.raiz)
    child = prepare_community(client, store, dict(action='reply', video_id='video-1',
        thread_id='UgxThread', parent_id='UgxTop', text='A distinct uncertain reply'))
    child.status = 'uncertain'
    store.save(child)
    rows, _, _ = save_scan(brand, client)
    assert rows[0]['platform_reply_state'] == 'owner_reply_found'
    assert rows[0]['reply_state'] == 'uncertain'
    assert store.load(child.id).status == 'uncertain'
    assert store.load(child.id).result_id is None and not api.writes


@pytest.mark.parametrize('baseline', ['missing', 'unknown', 'negative'])
@pytest.mark.parametrize('remote', ['owner', 'partial', 'hidden_author'])
def test_native_meta_draft_rechecks_platform_replies_before_preparing(tmp_path, baseline, remote):
    from tests.test_meta_comments import service as meta_service
    from tests.test_meta_comment_inbox import remote_row
    from socialctl.management.meta_comment_inbox import CommentInboxStore, sync_inbox, prepare_inbox_draft
    from socialctl.management.reply_evidence import reply_evidence
    _, brand, graph, client, changes = meta_service(tmp_path)
    parent = remote_row(graph, '800', 'Question')
    own = remote_row(graph, '900', 'Already answered', author='123')
    own['parent'] = {'id':'800'}
    graph.pages = lambda request: {'data':[parent] if request.url.path.endswith('/789/comments') else []}
    inbox = CommentInboxStore(brand.raiz)
    sync_inbox(client, inbox, media_id='789')
    if baseline != 'missing':
        data = inbox.load()
        data.comments[0].reply_evidence = reply_evidence('facebook', '123', '789', '800', complete=baseline == 'negative')
        inbox.save(data)
    def pages(request):
        if request.url.path.endswith('/789/comments'): return {'data':[parent]}
        if remote == 'partial': return {'data':[], 'paging':{'next':'https://graph.facebook.com/next'}}
        if remote == 'hidden_author':
            return {'data':[{k:v for k,v in own.items() if k != 'from'}]}
        return {'data':[own]}
    graph.pages = pages
    with pytest.raises(Exception, match='owner reply|coverage is incomplete'):
        prepare_inbox_draft(client, changes, inbox, media_id='789', comment_id='800', text='Duplicate')
    assert not list(changes.root.glob('*.json'))
    assert inbox.load().comments[0].change_id is None and not graph.writes


def test_deliberate_standalone_reply_proposal_remains_available_after_inbox_block(tmp_path):
    from tests.test_meta_comment_inbox import observed_comment, remote_row
    from socialctl.management.meta_comment_inbox import prepare_inbox_draft
    from socialctl.management.meta_comments import prepare_comment
    _, graph, client, changes, inbox = observed_comment(tmp_path)
    graph.comments['900'] = remote_row(graph, '900', 'Prior answer', author='123')
    graph.comments['900']['parent'] = {'id':'800'}
    with pytest.raises(Exception, match='owner reply'):
        prepare_inbox_draft(client, changes, inbox, media_id='789', comment_id='800', text='Default inbox reply')
    followup = prepare_comment(client, changes, media_id='789', action='reply',
        parent_id='800', text='Explicitly requested additional clarification')
    assert followup.action == 'reply' and followup.parent_id == '800'
    assert followup.inbox_binding is None and followup.status == 'proposed'
    assert followup.text == 'Explicitly requested additional clarification'
    assert not graph.writes
