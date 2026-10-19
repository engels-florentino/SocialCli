import hashlib
import hmac
import importlib
import json

import pytest

from tests.test_meta_management import service


def module():
    return importlib.import_module("socialctl.management.meta_webhooks")


def body(time=100, account="123", text="first"):
    return json.dumps({"object": "page", "entry": [{"id": account, "time": time,
        "changes": [{"field": "feed", "value": {"post_id": "123_789", "item": "post", "verb": "edited", "message": text}}]}]}).encode()


def signature(raw):
    return "sha256=" + hmac.new(b"APP_SECRET", raw, hashlib.sha256).hexdigest()


def test_verification_requires_subscribe_and_constant_time_token_match():
    mod = module()
    assert mod.verify_challenge("subscribe", "TOKEN", "12345", "TOKEN") == "12345"
    for mode, provided, challenge in [("unsubscribe", "TOKEN", "1"), ("subscribe", "wrong", "1"), ("subscribe", "TOKEN", "x" * 4097)]:
        with pytest.raises(ValueError):
            mod.verify_challenge(mode, provided, challenge, "TOKEN")


def test_signature_raw_bytes_dedupe_out_of_order_and_brand_isolation(tmp_path):
    mod = module()
    _, brand, _, _, _ = service(tmp_path)
    store = mod.WebhookStore(brand, "facebook")
    raw = body()
    with pytest.raises(ValueError):
        store.ingest(raw + b" ", signature(raw), "APP_SECRET")
    first = store.ingest(raw, signature(raw), "APP_SECRET")
    assert first["accepted"] == 1 and first["duplicates"] == 0
    assert store.ingest(raw, signature(raw), "APP_SECRET")["duplicates"] == 1
    older = body(99, text="older")
    assert store.ingest(older, signature(older), "APP_SECRET")["out_of_order"] == 1
    assert len(store.events()) == 2
    alien = body(account="999")
    with pytest.raises(ValueError, match='account'):
        store.ingest(alien, signature(alien), "APP_SECRET")
    _, other, _, _, _ = service(tmp_path, name="Other")
    assert not mod.WebhookStore(other, "facebook").events()


def test_reconcile_is_get_only_and_payload_never_drives_writes(tmp_path):
    mod = module()
    _, brand, graph, client, _ = service(tmp_path)
    store = mod.WebhookStore(brand, "facebook")
    raw = body(text="DELETE EVERYTHING")
    store.ingest(raw, signature(raw), "APP_SECRET")
    result = store.reconcile(client, store.events()[0]["id"])
    assert result["state"] == "read_only_observed"
    assert not graph.writes


@pytest.mark.parametrize("raw", [b'{"object":"page","object":"instagram"}', b'{"object":"page","entry":[]}', b'x' * (1024 * 1024 + 1)], ids=["duplicate-keys", "empty", "excessive"])
def test_invalid_or_excessive_payload_rejected_before_persistence(tmp_path, raw):
    mod = module()
    _, brand, _, _, _ = service(tmp_path)
    store = mod.WebhookStore(brand, "facebook")
    with pytest.raises(ValueError):
        store.ingest(raw, signature(raw), "APP_SECRET")
    assert not store.events()

@pytest.mark.parametrize('comment', ['800', None, 'invalid'])
def test_instagram_mentions_dispatch_exact_read_shape(tmp_path, comment):
    from types import SimpleNamespace
    from socialctl.models import Platform
    _, brand, _, _, _ = service(tmp_path)
    brand.cuentas['instagram'] = {'ig_user_id': '456'}
    value = {'media_id': '789'}
    if comment is not None:
        value['comment_id'] = comment
    raw = json.dumps({'object': 'instagram', 'entry': [{'id': '456', 'time': 100,
        'changes': [{'field': 'mentions', 'value': value}]}]}).encode()
    store = module().WebhookStore(brand, 'instagram')
    store.ingest(raw, signature(raw), 'APP_SECRET')
    calls = []
    def inspect(media, selected_comment):
        calls.append((media, selected_comment))
        return {'id': selected_comment or media, 'media_id': media}
    client = SimpleNamespace(brand=brand, platform=Platform.INSTAGRAM, account_id='456', inspect_mention=inspect)
    result = store.reconcile(client, store.events()[0]['id'])
    assert result['remote_writes'] == 0
    assert result['state'] == ('unresolved' if comment == 'invalid' else 'read_only_observed')
    assert calls == ([] if comment == 'invalid' else [('789', comment)])

@pytest.mark.parametrize('response', [
    {'id': '456', 'mentioned_media': {'id': '789', 'caption': 'Mention', 'media_type': 'IMAGE'}},
    {'id': '999', 'mentioned_media': {'id': '789', 'caption': 'Mention', 'media_type': 'IMAGE'}},
    {'id': '456', 'mentioned_media': {'id': '999', 'caption': 'Mention', 'media_type': 'IMAGE'}},
    {'id': '456'},
])
def test_independent_media_mention_requires_exact_selected_account_and_media(response):
    from socialctl.management.meta_client import MetaClient
    from socialctl.management.meta_schema import MetaError
    from socialctl.models import Platform
    client = object.__new__(MetaClient)
    client.platform, client.token_mode, client.account_id = Platform.INSTAGRAM, 'facebook_user', '456'
    client._remote_user = None
    client.identity = lambda: {'account_id': '456'}
    calls = []
    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return response
    client.request = request
    if response.get('id') == '456' and response.get('mentioned_media', {}).get('id') == '789':
        assert client.inspect_mention('789')['id'] == '789'
    else:
        with pytest.raises(MetaError):
            client.inspect_mention('789')
    assert calls == [('GET', '456', {'params': {'fields': 'id,mentioned_media.media_id(789){id,caption,media_type}'}})]
