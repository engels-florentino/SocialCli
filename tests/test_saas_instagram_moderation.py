"""Candidate moderation binds an exact visitor comment and retains User credentials."""
import json
from urllib.parse import parse_qs

import httpx
import pytest

from tests.test_saas_meta_user import MetaProvider, connected, call, proposal

EDIT={'action':'comment-moderate','target_id':'789','comment_id':'800','hide':True}


class ModerationProvider(MetaProvider):
    def __init__(self):
        super().__init__();self.hidden=False;self.author='777';self.incomplete=False;self.actor='111';self.comment_media='789'

    def __call__(self,request):
        path=request.url.path.removeprefix('/v26.0/')
        if request.method=='GET' and path=='me':
            return httpx.Response(200,json={'id':self.actor})
        if request.method=='GET' and path=='789/comments':
            assert request.headers['authorization']=='Bearer USER-PRIVATE'
            rows=[] if self.hidden else [{'id':'800','text':'Consenting visitor fixture','from':{'id':self.author},'hidden':False}]
            result={'data':rows}
            if self.incomplete:result['paging']={'next':'https://graph.facebook.com/next'}
            return httpx.Response(200,json=result)
        if request.method=='GET' and path=='800':
            assert request.headers['authorization']=='Bearer USER-PRIVATE'
            return httpx.Response(200,json={'id':'800','text':'Consenting visitor fixture',
                'from':{'id':self.author},'hidden':self.hidden,'media':{'id':self.comment_media}})
        if request.method=='POST' and path=='800':
            assert request.headers['authorization']=='Bearer USER-PRIVATE'
            self.writes.append(('POST',path))
            self.hidden=parse_qs(request.content.decode())['hide']==['true']
            if self.timeout:raise httpx.ReadTimeout('lost response',request=request)
            return httpx.Response(200,json={'success':True})
        return super().__call__(request)


def enable(client,connection,now,monkeypatch,**overrides):
    vault=client.app.state.saas.vault
    with vault.transaction() as db:cap=vault.get(db,'saas-capability',connection['connection_id'])['payload']
    policy={'connection_id':connection['connection_id'],'installation_id':cap['installation_id'],
            'account_id':'456','page_id':'123','target_id':'789','comment_id':'800','expires_at':now[0]+3600}
    monkeypatch.setenv('SOCIALCLI_IG_MODERATION_PILOT',json.dumps(policy|overrides))


def test_exact_pilot_hides_and_restores_visitor_comment_without_token_export(tmp_path,monkeypatch):
    client,now,graph,connection=connected(tmp_path,ModerationProvider())
    enable(client,connection,now,monkeypatch)
    for hidden in (True,False):
        edit=EDIT|{'hide':hidden};approved=proposal(client,connection,edit)
        result=call(client,connection,'mutate',edit=edit,**approved)
        assert result.status_code==200,result.text
        assert result.json()['receipt']=={'success':True,'target_id':'800'}
        assert graph.hidden is hidden
        assert 'PRIVATE' not in result.text
        assert call(client,connection,'mutate',edit=edit,**approved).json()==result.json()
    assert len(graph.writes)==2


@pytest.mark.parametrize('invalid',['absent','expired','connection_id','installation_id','account_id','page_id','target_id','comment_id'])
def test_pilot_denies_foreign_or_expired_bindings_before_any_write(tmp_path,monkeypatch,invalid):
    client,now,graph,connection=connected(tmp_path,ModerationProvider())
    enable(client,connection,now,monkeypatch)
    approved=proposal(client,connection,EDIT)
    if invalid=='absent':monkeypatch.delenv('SOCIALCLI_IG_MODERATION_PILOT')
    elif invalid=='expired':enable(client,connection,now,monkeypatch,expires_at=now[0]-1)
    else:enable(client,connection,now,monkeypatch,**{invalid:'999'})
    result=call(client,connection,'mutate',edit=EDIT,**approved)
    assert result.status_code==403,result.text
    assert 'capability_not_enabled' in result.text
    assert not graph.writes


@pytest.mark.parametrize('invalid',['own_author','missing_author','missing_scope','foreign_media','incomplete'])
def test_pilot_never_turns_ineligible_comment_into_a_success(tmp_path,monkeypatch,invalid):
    client,now,graph,connection=connected(tmp_path,ModerationProvider())
    enable(client,connection,now,monkeypatch)
    if invalid=='own_author':graph.author='456'
    elif invalid=='missing_author':graph.author=None
    elif invalid=='missing_scope':graph.scopes.remove('instagram_manage_comments')
    elif invalid=='foreign_media':graph.media_owner='999'
    else:graph.incomplete=True
    result=call(client,connection,edit=EDIT)
    assert result.status_code in {400,409},result.text
    assert not graph.writes


def test_receipts_and_inspection_survive_expired_pilot(tmp_path,monkeypatch):
    client,now,graph,connection=connected(tmp_path,ModerationProvider())
    enable(client,connection,now,monkeypatch)
    approved=proposal(client,connection,EDIT)
    result=call(client,connection,'mutate',edit=EDIT,**approved)
    assert result.status_code==200,result.text
    monkeypatch.delenv('SOCIALCLI_IG_MODERATION_PILOT')
    assert call(client,connection,'status',edit=EDIT,**approved).json()==result.json()
    # Inspection remains read-only and available after the pilot ends.
    assert call(client,connection,edit=EDIT).json()['before']['hidden'] is True
    assert len(graph.writes)==1


def test_lost_provider_response_never_replays_even_with_new_operation_id(tmp_path,monkeypatch):
    client,now,graph,connection=connected(tmp_path,ModerationProvider())
    enable(client,connection,now,monkeypatch)
    approved=proposal(client,connection,EDIT);graph.timeout=True
    result=call(client,connection,'mutate',edit=EDIT,**approved)
    assert result.status_code==409 and 'operation_uncertain' in result.text
    assert call(client,connection,'mutate',edit=EDIT,**approved).status_code==409
    other=proposal(client,connection,EDIT)
    assert call(client,connection,'mutate',edit=EDIT,**other).status_code==409
    assert len(graph.writes)==1


@pytest.mark.parametrize('lose_response',[False,True])
def test_public_prepare_apply_reconcile_use_scoped_server_only(tmp_path,monkeypatch,lose_response):
    from socialctl.brands import crear_brand
    from socialctl.models import Platform
    from socialctl.management.meta_client import MetaClient
    from socialctl.management.meta_changes import MetaStore,prepare_meta,apply_meta
    from socialctl.connections import keychain
    server,now,graph,connection=connected(tmp_path/'server',ModerationProvider())
    enable(server,connection,now,monkeypatch)
    brand=crear_brand(tmp_path/'client','Example')
    brand.cuentas={'facebook':{'page_id':'123'},'instagram':{'ig_user_id':'456'}}
    brand.guardar_secreto(Platform.INSTAGRAM,{'version':1,'auth_mode':'broker','protocol':'saas-v1',
        'service_url':'https://social.example','account_id':'456','connection_id':connection['connection_id']})
    monkeypatch.setattr(keychain,'get',lambda *args:connection['connection_secret'])
    responses=[]
    def transport(request):
        assert request.url.host=='social.example'
        assert request.url.path.endswith('/instagram-management')
        result=server.request(request.method,str(request.url),headers=dict(request.headers),content=request.content)
        responses.append(result.text)
        if lose_response and json.loads(request.content)['phase']=='mutate':
            monkeypatch.delenv('SOCIALCLI_IG_MODERATION_PILOT')
            raise httpx.ReadTimeout('lost response',request=request)
        return httpx.Response(result.status_code,content=result.content,headers=result.headers)
    http=httpx.Client(transport=httpx.MockTransport(transport));store=MetaStore(brand.raiz)
    client=MetaClient(brand,Platform.INSTAGRAM,http)
    change=prepare_meta(client,store,EDIT)
    assert change.identity['token_mode']=='facebook_user' and not graph.writes
    result=apply_meta(client,store,change.id,change.fingerprint)
    assert result.status=='verified'
    assert result.receipt=={'success':True,'target_id':'800'}
    assert apply_meta(client,store,change.id,change.fingerprint).status=='verified'
    assert len(graph.writes)==1
    assert 'PRIVATE' not in ''.join(responses)+store.load(change.id).model_dump_json()


@pytest.mark.parametrize('observed', ['changed', 'unchanged', 'unreadable', 'wrong_identity', 'stale_once'])
def test_public_provider_timeout_reconciles_after_pilot_expiry_without_receipt(tmp_path, monkeypatch, observed):
    from socialctl.brands import crear_brand
    from socialctl.models import Platform
    from socialctl.management.meta_client import MetaClient
    from socialctl.management.meta_changes import MetaStore, prepare_meta, apply_meta, reconcile_meta
    from socialctl.connections import keychain
    server, now, graph, connection = connected(tmp_path/'server', ModerationProvider())
    enable(server, connection, now, monkeypatch)
    brand = crear_brand(tmp_path/'client', 'Example')
    brand.cuentas = {'facebook': {'page_id': '123'}, 'instagram': {'ig_user_id': '456'}}
    brand.guardar_secreto(Platform.INSTAGRAM, {'version': 1, 'auth_mode': 'broker', 'protocol': 'saas-v1',
        'service_url': 'https://social.example', 'account_id': '456', 'connection_id': connection['connection_id']})
    monkeypatch.setattr(keychain, 'get', lambda *args: connection['connection_secret'])
    phases = []
    def transport(request):
        assert request.url.host == 'social.example'
        phases.append(json.loads(request.content)['phase'])
        response = server.request(request.method, str(request.url), headers=dict(request.headers), content=request.content)
        return httpx.Response(response.status_code, content=response.content, headers=response.headers)
    client = MetaClient(brand, Platform.INSTAGRAM, httpx.Client(transport=httpx.MockTransport(transport)))
    store = MetaStore(brand.raiz)
    change = prepare_meta(client, store, EDIT)
    graph.timeout = True
    # Keep readback unavailable until the next explicit reconciliation.
    original = graph.__class__.__call__
    def unavailable_after_write(self, request):
        if self.writes and request.method == 'GET' and request.url.path.endswith('/789/comments'):
            raise httpx.ReadTimeout('fictional read unavailable', request=request)
        return original(self, request)
    monkeypatch.setattr(ModerationProvider, '__call__', unavailable_after_write)
    assert apply_meta(client, store, change.id, change.fingerprint).status == 'uncertain'
    monkeypatch.setattr(ModerationProvider, '__call__', original)
    enable(server, connection, now, monkeypatch, expires_at=now[0]-1)
    if observed == 'unchanged': graph.hidden = False
    if observed == 'unreadable': graph.incomplete = True
    if observed == 'wrong_identity': graph.actor = '999'
    if observed == 'stale_once':
        graph.hidden = False
        stale_reads = []
        def eventually_consistent(self, request):
            response = original(self, request)
            if (request.method == 'GET' and request.url.path.endswith('/789/comments')
                    and not stale_reads):
                stale_reads.append(self.hidden)
                # The status read saw the old state; every subsequent GET sees
                # the applied hide, including the old client's fallback GET.
                self.hidden = True
            return response
        monkeypatch.setattr(ModerationProvider, '__call__', eventually_consistent)
    result = reconcile_meta(client, store, change.id)
    assert result.receipt is None
    assert result.status == ('verified' if observed == 'changed' else 'uncertain')
    assert len(graph.writes) == 1
    if observed == 'stale_once':
        vault = server.app.state.saas.vault
        def original_server_state():
            with vault.read_transaction() as db:
                rows = db.execute("SELECT id FROM records WHERE kind='saas-meta-operation'").fetchall()
                assert len(rows) == 1
                return vault.get(db, 'saas-meta-operation', rows[0]['id'])['payload']
        assert graph.hidden is True and stale_reads == [False]
        assert original_server_state()['status'] == 'uncertain'
        # A later explicit public reconciliation must resolve the server guard
        # before local verification permits a separately approved restoration.
        result = reconcile_meta(client, store, change.id)
        assert result.status == 'verified' and result.receipt is None
        saved = original_server_state()
        assert saved['status'] == 'verified_readback'
        assert 'receipt' not in saved['response'] and len(graph.writes) == 1
    if observed in {'changed', 'stale_once'}:
        graph.timeout = False
        enable(server, connection, now, monkeypatch)
        restore = prepare_meta(client, store, EDIT | {'hide': False})
        assert apply_meta(client, store, restore.id, restore.fingerprint).status == 'verified'
        assert graph.hidden is False and len(graph.writes) == 2
        assert reconcile_meta(client, store, change.id).receipt is None
    else:
        graph.incomplete = False
        graph.actor = '111'
        graph.timeout = False
        enable(server, connection, now, monkeypatch)
        # The unresolved operation still guards the target against another UUID.
        other = proposal(server, connection, EDIT | {'hide': False})
        response = call(server, connection, 'mutate', edit=EDIT | {'hide': False}, **other)
        assert response.status_code == 409 and 'operation_uncertain' in response.text
        assert len(graph.writes) == 1
