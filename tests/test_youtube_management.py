from tests.terminal import plain
import json
from pathlib import Path

import httpx
import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from socialctl.brands import crear_brand
from socialctl.cli import app
from socialctl.authflow import construir_url_autorizacion
from socialctl.management.changes import (
    ApprovalMismatch,
    ChangeConflict,
    ChangeError,
    ChangeLocked,
    ChangeStore,
    EditFile,
    apply_youtube_change,
    load_edit_file,
    prepare_youtube_change,
)
from socialctl.management.youtube import (
    YouTubeManagementClient,
    YouTubeManagementError,
)
from socialctl.models import Platform


runner = CliRunner()
VIDEOS = "https://www.googleapis.com/youtube/v3/videos"
CHANNELS = "https://www.googleapis.com/youtube/v3/channels"


def _brand(tmp_path: Path, name: str = "Histopast", channel: str = "channel-a"):
    brand = crear_brand(tmp_path, name)
    (brand.raiz / "accounts.yml").write_text(
        yaml.safe_dump({"youtube": {"channel_id": channel}}), encoding="utf-8"
    )
    brand = __import__("socialctl.brands", fromlist=["cargar_brand"]).cargar_brand(
        tmp_path, name
    )
    brand.guardar_secreto(
        __import__("socialctl.models", fromlist=["Platform"]).Platform.YOUTUBE,
        {"access_token": f"token-{name}-long-secret"},
    )
    return brand


def _snippet(**updates):
    value = {
        "publishedAt": "2025-01-02T03:04:05Z",
        "channelId": "channel-a",
        "title": "Título anterior",
        "description": "Descripción anterior",
        "thumbnails": {"default": {"url": "https://img.invalid/x.jpg"}},
        "channelTitle": "Histopast",
        "tags": ["historia", "mapas"],
        "categoryId": "27",
        "liveBroadcastContent": "none",
        "defaultLanguage": "es",
        "localized": {"title": "Título anterior", "description": "Descripción anterior"},
        "defaultAudioLanguage": "es-ES",
    }
    value.update(updates)
    return value


class YouTubeAPI:
    def __init__(self, snippet=None, *, etag="etag-1", authenticated="channel-a"):
        self.snippet = snippet or _snippet()
        self.etag = etag
        self.authenticated = authenticated
        self.requests = []
        self.on_put = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/youtube/v3/channels":
            assert request.url.params["mine"] == "true"
            return httpx.Response(200, json={"items": [{"id": self.authenticated}]})
        if request.url.path == "/youtube/v3/videos" and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"id": request.url.params["id"], "etag": self.etag, "snippet": self.snippet}
                    ]
                },
            )
        if request.url.path == "/youtube/v3/videos" and request.method == "PUT":
            if self.on_put:
                return self.on_put(request)
            body = json.loads(request.content)
            self.snippet = {**self.snippet, **body["snippet"]}
            self.etag = "etag-2"
            return httpx.Response(
                200,
                json={"id": body["id"], "etag": self.etag, "snippet": self.snippet},
            )
        return httpx.Response(599, text="unexpected request")

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def _spec(tmp_path: Path, patch=None):
    path = tmp_path / "change.yml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "platform": "youtube",
                "video_id": "video-1",
                "patch": patch or {"title": "Título nuevo"},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return load_edit_file(path)


def test_inspect_verifies_configured_authenticated_and_owner_channels(tmp_path):
    brand = _brand(tmp_path)
    api = YouTubeAPI()

    result = YouTubeManagementClient(brand, api.client()).inspect("video-1")

    assert result.video_id == "video-1"
    assert result.channel_id == "channel-a"
    assert result.etag == "etag-1"
    assert result.snippet["defaultAudioLanguage"] == "es-ES"
    assert [r.url.path for r in api.requests] == [
        "/youtube/v3/channels",
        "/youtube/v3/videos",
    ]


def test_inspect_rejects_video_from_another_brand_channel(tmp_path):
    brand_a = _brand(tmp_path, "MarcaA", "channel-a")
    brand_b = _brand(tmp_path, "MarcaB", "channel-b")
    api_a = YouTubeAPI(_snippet(channelId="channel-b"), authenticated="channel-a")
    api_b = YouTubeAPI(_snippet(channelId="channel-a"), authenticated="channel-b")

    with pytest.raises(YouTubeManagementError, match="does not belong"):
        YouTubeManagementClient(brand_a, api_a.client()).inspect("video-1")
    with pytest.raises(YouTubeManagementError, match="does not belong"):
        YouTubeManagementClient(brand_b, api_b.client()).inspect("video-1")

    assert not (brand_a.raiz / ".socialctl" / "changes").exists()
    assert not (brand_b.raiz / ".socialctl" / "changes").exists()


def test_inspect_rejects_token_for_a_different_configured_channel(tmp_path):
    brand = _brand(tmp_path, channel="configured")
    api = YouTubeAPI(_snippet(channelId="configured"), authenticated="authenticated")

    with pytest.raises(YouTubeManagementError, match="authenticated channel"):
        YouTubeManagementClient(brand, api.client()).inspect("video-1")


@pytest.mark.parametrize(
    "raw, message",
    [
        ({"version": 1, "platform": "youtube", "video_id": "v", "patch": {"status": "public"}}, "field"),
        ({"version": 1, "platform": "youtube", "video_id": "v", "patch": {"title": None}}, "null"),
        ({"version": 1, "platform": "youtube", "video_id": "v", "patch": {}}, "empty"),
        ({"version": 1, "platform": "youtube", "video_id": "v", "patch": {"title": "x"}, "extra": 1}, "extra"),
    ],
)
def test_edit_file_rejects_unknown_null_empty_and_extra_values(tmp_path, raw, message):
    path = tmp_path / "change.yml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(ChangeError, match=message):
        load_edit_file(path)


@pytest.mark.parametrize(
    "patch",
    [
        {"defaultAudioLanguage": "fr"},
        {"unknownMutable": "value"},
        {"unknownMutable": None},
        {"title": None},
    ],
)
def test_edit_model_rejects_direct_unsupported_or_null_patch(patch):
    with pytest.raises(ValidationError):
        EditFile(version=1, platform="youtube", video_id="video-1", patch=patch)


@pytest.mark.parametrize(
    "key,value",
    [
        ("defaultAudioLanguage", "fr"),
        ("unknownMutable", "value"),
        ("unknownMutable", None),
    ],
)
def test_prepare_revalidates_a_mutated_edit_model_before_network(tmp_path, key, value):
    brand = _brand(tmp_path)
    api = YouTubeAPI()
    edit = _spec(tmp_path)
    edit.patch[key] = value

    with pytest.raises(ChangeError, match="patch"):
        prepare_youtube_change(
            YouTubeManagementClient(brand, api.client()), ChangeStore(brand.raiz), edit
        )

    assert api.requests == []


def test_prepare_preserves_all_writable_snippet_fields_and_saves_immutable_digest(tmp_path):
    brand = _brand(tmp_path)
    api = YouTubeAPI()
    store = ChangeStore(brand.raiz)

    change = prepare_youtube_change(
        YouTubeManagementClient(brand, api.client()),
        store,
        _spec(tmp_path, {"title": "Título nuevo", "defaultLanguage": "en"}),
    )

    assert change.version == 1
    assert change.platform == "youtube"
    assert change.target_account == "channel-a"
    assert change.observed_etag == "etag-1"
    assert change.before == {
        "title": "Título anterior",
        "description": "Descripción anterior",
        "tags": ["historia", "mapas"],
        "categoryId": "27",
        "defaultLanguage": "es",
        "defaultAudioLanguage": "es-ES",
    }
    assert change.after == {
        **change.before,
        "title": "Título nuevo",
        "defaultLanguage": "en",
    }
    assert len(change.fingerprint) == 64
    assert store.load(change.id).fingerprint == change.fingerprint


def test_prepare_fails_closed_for_unknown_snippet_field_or_missing_etag(tmp_path):
    brand = _brand(tmp_path)
    unknown = YouTubeAPI(_snippet(futureMutableThing="danger"))
    missing_etag = YouTubeAPI(etag="")

    with pytest.raises(YouTubeManagementError, match="unknown"):
        prepare_youtube_change(
            YouTubeManagementClient(brand, unknown.client()), ChangeStore(brand.raiz), _spec(tmp_path)
        )
    with pytest.raises(YouTubeManagementError, match="ETag"):
        prepare_youtube_change(
            YouTubeManagementClient(brand, missing_etag.client()), ChangeStore(brand.raiz), _spec(tmp_path)
        )


@pytest.mark.parametrize(
    "patch, message",
    [
        ({"title": ""}, "title"),
        ({"title": "x" * 101}, "100"),
        ({"description": "é" * 2501}, "5000 bytes"),
        ({"tags": ["x" * 501]}, "500"),
        ({"categoryId": ""}, "category"),
    ],
)
def test_prepare_validates_required_fields_and_youtube_limits(tmp_path, patch, message):
    brand = _brand(tmp_path)
    api = YouTubeAPI()
    with pytest.raises(ChangeError, match=message):
        prepare_youtube_change(
            YouTubeManagementClient(brand, api.client()), ChangeStore(brand.raiz), _spec(tmp_path, patch)
        )


def test_store_rejects_non_uuid_path_and_tampered_immutable_proposal(tmp_path):
    brand = _brand(tmp_path)
    api = YouTubeAPI()
    store = ChangeStore(brand.raiz)
    change = prepare_youtube_change(
        YouTubeManagementClient(brand, api.client()), store, _spec(tmp_path)
    )

    with pytest.raises(ChangeError, match="UUID"):
        store.load("../youtube.json")

    path = store.path_for(change.id)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["after"]["title"] = "Manipulado"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ChangeError, match="fingerprint"):
        store.load(change.id)


def test_atomic_save_syncs_each_new_directory_and_replaced_file_directory(tmp_path, monkeypatch):
    brand = _brand(tmp_path)
    api = YouTubeAPI()
    store = ChangeStore(brand.raiz)
    synced = []
    original = store._sync_directory

    def record(path):
        original(path)
        synced.append(path)

    monkeypatch.setattr(store, "_sync_directory", record)
    prepare_youtube_change(
        YouTubeManagementClient(brand, api.client()), store, _spec(tmp_path)
    )

    assert synced == [
        brand.raiz,
        brand.raiz / ".socialctl",
        brand.raiz / ".socialctl" / "changes",
    ]


def test_retry_resyncs_parent_after_new_directory_sync_failure_before_put(
    tmp_path, monkeypatch
):
    brand = _brand(tmp_path)
    api = YouTubeAPI()
    store = ChangeStore(brand.raiz)
    original = store._sync_directory
    failed_once = False
    successful = []

    def fail_first_brand_sync(path):
        nonlocal failed_once
        if path == brand.raiz and not failed_once:
            failed_once = True
            raise OSError("simulated parent directory fsync failure")
        original(path)
        successful.append(path)

    monkeypatch.setattr(store, "_sync_directory", fail_first_brand_sync)
    edit = _spec(tmp_path)

    with pytest.raises(ChangeError, match="save"):
        prepare_youtube_change(
            YouTubeManagementClient(brand, api.client()), store, edit
        )
    assert (brand.raiz / ".socialctl").is_dir()
    assert brand.raiz not in successful

    change = prepare_youtube_change(
        YouTubeManagementClient(brand, api.client()), store, edit
    )

    def observe_put(request):
        assert brand.raiz in successful
        body = json.loads(request.content)
        api.snippet = {**api.snippet, **body["snippet"]}
        api.etag = "etag-after-retry"
        return httpx.Response(200, json={"id": body["id"]})

    api.on_put = observe_put
    result = apply_youtube_change(
        YouTubeManagementClient(brand, api.client()),
        store,
        change.id,
        change.fingerprint,
    )

    assert result.status == "applied"


def test_apply_requires_exact_digest_at_service_level_before_network(tmp_path):
    brand = _brand(tmp_path)
    api = YouTubeAPI()
    store = ChangeStore(brand.raiz)
    change = prepare_youtube_change(
        YouTubeManagementClient(brand, api.client()), store, _spec(tmp_path)
    )
    api.requests.clear()

    with pytest.raises(ApprovalMismatch):
        apply_youtube_change(
            YouTubeManagementClient(brand, api.client()), store, change.id, "wrong"
        )

    assert api.requests == []
    assert store.load(change.id).status == "proposed"


def test_apply_journals_intent_before_put_uses_if_match_and_verifies_without_upload(tmp_path):
    brand = _brand(tmp_path)
    api = YouTubeAPI()
    store = ChangeStore(brand.raiz)
    change = prepare_youtube_change(
        YouTubeManagementClient(brand, api.client()), store, _spec(tmp_path)
    )
    api.requests.clear()

    def inspect_intent(request):
        persisted = store.load(change.id)
        assert persisted.status == "applying"
        assert persisted.journal[-1]["event"] == "write_intent"
        assert request.headers["if-match"] == "etag-1"
        body = json.loads(request.content)
        assert set(body) == {"id", "snippet"}
        assert "status" not in body
        assert body["snippet"]["defaultAudioLanguage"] == "es-ES"
        api.snippet = {**api.snippet, **body["snippet"]}
        api.etag = "etag-2"
        return httpx.Response(200, json={"id": body["id"], "etag": "etag-2", "snippet": api.snippet})

    api.on_put = inspect_intent
    result = apply_youtube_change(
        YouTubeManagementClient(brand, api.client()), store, change.id, change.fingerprint
    )

    assert result.status == "applied"
    assert result.verified is True
    assert [r.method for r in api.requests].count("PUT") == 1
    assert all("/upload/" not in str(r.url) for r in api.requests)
    put = next(r for r in api.requests if r.method == "PUT")
    assert put.url.params["part"] == "snippet"


def test_apply_completes_intent_directory_sync_before_put(tmp_path, monkeypatch):
    brand = _brand(tmp_path)
    api = YouTubeAPI()
    store = ChangeStore(brand.raiz)
    change = prepare_youtube_change(
        YouTubeManagementClient(brand, api.client()), store, _spec(tmp_path)
    )
    events = []
    original = store._sync_directory

    def record(path):
        original(path)
        events.append(("directory_synced", path))

    def observe_put(request):
        assert events[-1] == ("directory_synced", store.root)
        assert store.load(change.id).status == "applying"
        events.append(("put", request.url.path))
        body = json.loads(request.content)
        api.snippet = {**api.snippet, **body["snippet"]}
        api.etag = "etag-after"
        return httpx.Response(200, json={"id": body["id"]})

    monkeypatch.setattr(store, "_sync_directory", record)
    api.on_put = observe_put
    apply_youtube_change(
        YouTubeManagementClient(brand, api.client()), store, change.id, change.fingerprint
    )

    assert next(event for event in events if event[0] == "put")[0] == "put"


def test_intent_directory_sync_failure_prevents_put(tmp_path, monkeypatch):
    brand = _brand(tmp_path)
    api = YouTubeAPI()
    store = ChangeStore(brand.raiz)
    change = prepare_youtube_change(
        YouTubeManagementClient(brand, api.client()), store, _spec(tmp_path)
    )
    api.requests.clear()

    def fail(_path):
        raise OSError("simulated directory fsync failure")

    monkeypatch.setattr(store, "_sync_directory", fail)
    with pytest.raises(ChangeError, match="save"):
        apply_youtube_change(
            YouTubeManagementClient(brand, api.client()), store, change.id, change.fingerprint
        )

    assert [request for request in api.requests if request.method == "PUT"] == []


def test_apply_uses_fresh_etag_when_snippet_is_unchanged(tmp_path):
    brand = _brand(tmp_path)
    api = YouTubeAPI()
    store = ChangeStore(brand.raiz)
    change = prepare_youtube_change(
        YouTubeManagementClient(brand, api.client()), store, _spec(tmp_path)
    )
    assert change.observed_etag == "etag-1"
    api.etag = "etag-fresh"

    def assert_fresh(request):
        assert request.headers["if-match"] == "etag-fresh"
        body = json.loads(request.content)
        api.snippet = {**api.snippet, **body["snippet"]}
        api.etag = "etag-after"
        return httpx.Response(
            200, json={"id": body["id"], "etag": api.etag, "snippet": api.snippet}
        )

    api.on_put = assert_fresh
    result = apply_youtube_change(
        YouTubeManagementClient(brand, api.client()), store, change.id, change.fingerprint
    )

    assert result.status == "applied"


def test_apply_detects_remote_change_before_put_and_does_not_mutate(tmp_path):
    brand = _brand(tmp_path)
    api = YouTubeAPI()
    store = ChangeStore(brand.raiz)
    change = prepare_youtube_change(
        YouTubeManagementClient(brand, api.client()), store, _spec(tmp_path)
    )
    api.snippet = _snippet(description="cambio remoto")
    api.etag = "etag-remote"
    api.requests.clear()

    with pytest.raises(ChangeConflict):
        apply_youtube_change(
            YouTubeManagementClient(brand, api.client()), store, change.id, change.fingerprint
        )

    assert [r.method for r in api.requests].count("PUT") == 0
    assert store.load(change.id).status == "conflict"


def test_apply_412_is_conflict_and_never_retries(tmp_path):
    brand = _brand(tmp_path)
    api = YouTubeAPI()
    store = ChangeStore(brand.raiz)
    change = prepare_youtube_change(
        YouTubeManagementClient(brand, api.client()), store, _spec(tmp_path)
    )
    api.on_put = lambda request: httpx.Response(412, json={"error": {"message": "conditionNotMet"}})

    with pytest.raises(ChangeConflict):
        apply_youtube_change(
            YouTubeManagementClient(brand, api.client()), store, change.id, change.fingerprint
        )
    with pytest.raises(ChangeError, match="conflict"):
        apply_youtube_change(
            YouTubeManagementClient(brand, api.client()), store, change.id, change.fingerprint
        )

    assert [r.method for r in api.requests].count("PUT") == 1


def test_uncertain_apply_is_reconciled_read_only_and_never_repeats_put(tmp_path):
    brand = _brand(tmp_path)
    api = YouTubeAPI()
    store = ChangeStore(brand.raiz)
    change = prepare_youtube_change(
        YouTubeManagementClient(brand, api.client()), store, _spec(tmp_path)
    )

    def timeout_after_remote_acceptance(request):
        body = json.loads(request.content)
        api.snippet = {**api.snippet, **body["snippet"]}
        api.etag = "etag-2"
        raise httpx.ReadTimeout("token-Histopast-long-secret", request=request)

    api.on_put = timeout_after_remote_acceptance
    with pytest.raises(ChangeError, match="uncertain"):
        apply_youtube_change(
            YouTubeManagementClient(brand, api.client()), store, change.id, change.fingerprint
        )
    assert store.load(change.id).status == "uncertain"

    api.on_put = None
    reconciled = apply_youtube_change(
        YouTubeManagementClient(brand, api.client()), store, change.id, change.fingerprint
    )
    assert reconciled.status == "applied"
    assert reconciled.journal[-1]["event"] == "reconciled_applied"
    assert [r.method for r in api.requests].count("PUT") == 1


def test_uncertain_apply_still_before_becomes_manual_review_without_repeat(tmp_path):
    brand = _brand(tmp_path)
    api = YouTubeAPI()
    store = ChangeStore(brand.raiz)
    change = prepare_youtube_change(
        YouTubeManagementClient(brand, api.client()), store, _spec(tmp_path)
    )
    api.on_put = lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("timeout", request=request))
    with pytest.raises(ChangeError, match="uncertain"):
        apply_youtube_change(
            YouTubeManagementClient(brand, api.client()), store, change.id, change.fingerprint
        )
    api.on_put = None

    with pytest.raises(ChangeError, match="manual review"):
        apply_youtube_change(
            YouTubeManagementClient(brand, api.client()), store, change.id, change.fingerprint
        )

    assert store.load(change.id).status == "manual_review"
    assert [r.method for r in api.requests].count("PUT") == 1


def test_apply_lock_is_per_change_and_nonblocking(tmp_path):
    brand = _brand(tmp_path)
    store = ChangeStore(brand.raiz)
    change_id = "9e3a41cd-8799-4595-9b90-76d7b4e0338b"

    with store.apply_lock(change_id):
        with pytest.raises(ChangeLocked):
            with store.apply_lock(change_id):
                pass


def test_management_permission_error_is_actionable_sanitized_and_journaled(tmp_path):
    brand = _brand(tmp_path)
    api = YouTubeAPI()
    store = ChangeStore(brand.raiz)
    change = prepare_youtube_change(
        YouTubeManagementClient(brand, api.client()), store, _spec(tmp_path)
    )
    token = "token-Histopast-long-secret"
    api.on_put = lambda request: httpx.Response(
        403, json={"error": {"message": f"insufficientPermissions {token}"}}
    )

    with pytest.raises(ChangeError) as captured:
        apply_youtube_change(
            YouTubeManagementClient(brand, api.client()), store, change.id, change.fingerprint
        )

    message = str(captured.value)
    assert "youtube.force-ssl" in message
    assert token not in message
    persisted = store.load(change.id)
    assert persisted.status == "failed"
    assert token not in json.dumps(persisted.model_dump(mode="json"))


def test_youtube_auth_management_scope_is_explicit_opt_in():
    base = construir_url_autorizacion(
        Platform.YOUTUBE,
        "client",
        "http://localhost/callback",
        state="state",
        code_verifier="a" * 43,
    )
    management = construir_url_autorizacion(
        Platform.YOUTUBE,
        "client",
        "http://localhost/callback",
        state="state",
        code_verifier="a" * 43,
        youtube_management=True,
    )
    assert "youtube.force-ssl" not in str(httpx.URL(base).params["scope"])
    assert "youtube.force-ssl" in str(httpx.URL(management).params["scope"])


def test_top_level_and_nested_help_expose_management_commands():
    top = runner.invoke(app, ["--help"])
    auth = runner.invoke(app, ["auth", "--help"])
    content = runner.invoke(app, ["content", "--help"])
    changes = runner.invoke(app, ["changes", "--help"])

    assert top.exit_code == auth.exit_code == content.exit_code == changes.exit_code == 0
    assert "content" in top.stdout and "changes" in top.stdout
    assert "--management" in plain(auth.stdout)
    assert "show" in content.stdout and "edit" in content.stdout
    assert "apply" in changes.stdout and "status" in changes.stdout


def test_cli_show_edit_status_and_apply_requires_typed_digest_even_with_yes(tmp_path, monkeypatch):
    brand = _brand(tmp_path)
    api = YouTubeAPI()
    monkeypatch.setattr(
        "socialctl.management.cli.make_http_client",
        lambda: api.client(),
    )
    spec_path = tmp_path / "change.yml"
    spec_path.write_text(
        yaml.safe_dump(
            {"version": 1, "platform": "youtube", "video_id": "video-1", "patch": {"title": "Nuevo"}}
        ),
        encoding="utf-8",
    )

    shown = runner.invoke(
        app, ["content", "show", "video-1", "--brand", brand.nombre, "--root", str(tmp_path)]
    )
    assert shown.exit_code == 0
    assert "defaultAudioLanguage" in shown.stdout

    edited = runner.invoke(
        app,
        ["content", "edit", "--file", str(spec_path), "--brand", brand.nombre, "--root", str(tmp_path), "--dry-run"],
    )
    assert edited.exit_code == 0
    assert "Complete proposed snippet" in edited.stdout
    assert "Restoration" in edited.stdout
    change_id = next((brand.raiz / ".socialctl" / "changes").glob("*.json")).stem
    change = ChangeStore(brand.raiz).load(change_id)

    status = runner.invoke(
        app, ["changes", "status", change_id, "--brand", brand.nombre, "--root", str(tmp_path)]
    )
    assert status.exit_code == 0
    assert change.fingerprint in status.stdout

    refused = runner.invoke(
        app,
        ["changes", "apply", change_id, "--brand", brand.nombre, "--root", str(tmp_path), "--yes"],
        input="wrong\n",
    )
    assert refused.exit_code == 1
    assert "--yes does not replace" in refused.stdout
    assert [r.method for r in api.requests].count("PUT") == 0

    applied = runner.invoke(
        app,
        ["changes", "apply", change_id, "--brand", brand.nombre, "--root", str(tmp_path), "--yes"],
        input=f"{change.fingerprint}\n",
    )
    assert applied.exit_code == 0
    assert "applied and verified" in applied.stdout
    assert [r.method for r in api.requests].count("PUT") == 1
