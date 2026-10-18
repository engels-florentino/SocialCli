"""JSON write guidance uses real journals over offline provider transports."""
import json

import httpx
import pytest
from typer.testing import CliRunner

from socialctl.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def no_live_http(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('live HTTP forbidden')
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', blocked)


def prepared(tmp_path, monkeypatch, kind):
    if kind == 'meta':
        from tests.test_meta_comments import service
        mod, brand, api, client, store = service(tmp_path)
        (brand.raiz/'accounts.yml').write_text("facebook:\n  page_id: '123'\n")
        monkeypatch.setattr('socialctl.management.comments_cli.make_http_client',
            lambda: httpx.Client(transport=httpx.MockTransport(api)))
        change = mod.prepare_comment(client, store, media_id='789', text='Exact supplied text')
        command = ['comments']
    else:
        from tests.test_youtube_community import service
        from socialctl.management.community_changes import prepare_community
        brand, api, client, store = service(tmp_path)
        monkeypatch.setattr('socialctl.management.community_cli.make_http_client',
            lambda: httpx.Client(transport=httpx.MockTransport(api.handler)))
        change = prepare_community(client, store, {'action':'add','video_id':'video-1','text':'Exact supplied text'})
        command = ['content','youtube-community']
    return brand, api, store, change, command


@pytest.mark.parametrize('kind',['meta','youtube'])
@pytest.mark.parametrize('approval',['exact','wrong','eof'])
def test_json_apply_keeps_full_preview_and_exact_approval(tmp_path,monkeypatch,kind,approval):
    brand, api, store, change, command = prepared(tmp_path,monkeypatch,kind)
    answer = change.fingerprint+'\n' if approval=='exact' else 'wrong\n' if approval=='wrong' else ''
    result = runner.invoke(app,[*command,'apply',change.id,'--brand',brand.nombre,'--root',str(tmp_path),'--json', "--approval-stdin"],input=answer)
    payload = json.loads(result.stdout)
    assert 'Exact supplied text' in result.stderr and change.fingerprint in result.stderr
    assert change.id in result.stderr and 'Enter the exact fingerprint' in result.stderr
    if approval=='exact':
        assert result.exit_code==0, result.output
        assert payload['status']=='verified' and payload['id']==change.id
        assert 'error_detail' not in payload
        assert len(api.writes)==1 and store.load(change.id).status=='verified'
    else:
        assert result.exit_code==1 and not api.writes
        assert payload['error']['code']==('approval_mismatch' if approval=='wrong' else 'operation_cancelled')
        assert payload['error']['retryable'] is False
        assert store.load(change.id).status=='proposed'


@pytest.mark.parametrize('kind',['meta','youtube'])
def test_lost_post_result_reconciles_without_replay_and_retains_journal(tmp_path,monkeypatch,kind):
    brand,api,store,change,command=prepared(tmp_path,monkeypatch,kind)
    if kind=='meta':api.failure='lost-id'
    else:api.on_write=lambda request:httpx.Response(200,json={})
    common=['--brand',brand.nombre,'--root',str(tmp_path),'--json']
    applied=runner.invoke(app,[*command,'apply',change.id,*common, "--approval-stdin"],input=change.fingerprint+'\n')
    assert applied.exit_code==1,applied.output
    result=json.loads(applied.stdout)
    assert result['status']=='uncertain' and result['journal']
    assert result['error_detail']['action']=='reconcile' and not result['error_detail']['retryable']
    assert len(api.writes)==1
    reconciled=runner.invoke(app,[*command,'reconcile',change.id,*common])
    assert reconciled.exit_code==0,reconciled.output  # preserve legacy reconcile exit
    assert json.loads(reconciled.stdout)['error_detail']['action']=='reconcile'
    assert len(api.writes)==1
    repeated=runner.invoke(app,[*command,'apply',change.id,*common, "--approval-stdin"],input=change.fingerprint+'\n')
    assert repeated.exit_code==1 and len(api.writes)==1
    assert json.loads(repeated.stdout)['error_detail']['action']=='reconcile'
    status=runner.invoke(app,[*command,'status',change.id,*common])
    assert status.exit_code==0 and json.loads(status.stdout)['id']==change.id
    assert json.loads(status.stdout)['journal']==store.load(change.id).model_dump(mode='json')['journal']


@pytest.mark.parametrize('kind',['meta','youtube'])
def test_after_write_persistence_failure_requires_reconcile(tmp_path,monkeypatch,kind):
    brand,api,store,change,command=prepared(tmp_path,monkeypatch,kind)
    original=type(store).save
    def save(self,value):
        if api.writes:raise OSError('private access_token=never-print')
        return original(self,value)
    monkeypatch.setattr(type(store),'save',save)
    result=runner.invoke(app,[*command,'apply',change.id,'--brand',brand.nombre,'--root',str(tmp_path),'--json', "--approval-stdin"],input=change.fingerprint+'\n')
    assert result.exit_code==1 and len(api.writes)==1,result.output
    payload=json.loads(result.stdout)
    assert payload['error']['action']=='reconcile' and not payload['error']['retryable']
    assert payload['change_id']==change.id
    assert 'never-print' not in result.stdout
    assert store.load(change.id).status=='applying'


def test_duplicate_verified_comment_is_reported_as_not_sent(tmp_path,monkeypatch):
    from socialctl.management.meta_comments import prepare_comment
    from socialctl.models import Platform
    brand,api,store,change,command=prepared(tmp_path,monkeypatch,'meta')
    common=['--brand',brand.nombre,'--root',str(tmp_path),'--json']
    first=runner.invoke(app,[*command,'apply',change.id,*common,'--approval-fingerprint',change.fingerprint])
    assert first.exit_code==0
    import socialctl.management.meta_comments as mod
    client=mod.MetaCommentsClient(brand, Platform.FACEBOOK,
        httpx.Client(transport=httpx.MockTransport(api)))
    duplicate=prepare_comment(client,store,media_id='789',text='Exact supplied text')
    result=runner.invoke(app,[*command,'apply',duplicate.id,*common,'--approval-fingerprint',duplicate.fingerprint])
    assert result.exit_code==1
    assert json.loads(result.stdout)['error']['code']=='operation_already_verified'
    assert store.load(duplicate.id).status=='proposed'
    assert not store.load(duplicate.id).journal
    assert len(api.writes)==1


@pytest.mark.parametrize('command',[['comments'],['content','youtube-community']])
@pytest.mark.parametrize('operation',['apply','reconcile','status'])
def test_json_missing_workspace_is_structured_before_remote_calls(tmp_path,command,operation):
    result=runner.invoke(app,[*command,operation,'missing','--brand','Missing','--root',str(tmp_path),'--json'])
    assert result.exit_code==1,result.output
    assert json.loads(result.stdout)['error']['code']=='invalid_input'


@pytest.mark.parametrize('failure',[None,'lost-id'])
def test_inbox_draft_json_keeps_binding_and_uncertain_reconciliation(tmp_path,monkeypatch,failure):
    from tests.test_meta_comments import service
    from tests.test_meta_comment_inbox import remote_row
    from socialctl.management.meta_comment_inbox import CommentInboxStore, sync_inbox, prepare_inbox_draft
    mod,brand,api,client,store=service(tmp_path)
    (brand.raiz/'accounts.yml').write_text("facebook:\n  page_id: '123'\n")
    api.comments['800']=remote_row(api,'800','Question?')
    inbox=CommentInboxStore(brand.raiz)
    sync_inbox(client,inbox,media_id='789')
    change,record=prepare_inbox_draft(client,store,inbox,media_id='789',comment_id='800',text='Exact supplied text')
    monkeypatch.setattr('socialctl.management.comments_cli.make_http_client',lambda:httpx.Client(transport=httpx.MockTransport(api)))
    common=['--brand',brand.nombre,'--root',str(tmp_path),'--json']
    wrong_route=runner.invoke(app,['comments','apply',change.id,*common, "--approval-stdin"],input=change.fingerprint+'\n')
    assert wrong_route.exit_code==1 and not api.writes
    assert json.loads(wrong_route.stdout)['error']['retryable'] is False
    api.failure=failure
    result=runner.invoke(app,['comments','apply-draft','789','800','--platform','facebook',*common, "--approval-stdin"],input=change.fingerprint+'\n')
    assert result.exit_code==(1 if failure else 0),result.output
    payload=json.loads(result.stdout)
    assert payload['change_id']==change.id and len(api.writes)==1
    assert 'Exact supplied text' in result.stderr
    assert payload['status']==('incierto' if failure else 'respondido')
    if failure:
        assert payload['error_detail']['action']=='reconcile'
        checked=runner.invoke(app,['comments','reconcile-draft','789','800','--platform','facebook',*common])
        assert checked.exit_code==1 and len(api.writes)==1,checked.output
        assert json.loads(checked.stdout)['error_detail']['retryable'] is False
    else:
        assert payload['response_id']=='900'


@pytest.mark.parametrize('kind',['meta','youtube'])
def test_interruption_during_remote_write_preserves_uncertain_intent(tmp_path,monkeypatch,kind):
    brand,api,store,change,command=prepared(tmp_path,monkeypatch,kind)
    def interrupt(*args):raise KeyboardInterrupt()
    if kind=='meta':api.after_write=interrupt
    else:api.on_write=interrupt
    result=runner.invoke(app,[*command,'apply',change.id,'--brand',brand.nombre,'--root',str(tmp_path),'--json', "--approval-stdin"],input=change.fingerprint+'\n')
    assert result.exit_code==1 and len(api.writes)==1,result.output
    assert json.loads(result.stdout)['error']['action']=='reconcile'
    assert store.load(change.id).status=='applying'


@pytest.mark.parametrize('kind',['meta','youtube'])
def test_missing_proposal_has_one_safe_json_error_and_no_remote_request(tmp_path,monkeypatch,kind):
    brand,api,store,change,command=prepared(tmp_path,monkeypatch,kind)
    result=runner.invoke(app,[*command,'apply','00000000-0000-0000-0000-000000000099','--brand',brand.nombre,'--root',str(tmp_path),'--json'])
    assert result.exit_code==1 and not api.writes,result.output
    assert json.loads(result.stdout)['error']['retryable'] is False
    assert store.load(change.id).status=='proposed'
