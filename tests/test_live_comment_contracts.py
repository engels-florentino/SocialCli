"""Sanitized live-response shapes, fictional accounts, no live writes."""
import httpx
import pytest
from tests.test_youtube_community import service as youtube, comment
from tests.test_meta_comments import service as meta
from tests.test_meta_comment_inbox import remote_row
from socialctl.agent_errors import error_document
from socialctl.management.meta_comment_inbox import CommentInboxStore, sync_inbox
from socialctl.management.youtube_resources import ResourceError


def test_comments_cursor_traversal_accepts_omitted_total_with_valid_envelope(tmp_path):
    _, api, client, _ = youtube(tmp_path)
    def response(r):
        if not r.url.path.endswith('/comments'): return None
        second = bool(r.url.params.get('pageToken'))
        return httpx.Response(200, json={'kind':'youtube#commentListResponse','etag':'opaque',
            'pageInfo':{'resultsPerPage':50},
            'items':[comment('reply-b' if second else 'reply-a',parent='UgxTop')],
            **({} if second else {'nextPageToken':'next'})})
    api.on_read=response
    result=client.list_replies('video-1','UgxThread','UgxTop')
    assert result['complete'] and len(result['items'])==2 and result['pages']==2
    assert result['absence_proven'] is False and not api.writes


def test_disabled_comments_are_not_reported_as_missing_permissions(tmp_path):
    _, api, client, _ = youtube(tmp_path)
    api.on_read=lambda r:httpx.Response(403,json={'error':{'code':403,'errors':[{'reason':'commentsDisabled','message':'SECRET'}]}}) if r.url.path.endswith('/commentThreads') else None
    result=client.list_threads('video-1')
    assert result['error_detail']['code']=='comments_disabled'
    assert not result['error_detail']['requires_user_action']
    assert 'SECRET' not in str(result) and not api.writes


def test_quota_403_has_retry_later_action():
    result=error_document(ResourceError('safe',http_status=403,provider_reason='quotaExceeded'),read_only=True)
    assert result['code']=='rate_limited' and result['retryable']


@pytest.mark.parametrize('platform',['facebook','instagram'])
def test_missing_author_is_preserved_as_unknown_not_invented_or_discarded(tmp_path,platform):
    _,_,graph,client,_=meta(tmp_path,platform)
    hidden=remote_row(graph,'801','A visible comment');hidden.pop('from')
    visible=remote_row(graph,'802','Another comment')
    graph.pages=lambda r:{'data':[hidden,visible]}
    store=CommentInboxStore(client.brand.raiz)
    result=sync_inbox(client,store,media_id='789')
    assert result['complete'] and result['new']==2
    assert result['unknown_author']==1 and result['skipped_unverifiable']==0
    assert store.load().comments[0].author_id is None
    assert len(result['groups'])==2 and not graph.writes


def test_old_unknown_author_is_filtered_without_poisoning_current_coverage(tmp_path):
    _,_,graph,client,_=meta(tmp_path)
    row=remote_row(graph,'801','Old',created='2025-01-01T00:00:00Z');row.pop('from')
    graph.pages=lambda r:{'data':[row]}
    result=sync_inbox(client,CommentInboxStore(client.brand.raiz),media_id='789',since='2026-01-01T00:00:00Z')
    assert result['complete'] and result['new']==0 and result['skipped_before_since']==1

@pytest.mark.parametrize('fault',[None,'wrong_actor','wrong_parent','different_link','base_link','duplicate','no_receipt','wrong_text'])
def test_facebook_alias_requires_exact_provider_comment_permalink_and_membership(tmp_path,fault):
    mod,_,graph,client,store=meta(tmp_path)
    change=mod.prepare_comment(client,store,media_id='789',text='Test link')
    url='https://www.facebook.com/reel/789/?comment_id=900'
    def after():
        graph.comments['900'].pop('parent')
        graph.comments['900']['permalink_url']=url
        if fault=='wrong_actor': graph.comments['900']['from']={'id':'999'}
        if fault=='wrong_parent': graph.comments['900']['parent']={'id':'999'}
        if fault=='wrong_text': graph.comments['900']['message']='Different'
        if fault=='base_link': graph.comments['900']['permalink_url']='https://www.facebook.com/reel/789/'
    graph.after_write=after
    def pages(r):
        row=graph.row('456_900','Test link')
        row.pop('parent')
        row['permalink_url']=url if fault!='different_link' else url+'1'
        return {'data':[row,dict(row,id='457_900')] if fault=='duplicate' else [row]}
    graph.pages=pages
    if fault=='no_receipt': graph.failure='lost-id'
    result=mod.apply_comment(client,store,change.id,change.fingerprint)
    assert result.status==('verified' if fault is None else 'uncertain')
    assert len(graph.writes)==1
    mod.reconcile_comment(client,store,change.id)
    assert len(graph.writes)==1

@pytest.mark.parametrize('field,value',[('from',{'id':'999'}),('parent',{'id':'888'}),('message','Other')])
def test_conflicting_same_permalink_is_never_filtered_out_to_make_a_match(tmp_path,field,value):
    mod,_,graph,client,store=meta(tmp_path)
    change=mod.prepare_comment(client,store,media_id='789',text='Test link')
    url='https://www.facebook.com/reel/789/?comment_id=900'
    def after():
        graph.comments['900'].pop('parent')
        graph.comments['900']['permalink_url']=url
    graph.after_write=after
    def pages(r):
        first={**graph.row('456_900','Test link'),'permalink_url':url}
        second={**first,'id':'457_900',field:value}
        return {'data':[first,second]}
    graph.pages=pages
    assert mod.apply_comment(client,store,change.id,change.fingerprint).status=='uncertain'
    assert len(graph.writes)==1


def test_unknown_becomes_own_without_stale_inbox_or_reply_target(tmp_path,monkeypatch):
    from datetime import datetime,timezone
    from socialctl import inbox as mod
    from socialctl.engagement import review_inbox
    from socialctl.management.meta_client import MetaClient
    from socialctl.management.meta_comment_inbox import prepare_inbox_draft
    from socialctl.read_budget import ReadBudget
    from socialctl.models import Platform
    cm,brand,graph,client,comments=meta(tmp_path)
    hidden=remote_row(graph,'801','A comment',created='2026-10-10T12:00:00Z');hidden.pop('from')
    graph.pages=lambda r:{'data':[hidden] if r.url.path.endswith('/789/comments') else []}
    monkeypatch.setattr(mod,'read_client',lambda *a,**k:httpx.Client(transport=httpx.MockTransport(graph)))
    monkeypatch.setattr(MetaClient,'content_list',lambda *a,**k:{'data':[{'id':'789'}],'complete':True})
    def sync():return mod.sync_brand_inbox(brand,since=datetime(2026,10,1,tzinfo=timezone.utc),budget=ReadBudget(10),only=[Platform.FACEBOOK])
    assert sync().data['items'][0]['author_id'] is None
    store=CommentInboxStore(brand.raiz)
    with pytest.raises(cm.CommentError,match='verified external author'):
        prepare_inbox_draft(client,comments,store,media_id='789',comment_id='801',text='Blocked')
    hidden['from']={'id':client.account_id}
    assert sync().data['items']==[]
    assert store.load().comments[0].author_id==client.account_id
    assert review_inbox(brand)['items']==[]
    from socialctl.management.meta_comment_inbox import grouped_inbox
    assert grouped_inbox(store.load())==[]
    with pytest.raises(cm.CommentError,match='verified external author'):
        prepare_inbox_draft(client,comments,store,media_id='789',comment_id='801',text='Blocked')
    assert not graph.writes
