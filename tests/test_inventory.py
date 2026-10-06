import json
import os
import stat
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from socialctl.brands import crear_brand, cargar_brand
from socialctl.models import Platform
from socialctl.identity import ReadError, IdentityClient, auth_status
from socialctl.inventory import InventoryStore, sync_content


def brand_at(tmp_path, name="Alpha", account="chan-a"):
    brand = crear_brand(tmp_path, name)
    brand.cuentas = {"youtube": {"channel_id": account},
                    "facebook": {"page_id": "123"}, "instagram": {"ig_user_id": "456"}}
    import yaml
    (brand.raiz / "accounts.yml").write_text(yaml.safe_dump(brand.cuentas))
    for platform in (Platform.YOUTUBE, Platform.FACEBOOK, Platform.INSTAGRAM):
        brand.guardar_secreto(platform, {"access_token": "secret-never-print"})
    return brand


class API:
    def __init__(self, *, account="chan-a", pages=None, failure=None):
        self.account = account
        self.pages = pages or {None: (["v1"], None)}
        self.failure = failure
        self.requests = []

    def handle(self, request):
        self.requests.append(request)
        assert request.method == "GET"
        path, params = request.url.path, request.url.params
        if path.endswith("/channels"):
            return httpx.Response(200, json={"items": [{"id": self.account,
                "contentDetails": {"relatedPlaylists": {"uploads": "UU-a"}}}]})
        if path.endswith("/playlistItems"):
            cursor = params.get("pageToken")
            if self.failure and cursor == "next":
                if self.failure == "timeout":
                    raise httpx.ReadTimeout("secret-never-print")
                return httpx.Response(self.failure, json={"error": {"message": "secret-never-print"}})
            ids, following = self.pages[cursor]
            return httpx.Response(200, json={"items": [{"contentDetails": {"videoId": i}} for i in ids],
                                           **({"nextPageToken": following} if following else {})})
        if path.endswith("/videos"):
            return httpx.Response(200, json={"items": [{"id": i,
                "snippet": {"channelId": self.account, "title": "Same title", "defaultLanguage": "es", "publishedAt": "2025-01-01T00:00:00Z"},
                "status": {"privacyStatus": "private", "uploadStatus": "processed"},
                "processingDetails": {"processingStatus": "succeeded"},
                "contentDetails": {"regionRestriction": {"blocked": ["US"]}}}
                for i in params["id"].split(",")]})
        raise AssertionError(path)

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handle))


def test_auth_status_identity_unknown_metadata_and_no_secret(tmp_path):
    brand = brand_at(tmp_path)
    result = auth_status(brand, Platform.YOUTUBE, API().client())
    assert result["version"] == 1
    assert result["identity"]["verified"] is True
    assert result["granted_scopes"]["state"] == "unknown"
    assert result["expiry"]["state"] == "unknown"
    assert result["quota_balance"] == {"state": "unknown", "value": None}
    assert "secret-never-print" not in json.dumps(result)


def test_auth_status_mismatch_exposes_safe_comparison(tmp_path):
    brand = brand_at(tmp_path)
    result = auth_status(brand, Platform.YOUTUBE, API(account="chan-b").client())
    assert result["failure_class"] == "identity_mismatch"
    assert result["identity"]["configured_account_id"] == "chan-a"
    assert result["identity"]["authenticated_account_id"] == "chan-b"
    assert result["identity"]["verified"] is False


def test_instagram_missing_link_never_falls_back_to_page_id(tmp_path):
    brand = brand_at(tmp_path)
    brand.cuentas["instagram"]["ig_user_id"] = "123"
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200,
            json={"id": "123", "instagram_business_account": ["unexpected"]}))) as client:
        result = auth_status(brand, Platform.INSTAGRAM, client)
    assert result["failure_class"] == "identity_mismatch"
    assert result["identity"]["verified"] is False


def test_known_scopes_and_expiry_are_not_assumed_from_request(tmp_path):
    brand = brand_at(tmp_path)
    brand.guardar_secreto(Platform.YOUTUBE, {"access_token": "secret-never-print",
        "scope": "youtube.readonly youtube.upload", "expira_en": 9999999999})
    result = auth_status(brand, Platform.YOUTUBE, API().client())
    assert result["granted_scopes"]["values"] == ["youtube.readonly", "youtube.upload"]
    assert result["expiry"]["state"] == "known"
    assert result["write_permissions_verified"] is False


def test_wrong_account_stops_before_content_and_persistence(tmp_path):
    brand, api = brand_at(tmp_path), API(account="other")
    with pytest.raises(ReadError, match="identity_mismatch"):
        sync_content(brand, Platform.YOUTUBE, api.client())
    assert len(api.requests) == 1
    assert not (brand.raiz / ".socialctl/inventory").exists()


def test_sync_stable_ids_idempotent_raw_protected_and_separate(tmp_path):
    brand = brand_at(tmp_path)
    api = API(pages={None: (["v1"], "next"), "next": (["v2"], None)})
    first = sync_content(brand, Platform.YOUTUBE, api.client())
    second = sync_content(brand, Platform.YOUTUBE, api.client())
    assert first["complete"] and second["complete"]
    store = InventoryStore(brand, Platform.YOUTUBE, "chan-a")
    rows = store.list_items()
    assert [r["remote_id"] for r in rows] == ["v1", "v2"]
    assert rows[0]["language"] == "es"
    assert rows[0]["observed_status"]["privacyStatus"] == "private"
    assert rows[0]["processing"]["processingStatus"] == "succeeded"
    assert rows[0]["restrictions"] == {"blocked": ["US"]}
    assert "raw" not in rows[0]
    assert store.load()["items"]["v1"]["raw"]["id"] == "v1"
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700
    assert list(brand.dir_posts.iterdir()) == []
    assert not (brand.raiz / "metricas").exists()


@pytest.mark.parametrize("failure,code", [(429, "rate_limited"), (403, "permission_denied"), ("timeout", "timeout")])
def test_partial_results_persist_and_resume_without_tombstones(tmp_path, failure, code):
    brand = brand_at(tmp_path)
    api = API(pages={None: (["v1"], "next"), "next": (["v2"], None)}, failure=failure)
    result = sync_content(brand, Platform.YOUTUBE, api.client())
    assert not result["complete"] and result["failure_class"] == code
    assert result["resume_available"]
    assert result["observed_items"] == 1
    api.failure = None
    api.requests.clear()
    result = sync_content(brand, Platform.YOUTUBE, api.client(), resume=True)
    assert result["complete"]
    assert next(r for r in api.requests if r.url.path.endswith("playlistItems")).url.params["pageToken"] == "next"
    rows = InventoryStore(brand, Platform.YOUTUBE, "chan-a").list_items()
    assert len(rows) == 2 and all(r["presence"] == "observed" for r in rows)


def test_repeated_cursor_and_bound_stop(tmp_path):
    brand = brand_at(tmp_path)
    api = API(pages={None: (["v1"], "next"), "next": (["v2"], "next")})
    assert sync_content(brand, Platform.YOUTUBE, api.client())["failure_class"] == "repeated_cursor"
    result = sync_content(brand, Platform.YOUTUBE, api.client(), max_pages=1)
    assert result["failure_class"] == "page_limit"


def test_complete_absence_is_not_deletion_and_account_changes_are_separate(tmp_path):
    brand = brand_at(tmp_path)
    sync_content(brand, Platform.YOUTUBE, API().client())
    sync_content(brand, Platform.YOUTUBE, API(pages={None: ([], None)}).client())
    row = InventoryStore(brand, Platform.YOUTUBE, "chan-a").list_items()[0]
    assert row["presence"] == "not_observed" and row["deletion_proven"] is False
    brand.cuentas["youtube"]["channel_id"] = "chan-b"
    sync_content(brand, Platform.YOUTUBE, API(account="chan-b").client())
    assert InventoryStore(brand, Platform.YOUTUBE, "chan-a").list_items()[0]["account_id"] == "chan-a"
    assert InventoryStore(brand, Platform.YOUTUBE, "chan-b").list_items()[0]["account_id"] == "chan-b"
    other = brand_at(tmp_path, "Beta")
    assert InventoryStore(other, Platform.YOUTUBE, "chan-a").list_items() == []


@pytest.mark.parametrize("platform", [Platform.FACEBOOK, Platform.INSTAGRAM])
def test_meta_owned_inventory_and_untrusted_paging_urls(tmp_path, platform):
    brand, requests = brand_at(tmp_path), []
    def handle(request):
        requests.append(request)
        assert request.method == "GET" and request.url.host == "graph.facebook.com"
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"id": "123", "instagram_business_account": {"id": "456"}})
        account = "123" if platform is Platform.FACEBOOK else "456"
        if request.url.params.get("after") == "cursor2":
            return httpx.Response(200, json={"data": []})
        owner = "from" if platform is Platform.FACEBOOK else "owner"
        return httpx.Response(200, json={"data": [{"id": "789", owner: {"id": account},
            "media_type": "VIDEO", "timestamp": "2025-01-01", "created_time": "2025-01-01"}],
            "paging": {"next": "https://evil.invalid/?access_token=secret", "cursors": {"after": "cursor2"}}})
    result = sync_content(brand, platform, httpx.Client(transport=httpx.MockTransport(handle)))
    assert result["complete"] and result["observed_items"] == 1
    assert len(requests) == 3


def test_cli_preserves_auth_and_show_and_json_errors(tmp_path, monkeypatch):
    from socialctl.cli import app
    from socialctl import inventory_cli
    brand_at(tmp_path)
    monkeypatch.setattr(inventory_cli, "make_http_client", lambda: API().client())
    args = ["--brand", "Alpha", "--root", str(tmp_path), "--json"]
    runner = CliRunner()
    result = runner.invoke(app, ["auth", "status", "--platform", "youtube", *args])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["identity"]["verified"]
    result = runner.invoke(app, ["content", "sync", "--platform", "youtube", *args])
    assert result.exit_code == 0, result.output
    for command in ("list", "export"):
        result = runner.invoke(app, ["content", command, "--platform", "youtube", *args])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["items"][0]["remote_id"] == "v1"
        assert "raw" not in json.loads(result.output)["items"][0]
    assert runner.invoke(app, ["auth", "--help"]).exit_code == 0
    assert runner.invoke(app, ["content", "show", "--help"]).exit_code == 0
    assert runner.invoke(app, ["content", "sync", "--platform", "youtube"]).exit_code != 0


def test_inventory_rejects_symlinks(tmp_path):
    brand = brand_at(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (brand.raiz / ".socialctl").symlink_to(outside, target_is_directory=True)
    with pytest.raises((ReadError, ValueError)):
        sync_content(brand, Platform.YOUTUBE, API().client())
    assert list(outside.iterdir()) == []


def test_doctor_remote_health_is_server_authority_and_sanitized(tmp_path, monkeypatch):
    from socialctl.cli import app
    from socialctl import inventory_cli
    brand = brand_at(tmp_path)
    path = brand.raiz / ".socialctl/remote-executor.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"host": "example.invalid", "user": "runner", "port": 22,
        "root": "/srv/data", "executable": "/srv/app/.venv/bin/socialctl"}))
    requests = []
    def capture(argv, incoming, **kwargs):
        requests.append(argv)
        assert kwargs["timeout"] == 30
        return json.dumps({"state": "finished", "stale": False,
            "backend": "sqlite", "access_token": "secret-never-print"}).encode()
    monkeypatch.setattr(inventory_cli, "capture_remote", capture)
    monkeypatch.setattr(inventory_cli, "make_http_client", lambda: API().client())
    result = CliRunner().invoke(app, ["doctor", "--brand", "Alpha", "--root", str(tmp_path), "--platform", "youtube", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["queue"]["authority"] == "remote" and data["queue"]["state"] == "finished"
    assert "secret-never-print" not in result.output
    assert len(requests) == 1 and "schedule-health" in requests[0][-1]


def test_expired_status_never_refreshes_or_opens_browser(tmp_path):
    brand, api = brand_at(tmp_path), API()
    brand.guardar_secreto(Platform.YOUTUBE, {"access_token": "secret-never-print", "expira_en": 1})
    result = auth_status(brand, Platform.YOUTUBE, api.client())
    assert result["failure_class"] == "credentials_expired" and result["expiry"]["expired"] is True
    assert api.requests == []


def test_missing_video_resource_does_not_prove_deleted(tmp_path):
    brand = brand_at(tmp_path)
    api = API()
    def handle(request):
        if request.url.path.endswith("/videos"):
            return httpx.Response(200, json={"items": []})
        return api.handle(request)
    result = sync_content(brand, Platform.YOUTUBE, httpx.Client(transport=httpx.MockTransport(handle)))
    assert result["complete"] and result["missing_resource_ids"] == ["v1"]
    assert result["deletion_proven"] is False


@pytest.mark.parametrize("platform", [Platform.FACEBOOK, Platform.INSTAGRAM])
def test_wrong_meta_owner_refuses_entire_page(tmp_path, platform):
    brand = brand_at(tmp_path)
    def handle(request):
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"id": "123", "instagram_business_account": {"id": "456"}})
        return httpx.Response(200, json={"data": [{"id": "789", "owner": {"id": "000"}, "from": {"id": "000"}}]})
    result = sync_content(brand, platform, httpx.Client(transport=httpx.MockTransport(handle)))
    assert not result["complete"] and result["failure_class"] == "content_owner_mismatch"
    assert result["stored_items"] == 0


def test_oauth_grant_metadata_records_only_response_scopes(tmp_path):
    from socialctl.authflow import canjear_codigo
    def handle(request):
        return httpx.Response(200, json={"access_token": "secret-never-print", "scope": "scope.a scope.b"})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = canjear_codigo(Platform.YOUTUBE, "code", {"client_id": "client", "client_secret": "secret"},
            "http://localhost/callback", client, code_verifier="verifier")
    assert result["granted_scopes"] == ["scope.a", "scope.b"]
    assert result["expira_en"] is None


def test_refresh_does_not_relabel_old_scope_as_current_grant(tmp_path):
    from socialctl.auth import obtener_token
    brand = brand_at(tmp_path)
    brand.guardar_secreto(Platform.YOUTUBE, {"access_token": "old", "refresh_token": "refresh", "expira_en": 1,
        "granted_scopes": ["old.scope"]})
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"access_token": "new", "expires_in": 3600}))) as client:
        obtener_token(brand, Platform.YOUTUBE, client)
    assert auth_status(brand, Platform.YOUTUBE, API().client())["granted_scopes"]["state"] == "unknown"


def test_malformed_second_page_is_durable_partial_result(tmp_path):
    brand, api = brand_at(tmp_path), API(pages={None: (["v1"], "next")})
    def handle(request):
        if request.url.params.get("pageToken") == "next":
            return httpx.Response(200, json={"items": [{"contentDetails": []}]})
        return api.handle(request)
    result = sync_content(brand, Platform.YOUTUBE, httpx.Client(transport=httpx.MockTransport(handle)))
    assert result["failure_class"] == "invalid_response" and result["observed_items"] == 1


def test_concurrent_sync_refuses_second_writer(tmp_path):
    brand = brand_at(tmp_path)
    store = InventoryStore(brand, Platform.YOUTUBE, "chan-a")
    with store.lock():
        with pytest.raises(ReadError, match="inventory_locked"):
            sync_content(brand, Platform.YOUTUBE, API().client())


def test_resume_requires_existing_checkpoint_and_new_sync_resets_seen_ids(tmp_path):
    brand = brand_at(tmp_path)
    with pytest.raises(ReadError, match="resume_unavailable"):
        sync_content(brand, Platform.YOUTUBE, API().client(), resume=True)
    sync_content(brand, Platform.YOUTUBE, API(pages={None: (["v1"], "next")}, failure=403).client())
    result = sync_content(brand, Platform.YOUTUBE, API(pages={None: (["v2"], None)}).client())
    assert result["complete"] and result["observed_items"] == 1
    rows = InventoryStore(brand, Platform.YOUTUBE, "chan-a").list_items()
    assert {r["remote_id"]: r["presence"] for r in rows} == {"v1": "not_observed", "v2": "observed"}


def test_doctor_does_not_report_cached_queue_as_remote_health(tmp_path, monkeypatch):
    from socialctl import inventory_cli
    from socialctl.cli import app
    brand = brand_at(tmp_path)
    path = brand.raiz / ".socialctl/remote-executor.json"
    path.parent.mkdir()
    path.write_text("{}")
    monkeypatch.setattr(inventory_cli, "make_http_client", lambda: API().client())
    result = CliRunner().invoke(app, ["doctor", "--brand", "Alpha", "--root", str(tmp_path), "--platform", "youtube", "--json"])
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["queue"]["authority"] == "remote" and data["queue"]["state"] == "unknown"
    assert data["queue"]["failure_class"] == "remote_health_unavailable"


def test_cli_cached_historical_account_and_capabilities_from_other_cwd(tmp_path, monkeypatch):
    from socialctl.cli import app
    brand = brand_at(tmp_path)
    sync_content(brand, Platform.YOUTUBE, API().client())
    import yaml
    brand.cuentas["youtube"]["channel_id"] = "chan-b"
    (brand.raiz / "accounts.yml").write_text(yaml.safe_dump(brand.cuentas))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    runner = CliRunner()
    args = ["--brand", "Alpha", "--root", str(tmp_path), "--platform", "youtube", "--json"]
    result = runner.invoke(app, ["content", "list", "--account", "chan-a", *args])
    assert result.exit_code == 0
    assert json.loads(result.output)["historical_account"] is True
    assert json.loads(result.output)["items"][0]["account_id"] == "chan-a"
    result = runner.invoke(app, ["capabilities", *args])
    assert result.exit_code == 0
    report = json.loads(result.output)
    discovery = [row for row in report["capabilities"]
                 if row["api"] in {"youtube", "youtubeAnalytics", "youtubereporting"}]
    ui_only = [row for row in report["capabilities"] if row["availability"] == "ui_only"]
    scheduling = [row for row in report["capabilities"] if row["id"] == "youtube.native-scheduling"]
    assert len(discovery) == 99
    assert len(ui_only) == 6
    assert len(scheduling) == 1
    assert report["classification"]["records"] == len(discovery) + len(ui_only) + len(scheduling)


@pytest.mark.parametrize("platform", [Platform.FACEBOOK, Platform.INSTAGRAM])
@pytest.mark.parametrize("container", ["paging", "cursors"])
@pytest.mark.parametrize("malformed", [[], False, "", None])
def test_malformed_meta_pagination_cannot_mark_prior_content_absent(tmp_path, platform, container, malformed):
    brand = brand_at(tmp_path)
    account = "123" if platform is Platform.FACEBOOK else "456"
    owner = "from" if platform is Platform.FACEBOOK else "owner"
    page = {"data": [{"id": "789", owner: {"id": account}}]}
    def handle(request):
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"id": "123", "instagram_business_account": {"id": "456"}})
        return httpx.Response(200, json=page)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert sync_content(brand, platform, client)["complete"]
        page = {"data": [], "paging": malformed if container == "paging" else {"cursors": malformed}}
        result = sync_content(brand, platform, client)
    assert result["failure_class"] == "invalid_response"
    assert not result["complete"] and result["resume_available"]
    row = InventoryStore(brand, platform, account).list_items()[0]
    assert row["presence"] == "observed" and row["deletion_proven"] is False


@pytest.mark.parametrize("paging", [{"next": False}, {"next": ""}, {"next": None},
                                    {"cursors": {"after": False}}, {"cursors": {"after": ""}},
                                    {"cursors": {"before": None}}])
def test_present_invalid_meta_pagination_values_are_not_end_of_list(tmp_path, paging):
    brand = brand_at(tmp_path)
    def handle(request):
        return httpx.Response(200, json={"id": "123"} if request.url.path.endswith("/me") else {"data": [], "paging": paging})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = sync_content(brand, Platform.FACEBOOK, client)
    assert not result["complete"] and result["failure_class"] in {"invalid_response", "invalid_cursor"}


@pytest.mark.parametrize("command", ["list", "export"])
def test_cached_output_uses_one_snapshot_during_concurrent_replacement(tmp_path, monkeypatch, command):
    import copy
    from socialctl.cli import app
    brand = brand_at(tmp_path)
    sync_content(brand, Platform.YOUTUBE, API().client())
    store = InventoryStore(brand, Platform.YOUTUBE, "chan-a")
    old = store.load()
    replacement = copy.deepcopy(old)
    replacement["sync"].update(id="new-sync", complete=False, source="new-source")
    row = replacement["items"].pop("v1")
    row.update(remote_id="v2", last_sync_id="new-sync")
    replacement["items"]["v2"] = row
    original_load = InventoryStore.load
    loads = []
    def load_then_replace(self):
        state = original_load(self)
        loads.append(state["sync"]["id"])
        if len(loads) == 1:
            # Atomic replacement occurs after opening the old snapshot and
            # before the CLI renders its metadata/items, as a real writer can.
            self.save(replacement)
        return state
    monkeypatch.setattr(InventoryStore, "load", load_then_replace)
    result = CliRunner().invoke(app, ["content", command, "--brand", "Alpha", "--root", str(tmp_path),
                                     "--platform", "youtube", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["sync"]["id"] == old["sync"]["id"] and data["sync"]["complete"] is True
    assert [row["remote_id"] for row in data["items"]] == ["v1"]
    assert "raw" not in data["items"][0]
    assert len(loads) == 1
