"""Real service/store over a strict, entirely local Graph transport."""
import importlib
import json
import subprocess

import httpx
import pytest

from socialctl.brands import crear_brand
from socialctl.models import Platform


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    def blocked(*args, **kwargs):
        raise AssertionError("external transport/subprocess prohibited")
    monkeypatch.setattr(subprocess, "Popen", blocked)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)


class Graph:
    def __init__(self, platform="facebook"):
        self.platform = platform
        self.account = "123" if platform == "facebook" else "456"
        self.comments = {}
        self.writes = []
        self.owner = self.account
        self.identity = "123"
        self.failure = None
        self.after_write = None
        self.pages = None

    def row(self, ident, text, author=None):
        return {"id": ident, "message" if self.platform == "facebook" else "text": text,
                "from": {"id": author or self.account}, "parent": {"id": "789"}}

    def __call__(self, request):
        path = request.url.path.removeprefix("/v26.0/")
        if request.method == "GET" and self.failure == "read_permission":
            return httpx.Response(403, json={"error": {"message": "SECRET"}})
        if request.method != "GET":
            self.writes.append((request.method, path, request.content.decode()))
            if self.failure == "timeout":
                self.comments["900"] = self.row("900", "Exact supplied text")
                raise httpx.ReadTimeout("SECRET", request=request)
            if self.failure == "permission":
                return httpx.Response(403, json={"error": {"message": "SECRET"}})
            if request.method == "DELETE":
                self.comments.pop(path)
                return httpx.Response(200, json={"success": True})
            from urllib.parse import parse_qs
            body = parse_qs(request.content.decode())
            text = body["message"][0]
            ident = path if path in self.comments else "900"
            self.comments[ident] = self.row(ident, text)
            if self.after_write:
                self.after_write()
            return httpx.Response(200, json={} if self.failure == "lost-id" else
                                  ({"success": True} if path == ident else {"id": ident}))
        if path == "me":
            return httpx.Response(200, json={"id": self.identity,
                "instagram_business_account": {"id": "456"}})
        if path == "789":
            return httpx.Response(200, json={"id": "789",
                "from" if self.platform == "facebook" else "owner": {"id": self.owner}})
        if path in {"789/comments", "800/comments", "800/replies"}:
            if self.pages:
                return httpx.Response(200, json=self.pages(request))
            return httpx.Response(200, json={"data": list(self.comments.values())})
        if path in self.comments:
            return httpx.Response(200, json=self.comments[path])
        return httpx.Response(400, json={"error": {"message": "Unsupported request SECRET"}})


def service(tmp_path, platform="facebook"):
    mod = importlib.import_module("socialctl.management.meta_comments")
    brand = crear_brand(tmp_path, "Example")
    brand.cuentas = {"facebook": {"page_id": "123"}, "instagram": {"ig_user_id": "456"}}
    brand.guardar_secreto(Platform(platform), {"access_token": "SECRET"})
    graph = Graph(platform)
    client = mod.MetaCommentsClient(brand, Platform(platform), httpx.Client(transport=httpx.MockTransport(graph)))
    return mod, brand, graph, client, mod.CommentStore(brand.raiz)


def test_comment_service_exists():
    from socialctl.management import __path__
    import pkgutil
    assert "meta_comments" in {m.name for m in pkgutil.iter_modules(__path__)}


@pytest.mark.parametrize("platform", ["facebook", "instagram"])
def test_exact_approval_durable_intent_and_id_before_verification(tmp_path, platform):
    mod, brand, graph, client, store = service(tmp_path, platform)
    change = mod.prepare_comment(client, store, media_id="789", text="Exact supplied text")
    with pytest.raises(mod.CommentError):
        mod.apply_comment(client, store, change.id, "wrong")
    assert not graph.writes
    def check_intent():
        assert store.load(change.id).status == "applying"
    graph.after_write = check_intent
    result = mod.apply_comment(client, store, change.id, change.fingerprint)
    assert result.status == "verified"
    assert store.load(change.id).remote_id == "900"
    assert result.text == "Exact supplied text"
    assert len(graph.writes) == 1
    mod.apply_comment(client, store, change.id, change.fingerprint)
    assert len(graph.writes) == 1


@pytest.mark.parametrize("failure", ["timeout", "lost-id"])
def test_uncertain_create_never_reposts_or_adopts_identical_candidate(tmp_path, failure):
    mod, _, graph, client, store = service(tmp_path)
    change = mod.prepare_comment(client, store, media_id="789", text="Exact supplied text")
    graph.failure = failure
    result = mod.apply_comment(client, store, change.id, change.fingerprint)
    assert result.status == "uncertain"
    result = mod.reconcile_comment(client, store, change.id)
    assert result.status == "uncertain"
    assert result.remote_id is None
    assert result.candidates == ["900"]
    mod.apply_comment(client, store, change.id, change.fingerprint)
    assert len(graph.writes) == 1
    assert "SECRET" not in store.path_for(change.id).read_text()


def test_permission_rejection_retains_proposal_without_secret_or_replay(tmp_path):
    mod, _, graph, client, store = service(tmp_path)
    change = mod.prepare_comment(client, store, media_id="789", text="Exact supplied text")
    graph.failure = "permission"
    assert mod.apply_comment(client, store, change.id, change.fingerprint).status == "rejected"
    mod.apply_comment(client, store, change.id, change.fingerprint)
    assert len(graph.writes) == 1
    assert "SECRET" not in store.path_for(change.id).read_text()


@pytest.mark.parametrize("drift", ["identity", "owner", "brand", "account"])
def test_prewrite_identity_and_brand_binding(tmp_path, drift):
    mod, brand, graph, client, store = service(tmp_path)
    change = mod.prepare_comment(client, store, media_id="789", text="Exact supplied text")
    if drift == "identity": graph.identity = "999"
    if drift == "owner": graph.owner = "999"
    if drift == "brand": brand.nombre = "Other"
    if drift == "account": brand.cuentas["facebook"]["page_id"] = "999"
    with pytest.raises(mod.CommentError):
        mod.apply_comment(client, store, change.id, change.fingerprint)
    assert not graph.writes


def test_cursor_pagination_ignores_credential_bearing_next_url(tmp_path):
    mod, _, graph, client, store = service(tmp_path)
    def pages(request):
        if request.url.params.get("after") == "safe_cursor":
            return {"data": [graph.row("802", "two")]}
        return {"data": [graph.row("801", "one")], "paging": {
            "cursors": {"after": "safe_cursor"}, "next": "https://evil.invalid/?access_token=SECRET"}}
    graph.pages = pages
    result = client.list_comments("789")
    assert [r["id"] for r in result["data"]] == ["801", "802"]
    assert result["complete"] is True


@pytest.mark.parametrize("platform,field", [("facebook", "is_hidden"), ("instagram", "hidden")])
def test_comment_inventory_can_request_provider_moderation_state(tmp_path, platform, field):
    mod, _, graph, client, _ = service(tmp_path, platform)
    seen = []
    def pages(request):
        seen.append(request.url.params.get("fields"))
        return {"data": [graph.row("801", "one")]}
    graph.pages = pages
    result = client.list_comments("789", include_moderation=True)
    assert result["complete"] is True
    assert field in seen[-1]


def test_partial_pagination_is_not_absence(tmp_path):
    mod, _, graph, client, store = service(tmp_path)
    graph.pages = lambda request: {"data": [], "paging": {"next": "https://evil.invalid/"}}
    result = client.list_comments("789")
    assert result["complete"] is False
    with pytest.raises(mod.CommentError):
        mod.prepare_comment(client, store, media_id="789", action="reply", parent_id="800", text="Reply")


@pytest.mark.parametrize("action", ["edit", "delete"])
def test_facebook_own_comment_mutations_bind_before_state(tmp_path, action):
    mod, _, graph, client, store = service(tmp_path)
    graph.comments["800"] = graph.row("800", "old")
    change = mod.prepare_comment(client, store, media_id="789", action=action,
                                 comment_id="800", text="new" if action == "edit" else None)
    graph.comments["800"]["message"] = "concurrent change"
    with pytest.raises(mod.CommentError):
        mod.apply_comment(client, store, change.id, change.fingerprint)
    assert not graph.writes


def test_instagram_edit_explicitly_unavailable_and_foreign_delete_denied(tmp_path):
    mod, _, graph, client, store = service(tmp_path, "instagram")
    with pytest.raises(mod.CommentError, match='Instagram.*editing'):
        mod.prepare_comment(client, store, media_id="789", action="edit", comment_id="800", text="new")
    graph.comments["800"] = graph.row("800", "old", author="999")
    with pytest.raises(mod.CommentError):
        mod.prepare_comment(client, store, media_id="789", action="delete", comment_id="800")
    assert not graph.writes


def test_failed_intent_save_blocks_remote_write(tmp_path, monkeypatch):
    mod, _, graph, client, store = service(tmp_path)
    change = mod.prepare_comment(client, store, media_id="789", text="Exact supplied text")
    def fail(change): raise mod.CommentError("disk unavailable")
    monkeypatch.setattr(store, "save", fail)
    with pytest.raises(mod.CommentError):
        mod.apply_comment(client, store, change.id, change.fingerprint)
    assert not graph.writes


def test_crash_after_remote_id_saved_can_verify_without_post(tmp_path, monkeypatch):
    mod, _, graph, client, store = service(tmp_path)
    change = mod.prepare_comment(client, store, media_id="789", text="Exact supplied text")
    original = client.verify_comment
    monkeypatch.setattr(client, "verify_comment", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        mod.apply_comment(client, store, change.id, change.fingerprint)
    assert store.load(change.id).remote_id == "900"
    monkeypatch.setattr(client, "verify_comment", original)
    assert mod.reconcile_comment(client, store, change.id).status == "verified"
    assert len(graph.writes) == 1


@pytest.mark.parametrize("platform", ["facebook", "instagram"])
def test_reply_uses_verified_parent_edge(tmp_path, platform):
    mod, _, graph, client, store = service(tmp_path, platform)
    graph.comments["800"] = graph.row("800", "Question", author="999")
    change = mod.prepare_comment(client, store, media_id="789", action="reply", parent_id="800", text="Exact supplied text")
    result = mod.apply_comment(client, store, change.id, change.fingerprint)
    assert result.status == "verified"
    assert graph.writes[0][1] == ("800/replies" if platform == "instagram" else "800/comments")


@pytest.mark.parametrize("platform,action", [("facebook", "edit"), ("facebook", "delete"), ("instagram", "delete")])
def test_own_edit_or_delete_is_explicit_and_not_replayed(tmp_path, platform, action):
    mod, _, graph, client, store = service(tmp_path, platform)
    graph.comments["800"] = graph.row("800", "old")
    change = mod.prepare_comment(client, store, media_id="789", action=action, comment_id="800",
                                 text="new" if action == "edit" else None)
    result = mod.apply_comment(client, store, change.id, change.fingerprint)
    assert result.status == ("verified" if action == "edit" else "applied_unverified")
    mod.apply_comment(client, store, change.id, change.fingerprint)
    assert len(graph.writes) == 1


def test_apply_revalidates_unavailable_instagram_edit_even_for_handwritten_change(tmp_path):
    mod, _, graph, client, store = service(tmp_path, "instagram")
    change = mod.prepare_comment(client, store, media_id="789", text="Exact supplied text")
    graph.comments["800"] = graph.row("800", "old")
    change.action = "edit"
    change.comment_id = "800"
    change.before["comment"] = client.find_comment("789", "800", own=True)
    change.fingerprint = mod.fingerprint(change)
    store.save(change)
    with pytest.raises(mod.CommentError, match='Instagram.*editing'):
        mod.apply_comment(client, store, change.id, change.fingerprint)
    assert not graph.writes


def test_missing_author_id_cannot_verify_own_instagram_comment(tmp_path):
    mod, _, graph, client, store = service(tmp_path, "instagram")
    graph.comments["800"] = {"id": "800", "text": "old", "from": {"username": "Example"}}
    with pytest.raises(mod.CommentError, match='own author'):
        mod.prepare_comment(client, store, media_id="789", action="delete", comment_id="800")


def test_response_id_save_failure_keeps_applying_and_never_posts_again(tmp_path, monkeypatch):
    mod, _, graph, client, store = service(tmp_path)
    change = mod.prepare_comment(client, store, media_id="789", text="Exact supplied text")
    original = store.save
    def fail_id(change):
        if change.remote_id:
            raise mod.CommentError("disk unavailable")
        original(change)
    monkeypatch.setattr(store, "save", fail_id)
    with pytest.raises(mod.CommentError):
        mod.apply_comment(client, store, change.id, change.fingerprint)
    assert store.load(change.id).status == "applying"
    assert store.load(change.id).remote_id is None
    monkeypatch.setattr(store, "save", original)
    assert mod.apply_comment(client, store, change.id, change.fingerprint).status == "uncertain"
    assert len(graph.writes) == 1


def test_interprocess_lock_blocks_second_writer(tmp_path):
    mod, _, graph, client, store = service(tmp_path)
    change = mod.prepare_comment(client, store, media_id="789", text="Exact supplied text")
    from socialctl.management.changes import ChangeLocked
    with store.apply_lock(change.id):
        with pytest.raises(ChangeLocked):
            mod.apply_comment(client, mod.CommentStore(client.brand.raiz), change.id, change.fingerprint)
    assert not graph.writes


@pytest.mark.parametrize("action", ["edit", "delete"])
def test_facebook_own_reply_can_be_mutated_only_under_verified_parent(tmp_path, action):
    mod, _, graph, client, store = service(tmp_path)
    graph.comments["800"] = graph.row("800", "Question", author="999")
    graph.comments["801"] = graph.row("801", "Our reply")
    change = mod.prepare_comment(client, store, media_id="789", action=action,
        parent_id="800", comment_id="801", text="Updated reply" if action == "edit" else None)
    result = mod.apply_comment(client, store, change.id, change.fingerprint)
    assert result.status == ("verified" if action == "edit" else "applied_unverified")
    assert graph.writes[0][1] == "801"


def test_write_uses_same_token_as_authenticated_identity_even_if_credentials_rotate(tmp_path):
    mod, brand, graph, client, store = service(tmp_path)
    headers = []
    def transport(request):
        headers.append(request.headers["Authorization"])
        result = graph(request)
        if request.url.path.endswith("/me"):
            brand.guardar_secreto(Platform.FACEBOOK, {"access_token": "DIFFERENT_ACCOUNT_TOKEN"})
        return result
    client.client = httpx.Client(transport=httpx.MockTransport(transport))
    change = mod.prepare_comment(client, store, media_id="789", text="Exact supplied text")
    assert mod.apply_comment(client, store, change.id, change.fingerprint).status == "verified"
    assert set(headers) == {"Bearer SECRET"}
