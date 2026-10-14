import json
import httpx
import pytest
import yaml
from typer.testing import CliRunner
from socialctl.cli import app
from tests.test_community_batches import prepared

runner=CliRunner()


def invoke(tmp_path,*args,input=None):
    return runner.invoke(app,['changes',*args,'--brand','Example','--root',str(tmp_path),'--json'],input=input)


def test_batch_json_full_preview_exact_approval_and_status(tmp_path,monkeypatch):
    batches,_,brand,graph,_,children,refs=prepared(tmp_path,monkeypatch)
    (brand.raiz/'accounts.yml').write_text(yaml.safe_dump(brand.cuentas))
    batch=batches.prepare_community_batch(brand,refs[:1])
    bad=invoke(tmp_path,'apply-batch',batch.id,'--approval','wrong')
    assert bad.exit_code==1 and json.loads(bad.stdout)['error']['code']=='approval_mismatch'
    assert not graph.writes
    result=invoke(tmp_path,'apply-batch',batch.id,'--approval',batch.fingerprint)
    assert result.exit_code==0,result.output
    assert json.loads(result.stdout)['status']=='verified'
    assert children[0].text in result.stderr
    assert json.loads(invoke(tmp_path,'status-batch',batch.id).stdout)['status']=='verified'
    assert len(graph.writes)==1


def test_batch_uses_bound_inbox_rate_limit_and_updates_state(tmp_path,monkeypatch):
    from tests.test_meta_comments import service
    from tests.test_meta_comment_inbox import remote_row
    from socialctl.management.meta_comment_inbox import CommentInboxStore,sync_inbox,prepare_inbox_draft
    from socialctl.management import community_batches as batches
    _,brand,graph,client,store=service(tmp_path)
    graph.comments['800']=remote_row(graph,'800','Question')
    inbox=CommentInboxStore(brand.raiz)
    sync_inbox(client,inbox,media_id='789')
    child,_=prepare_inbox_draft(client,store,inbox,media_id='789',comment_id='800',text='Exact reply')
    batch=batches.prepare_community_batch(brand,[{'provider':'meta-comments','change_id':child.id}])
    data=inbox.load()
    from socialctl.management.changes import now_utc
    data.comments[0].journal=[{'at':now_utc().isoformat(),'event':'reply_write_intent'} for _ in range(10)]
    inbox.save(data)
    monkeypatch.setattr(batches,'http_client',lambda:httpx.Client(transport=httpx.MockTransport(graph)))
    result=batches.apply_community_batch(brand,batch.id,batch.fingerprint)
    assert not graph.writes
    assert result.status=='partial' and inbox.load().comments[0].status=='bloqueado'
    data=inbox.load();data.comments[0].journal=[];inbox.save(data)
    result=batches.apply_community_batch(brand,batch.id,batch.fingerprint)
    assert result.status=='verified' and len(graph.writes)==1
    assert inbox.load().comments[0].status=='respondido'
    batches.reconcile_community_batch(brand,batch.id)
    assert len(graph.writes)==1


@pytest.mark.parametrize('interrupt',[False,True])
def test_batch_json_lost_reply_retains_id_and_never_replays(tmp_path,monkeypatch,interrupt):
    batches,_,brand,graph,store,children,refs=prepared(tmp_path,monkeypatch)
    (brand.raiz/'accounts.yml').write_text(yaml.safe_dump(brand.cuentas))
    batch=batches.prepare_community_batch(brand,refs[:1])
    if interrupt:graph.after_write=lambda:(_ for _ in ()).throw(KeyboardInterrupt())
    else:graph.failure='timeout'
    result=invoke(tmp_path,'apply-batch',batch.id,'--approval',batch.fingerprint)
    assert result.exit_code==1,result.output
    value=json.loads(result.stdout)
    assert (value.get('error') or value.get('error_detail'))['action']=='reconcile'
    assert value.get('change_id',value.get('id'))==batch.id
    assert len(graph.writes)==1
    graph.failure=None;graph.after_write=None
    invoke(tmp_path,'reconcile-batch',batch.id)
    invoke(tmp_path,'apply-batch',batch.id,'--approval',batch.fingerprint)
    assert len(graph.writes)==1


def test_batch_json_eof_has_no_write(tmp_path,monkeypatch):
    batches,_,brand,graph,_,_,refs=prepared(tmp_path,monkeypatch)
    (brand.raiz/'accounts.yml').write_text(yaml.safe_dump(brand.cuentas))
    batch=batches.prepare_community_batch(brand,refs)
    result=invoke(tmp_path,'apply-batch',batch.id,input='')
    assert result.exit_code==1 and json.loads(result.stdout)['error']['code']=='operation_cancelled'
    assert not graph.writes
