"""Fictional signed callbacks exercise durable owner isolation; no live writes."""
import hashlib
import hmac
import json
import sqlite3

import httpx
import pytest

from socialctl.connection_service.settings import AppCredentials
from socialctl.saas.meta_webhooks import RETENTION, MAX_BODY
from socialctl.saas.lifecycle import AccountLifecycle
from tests.test_saas_auth import USER, service, login, fields
from tests.test_saas_connections import new_installation

PATH = '/api/saas/v1/meta/webhook'
TOKEN = 'fictional-verification-token'
SECRET = 'fictional-app-secret'


class Graph:
    def __init__(self):
        self.calls = []
        self.callback = 'https://social.example' + PATH
        self.app_fields = ['feed', 'comments', 'mentions']
        self.page_fields = ['feed']
        self.token_type = 'PAGE'
        self.page = '123'
        self.ig = '456'
        self.hook = None

    def __call__(self, request):
        self.calls.append(request)
        assert request.method == 'GET', 'Receiver must never mutate a provider'
        path = request.url.path.removeprefix('/v26.0/')
        if self.hook:
            hook, self.hook = self.hook, None
            hook()
        if path == 'debug_token':
            return httpx.Response(200, json={'data': {'is_valid': True, 'app_id': 'fake-app',
                'type': self.token_type, 'profile_id': self.page, 'scopes': ['pages_manage_metadata',
                'pages_read_engagement', 'instagram_basic', 'instagram_manage_comments']}})
        if path == 'me':
            return httpx.Response(200, json={'id': self.page,
                'instagram_business_account': {'id': self.ig}})
        if path == 'fake-app/subscriptions':
            return httpx.Response(200, json={'data': [{'object': obj, 'callback_url': self.callback,
                'active': True, 'fields': [{'name': field, 'version': 'v26.0'} for field in self.app_fields]}
                for obj in ['page', 'instagram']]})
        if path == '123/subscribed_apps':
            return httpx.Response(200, json={'data': [{'id': 'fake-app', 'subscribed_fields': self.page_fields}]})
        return httpx.Response(404)


@pytest.fixture
def receiver(tmp_path, monkeypatch):
    monkeypatch.setenv('SOCIALCLI_META_WEBHOOK_VERIFY_TOKEN', TOKEN)
    graph = Graph()
    client, now, _ = service(tmp_path, provider=graph)
    connections = client.app.state.saas_connections
    connections.providers.settings.providers.update({p: AppCredentials('fake-app', SECRET)
        for p in ['facebook', 'instagram']})
    login(client)
    installation = new_installation(client)
    cap = {'user_id': USER, 'installation_id': installation['installation_id'], 'platform': 'instagram',
        'grant_id': 'fictional-grant', 'account': {'id': '456', 'page_id': '123'}}
    vault = connections.vault
    with vault.transaction() as db:
        connections.access.activate_trial(db, USER, now[0])
        db.execute('INSERT INTO saas_slots VALUES(?,?,?)', (USER, 'instagram', cap['grant_id']))
        vault.put(db, 'saas-grant', cap['grant_id'], {**cap, 'grant': {'access_token': 'PAGE-PRIVATE',
            'expires_at': now[0]+86400, 'granted_scopes': ['pages_manage_metadata',
                'pages_read_engagement', 'instagram_basic', 'instagram_manage_comments']}}, expires=now[0]+30*86400)
        vault.put(db, 'saas-capability', 'fixture-connection', cap, expires=now[0]+30*86400,
            credential_hash=hashlib.sha256(b'fixture-secret-long-enough').hexdigest())
    headers = {'Authorization': 'Bearer fixture-secret-long-enough'}
    return client, now, graph, cap, headers


def enable(client, platform='instagram'):
    path = '/account/connections/' + platform + '/webhooks'
    form = fields(client.get(path))
    form.update(webhook_accept='1', field_comments='1', field_mentions='1')
    return client.post(path, data=form, headers={'Origin': 'https://social.example'}, follow_redirects=False)


def body(account='456', timestamp=1000, field='comments', media='789', **extra):
    value = {'id': '800', 'media': {'id': media}, 'text': 'PRIVATE COMMENT',
        'from': {'username': 'PRIVATE HANDLE'}, **extra}
    return json.dumps({'object': 'instagram', 'entry': [{'id': account, 'time': timestamp,
        'changes': [{'field': field, 'value': value}]}]}).encode()


def send(client, raw=None, secret=SECRET):
    raw = raw or body()
    signature = 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(PATH, content=raw, headers={'X-Hub-Signature-256': signature})


def event_path():
    return '/api/saas/v1/connections/fixture-connection/meta/webhook-events'


def test_challenge_is_plain_exact_and_rejects_invalid_or_duplicate_values(receiver):
    client, *_ = receiver
    args = {'hub.mode': 'subscribe', 'hub.challenge': '123456', 'hub.verify_token': TOKEN}
    response = client.get(PATH, params=args)
    assert response.status_code == 200 and response.text == '123456'
    assert response.headers['content-type'].startswith('text/plain')
    for key in args:
        broken = {**args, key: 'wrong'}
        if key == 'hub.challenge': broken[key] = ''
        assert client.get(PATH, params=broken).status_code == 403
    assert client.get(PATH, params=list(args.items())+[('hub.verify_token', TOKEN)]).status_code == 403


def test_disabled_receiver_does_not_advertise_delivery(tmp_path, monkeypatch):
    monkeypatch.delenv('SOCIALCLI_META_WEBHOOK_VERIFY_TOKEN', raising=False)
    client, *_ = service(tmp_path)
    assert client.get(PATH).status_code == 503
    assert send(client).status_code == 503


def test_owner_consent_registration_checks_subscription_and_no_social_writes(receiver):
    client, _, graph, cap, _ = receiver
    assert enable(client).status_code == 303
    assert {r.url.path for r in graph.calls} == {'/v26.0/debug_token', '/v26.0/me',
        '/v26.0/fake-app/subscriptions', '/v26.0/123/subscribed_apps'}
    with client.app.state.saas.vault.read_transaction() as db:
        rows = db.execute("SELECT id FROM records WHERE kind='saas-meta-webhook-binding'").fetchall()
        record = client.app.state.saas.vault.get(db, 'saas-meta-webhook-binding', rows[0]['id'])
        assert record['payload']['grant_id'] == cap['grant_id']
        assert record['payload']['consent']['source'] == 'owner_browser'


@pytest.mark.parametrize('attribute,value', [('callback', 'https://foreign.example/callback'),
    ('app_fields', ['comments']), ('page_fields', []), ('token_type', 'USER'),
    ('page', '999'), ('ig', '999')])
def test_registration_rejects_unverified_route_token_and_association(receiver, attribute, value):
    client, _, graph, *_ = receiver
    setattr(graph, attribute, value)
    assert enable(client).status_code == 409
    assert send(client).json()['accepted'] == 0


def test_consent_csrf_and_concurrent_disconnect_cannot_register(receiver):
    client, _, graph, cap, _ = receiver
    path = '/account/connections/instagram/webhooks'
    form = fields(client.get(path))
    form.pop('webhook_accept', None)
    assert client.post(path, data=form, headers={'Origin': 'https://social.example'}).status_code == 400
    assert client.post(path, data={**form, 'webhook_accept': '1'},
        headers={'Origin': 'https://foreign.example'}).status_code == 403
    def disconnect():
        with client.app.state.saas.vault.transaction() as db:
            AccountLifecycle(client.app.state.saas, lambda:1000).disconnect(db, USER, 'instagram')
    graph.hook = disconnect
    assert enable(client).status_code == 409
    assert send(client).json()['accepted'] == 0


def test_raw_body_hmac_body_limits_and_minimized_encrypted_hints(receiver):
    client, _, _, _, headers = receiver
    assert enable(client).status_code == 303
    raw = body()
    bad = client.post(PATH, content=raw+b' ', headers={'X-Hub-Signature-256':
        'sha256='+hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()})
    assert bad.status_code == 403
    assert send(client, b' ' * (MAX_BODY+1)).status_code == 413
    assert send(client).json()['accepted'] == 1
    events = client.get(event_path(), headers=headers).json()['events']
    assert len(events) == 1 and events[0]['target_id'] == '789'
    assert 'PRIVATE' not in json.dumps(events)
    assert 'PRIVATE COMMENT' not in client.app.state.saas.vault.path.read_bytes().decode(errors='ignore')


def test_duplicates_late_events_and_read_ack_keep_dedupe(receiver):
    client, now, _, _, headers = receiver
    enable(client)
    assert send(client).json()['accepted'] == 1
    assert send(client).json()['duplicates'] == 1
    assert send(client, body(timestamp=999)).json()['out_of_order'] == 1
    events = client.get(event_path(), headers=headers).json()['events']
    assert len(events) == 2 and any(e['out_of_order'] for e in events)
    identifier = events[0]['id']
    assert client.post(event_path()+'/'+identifier+'/ack', headers=headers).status_code == 200
    assert len(client.get(event_path(), headers=headers).json()['events']) == 1
    assert send(client).json()['duplicates'] == 1
    now[0] += RETENTION+1
    # Provider retry older than retention is acknowledged as ignored, never recreated.
    assert send(client).json()['accepted'] == 0
    with client.app.state.saas.vault.read_transaction() as db:
        assert db.execute("SELECT count(*) FROM records WHERE kind='saas-meta-webhook-event'").fetchone()[0] == 0


def test_mixed_accounts_route_independently_and_foreign_account_never_consumed(receiver):
    client, _, _, _, headers = receiver
    enable(client)
    raw = json.loads(body())
    raw['entry'].insert(0, json.loads(body(account='999'))['entry'][0])
    assert send(client, json.dumps(raw).encode()).json()['accepted'] == 1
    assert len(client.get(event_path(), headers=headers).json()['events']) == 1
    assert client.get(event_path()).status_code == 404
    assert client.get(event_path(), headers={'Authorization':'Bearer wrong'}).status_code == 404


def test_transient_store_failure_is_not_acknowledged_and_retry_is_safe(receiver, monkeypatch):
    client, _, _, _, headers = receiver
    enable(client)
    vault = client.app.state.saas.vault
    original = vault.put
    def fail(db, kind, *args, **kwargs):
        if kind == 'saas-meta-webhook-event': raise sqlite3.OperationalError('sensitive internal diagnostic')
        return original(db, kind, *args, **kwargs)
    monkeypatch.setattr(vault, 'put', fail)
    response = send(client)
    assert response.status_code == 503 and 'sensitive' not in response.text
    monkeypatch.setattr(vault, 'put', original)
    assert send(client).json()['accepted'] == 1
    assert len(client.get(event_path(), headers=headers).json()['events']) == 1


def test_full_queue_returns_backpressure_and_never_evicts_dedupe(receiver, monkeypatch):
    import socialctl.saas.meta_webhooks as module
    client, _, _, _, headers = receiver
    enable(client)
    monkeypatch.setattr(module, 'OWNER_LIMIT', 1)
    assert send(client).json()['accepted'] == 1
    assert send(client, body(media='790')).status_code == 503
    assert send(client).json()['duplicates'] == 1
    assert len(client.get(event_path(), headers=headers).json()['events']) == 1


def test_grant_deletion_removes_hints_and_revoked_installation_cannot_consume(receiver):
    client, now, _, cap, headers = receiver
    enable(client); send(client)
    vault = client.app.state.saas.vault
    with vault.transaction() as db:
        vault.delete(db, 'saas-installation', cap['installation_id'])
    assert client.get(event_path(), headers=headers).status_code == 401
    with vault.transaction() as db:
        vault.delete(db, 'saas-grant', cap['grant_id'])
        assert db.execute("SELECT count(*) FROM records WHERE kind IN ('saas-meta-webhook-event','saas-meta-webhook-binding')").fetchone()[0] == 0
    assert send(client).json()['accepted'] == 0


def test_owner_deletion_and_restore_reset_remove_all_queue_storage(receiver):
    from socialctl.saas.restore_guard import _wipe_access
    client, now, _, _, _ = receiver
    enable(client); send(client)
    vault = client.app.state.saas.vault
    with vault.transaction() as db:
        _wipe_access(db, quarantine=False)
        assert db.execute("SELECT count(*) FROM records WHERE kind IN ('saas-meta-webhook-event','saas-meta-webhook-binding')").fetchone()[0] == 0
    # Owner deletion uses the indexed encrypted owner relation for any remaining records.
    with vault.transaction() as db:
        vault.put(db, 'saas-meta-webhook-event', 'remaining', {'user_id':USER, 'grant_id':'gone'}, expires=now[0]+RETENTION)
    AccountLifecycle(client.app.state.saas, lambda:now[0]).request_delete(USER)
    with vault.read_transaction() as db:
        assert db.execute("SELECT count(*) FROM records WHERE kind='saas-meta-webhook-event'").fetchone()[0] == 0


def test_mixed_valid_tenants_are_isolated_at_read_and_ack(receiver):
    from socialctl.saas.models import AuthenticatedUser
    from socialctl.saas import consent
    client, now, graph, cap, headers = receiver
    enable(client)
    metadata = client.app.state.saas; vault = metadata.vault
    other = '22222222-2222-4222-8222-222222222222'
    other_cap = {**cap, 'user_id':other, 'grant_id':'other-grant',
        'installation_id':'other-installation', 'account':{'id':'457','page_id':'123'}}
    with vault.transaction() as db:
        metadata.ensure_user(db, AuthenticatedUser(other, 'other@example.test'), now[0])
        consent.accept_privacy(metadata, db, other, now[0])
        client.app.state.saas_connections.access.activate_trial(db, other, now[0])
        vault.put(db,'saas-grant','other-grant',{**other_cap,'grant':{'access_token':'OTHER-PAGE-PRIVATE',
            'expires_at':now[0]+86400}},expires=now[0]+86400)
        db.execute('INSERT INTO saas_slots VALUES(?,?,?)',(other,'instagram','other-grant'))
        original = vault.get(db,'saas-installation',cap['installation_id'])
        vault.put(db,'saas-installation','other-installation',{**original['payload'],'user_id':other},
            expires=now[0]+86400)
        vault.put(db,'saas-capability','other-connection',other_cap,expires=now[0]+86400,
            credential_hash=hashlib.sha256(b'other-secret-long-enough').hexdigest())
    graph.ig = '457'
    client.app.state.saas_meta_webhooks.register(other, 'instagram', {'comments'})
    raw = json.loads(body())
    raw['entry'].extend(json.loads(body(account='457',media='790'))['entry'])
    raw['entry'].extend(json.loads(body(account='999'))['entry'])
    assert send(client,json.dumps(raw).encode()).json()['accepted'] == 2
    owned = client.get(event_path(),headers=headers).json()['events']
    path = '/api/saas/v1/connections/other-connection/meta/webhook-events'
    other_headers = {'Authorization':'Bearer other-secret-long-enough'}
    foreign = client.get(path,headers=other_headers).json()['events']
    assert [e['target_id'] for e in owned] == ['789']
    assert [e['target_id'] for e in foreign] == ['790']
    assert client.post(event_path()+'/'+foreign[0]['id']+'/ack',headers=headers).status_code == 404
    assert client.post(path+'/'+owned[0]['id']+'/ack',headers=other_headers).status_code == 404


def test_failure_after_first_valid_event_rolls_back_entire_batch(receiver, monkeypatch):
    client, _, _, _, headers = receiver
    enable(client)
    vault = client.app.state.saas.vault; original = vault.put; seen = []
    def fail_second(db,kind,*args,**kwargs):
        if kind == 'saas-meta-webhook-event':
            seen.append(kind)
            if len(seen) == 2: raise sqlite3.OperationalError('private')
        return original(db,kind,*args,**kwargs)
    raw = json.loads(body())
    raw['entry'].extend(json.loads(body(media='790'))['entry'])
    monkeypatch.setattr(vault,'put',fail_second)
    assert send(client,json.dumps(raw).encode()).status_code == 503
    assert client.get(event_path(),headers=headers).json()['events'] == []
    monkeypatch.setattr(vault,'put',original)
    assert send(client,json.dumps(raw).encode()).json()['accepted'] == 2


def test_unsigned_malformed_and_excessive_batches_never_store_events(receiver):
    client, *_ = receiver
    enable(client)
    assert client.post(PATH,content=body()).status_code == 403
    assert send(client,b'{invalid').status_code == 400
    assert send(client,b'{"object":"instagram","entry":[],"entry":[]}').status_code == 400
    value = json.loads(body()); value['entry'][0]['changes'] *= 1001
    assert send(client,json.dumps(value).encode()).status_code == 413
    # Chunked transfer must enforce the same body cap without Content-Length.
    raw = b' '*(MAX_BODY+1)
    signature='sha256='+hmac.new(SECRET.encode(),raw,hashlib.sha256).hexdigest()
    assert client.post(PATH,content=iter([raw[:MAX_BODY],raw[MAX_BODY:]]),
        headers={'X-Hub-Signature-256':signature}).status_code == 413


def test_bounded_previous_app_secret_rotation(receiver, monkeypatch):
    from socialctl.saas.meta_webhooks import MetaWebhooks
    client, now, _, _, _ = receiver
    monkeypatch.setenv('SOCIALCLI_META_WEBHOOK_PREVIOUS_SECRET','old-fictional-secret')
    monkeypatch.setenv('SOCIALCLI_META_WEBHOOK_PREVIOUS_UNTIL',str(now[0]+60))
    rotated = MetaWebhooks(client.app.state.saas_connections,'https://social.example',lambda:now[0])
    raw = body(); signature='sha256='+hmac.new(b'old-fictional-secret',raw,hashlib.sha256).hexdigest()
    assert rotated.ingest(raw,signature)['accepted'] == 0
    now[0] += 61
    with pytest.raises(Exception) as error: rotated.ingest(raw,signature)
    assert error.value.status_code == 403
    monkeypatch.setenv('SOCIALCLI_META_WEBHOOK_PREVIOUS_UNTIL',str(now[0]+36*3600+1))
    with pytest.raises(ValueError,match='bounded Meta webhook'):
        MetaWebhooks(client.app.state.saas_connections,'https://social.example',lambda:now[0])


def test_mentions_only_keep_exact_comment_media_and_reject_unsubscribed_fields(receiver):
    client, _, _, _, headers = receiver
    enable(client)
    raw = body(field='mentions',media_id='999',comment_id='900')
    assert send(client,raw).json()['accepted'] == 1
    events = client.get(event_path(),headers=headers).json()['events']
    assert events[0]['target_id'] == '999' and events[0]['comment_id'] == '900'
    assert events[0]['field'] == 'mentions'
    assert send(client,body(field='messages')).json()['accepted'] == 0
    assert send(client,body(timestamp=2000)).json()['accepted'] == 0


def test_replaced_account_and_expired_binding_cannot_receive(receiver):
    client, now, _, cap, _ = receiver
    enable(client)
    vault = client.app.state.saas.vault
    with vault.transaction() as db:
        db.execute('UPDATE saas_slots SET grant_id=? WHERE user_id=? AND platform=?',
            ('replacement',USER,'instagram'))
    assert send(client).json()['accepted'] == 0
    with vault.transaction() as db:
        db.execute('UPDATE saas_slots SET grant_id=? WHERE user_id=? AND platform=?',
            (cap['grant_id'],USER,'instagram'))
    now[0] += 86401
    assert send(client,body(timestamp=int(now[0]))).json()['accepted'] == 0


def test_subscription_fields_must_be_arrays_not_ambiguous_strings(receiver):
    client, _, graph, *_ = receiver
    graph.page_fields = 'feed'
    assert enable(client).status_code == 409


def test_facebook_feed_uses_page_owner_without_instagram_assumptions(receiver):
    client, now, _, cap, headers = receiver
    vault = client.app.state.saas.vault
    with vault.transaction() as db:
        fb = {**cap,'platform':'facebook','account':{'id':'123'},'grant_id':'facebook-grant'}
        old = vault.get(db,'saas-grant',cap['grant_id'])
        vault.put(db,'saas-grant','facebook-grant',{**fb,'grant':old['payload']['grant']},expires=old['expires'])
        db.execute('INSERT INTO saas_slots VALUES(?,?,?)',(USER,'facebook','facebook-grant'))
        vault.put(db,'saas-capability','facebook-connection',fb,expires=old['expires'],
            credential_hash=hashlib.sha256(b'fixture-secret-long-enough').hexdigest())
    path='/account/connections/facebook/webhooks'
    form=fields(client.get(path)); form.update(webhook_accept='1',field_feed='1')
    assert client.post(path,data=form,headers={'Origin':'https://social.example'},follow_redirects=False).status_code == 303
    raw=json.dumps({'object':'page','entry':[{'id':'123','time':1000,'changes':[{
        'field':'feed','value':{'post_id':'123_789','comment_id':'123_800','verb':'add','message':'PRIVATE'}}]}]}).encode()
    assert send(client,raw).json()['accepted'] == 1
    response=client.get('/api/saas/v1/connections/facebook-connection/meta/webhook-events',headers=headers)
    assert response.json()['events'][0]['target_id'] == '123_789'


def test_whole_batch_rolls_back_when_global_capacity_is_exhausted(receiver, monkeypatch):
    import socialctl.saas.meta_webhooks as module
    client, _, _, _, headers = receiver
    enable(client)
    monkeypatch.setattr(module,'GLOBAL_LIMIT',1)
    value=json.loads(body()); value['entry'].extend(json.loads(body(media='790'))['entry'])
    assert send(client,json.dumps(value).encode()).status_code == 503
    assert client.get(event_path(),headers=headers).json()['events'] == []


def test_signed_media_only_mention_is_minimized_owned_consumable_and_deduplicated(receiver):
    client, now, graph, cap, headers = receiver
    assert enable(client).status_code == 303
    baseline = len(graph.calls)
    raw = json.dumps({'object': 'instagram', 'entry': [{'id': '456', 'time': int(now[0]),
        'changes': [{'field': 'mentions', 'value': {'media_id': '789',
            'text': 'PRIVATE CAPTION', 'from': {'username': 'PRIVATE AUTHOR'}}}]}]}).encode()
    result = send(client, raw)
    assert result.status_code == 200 and result.json()['accepted'] == 1
    assert send(client, raw).json()['duplicates'] == 1
    events = client.get(event_path(), headers=headers).json()['events']
    assert len(events) == 1
    event = events[0]
    assert (event['field'], event['target_id'], event['comment_id']) == ('mentions', '789', None)
    assert client.get(event_path()).status_code in {401, 403, 404}
    vault = client.app.state.saas.vault
    with vault.read_transaction() as db:
        saved = vault.get(db, 'saas-meta-webhook-event', event['id'])['payload']
        assert saved['user_id'] == cap['user_id'] and saved['grant_id'] == cap['grant_id']
        assert 'PRIVATE' not in json.dumps(saved)
    consumed = client.post(event_path()+'/'+event['id']+'/ack', headers=headers)
    assert consumed.status_code == 200 and consumed.json()['remote_writes'] == 0
    assert client.get(event_path(), headers=headers).json()['events'] == []
    assert send(client, raw).json()['duplicates'] == 1
    assert len(graph.calls) == baseline
    malformed = json.loads(raw)
    malformed['entry'][0]['changes'][0]['value']['comment_id'] = 'invalid'
    assert send(client, json.dumps(malformed).encode()).json()['accepted'] == 0
