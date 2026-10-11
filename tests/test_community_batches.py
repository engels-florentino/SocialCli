import pytest
import httpx
from tests.test_meta_comments import service
from socialctl.management.changes import ChangeError


def prepared(tmp_path,monkeypatch):
    from socialctl.management import community_batches as batches
    mod,brand,graph,client,store=service(tmp_path)
    changes=[mod.prepare_comment(client,store,media_id='789',text=text) for text in ('First exact text','Second exact text')]
    # Different explicit parents are independent reply targets in a real batch;
    # use separate owned media for this synthetic add batch.
    changes[1].media_id='790'
    changes[1].before['media']['id']='790'
    changes[1].fingerprint=mod.fingerprint(changes[1]);store.save(changes[1])
    original=graph.__call__
    def handler(request):
        if request.method=='GET' and request.url.path.endswith('/790'):
            return httpx.Response(200,json={'id':'790','from':{'id':'123'}})
        return original(request)
    monkeypatch.setattr(batches,'http_client',lambda:httpx.Client(transport=httpx.MockTransport(handler)))
    refs=[{'provider':'meta-comments','change_id':c.id} for c in changes]
    return batches,mod,brand,graph,store,changes,refs


def test_batch_order_digest_exact_children_and_duplicates(tmp_path,monkeypatch):
    batches,mod,brand,graph,store,children,refs=prepared(tmp_path,monkeypatch)
    a=batches.prepare_community_batch(brand,refs)
    b=batches.prepare_community_batch(brand,list(reversed(refs)))
    assert a.fingerprint!=b.fingerprint
    assert all(c.text in str(a.model_dump()) for c in children)
    assert not graph.writes
    with pytest.raises(ChangeError):batches.prepare_community_batch(brand,[refs[0],refs[0]])
    with pytest.raises(ChangeError):batches.apply_community_batch(brand,a.id,'wrong')
    assert not graph.writes


def test_batch_changed_child_blocks_before_any_write(tmp_path,monkeypatch):
    batches,mod,brand,graph,store,children,refs=prepared(tmp_path,monkeypatch)
    batch=batches.prepare_community_batch(brand,refs)
    children[1].text='Changed';children[1].fingerprint=mod.fingerprint(children[1]);store.save(children[1])
    with pytest.raises(ChangeError):batches.apply_community_batch(brand,batch.id,batch.fingerprint)
    assert not graph.writes


def test_batch_uncertain_stops_and_never_replays(tmp_path,monkeypatch):
    batches,mod,brand,graph,store,children,refs=prepared(tmp_path,monkeypatch)
    batch=batches.prepare_community_batch(brand,refs)
    graph.failure='timeout'
    first=batches.apply_community_batch(brand,batch.id,batch.fingerprint)
    assert first.status=='partial' and len(graph.writes)==1
    graph.failure=None
    batches.apply_community_batch(brand,batch.id,batch.fingerprint)
    batches.reconcile_community_batch(brand,batch.id)
    assert len(graph.writes)==1
    assert store.load(children[1].id).status=='proposed'


def test_account_change_invalidates_batch_before_writes(tmp_path,monkeypatch):
    batches,mod,brand,graph,store,children,refs=prepared(tmp_path,monkeypatch)
    batch=batches.prepare_community_batch(brand,refs)
    brand.cuentas={'facebook':{'page_id':'999'}}
    with pytest.raises(ChangeError):batches.apply_community_batch(brand,batch.id,batch.fingerprint)
    assert not graph.writes


def test_batch_interruption_after_remote_write_does_not_replay(tmp_path,monkeypatch):
    batches,mod,brand,graph,store,children,refs=prepared(tmp_path,monkeypatch)
    batch=batches.prepare_community_batch(brand,refs)
    graph.after_write=lambda:(_ for _ in ()).throw(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):batches.apply_community_batch(brand,batch.id,batch.fingerprint)
    graph.after_write=None
    batches.reconcile_community_batch(brand,batch.id)
    batches.apply_community_batch(brand,batch.id,batch.fingerprint)
    assert len(graph.writes)==1


def test_verified_children_skipped_on_resume_and_reconcile_does_not_start_pending(tmp_path,monkeypatch):
    batches,mod,brand,graph,store,children,refs=prepared(tmp_path,monkeypatch)
    batch=batches.prepare_community_batch(brand,refs)
    children[0].status='verified';store.save(children[0])
    batches.reconcile_community_batch(brand,batch.id)
    assert not graph.writes and store.load(children[1].id).status=='proposed'
    batches.apply_community_batch(brand,batch.id,batch.fingerprint)
    assert len(graph.writes)==1 and graph.writes[0][1]=='790/comments'


@pytest.mark.parametrize('preprepared',[False,True])
def test_verified_youtube_reply_cannot_repeat_in_another_batch(tmp_path,monkeypatch,preprepared):
    from tests.test_youtube_community import service as youtube_service
    from socialctl.management.community_changes import prepare_community
    from socialctl.management import community_batches as batches
    brand,api,client,store=youtube_service(tmp_path)
    monkeypatch.setattr(batches,'http_client',lambda:httpx.Client(transport=httpx.MockTransport(api.handler)))
    edit={'action':'reply','video_id':'video-1','thread_id':'UgxThread','parent_id':'UgxTop','text':'Exact durable reply'}
    first=prepare_community(client,store,edit)
    batch1=batches.prepare_community_batch(brand,[{'provider':'youtube-community','change_id':first.id}])
    if preprepared:
        second=prepare_community(client,store,edit)
        batch2=batches.prepare_community_batch(brand,[{'provider':'youtube-community','change_id':second.id}])
    assert batches.apply_community_batch(brand,batch1.id,batch1.fingerprint).status=='verified'
    api.on_read=lambda request:httpx.Response(200,json={'items':[]}) if request.url.path.endswith('/comments') else None
    if not preprepared:
        second=prepare_community(client,store,edit)
        batch2=batches.prepare_community_batch(brand,[{'provider':'youtube-community','change_id':second.id}])
    result=batches.apply_community_batch(brand,batch2.id,batch2.fingerprint)
    assert len(api.writes)==1
    assert result.status=='partial'
