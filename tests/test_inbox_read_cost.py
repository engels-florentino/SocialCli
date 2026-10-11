"""Bounded inbox scans reuse verified bindings, without changing mutation clients."""
import httpx
import pytest

from socialctl.inbox import _youtube_rows


def viewer_threads(api, count=6, replies=0):
    from tests.test_youtube_community import thread, comment
    api.threads = []
    for index in range(count):
        top = comment(f'top-{index}', author='viewer')
        top['snippet']['publishedAt'] = '2026-10-10T12:00:00Z'
        row = thread(f'thread-{index}', top)
        row['snippet']['totalReplyCount'] = replies
        api.threads.append(row)


def test_youtube_zero_reply_threads_do_not_fetch_empty_reply_lists(tmp_path):
    from tests.test_youtube_community import service
    _, api, client, _ = service(tmp_path)
    viewer_threads(api)
    rows, complete, errors = _youtube_rows(client, 'video-1')
    assert len(rows) == 6 and complete and not errors
    assert not [r for r in api.requests if r.url.path.endswith('/comments')]


@pytest.mark.parametrize('count', [None, False, '0', -1, 1])
def test_unknown_reply_counts_still_fetch_replies(tmp_path, count):
    from tests.test_youtube_community import service
    _, api, client, _ = service(tmp_path)
    viewer_threads(api, count=1, replies=count)
    _youtube_rows(client, 'video-1')
    assert any(r.url.path.endswith('/comments') for r in api.requests)


def test_youtube_scan_uses_one_identity_and_owned_video_check(tmp_path):
    from tests.test_youtube_community import service
    from socialctl.management.inbox_readers import YouTubeInboxClient
    _, api, original, _ = service(tmp_path)
    viewer_threads(api, replies=1)
    client = YouTubeInboxClient(original.brand, original.client)
    rows, complete, errors = _youtube_rows(client, 'video-1')
    assert len(rows) == 6 and complete and not errors
    assert len(api.requests) == 9  # identity + owned video + threads + six replies
    with pytest.raises(Exception, match='read-only'):
        client._request('POST', 'https://www.googleapis.com/youtube/v3/comments')
    with pytest.raises(Exception, match='read-only'):
        client.update_snippet('video-1', {}, etag='video-etag')
    assert not api.writes
    # A separate mutation-capable client must still refresh remote ownership.
    api.actor = 'other-channel'
    with pytest.raises(Exception, match='channel'):
        original.list_threads('video-1')


@pytest.mark.parametrize('platform', ['facebook', 'instagram'])
def test_meta_scan_reuses_verified_parent_instead_of_rescanning_all_comments(tmp_path, platform):
    from tests.test_meta_comments import service
    from tests.test_meta_comment_inbox import remote_row
    from socialctl.management.inbox_readers import MetaInboxClient
    _, brand, graph, original, _ = service(tmp_path, platform)
    top = remote_row(graph, '800', 'Top')
    reply = remote_row(graph, '801', 'Reply')
    if platform == 'facebook': reply['parent'] = {'id':'800'}
    else: reply['parent_id'] = '800'
    graph.pages = lambda request: {'data':[reply] if '/800/' in request.url.path else [top]}
    requests = []
    def provider(request):
        requests.append(request)
        return graph(request)
    client = MetaInboxClient(brand, original.platform, httpx.Client(transport=httpx.MockTransport(provider)))
    assert client.list_comments('789')['data'] == [top]
    assert client.list_comments('789', parent_id='800')['data'] == [reply]
    assert len(requests) == 4  # identity + media owner + top comments + replies
    with pytest.raises(Exception, match='read-only'): client._request('POST','789/comments')
    assert not graph.writes
    graph.owner = '999'
    with pytest.raises(Exception, match='owner'): original.list_comments('789')


def test_youtube_deadline_keeps_all_verified_tops_without_repeated_failed_reads(tmp_path):
    from tests.test_youtube_community import service
    from socialctl.read_budget import ReadDeadlineExceeded
    _, api, client, _ = service(tmp_path)
    viewer_threads(api, replies=1)
    attempts = []
    def read(request):
        if request.url.path.endswith('/comments'):
            attempts.append(request)
            raise ReadDeadlineExceeded()
    api.on_read = read
    rows, complete, errors = _youtube_rows(client, 'video-1')
    assert len(rows) == 6 and not complete
    assert len(attempts) == 1
    assert len(errors) == 1 and 'time limit' in errors[0]


def test_youtube_later_page_deadline_keeps_verified_rows(tmp_path):
    from tests.test_youtube_community import service
    from socialctl.read_budget import ReadDeadlineExceeded
    _, api, client, _ = service(tmp_path)
    viewer_threads(api, count=1)
    def read(request):
        if request.url.path.endswith('/commentThreads'):
            if request.url.params.get('pageToken'): raise ReadDeadlineExceeded()
            return httpx.Response(200, json={'items':api.threads, 'nextPageToken':'next',
                'pageInfo':{'totalResults':2,'resultsPerPage':50}})
    api.on_read = read
    report = client.list_threads('video-1')
    assert len(report['items']) == 1 and not report['complete']
    assert report.get('timed_out') and 'time limit' in report['error']


def test_youtube_transport_error_identifies_phase_without_echoing_credentials(tmp_path):
    from tests.test_youtube_community import service
    _, api, client, _ = service(tmp_path)
    def read(request): raise httpx.ConnectError('https://secret-token@example.invalid')
    api.on_read = read
    with pytest.raises(Exception) as caught: client.identity()
    assert 'ConnectError' in str(caught.value)
    assert 'secret-token' not in str(caught.value)


def test_meta_transport_error_identifies_phase_without_echoing_credentials(tmp_path):
    from tests.test_meta_comments import service
    _, brand, graph, client, _ = service(tmp_path)
    graph.pages = lambda request: (_ for _ in ()).throw(httpx.ReadTimeout('secret-token'))
    with pytest.raises(Exception) as caught: client.list_comments('789')
    assert 'ReadTimeout' in str(caught.value)
    assert 'secret-token' not in str(caught.value)


def test_meta_later_page_deadline_retains_comments(tmp_path):
    from tests.test_meta_comments import service
    from tests.test_meta_comment_inbox import remote_row
    from socialctl.read_budget import ReadDeadlineExceeded
    _, _, graph, client, _ = service(tmp_path)
    top = remote_row(graph, '800', 'Top')
    def pages(request):
        if request.url.params.get('after'): raise ReadDeadlineExceeded()
        return {'data':[top], 'paging':{'next':'https://ignored.invalid','cursors':{'after':'next'}}}
    graph.pages = pages
    report = client.list_comments('789')
    assert report['data'] == [top] and report.get('timed_out')
    assert not report['complete'] and 'time limit' in report['error']


@pytest.mark.parametrize('platform', ['youtube','facebook'])
def test_deadline_during_mutation_is_still_uncertain(tmp_path, platform):
    from socialctl.read_budget import ReadDeadlineExceeded
    if platform == 'youtube':
        from tests.test_youtube_community import service
        from socialctl.management.youtube_resources import ResourceUncertain
        _, api, client, _ = service(tmp_path)
        api.on_write = lambda request: (_ for _ in ()).throw(ReadDeadlineExceeded())
        with pytest.raises(ResourceUncertain):
            client._request('POST','https://www.googleapis.com/youtube/v3/comments',json={})
        assert len(api.writes) == 1
    else:
        from tests.test_meta_comments import service
        from socialctl.management.meta_comments import CommentUncertain
        _, _, graph, client, _ = service(tmp_path)
        graph.after_write = lambda: (_ for _ in ()).throw(ReadDeadlineExceeded())
        with pytest.raises(CommentUncertain):
            client._request('POST','789/comments',data={'message':'Test'})
        assert len(graph.writes) == 1


@pytest.mark.parametrize('bad_field', ['from', 'message', 'id', 'parent'])
def test_meta_invalid_comment_does_not_discard_other_verified_comments(tmp_path, bad_field):
    from tests.test_meta_comments import service
    from tests.test_meta_comment_inbox import remote_row
    from socialctl.management.meta_comment_inbox import CommentInboxStore, sync_inbox
    from socialctl.management.inbox_readers import MetaInboxClient
    _, brand, graph, original, _ = service(tmp_path)
    valid = remote_row(graph, '800', 'A valid viewer comment')
    bad = remote_row(graph, '801', 'Unverifiable')
    bad[bad_field] = {'id':'999'} if bad_field == 'parent' else None
    graph.pages = lambda request: {'data':[bad, valid]}
    client = MetaInboxClient(brand, original.platform, original.client)
    store = CommentInboxStore(brand.raiz)
    result = sync_inbox(client, store, media_id='789')
    assert [r.comment_id for r in store.load().comments] == ['800']
    assert not result['complete'] and result['skipped_unverifiable'] == 1
    assert result['parent_ids'] == ['800']
    assert result['error'] and not store.load().cursors[0].complete


def test_meta_conflicting_duplicate_cannot_be_adopted_as_a_reply_parent(tmp_path):
    from tests.test_meta_comments import service
    from tests.test_meta_comment_inbox import remote_row
    from socialctl.management.meta_comment_inbox import CommentInboxStore, sync_inbox
    from socialctl.management.inbox_readers import MetaInboxClient
    _, brand, graph, original, _ = service(tmp_path)
    first = remote_row(graph, '800', 'First')
    conflict = remote_row(graph, '800', 'Different')
    valid = remote_row(graph, '802', 'Unambiguous')
    graph.pages = lambda request: {'data':[first, conflict, valid]}
    client = MetaInboxClient(brand, original.platform, original.client)
    store = CommentInboxStore(brand.raiz)
    result = sync_inbox(client, store, media_id='789')
    assert [r.comment_id for r in store.load().comments] == ['802']
    assert not result['complete'] and result['parent_ids'] == ['802']


def test_unified_inbox_explains_skipped_comments_while_returning_valid_observations(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from tests.test_meta_comments import service
    from tests.test_meta_comment_inbox import remote_row
    from socialctl import inbox as mod
    from socialctl.management.meta_client import MetaClient
    from socialctl.read_budget import ReadBudget
    _, brand, graph, _, _ = service(tmp_path)
    recent = remote_row(graph,'800','Recent',created='2026-10-10T12:00:00Z')
    own = remote_row(graph,'801','Our comment',author='123',created='2026-10-10T12:00:00Z')
    old = remote_row(graph,'802','Older',created='2026-10-08T12:00:00Z')
    invalid = remote_row(graph,'803','No author');invalid.pop('from')
    graph.pages = lambda request: {'data':[recent,own,old,invalid] if request.url.path.endswith('/789/comments') else []}
    monkeypatch.setattr(mod,'read_client',lambda *a,**k:httpx.Client(transport=httpx.MockTransport(graph)))
    monkeypatch.setattr(MetaClient,'content_list',lambda *a,**k:{'data':[{'id':'789'}],'complete':True})
    report = mod.sync_brand_inbox(brand,since=datetime(2026,10,9,tzinfo=timezone.utc),budget=ReadBudget(10))
    assert [r['comment_id'] for r in report.data['items']] == ['800']
    coverage = next(r for r in report.coverage if r['platform'] == 'facebook')
    assert coverage['skipped_comments'] == {'own':1,'before_since':1,'unverifiable':1}
    assert not coverage['complete'] and not report.data['watermarks']


@pytest.mark.parametrize('platform', ['facebook','instagram'])
def test_meta_missing_creation_time_is_retained_with_incomplete_interval_coverage(tmp_path, platform):
    from tests.test_meta_comments import service
    from tests.test_meta_comment_inbox import remote_row
    from socialctl.management.meta_comment_inbox import CommentInboxStore, sync_inbox
    _, brand, graph, client, _ = service(tmp_path, platform)
    unknown = remote_row(graph,'800','Unknown date')
    unknown.pop('created_time' if platform == 'facebook' else 'timestamp')
    graph.pages = lambda request: {'data':[unknown]}
    store = CommentInboxStore(brand.raiz)
    result = sync_inbox(client,store,media_id='789',since='2026-10-09T00:00:00Z')
    assert store.load().comments[0].created_time is None
    assert not result['complete'] and 'creation time' in result['error']
    assert result['unknown_creation_time'] == 1 and result['skipped_unverifiable'] == 0
    assert result['next_since'] == '2026-10-09T00:00:00Z'
