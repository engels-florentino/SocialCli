import json
import httpx
import pytest
import yaml
from typer.testing import CliRunner
from socialctl.cli import app
from socialctl.inbox import UnifiedInboxStore

runner=CliRunner()


def fixture(tmp_path,monkeypatch):
    from tests.test_meta_comments import service
    from tests.test_meta_comment_inbox import remote_row
    from tests.test_youtube_community import API,comment,thread
    from socialctl.management.meta_comment_inbox import CommentInboxStore,sync_inbox
    from socialctl.models import Platform
    _,brand,graph,client,_=service(tmp_path)
    brand.cuentas['youtube']={'channel_id':'channel-a'}
    (brand.raiz/'accounts.yml').write_text(yaml.safe_dump(brand.cuentas))
    brand.guardar_secreto(Platform.YOUTUBE,{'access_token':'fictional'})
    graph.comments['800']=remote_row(graph,'800','Meta question')
    graph.pages=lambda request: {'data':[r for r in graph.comments.values() if r.get('parent', {}).get('id')==request.url.path.split('/')[-2]]}
    sync_inbox(client,CommentInboxStore(brand.raiz),media_id='789')
    api=API();api.replies=[];api.threads=[thread(top=comment(author='viewer',text='YouTube question'))]
    api.threads[0]['snippet']['topLevelComment']['snippet']['textOriginal']='YouTube question'
    rows=[dict(platform='facebook',account_id='123',media_id='789',comment_id='800',author_id='999',text='Meta question',parent_id=None),
          dict(platform='youtube',account_id='channel-a',media_id='video-1',thread_id='UgxThread',comment_id='UgxTop',author_id='viewer',text='YouTube question',parent_id=None)]
    UnifiedInboxStore(brand.raiz).observe(rows,observed_at='2026-10-14T00:00:00Z')
    requests=[]
    def handler(request):
        requests.append(request)
        return api.handler(request) if 'googleapis.com' in request.url.host else graph(request)
    def transport(*a,**kw):return httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr('socialctl.engagement.http_client',transport)
    monkeypatch.setattr('socialctl.management.community_batches.http_client',transport)
    replies=[dict(platform=r['platform'],account_id=r['account_id'],comment_id=r['comment_id'],text='Reply '+r['platform']) for r in rows]
    path=tmp_path/'replies.yml';path.write_text(yaml.safe_dump({'version':1,'replies':replies}))
    return brand,graph,api,path,replies,requests


def invoke(tmp_path,*args,input=None):
    return runner.invoke(app,[*args,'--brand','Example','--root',str(tmp_path),'--json'],input=input)


def test_mixed_inbox_draft_full_preview_batch_apply_and_offline_review(tmp_path,monkeypatch):
    brand,graph,api,path,_,requests=fixture(tmp_path,monkeypatch)
    review=invoke(tmp_path,'inbox-review')
    assert review.exit_code==0,review.output
    assert {i['reply_state'] for i in json.loads(review.stdout)['items']}=={'observed'}
    result=invoke(tmp_path,'inbox-draft','--file',str(path),'--dry-run')
    assert result.exit_code==0,result.output
    value=json.loads(result.stdout);batch=value['batch']
    assert len(batch['children'])==2
    assert 'Reply facebook' in result.stderr and 'Reply youtube' in result.stderr
    assert not graph.writes and not api.writes
    review=json.loads(invoke(tmp_path,'inbox-review').stdout)
    assert {r['reply_state'] for r in review['items']}=={'draft'}
    result=invoke(tmp_path,'changes','apply-batch',batch['id'],'--approval',batch['fingerprint'])
    assert result.exit_code==0,result.output
    assert len(graph.writes)==len(api.writes)==1
    count=len(requests)
    review=json.loads(invoke(tmp_path,'inbox-review').stdout)
    assert len(requests)==count and review['coverage']['live_verified'] is False
    assert {r['reply_state'] for r in review['items']}=={'replied'}
    again=invoke(tmp_path,'inbox-draft','--file',str(path),'--dry-run')
    assert again.exit_code==1 and len(requests)==count


@pytest.mark.parametrize('case',['wrong_account','missing','duplicate','nested','tiktok','empty_text'])
def test_draft_validates_every_selection_before_provider_reads(tmp_path,monkeypatch,case):
    brand,graph,api,path,replies,requests=fixture(tmp_path,monkeypatch)
    if case=='wrong_account':replies[1]['account_id']='other'
    if case=='missing':replies[1]['comment_id']='missing'
    if case=='duplicate':replies[1]=replies[0]
    if case=='tiktok':replies[1]['platform']='tiktok'
    if case=='empty_text':replies[1]['text']=' '
    if case=='nested':
        store=UnifiedInboxStore(brand.raiz);data=store.load();row=next(r for r in data['items'].values() if r['platform']=='youtube');row['parent_id']='parent';store.path.write_text(json.dumps(data))
    path.write_text(yaml.safe_dump({'version':1,'replies':replies}))
    result=invoke(tmp_path,'inbox-draft','--file',str(path),'--dry-run')
    assert result.exit_code==1,result.output
    assert json.loads(result.stdout)['status']=='error'
    assert not requests and not graph.writes and not api.writes


def test_changed_second_parent_retains_first_draft_and_sends_nothing(tmp_path,monkeypatch):
    _,graph,api,path,_,_=fixture(tmp_path,monkeypatch)
    api.threads[0]['snippet']['topLevelComment']['snippet']['textOriginal']='Changed remotely'
    result=invoke(tmp_path,'inbox-draft','--file',str(path),'--dry-run')
    assert result.exit_code==1,result.output
    value=json.loads(result.stdout)
    assert value['status']=='partial' and len(value['prepared'])==1 and value['batch'] is None
    assert value['prepared'][0]['provider']=='meta-comments'
    assert not graph.writes and not api.writes
    assert json.loads(invoke(tmp_path,'inbox-review').stdout)['items'][0]['reply_state']=='draft'


def test_missing_dry_run_creates_no_drafts_or_requests(tmp_path,monkeypatch):
    _,_,_,path,_,requests=fixture(tmp_path,monkeypatch)
    result=invoke(tmp_path,'inbox-draft','--file',str(path))
    assert result.exit_code==1 and not requests
    assert {i['reply_state'] for i in json.loads(invoke(tmp_path,'inbox-review').stdout)['items']}=={'observed'}


def test_review_empty_cache_does_not_claim_complete_or_create_state(tmp_path):
    from socialctl.brands import crear_brand
    brand=crear_brand(tmp_path,'Example')
    result=invoke(tmp_path,'inbox-review')
    assert result.exit_code==0
    value=json.loads(result.stdout)
    assert value['items']==[] and value['coverage']['complete'] is False
    assert not UnifiedInboxStore(brand.raiz).path.exists()


def test_draft_interrupt_keeps_prepared_refs_and_no_writes(tmp_path,monkeypatch):
    _,graph,api,path,_,_=fixture(tmp_path,monkeypatch)
    api.on_read=lambda request:(_ for _ in ()).throw(KeyboardInterrupt())
    result=invoke(tmp_path,'inbox-draft','--file',str(path),'--dry-run')
    assert result.exit_code==1
    value=json.loads(result.stdout)
    assert value['status']=='partial' and len(value['prepared'])==1 and value['batch'] is None
    assert value['error']['code']=='read_interrupted'
    assert not graph.writes and not api.writes


def test_failed_meta_inbox_save_returns_durable_child_reference(tmp_path,monkeypatch):
    brand,graph,api,path,_,_=fixture(tmp_path,monkeypatch)
    from socialctl.management.meta_comment_inbox import CommentInboxStore
    from socialctl.management.meta_comments import CommentError,CommentStore
    monkeypatch.setattr(CommentInboxStore,'save',lambda *a:(_ for _ in ()).throw(CommentError('private storage diagnostic')))
    result=invoke(tmp_path,'inbox-draft','--file',str(path),'--dry-run')
    value=json.loads(result.stdout)
    assert result.exit_code==1 and value['status']=='partial'
    assert len(value['prepared'])==1 and value['batch'] is None
    ref=value['prepared'][0]
    assert ref['provider']=='meta-comments' and CommentStore(brand.raiz).load(ref['change_id']).status=='proposed'
    assert not graph.writes and not api.writes and 'private storage diagnostic' not in result.output


def test_previous_account_journals_do_not_block_remaining_current_accounts(tmp_path,monkeypatch):
    brand,graph,api,path,replies,_=fixture(tmp_path,monkeypatch)
    assert invoke(tmp_path,'inbox-draft','--file',str(path),'--dry-run').exit_code==0
    brand.cuentas['facebook']['page_id']='different-page'
    (brand.raiz/'accounts.yml').write_text(yaml.safe_dump(brand.cuentas))
    result=invoke(tmp_path,'inbox-review')
    assert result.exit_code==0,result.output
    value=json.loads(result.stdout)
    assert {r['platform'] for r in value['items']}=={'youtube'}
    assert value['coverage']['excluded_proposals']==1 and value['coverage']['excluded_accounts']==1
    path.write_text(yaml.safe_dump({'version':1,'replies':[replies[1]]}))
    assert invoke(tmp_path,'inbox-draft','--file',str(path),'--dry-run').exit_code==0
    assert not graph.writes and not api.writes


def test_foreign_brand_current_account_journal_still_blocks_review(tmp_path,monkeypatch):
    brand,_,_,path,_,_=fixture(tmp_path,monkeypatch)
    result=invoke(tmp_path,'inbox-draft','--file',str(path),'--dry-run')
    ref=json.loads(result.stdout)['prepared'][0]
    from socialctl.management.meta_comments import CommentStore,fingerprint
    store=CommentStore(brand.raiz);child=store.load(ref['change_id'])
    child.brand='Other';child.fingerprint=fingerprint(child);store.save(child)
    result=invoke(tmp_path,'inbox-review')
    assert result.exit_code==1 and json.loads(result.stdout)['status']=='error'


def test_unknown_facebook_author_can_prepare_and_reply_on_verified_owned_media(tmp_path, monkeypatch):
    brand, graph, api, path, replies, requests = fixture(tmp_path, monkeypatch)
    graph.comments['800'].pop('from')
    from socialctl.management.meta_comment_inbox import CommentInboxStore
    inbox = CommentInboxStore(brand.raiz)
    data = inbox.load(); data.comments[0].author_id = None; inbox.save(data)
    unified = UnifiedInboxStore(brand.raiz)
    data = unified.load()
    next(row for row in data['items'].values() if row['platform'] == 'facebook')['author_id'] = None
    unified.path.write_text(json.dumps(data))
    path.write_text(yaml.safe_dump({'version': 1, 'replies': replies[:1]}))
    result = invoke(tmp_path, 'inbox-draft', '--file', str(path), '--dry-run')
    assert result.exit_code == 0, result.output
    batch = json.loads(result.stdout)['batch']
    assert batch['children'][0]['proposal']['before']['parent']['author_id'] is None
    result = invoke(tmp_path, 'changes', 'apply-batch', batch['id'], '--approval', batch['fingerprint'])
    assert result.exit_code == 0, result.output
    assert len(graph.writes) == 1 and graph.writes[0][1] == '800/comments'


def test_failed_draft_identifies_child_platform_and_safe_reason(tmp_path, monkeypatch):
    _, graph, api, path, _, _ = fixture(tmp_path, monkeypatch)
    api.threads[0]['snippet']['topLevelComment']['snippet']['textOriginal'] = 'Changed remotely'
    result = invoke(tmp_path, 'inbox-draft', '--file', str(path), '--dry-run')
    value = json.loads(result.stdout)
    assert value['failed']['child_index'] == 2
    assert value['failed']['platform'] == 'youtube'
    assert value['failed']['comment_id'] == 'UgxTop'
    assert value['failed']['reason'] == 'comment_changed'
    assert len(value['prepared']) == 1 and not api.writes and not graph.writes


@pytest.mark.parametrize('platform', ['youtube', 'facebook'])
def test_nested_context_requires_explicit_verified_thread_parent_mapping(tmp_path, monkeypatch, platform):
    brand, graph, api, path, replies, requests = fixture(tmp_path, monkeypatch)
    from tests.test_youtube_community import comment
    if platform == 'youtube':
        source = dict(platform=platform, account_id='channel-a', media_id='video-1', thread_id='UgxThread',
            comment_id='UgxNested', parent_id='UgxTop', author_id='viewer-two', text='Nested question')
        remote = comment('UgxNested', author='viewer-two', parent='UgxTop')
        remote['snippet']['textDisplay'] = source['text']; api.replies.append(remote)
    else:
        source = dict(platform=platform, account_id='123', media_id='789', comment_id='801',
            parent_id='800', author_id='998', text='Nested question')
        graph.comments['801'] = graph.row('801', source['text'], author='998')
        graph.comments['801']['parent'] = {'id': '800'}
    UnifiedInboxStore(brand.raiz).observe([source], observed_at='2026-10-14T01:00:00Z')
    selection = {key: source[key] for key in ('platform', 'account_id', 'comment_id')}
    selection.update(text='Explicit thread reply', reply_parent_id=source['parent_id'])
    path.write_text(yaml.safe_dump({'version': 1, 'replies': [selection]}))
    result = invoke(tmp_path, 'inbox-draft', '--file', str(path), '--dry-run')
    assert result.exit_code == 0, result.output
    batch = json.loads(result.stdout)['batch']; proposal = batch['children'][0]['proposal']
    parent = proposal['edit']['parent_id'] if platform == 'youtube' else proposal['parent_id']
    assert parent == source['parent_id']
    assert proposal['before']['reply_context']['comment_id'] == source['comment_id']
    assert not graph.writes and not api.writes
    result = invoke(tmp_path, 'changes', 'apply-batch', batch['id'], '--approval', batch['fingerprint'])
    assert result.exit_code == 0, result.output
    assert len(api.writes if platform == 'youtube' else graph.writes) == 1
    review = json.loads(invoke(tmp_path, 'inbox-review').stdout)
    assert next(row for row in review['items'] if row['comment_id'] == source['comment_id'])['reply_state'] == 'replied'


def test_nested_mapping_cannot_silently_choose_an_unrelated_parent(tmp_path, monkeypatch):
    brand, graph, api, path, replies, requests = fixture(tmp_path, monkeypatch)
    source = dict(platform='youtube', account_id='channel-a', media_id='video-1', thread_id='UgxThread',
        comment_id='UgxNested', parent_id='UgxTop', author_id='viewer-two', text='Nested question')
    UnifiedInboxStore(brand.raiz).observe([source], observed_at='2026-10-14T01:00:00Z')
    path.write_text(yaml.safe_dump({'version': 1, 'replies': [dict(platform='youtube', account_id='channel-a',
        comment_id='UgxNested', reply_parent_id='Unrelated', text='Reply')]}))
    result = invoke(tmp_path, 'inbox-draft', '--file', str(path), '--dry-run')
    assert result.exit_code == 1
    assert json.loads(result.stdout)['failed']['reason'] == 'reply_parent_mismatch'
    assert not requests


def test_mapped_reply_context_change_blocks_write_after_preview(tmp_path, monkeypatch):
    brand, graph, api, path, _, _ = fixture(tmp_path, monkeypatch)
    source = dict(platform='facebook', account_id='123', media_id='789', comment_id='801',
        parent_id='800', author_id='998', text='Nested question')
    graph.comments['801'] = graph.row('801', source['text'], author='998')
    graph.comments['801']['parent'] = {'id': '800'}
    UnifiedInboxStore(brand.raiz).observe([source], observed_at='2026-10-14T01:00:00Z')
    path.write_text(yaml.safe_dump({'version': 1, 'replies': [dict(platform='facebook', account_id='123',
        comment_id='801', reply_parent_id='800', text='Reply')]}))
    result = invoke(tmp_path, 'inbox-draft', '--file', str(path), '--dry-run')
    batch = json.loads(result.stdout)['batch']
    graph.comments['801']['message'] = 'Changed since preview'
    result = invoke(tmp_path, 'changes', 'apply-batch', batch['id'], '--approval', batch['fingerprint'])
    value = json.loads(result.stdout)
    assert result.exit_code == 1 and value['summary']['blocked_count'] == 1
    assert not graph.writes


def test_instagram_nested_context_maps_explicitly_to_verified_parent_reply_edge(tmp_path, monkeypatch):
    from tests.test_meta_comments import service
    from tests.test_meta_comment_inbox import remote_row
    from socialctl.management.meta_comment_inbox import CommentInboxStore, sync_inbox
    from socialctl.engagement import prepare_replies
    from socialctl.management.community_batches import apply_community_batch
    from socialctl.read_budget import ReadBudget
    _, brand, graph, client, _ = service(tmp_path, 'instagram')
    graph.comments['800'] = remote_row(graph, '800', 'Root question')
    sync_inbox(client, CommentInboxStore(brand.raiz), media_id='789')
    graph.comments['801'] = remote_row(graph, '801', 'Nested question', author='998')
    graph.comments['801']['parent_id'] = '800'
    rows = [dict(platform='instagram', account_id='456', media_id='789', comment_id='800',
        author_id='999', text='Root question', parent_id=None),
        dict(platform='instagram', account_id='456', media_id='789', comment_id='801',
        author_id='998', text='Nested question', parent_id='800')]
    UnifiedInboxStore(brand.raiz).observe(rows, observed_at='2026-10-14T00:00:00Z')
    transport = lambda *args: httpx.Client(transport=httpx.MockTransport(graph))
    monkeypatch.setattr('socialctl.engagement.http_client', transport)
    monkeypatch.setattr('socialctl.management.community_batches.http_client', transport)
    batch = prepare_replies(brand, {'version': 1, 'replies': [dict(platform='instagram', account_id='456',
        comment_id='801', reply_parent_id='800', text='Explicit thread response')]}, budget=ReadBudget(10), prepared=[])
    assert batch.children[0].proposal['parent_id'] == '800'
    assert batch.children[0].proposal['before']['reply_context']['comment_id'] == '801'
    assert apply_community_batch(brand, batch.id, batch.fingerprint).status == 'verified'
    assert len(graph.writes) == 1 and graph.writes[0][1] == '800/replies'
