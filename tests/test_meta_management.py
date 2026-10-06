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
            return httpx.Response(200, json={"data": [{"id": "123"}] if self.liked else []})
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


@pytest.mark.parametrize("platform,field", [("facebook", "is_hidden"), ("instagram", "hide")])
def test_comment_moderation_is_explicit_and_uses_owned_media_membership(tmp_path, platform, field):
    mod, _, graph, client, store = service(tmp_path, platform)
    change = mod.prepare_meta(client, store, {"action": "comment-moderate", "target_id": graph.state["id"], "comment_id": "800", "hide": True})
    result = mod.apply_meta(client, store, change.id, change.fingerprint)
    assert graph.writes == [("POST", "800", {field: ["true"]})]
    assert result.status == "verified"


def test_page_like_unlike_separate_approved_changes(tmp_path):
    mod, _, graph, client, store = service(tmp_path)
    for action, method in [("like", "POST"), ("unlike", "DELETE")]:
        change = mod.prepare_meta(client, store, {"action": action, "target_id": "123_789"})
        result = mod.apply_meta(client, store, change.id, change.fingerprint)
        assert result.status == "verified"
        assert graph.writes[-1][:2] == (method, "123_789/likes")


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
