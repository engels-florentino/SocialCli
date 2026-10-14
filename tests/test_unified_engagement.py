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
    sync_inbox(client,CommentInboxStore(brand.raiz),media_id='789')
    api=API();api.threads=[thread(top=comment(author='viewer',text='YouTube question'))]
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
