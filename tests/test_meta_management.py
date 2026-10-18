"""Meta management contracts use isolated brands and a local Graph transport."""
import importlib
import time
from urllib.parse import parse_qs

import httpx
import pytest

from socialctl.brands import crear_brand
from socialctl.models import Platform


class Graph:
    def __init__(self, platform="facebook"):
        self.platform = platform
        self.actor = "123"
        self.owner = "123" if platform == "facebook" else "456"
        self.state = {"id": "123_789" if platform == "facebook" else "789",
                      "from" if platform == "facebook" else "owner": {"id": self.owner},
                      "message": "Before", "description": "Before", "title": "Title",
                      "comment_enabled": True, "is_published": True}
        self.video_state = dict(self.state)
        if platform == "facebook":
            self.video_state["id"] = "789"
        self.profile_state = {"id": "123", "about": "Before", "name": "Name"}
        self.writes = []
        self.failure = None
        self.on_write = None
        self.hidden = False
        self.liked = False
        self.subscribed = []

    def __call__(self, request):
        assert request.url.host == "graph.facebook.com"
        assert request.headers["Authorization"] == "Bearer SECRET"
        assert "access_token" not in request.url.params
        path = request.url.path.removeprefix("/v26.0/")
        if request.method != "GET":
            self.writes.append((request.method, path, parse_qs(request.content.decode())))
            if self.on_write:
                self.on_write()
            if self.failure == "timeout":
                raise httpx.ReadTimeout("SECRET", request=request)
            if self.failure == "redirect":
                return httpx.Response(307, headers={"Location": "https://evil.test"})
            if self.failure == "reject":
                return httpx.Response(403, json={"error": {"message": "SECRET"}})
            values = self.writes[-1][2]
            if path == "800":
                self.hidden = values.get("is_hidden", values.get("hide"))[0] == "true"
            if path.endswith("/likes"):
                self.liked = request.method == "POST"
            if path.endswith("/subscribed_apps"):
                self.subscribed = values["subscribed_fields"][0].split(",") if request.method == "POST" else []
            for key, value in values.items():
                target_state = self.state if path == "123_789" else (
                    self.video_state if path == "789" else (
                        self.profile_state if path == "123" else self.state
                    )
                )
                parsed = int(value[0]) if key == "scheduled_publish_time" else (
                    value[0] == "true" if value[0] in {"true", "false"} else value[0]
                )
                target_state[key] = parsed
            if path == "123":
                for key, value in values.items():
                    parsed = int(value[0]) if key == "scheduled_publish_time" else (
                        value[0] == "true" if value[0] in {"true", "false"} else value[0]
                    )
                    self.profile_state[key] = parsed
            return httpx.Response(200, json={"success": True})
        if path == "me":
            return httpx.Response(200, json={"id": self.actor, "instagram_business_account": {"id": "456"}})
        if path == "me/accounts":
            return httpx.Response(200, json={"data": [{"id": "123", "tasks": ["MANAGE"],
                                      "instagram_business_account": {"id": "456"}}]})
        if path == "me/permissions":
            return httpx.Response(200, json={"data": [{"permission": p, "status": "granted"}
                                      for p in ["instagram_basic", "instagram_manage_contents"]]})
        if path == "123_789":
            return httpx.Response(200, json=self.state)
        if path in {"123", "456"}:
            return httpx.Response(200, json=self.profile_state if path == "123" else {"id": path, "about": "Before", "name": "Name"})
        if path in {"123_789/comments", "789/comments"}:
            return httpx.Response(200, json={"data": [{"id": "800", "message": "Comment", "text": "Comment", "is_hidden": self.hidden, "hidden": self.hidden}]})
        if path == "123_789/reactions":
            return httpx.Response(200, json={"data": [{"id": "111", "name": "FACEBOOK", "type": "LIKE"}]})
        if path == "123_789/insights":
            return httpx.Response(200, json={"data": [
                {"name": "post_video_views", "period": "day", "values": [{"value": 7, "end_time": "2026-09-13T00:00:00+0000"}]},
            ]})
        if path == "789/insights":
            return httpx.Response(200, json={"data": [
                {"name": "views", "period": "day", "values": [{"value": 11, "end_time": "2026-09-13T00:00:00+0000"}]},
            ]})
        if path == "123_789/likes":
            return httpx.Response(200, json={"data": [], "summary": {
                "has_liked": self.liked, "can_like": True, "total_count": int(self.liked)}})
        if path == "789":
            if self.platform == "facebook":
                fields = str(request.url.params.get("fields", ""))
                if "title" not in set(fields.split(",")):
                    return httpx.Response(400, json={"error": {"message": "post/video ambiguity in mock"}})
            return httpx.Response(200, json=self.video_state)
        if path == "123/subscribed_apps":
            return httpx.Response(200, json={"data": [{"id": "999", "subscribed_fields": self.subscribed}] if self.subscribed else []})
        if path in {"123/feed", "123/photos", "123/posts", "123/videos", "123/video_reels", "123/stories", "123/scheduled_posts", "456/media", "456/stories"}:
            row = dict(self.state)
            if path.split("/")[-1] in {"videos", "video_reels"}:
                row["id"] = "789"
            return httpx.Response(200, json={"data": [row]})
        if self.platform == "instagram" and path == "456/content_publishing_limit":
            return httpx.Response(
                200,
                json={
                    "quota_usage": {
                        "quota_total": 24,
                        "quota_used": 6,
                    },
                    "config": {
                        "reset_time": "2026-09-13T10:00:00+00:00",
                        "time_unit": "DAY",
                    },
                },
            )
        return httpx.Response(400, json={"error": {"message": "unsupported SECRET"}})


def service(tmp_path, platform="facebook", name="Example", user=False):
    client_module = importlib.import_module("socialctl.management.meta_client")
    changes = importlib.import_module("socialctl.management.meta_changes")
    brand = crear_brand(tmp_path, name)
    brand.cuentas = {"facebook": {"page_id": "123"}, "instagram": {"ig_user_id": "456"}}
    secret = {"access_token": "SECRET"}
    if user:
        secret.update(token_mode="facebook_user", actor_id="111")
    brand.guardar_secreto(Platform(platform), secret)
    graph = Graph(platform)
    if user:
        graph.actor = "111"
    client = client_module.MetaClient(brand, Platform(platform), httpx.Client(transport=httpx.MockTransport(graph)))
    return changes, brand, graph, client, changes.MetaStore(brand.raiz)


def test_post_edit_exact_digest_intent_and_omission(tmp_path):
    mod, brand, graph, client, store = service(tmp_path)
    change = mod.prepare_meta(client, store, {"action": "post-update", "target_id": "123_789", "patch": {"message": "After"}})
    assert not graph.writes
    with pytest.raises(ValueError, match='approval'):
        mod.apply_meta(client, store, change.id, "wrong")
    def durable():
        assert store.load(change.id).status == "applying"
    graph.on_write = durable
    result = mod.apply_meta(client, store, change.id, change.fingerprint)
    assert result.status == "verified"
    assert graph.writes == [("POST", "123_789", {"message": ["After"]})]
    mod.apply_meta(client, store, change.id, change.fingerprint)
    assert len(graph.writes) == 1


@pytest.mark.parametrize("patch", [{"caption": "not writable"}, {"comment_enabled": None}, {"comment_enabled": "false"}])
def test_ig_only_documented_comment_enabled_patch(tmp_path, patch):
    mod, _, graph, client, store = service(tmp_path, "instagram")
    with pytest.raises(ValueError):
        mod.prepare_meta(client, store, {"action": "media-update", "target_id": "789", "patch": patch})
    assert not graph.writes


@pytest.mark.parametrize('initial', [True, False, None, 'false'])
def test_instagram_comment_setting_uses_read_field_and_write_parameter(tmp_path, initial):
    mod, brand, graph, client, store = service(tmp_path, 'instagram')
    state = {'id':'789', 'owner':{'id':'456'}}
    if initial is not None:state['is_comment_enabled'] = initial
    writes = []
    def transport(request):
        path = request.url.path.removeprefix('/v26.0/')
        if path == '789':
            if request.method == 'GET':
                fields = request.url.params['fields'].split(',')
                if 'comment_enabled' in fields:
                    return httpx.Response(400,json={'error':{'code':100,'message':'Tried accessing nonexisting field (comment_enabled)'}})
                return httpx.Response(200,json={k:v for k,v in state.items() if k in fields})
            values = parse_qs(request.content.decode())
            writes.append(values)
            assert values == {'comment_enabled':['false' if initial is True else 'true']}
            state['is_comment_enabled'] = values['comment_enabled'][0] == 'true'
            return httpx.Response(200,json={'success':True})
        return graph(request)
    client.client = httpx.Client(transport=httpx.MockTransport(transport))
    edit = {'action':'media-update','target_id':'789','patch':{'comment_enabled':not initial}}
    if type(initial) is not bool:
        with pytest.raises(ValueError,match='comment setting.*unavailable'):
            mod.prepare_meta(client,store,edit)
        assert not writes
        return
    change = mod.prepare_meta(client,store,edit)
    assert change.before == {'id':'789','comment_enabled':initial}
    result = mod.apply_meta(client,store,change.id,change.fingerprint)
    assert result.status == 'verified' and len(writes) == 1
    assert state['is_comment_enabled'] is (not initial)


def test_instagram_insights_do_not_request_write_only_comment_parameter(tmp_path):
    _, _, graph, client, _ = service(tmp_path, 'instagram')
    def transport(request):
        if request.method == 'GET' and request.url.path.endswith('/789'):
            if 'comment_enabled' in request.url.params.get('fields','').split(','):
                return httpx.Response(400,json={'error':{'code':100,'message':'nonexisting field'}})
            return httpx.Response(200,json={'id':'789','owner':{'id':'456'},'is_comment_enabled':True})
        return graph(request)
    client.client = httpx.Client(transport=httpx.MockTransport(transport))
    result = client.insights('789',['views'],period='day')
    assert result['complete'] and result['data'][0]['values'][0]['value'] == 11
    assert not graph.writes


@pytest.mark.parametrize('platform,target,metric', [('instagram','789','views'),('facebook','123_789','post_video_views')])
def test_content_insight_metric_ids_are_not_treated_as_object_ids(tmp_path, platform, target, metric):
    _, _, graph, client, _ = service(tmp_path, platform)
    metric_id = target+'/insights/'+metric+'/lifetime'
    def transport(request):
        if request.url.path.endswith('/insights'):
            return httpx.Response(200,json={'data':[{'id':metric_id,'name':metric,'period':'lifetime','values':[{'value':7}]}]})
        return graph(request)
    client.client = httpx.Client(transport=httpx.MockTransport(transport))
    result = client.insights(target,[metric],period='lifetime')
    assert result['complete'] and result['data'][0]['id'] == metric_id
    assert result['data'][0]['values'] == [{'value':7}]
    assert not graph.writes


@pytest.mark.parametrize('bad_id', ['999/insights/views/lifetime','789/insights/reach/lifetime',
    '789/insights/views/day','https://evil.example/789/insights/views/lifetime',{'invalid':'shape'}])
def test_insight_ids_must_bind_to_the_requested_resource_metric_and_period(tmp_path, bad_id):
    _, _, graph, client, _ = service(tmp_path,'instagram')
    def transport(request):
        if request.url.path.endswith('/insights'):
            return httpx.Response(200,json={'data':[{'id':bad_id,'name':'views','period':'lifetime','values':[{'value':7}]}]})
        return graph(request)
    client.client = httpx.Client(transport=httpx.MockTransport(transport))
    with pytest.raises(ValueError):client.insights('789',['views'],period='lifetime')
    assert not graph.writes


def test_insight_response_cannot_substitute_a_different_requested_period(tmp_path):
    _, _, graph, client, _ = service(tmp_path,'instagram')
    def transport(request):
        if request.url.path.endswith('/insights'):
            return httpx.Response(200,json={'data':[{'id':'789/insights/views/day',
                'name':'views','period':'day','values':[{'value':7}]}]})
        return graph(request)
    client.client = httpx.Client(transport=httpx.MockTransport(transport))
    with pytest.raises(ValueError,match='unrequested period'):
        client.insights('789',['views'],period='lifetime')
    assert not graph.writes


@pytest.mark.parametrize('returned,website,want', [
    ('+12129205486','https://example.test','verified'),
    ('+12129205487','https://example.test','uncertain'),
    ('12129205486','https://example.test','uncertain'),
    ('+12129205486 ext 1','https://example.test','uncertain'),
    ('+12129205486','https://other.example.test','uncertain'),
])
def test_profile_phone_format_readback_preserves_digits_and_other_fields(tmp_path, returned, website, want):
    mod, _, graph, client, store = service(tmp_path)
    graph.profile_state.update(phone='+12129205486',website='http://example.test')
    def transport(request):
        response = graph(request)
        if request.method == 'POST':
            graph.profile_state.update(phone=returned,website=website)
        return response
    client.client = httpx.Client(transport=httpx.MockTransport(transport))
    change = mod.prepare_meta(client,store,{'action':'profile-update','target_id':'123',
        'patch':{'phone':'+1 212-920-5486','website':'https://example.test'}})
    result = mod.apply_meta(client,store,change.id,change.fingerprint)
    assert result.status == want
    assert result.fingerprint == change.fingerprint
    if want == 'verified':
        assert result.journal[-1]['normalized_fields'] == {'phone':'+12129205486'}
    mod.reconcile_meta(client,store,change.id)
    assert len(graph.writes) == 1


def test_phone_format_equivalence_does_not_resolve_a_lost_write_receipt(tmp_path):
    mod, _, graph, client, store = service(tmp_path)
    graph.profile_state.update(phone='+12129205486',website='http://example.test')
    change = mod.prepare_meta(client,store,{'action':'profile-update','target_id':'123',
        'patch':{'phone':'+1 212-920-5486','website':'https://example.test'}})
    graph.failure = 'timeout'
    graph.on_write = lambda: graph.profile_state.update(website='https://example.test')
    result = mod.apply_meta(client,store,change.id,change.fingerprint)
    assert result.status == 'uncertain' and result.receipt is None
    assert mod.reconcile_meta(client,store,change.id).status == 'uncertain'
    assert len(graph.writes) == 1


def test_ig_delete_requires_facebook_user_token_and_actual_linked_asset(tmp_path):
    mod, _, graph, client, store = service(tmp_path, "instagram")
    with pytest.raises(ValueError, match="USER|user"):
        mod.prepare_meta(client, store, {"action": "media-delete", "target_id": "789"})
    mod, _, graph, client, store = service(tmp_path, "instagram", "UserBrand", user=True)
    change = mod.prepare_meta(client, store, {"action": "media-delete", "target_id": "789"})
    result = mod.apply_meta(client, store, change.id, change.fingerprint)
    assert graph.writes[0][:2] == ("DELETE", "789")
    assert result.status == "accepted_unverifiable"


@pytest.mark.parametrize("failure", ["timeout", "redirect"])
def test_uncertain_write_never_retries_including_new_uuid(tmp_path, failure):
    mod, _, graph, client, store = service(tmp_path)
    edit = {"action": "post-update", "target_id": "123_789", "patch": {"message": "After"}}
    change = mod.prepare_meta(client, store, edit)
    graph.failure = failure
    result = mod.apply_meta(client, store, change.id, change.fingerprint)
    assert result.status == "uncertain"
    mod.apply_meta(client, store, change.id, change.fingerprint)
    another = mod.prepare_meta(client, store, edit)
    with pytest.raises(ValueError, match='uncertain'):
        mod.apply_meta(client, store, another.id, another.fingerprint)
    assert len(graph.writes) == 1
    assert "SECRET" not in store.path_for(change.id).read_text()


def test_ownership_and_brand_are_not_id_prefix_inference(tmp_path):
    mod, _, graph, client, store = service(tmp_path)
    graph.state["from"]["id"] = "999"
    with pytest.raises(ValueError, match='owner'):
        mod.prepare_meta(client, store, {"action": "post-delete", "target_id": "123_789"})
    graph.state["from"]["id"] = "123"
    change = mod.prepare_meta(client, store, {"action": "post-delete", "target_id": "123_789"})
    _, _, _, other, _ = service(tmp_path, name="Other")
    with pytest.raises(ValueError, match='brand'):
        mod.apply_meta(other, store, change.id, change.fingerprint)
    assert not graph.writes


@pytest.mark.parametrize(
    "action,target_id,patch",
    [
        ("post-update", "123_789", {"message": "After"}),
        ("video-update", "789", {"description": "Video updated"}),
    ],
)
def test_facebook_content_update_is_exact_and_owned(tmp_path, action, target_id, patch):
    mod, _, graph, client, store = service(tmp_path)
    change = mod.prepare_meta(client, store, {"action": action, "target_id": target_id, "patch": patch})
    result = mod.apply_meta(client, store, change.id, change.fingerprint)
    assert result.status == "verified"
    assert graph.writes[-1][:2] == ("POST", target_id)


@pytest.mark.parametrize("action,target_id", [("post-delete", "123_789"), ("video-delete", "789")])
def test_facebook_content_delete_is_exact(tmp_path, action, target_id):
    mod, _, graph, client, store = service(tmp_path)
    change = mod.prepare_meta(client, store, {"action": action, "target_id": target_id})
    result = mod.apply_meta(client, store, change.id, change.fingerprint)
    assert result.status == "accepted_unverifiable"
    assert graph.writes[-1][0] == "DELETE"
    assert graph.writes[-1][1] == target_id


def test_facebook_profile_update_can_be_prepared_and_verified(tmp_path):
    mod, _, graph, client, store = service(tmp_path)
    change = mod.prepare_meta(client, store, {"action": "profile-update", "target_id": "123", "patch": {"about": "Acerca"}})
    result = mod.apply_meta(client, store, change.id, change.fingerprint)
    assert result.status == "verified"
    assert graph.writes[-1] == ("POST", "123", {"about": ["Acerca"]})


def test_profile_update_requires_observable_fields_before_preparing(tmp_path):
    mod, _, graph, client, store = service(tmp_path)
    # A missing field is not proof that it is unset, nor a verifiable preview.
    with pytest.raises(ValueError, match="did not return.*bio"):
        mod.prepare_meta(client, store, {
            "action": "profile-update", "target_id": "123", "patch": {"bio": "After"},
        })
    assert not graph.writes
    assert not list(store.root.glob("*.json"))


@pytest.mark.parametrize(
    "failure,status,stage,http_status",
    [
        ("reject", "rejected", "http", 400),
        ("server", "uncertain", "http", 503),
        ("timeout", "uncertain", "transport", None),
        ("invalid_json", "uncertain", "decode", 200),
        ("non_object", "uncertain", "response_shape", 200),
        ("embedded_error", "uncertain", "provider_error", 200),
        ("no_ack", "uncertain", "acknowledgement", 200),
    ],
)
def test_meta_write_diagnostics_survive_reload_without_replay_or_secrets(
    tmp_path, failure, status, stage, http_status,
):
    mod, _, graph, client, store = service(tmp_path)
    writes = []

    def transport(request):
        if request.method == "GET":
            return graph(request)
        writes.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("SECRET access_token=hidden", request=request)
        if failure == "invalid_json":
            return httpx.Response(200, text="SECRET not JSON")
        if failure == "non_object":
            return httpx.Response(200, json=["SECRET"])
        if failure == "no_ack":
            return httpx.Response(200, json={"success": False, "private": "SECRET"})
        return httpx.Response(http_status, json={"error": {
            "code": 200, "error_subcode": 9, "fbtrace_id": "safe-ref",
            "message": "SECRET access_token=hidden permission failure",
        }})

    client.client = httpx.Client(transport=httpx.MockTransport(transport))
    change = mod.prepare_meta(client, store, {
        "action": "profile-update", "target_id": "123", "patch": {"about": "After"},
    })
    mod.apply_meta(client, store, change.id, change.fingerprint)
    saved = store.load(change.id)
    assert saved.status == status
    failure_event = next(row for row in saved.journal if row["event"].startswith("write_")
                         and row["event"] != "write_intent")
    assert failure_event["error"]
    assert failure_event["error_detail"]["retryable"] is False
    assert failure_event["diagnostic"]["stage"] == stage
    assert failure_event["diagnostic"].get("http_status") == http_status
    assert failure_event["diagnostic"]["method"] == "POST"
    if failure in {"reject", "server", "embedded_error"}:
        assert "code=200" in failure_event["error"]
        assert "subcode=9" in failure_event["error"]
        assert "safe-ref" in failure_event["error"]
    serialized = store.path_for(change.id).read_text()
    assert "SECRET" not in serialized and "hidden" not in serialized
    assert saved.receipt is None and saved.result_id is None
    mod.reconcile_meta(client, store, change.id)
    mod.apply_meta(client, store, change.id, change.fingerprint)
    assert len(writes) == 1


def test_profile_readback_missing_field_records_unavailable_not_unset(tmp_path):
    mod, _, graph, client, store = service(tmp_path)
    graph.profile_state["bio"] = "Before"
    change = mod.prepare_meta(client, store, {
        "action": "profile-update", "target_id": "123", "patch": {"bio": "After"},
    })
    # Simulate a receipt followed by a provider that omits the requested field.
    def transport(request):
        if request.method == "POST":
            graph.profile_state.pop("bio")
            return httpx.Response(200, json={"success": True})
        return graph(request)
    client.client = httpx.Client(transport=httpx.MockTransport(transport))
    mod.apply_meta(client, store, change.id, change.fingerprint)
    saved = store.load(change.id)
    assert saved.status == "uncertain"
    assert saved.receipt["success"] is True
    assert saved.journal[-1]["event"] == "readback_unavailable"
    assert "bio" in saved.journal[-1]["error"]
    assert not any(e["event"] == "readback_mismatch_no_retry" for e in saved.journal)


def test_facebook_reactions_list_is_readonly_and_owner_scoped(tmp_path):
    mod, _, graph, client, store = service(tmp_path)
    result = client.reactions("123_789")
    assert result["complete"] is True
    assert result["data"][0]["id"] == "111"


def test_content_insights_require_explicit_allowlisted_metrics_and_preserve_values(tmp_path):
    _, _, graph, client, _ = service(tmp_path)
    result = client.insights("123_789", ["post_video_views"], period="day", since=1, until=2)
    assert result["target_id"] == "123_789"
    assert result["metrics"] == ["post_video_views"]
    assert result["complete"] is True
    assert result["data"][0]["values"][0]["value"] == 7
    with pytest.raises(ValueError, match='metric'):
        client.insights("123_789", ["page_impressions"])


def test_instagram_content_insights_use_ig_allowlist(tmp_path):
    _, _, _, client, _ = service(tmp_path, "instagram")
    result = client.insights("789", ["views", "reach"], period="day")
    assert result["complete"] is True
    assert result["data"][0]["name"] == "views"


@pytest.mark.parametrize("platform,field", [("facebook", "is_hidden")])
def test_comment_moderation_is_explicit_and_uses_owned_media_membership(tmp_path, platform, field):
    mod, _, graph, client, store = service(tmp_path, platform)
    change = mod.prepare_meta(client, store, {"action": "comment-moderate", "target_id": graph.state["id"], "comment_id": "800", "hide": True})
    result = mod.apply_meta(client, store, change.id, change.fingerprint)
    assert graph.writes == [("POST", "800", {field: ["true"]})]
    assert result.status == "verified"


def test_disabled_instagram_moderation_cannot_prepare_or_mutate(tmp_path):
    from socialctl.management.meta_schema import validate_edit
    mod, _, graph, client, store = service(tmp_path, 'instagram')
    raw = {'action': 'comment-moderate', 'target_id': '789', 'comment_id': '800', 'hide': True}
    with pytest.raises(ValueError, match='disabled'):
        mod.prepare_meta(client, store, raw)
    with pytest.raises(ValueError, match='disabled'):
        client.mutate(validate_edit(raw))
    assert graph.writes == []
    assert not list(store.root.glob('*.json'))


def test_disabled_instagram_moderation_preserves_old_proposals_and_reconciliation(tmp_path):
    from socialctl.management.meta_schema import validate_edit
    from socialctl.management.changes import now_utc
    mod, brand, graph, client, store = service(tmp_path, 'instagram')
    raw = {'action': 'comment-moderate', 'target_id': '789', 'comment_id': '800', 'hide': True}
    edit = validate_edit(raw)
    moment = now_utc()
    change = mod.MetaChange(id='44444444-4444-4444-8444-444444444444',
        brand=brand.nombre, brand_root=str(brand.raiz.resolve()), platform='instagram',
        identity=client.operation_identity(edit), edit=raw, before=client.snapshot(edit),
        created_at=moment, updated_at=moment)
    change.fingerprint = mod.fingerprint(change)
    store.save(change)
    with pytest.raises(ValueError, match='disabled'):
        mod.apply_meta(client, store, change.id, change.fingerprint)
    assert store.load(change.id).status == 'proposed'
    assert graph.writes == []
    change.status = 'uncertain'
    change.receipt = {'success': True, 'target_id': '800'}
    graph.hidden = True
    store.save(change)
    assert mod.reconcile_meta(client, store, change.id).status == 'verified'
    assert graph.writes == []


def test_page_like_unlike_separate_approved_changes(tmp_path):
    mod, _, graph, client, store = service(tmp_path)
    for action, method in [("like", "POST"), ("unlike", "DELETE")]:
        change = mod.prepare_meta(client, store, {"action": action, "target_id": "123_789"})
        result = mod.apply_meta(client, store, change.id, change.fingerprint)
        assert result.status == "verified"
        assert graph.writes[-1][:2] == (method, "123_789/likes")


def test_page_like_verification_uses_viewer_summary_when_profiles_are_hidden(tmp_path):
    mod, _, graph, client, store = service(tmp_path)
    def provider(request):
        if request.method == 'GET' and request.url.path.endswith('/123_789/likes'):
            return httpx.Response(200, json={'data': [], 'summary': {
                'has_liked': graph.liked, 'can_like': True, 'total_count': int(graph.liked)}})
        return graph(request)
    client.client = httpx.Client(transport=httpx.MockTransport(provider))
    change = mod.prepare_meta(client, store, {'action':'like', 'target_id':'123_789'})
    result = mod.apply_meta(client, store, change.id, change.fingerprint)
    assert result.status == 'verified'
    assert len(graph.writes) == 1
    assert mod.reconcile_meta(client, store, change.id).status == 'verified'
    assert len(graph.writes) == 1


@pytest.mark.parametrize('summary', [{}, {'has_liked':'false','can_like':True},
    {'has_liked':False,'can_like':False}])
def test_page_like_requires_explicit_viewer_state_and_eligibility(tmp_path, summary):
    mod, _, graph, client, store = service(tmp_path)
    def provider(request):
        if request.method == 'GET' and request.url.path.endswith('/123_789/likes'):
            return httpx.Response(200,json={'data':[], 'summary':summary})
        return graph(request)
    client.client = httpx.Client(transport=httpx.MockTransport(provider))
    with pytest.raises(ValueError):
        mod.prepare_meta(client, store, {'action':'like','target_id':'123_789'})
    assert not graph.writes


@pytest.mark.parametrize('failure,expected_status',[('timeout','uncertain'),('reject','rejected')])
def test_meta_apply_cli_nonverified_write_returns_failure_exit(tmp_path,monkeypatch,failure,expected_status):
    from typer.testing import CliRunner
    from socialctl.cli import app
    import socialctl.management.meta_cli as cli
    mod,brand,graph,client,store=service(tmp_path)
    (brand.raiz/'accounts.yml').write_text("facebook:\n  page_id: '123'\n")
    change=mod.prepare_meta(client,store,{'action':'post-update','target_id':'123_789','patch':{'message':'After'}})
    graph.failure=failure
    monkeypatch.setattr(cli,'make_http_client',lambda:httpx.Client(transport=httpx.MockTransport(graph)))
    result=CliRunner().invoke(app,['meta','apply',change.id,'--brand',brand.nombre,'--root',str(tmp_path),
        '--approval-fingerprint',change.fingerprint])
    assert result.exit_code==1
    assert store.load(change.id).status==expected_status
    assert len(graph.writes)==1
    assert change.id in result.output


def test_comment_like_unlike_binds_owned_media_and_reads_viewer_state(tmp_path):
    mod, _, graph, client, store = service(tmp_path)
    original = client.client
    def provider(request):
        if request.method == 'GET' and request.url.path.endswith('/800/likes'):
            return httpx.Response(200, json={'data': [], 'summary': {'has_liked': graph.liked, 'can_like': True}})
        return graph(request)
    client.client = httpx.Client(transport=httpx.MockTransport(provider))
    for action, method, liked in [('like', 'POST', True), ('unlike', 'DELETE', False)]:
        change = mod.prepare_meta(client, store, {'action': action, 'target_id': '123_789', 'comment_id': '800'})
        assert change.before['id'] == '800' and change.before['media_id'] == '123_789'
        result = mod.apply_meta(client, store, change.id, change.fingerprint)
        assert result.status == 'verified'
        assert result.result_id == '800' and result.before['comment_text'] == 'Comment'
        assert graph.liked is liked and graph.writes[-1][:2] == (method, '800/likes')
    client.client.close()
    original.close()


@pytest.mark.parametrize('failure', ['foreign_media', 'wrong_comment', 'missing_viewer_state', 'ineligible', 'user_token'])
def test_comment_like_never_guesses_binding_or_viewer_state(tmp_path, failure):
    mod, _, graph, client, store = service(tmp_path)
    if failure == 'foreign_media': graph.state['from']['id'] = '999'
    if failure == 'user_token': client.token_mode = 'facebook_user'; client.actor_id = '123'
    def provider(request):
        if request.method == 'GET' and request.url.path.endswith('/800/likes'):
            summary = {} if failure == 'missing_viewer_state' else {'has_liked': False, 'can_like': failure != 'ineligible'}
            return httpx.Response(200, json={'data': [], 'summary': summary})
        return graph(request)
    client.client = httpx.Client(transport=httpx.MockTransport(provider))
    from socialctl.management.meta_schema import MetaError
    with pytest.raises(MetaError):
        mod.prepare_meta(client, store, {'action': 'like', 'target_id': '123_789', 'comment_id': '801' if failure == 'wrong_comment' else '800'})
    assert not graph.writes


def test_comment_likes_on_distinct_comments_share_media_in_one_batch(tmp_path):
    from socialctl.management.community_batches import prepare_community_batch
    from socialctl.management.changes import ChangeError
    mod, brand, graph, client, store = service(tmp_path)
    def provider(request):
        if request.method == 'GET' and request.url.path.endswith('/123_789/comments'):
            return httpx.Response(200, json={'data':[{'id':'800', 'message':'First'}, {'id':'801', 'message':'Second'}]})
        if request.method == 'GET' and request.url.path.endswith('/likes'):
            return httpx.Response(200, json={'summary': {'has_liked': False, 'can_like': True}})
        return graph(request)
    client.client = httpx.Client(transport=httpx.MockTransport(provider))
    changes = [mod.prepare_meta(client, store, {'action':'like', 'target_id':'123_789', 'comment_id':cid}) for cid in ('800','801')]
    refs = [{'provider':'meta-management', 'change_id':c.id} for c in changes]
    assert len(prepare_community_batch(brand, refs).children) == 2
    with pytest.raises(ChangeError):
        prepare_community_batch(brand, [refs[0], refs[0]])


def test_subscriptions_require_exact_page_and_current_app_id(tmp_path):
    mod, brand, graph, client, store = service(tmp_path)
    brand.cuentas["facebook"]["app_id"] = "999"
    edit = {"action": "subscription-set", "target_id": "123", "subscribed_fields": ["feed"]}
    change = mod.prepare_meta(client, store, edit)
    result = mod.apply_meta(client, store, change.id, change.fingerprint)
    assert result.status == "verified"
    assert graph.writes == [("POST", "123/subscribed_apps", {"subscribed_fields": ["feed"]})]
    for fields in [["messages"], ["leadgen"], ["feed", "feed"]]:
        with pytest.raises(ValueError):
            mod.prepare_meta(client, store, {**edit, "subscribed_fields": fields})


def test_reads_enforce_resource_specific_ids_and_bounded_list_ownership(tmp_path):
    _, _, graph, client, _ = service(tmp_path, "instagram")
    assert client.profile(["name"])["id"] == "456"
    assert client.content_list("stories")["complete"]
    with pytest.raises(ValueError):
        client.content("123_789")
    with pytest.raises(ValueError):
        client.profile(["access_token"])
    graph.state["owner"]["id"] = "111"
    with pytest.raises(ValueError, match='owner'):
        client.content_list()


def test_facebook_content_inventory_exposes_documented_edges(tmp_path):
    _, _, _, client, _ = service(tmp_path)
    for edge in ("feed", "photos", "posts", "videos", "video_reels", "stories", "scheduled_posts"):
        result = client.content_list(edge)
        assert result["complete"] is True


def test_instagram_story_inventory_avoids_incompatible_comment_fields(tmp_path):
    _, _, graph, client, _ = service(tmp_path, "instagram")
    def provider(request):
        if request.url.path.endswith('/456/stories'):
            if 'is_comment_enabled' in request.url.params.get('fields', '').split(','):
                return httpx.Response(400, json={'error': {
                    'code': 10, 'message': 'Comments cannot be made on story media.'}})
            return httpx.Response(200, json={'data': [{
                'id': '789', 'owner': {'id': '456'}, 'media_type': 'IMAGE',
                'media_product_type': 'STORY', 'timestamp': '2026-10-18T13:26:07+0000'}]})
        return graph(request)
    client.client = httpx.Client(transport=httpx.MockTransport(provider))
    result = client.content_list('stories')
    assert result['complete'] is True
    assert result['data'][0]['id'] == '789'
    assert result['data'][0]['media_product_type'] == 'STORY'


def test_native_feed_reprogram_and_cancel_use_exact_remote_id(tmp_path):
    mod, _, graph, client, store = service(tmp_path)
    graph.state.update(is_published=False, scheduled_publish_time=int(time.time()) + 3600)
    edit = {"action": "schedule-reprogram", "target_id": "123_789", "scheduled_publish_time": int(time.time()) + 7200}
    change = mod.prepare_meta(client, store, edit)
    result = mod.apply_meta(client, store, change.id, change.fingerprint)
    assert result.status == "verified"
    assert graph.writes[-1] == ("POST", "123_789", {"scheduled_publish_time": [str(edit["scheduled_publish_time"])]})
    assert result.native_schedule["remote_id"] == "123_789"
    assert result.native_schedule["planner_visibility"] == "not_verifiable"
    cancel = mod.prepare_meta(client, store, {"action": "schedule-cancel", "target_id": "123_789"})
    result = mod.apply_meta(client, store, cancel.id, cancel.fingerprint)
    assert graph.writes[-1][:2] == ("DELETE", "123_789")
    assert result.status == "accepted_unverifiable"


def test_native_schedule_changed_or_published_prevents_mutation(tmp_path):
    mod, _, graph, client, store = service(tmp_path)
    with pytest.raises(ValueError):
        mod.prepare_meta(client, store, {"action": "schedule-cancel", "target_id": "123_789"})
    graph.state.update(is_published=False, scheduled_publish_time=int(time.time()) + 3600)
    change = mod.prepare_meta(client, store, {"action": "schedule-cancel", "target_id": "123_789"})
    graph.state["scheduled_publish_time"] += 100
    with pytest.raises(ValueError, match="changed"):
        mod.apply_meta(client, store, change.id, change.fingerprint)
    assert not graph.writes


def test_streaming_graph_error_reports_permission_code_without_secret(tmp_path):
    mod, _, _, client, _ = service(tmp_path)
    client.client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(400,json={'error':{'code':200,'error_subcode':9,'message':'SECRET access_token=SECRET','fbtrace_id':'safe-ref'}})))
    with pytest.raises(ValueError) as error:
        client.request('POST','123_789/likes')
    message=str(error.value)
    assert '200' in message and '9' in message and 'safe-ref' in message and 'pages_manage_engagement' in message
    assert 'SECRET' not in message


def test_user_token_cannot_substitute_page_for_like(tmp_path):
    mod, _, graph, client, store=service(tmp_path,user=True)
    with pytest.raises(ValueError,match='Page token'):
        mod.prepare_meta(client,store,{'action':'like','target_id':'123_789'})
    assert not graph.writes
