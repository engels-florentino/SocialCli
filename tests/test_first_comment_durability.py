import importlib
import json
import subprocess

import httpx
import pytest

from socialctl.adapters.base import ADAPTADORES, Adapter
from socialctl.models import CampaignType, Platform, PlatformPost, Post, PostResult, PostStatus
from socialctl.publisher import publicar
from tests.test_meta_comments import service


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    def blocked(*args, **kwargs): raise AssertionError("external process/HTTP prohibited")
    monkeypatch.setattr(subprocess, "Popen", blocked)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)


def setup_publication(tmp_path, monkeypatch, platform="facebook"):
    _, brand, graph, client, _ = service(tmp_path, platform)
    platform = Platform(platform)
    post = Post(slug="clip", brand="Example", campaign=CampaignType.POST_IMAGEN,
                platforms={platform: PlatformPost(platform=platform, body="Body",
                   first_comment="Exact supplied text", media=[])})
    uploads = []
    class Upload(Adapter):
        platform = Platform.FACEBOOK
        def publish(self, pp, b, http):
            uploads.append(pp)
            return PostResult(platform=platform, status=PostStatus.PUBLICADO, platform_id="789")
    monkeypatch.setitem(ADAPTADORES, platform, Upload)
    factory = httpx.Client
    monkeypatch.setattr("socialctl.publisher.httpx.Client", lambda **kwargs: factory(transport=httpx.MockTransport(graph)))
    return brand, graph, client, post, uploads


def test_media_is_durable_before_comment_and_comment_failure_keeps_published(tmp_path, monkeypatch):
    brand, graph, client, post, uploads = setup_publication(tmp_path, monkeypatch)
    observed = []
    def check_media():
        files = list((brand.raiz / ".socialctl" / "publications").glob("*.json"))
        data = json.loads(files[0].read_text())
        observed.append(data["media"]["platform_id"])
    graph.after_write = check_media
    graph.failure = "lost-id"
    result = publicar(post, brand)[0]
    assert observed == ["789"]
    assert result.status is PostStatus.PUBLICADO
    assert result.platform_id == "789"
    assert result.first_comment_status == "uncertain"
    assert result.publication_id
    assert len(uploads) == 1


def test_critical_media_callback_failure_blocks_comment_and_keeps_id(tmp_path, monkeypatch):
    brand, graph, client, post, uploads = setup_publication(tmp_path, monkeypatch)
    def fail(result): raise OSError("SECRET")
    result = publicar(post, brand, on_media_result=fail)[0]
    assert result.status is PostStatus.PUBLICADO
    assert result.platform_id == "789"
    assert result.first_comment_status == "pending"
    assert not graph.writes
    assert "SECRET" not in str(result)


def test_media_save_failure_blocks_comment_without_making_upload_retryable(tmp_path, monkeypatch):
    brand, graph, client, post, uploads = setup_publication(tmp_path, monkeypatch)
    mod = importlib.import_module("socialctl.publication_steps")
    original = mod.PublicationStore.write_json
    def fail_media(self, identifier, data):
        if data.get("media"):
            raise mod.CommentError("disk unavailable")
        return original(self, identifier, data)
    monkeypatch.setattr(mod.PublicationStore, "write_json", fail_media)
    result = publicar(post, brand)[0]
    assert result.status is PostStatus.PUBLICADO
    assert result.platform_id == "789"
    assert not graph.writes
    data = mod.PublicationStore(brand.raiz).load(result.publication_id)
    assert data["state"] == "uploading"


def test_upload_intent_failure_blocks_upload(tmp_path, monkeypatch):
    brand, graph, client, post, uploads = setup_publication(tmp_path, monkeypatch)
    mod = importlib.import_module("socialctl.publication_steps")
    def fail(*args): raise mod.CommentError("disk unavailable")
    monkeypatch.setattr(mod.PublicationStore, "write_json", fail)
    result = publicar(post, brand)[0]
    assert result.status is PostStatus.ERROR
    assert not uploads
    assert not graph.writes


def test_retry_first_resumes_only_comment_with_original_approval(tmp_path, monkeypatch):
    brand, graph, client, post, uploads = setup_publication(tmp_path, monkeypatch)
    def fail(result): raise OSError("disk")
    result = publicar(post, brand, on_media_result=fail)[0]
    mod = importlib.import_module("socialctl.publication_steps")
    # The client was closed by publisher; use a fresh local transport.
    with httpx.Client(transport=httpx.MockTransport(graph)) as http:
        resumed = mod.retry_first_comment(brand, http, result.publication_id)
    assert resumed.platform_id == "789"
    assert resumed.first_comment_status == "verified"
    assert len(uploads) == 1
    assert len(graph.writes) == 1


def test_fresh_explicit_publications_have_distinct_occurrences(tmp_path, monkeypatch):
    brand, graph, client, post, uploads = setup_publication(tmp_path, monkeypatch)
    # No comments needed to prove explicit new publishes preserve repeat semantics.
    post.platforms[Platform.FACEBOOK].first_comment = None
    first = publicar(post, brand)[0]
    second = publicar(post, brand)[0]
    assert first.publication_id != second.publication_id
    assert len(uploads) == 2


def test_legacy_first_comment_cannot_be_replayed(tmp_path, monkeypatch):
    brand, graph, client, post, uploads = setup_publication(tmp_path, monkeypatch)
    mod = importlib.import_module("socialctl.publication_steps")
    with pytest.raises(mod.CommentError):
        mod.retry_first_comment(brand, client.client, "00000000-0000-0000-0000-000000000000")
    assert not uploads and not graph.writes


def test_explicit_retry_of_rejected_first_comment_does_not_upload(tmp_path, monkeypatch):
    brand, graph, client, post, uploads = setup_publication(tmp_path, monkeypatch)
    graph.failure = "permission"
    first = publicar(post, brand)[0]
    assert first.first_comment_status == "rejected"
    graph.failure = None
    mod = importlib.import_module("socialctl.publication_steps")
    result = mod.retry_first_comment(brand, client.client, first.publication_id)
    assert result.first_comment_status == "verified"
    assert result.first_comment_change_id != first.first_comment_change_id
    assert not any("rejected" in warning for warning in result.warnings)
    assert len(uploads) == 1
    assert len(graph.writes) == 2


def test_stable_scheduler_occurrence_never_uploads_twice(tmp_path, monkeypatch):
    brand, graph, client, post, uploads = setup_publication(tmp_path, monkeypatch)
    occurrence = "00000000-0000-0000-0000-000000000001"
    graph.failure = "lost-id"
    first = publicar(post, brand, occurrence_ids={Platform.FACEBOOK: occurrence})[0]
    second = publicar(post, brand, occurrence_ids={Platform.FACEBOOK: occurrence})[0]
    assert first.platform_id == second.platform_id == "789"
    assert len(uploads) == 1
    assert len(graph.writes) == 1


def test_adapter_exception_secret_does_not_reach_any_durable_result(tmp_path, monkeypatch):
    brand, graph, client, post, uploads = setup_publication(tmp_path, monkeypatch)
    class Broken(Adapter):
        platform = Platform.FACEBOOK
        def publish(self, *args): raise RuntimeError("access_token=SECRET")
    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, Broken)
    result = publicar(post, brand)[0]
    assert result.status is PostStatus.ERROR
    assert result.riesgo_duplicado is True
    assert "uncertain" in result.error
    assert "SECRET" not in str(result)
    for path in (brand.raiz / ".socialctl").rglob("*.json"):
        assert "SECRET" not in path.read_text()


def test_stable_occurrence_repeats_required_persistence_callback_before_comment(tmp_path, monkeypatch):
    brand, graph, client, post, uploads = setup_publication(tmp_path, monkeypatch)
    occurrence = "00000000-0000-0000-0000-000000000002"
    def fail(result): raise OSError("disk")
    first = publicar(post, brand, occurrence_ids={Platform.FACEBOOK: occurrence}, on_media_result=fail)[0]
    assert not graph.writes
    second = publicar(post, brand, occurrence_ids={Platform.FACEBOOK: occurrence}, on_media_result=fail)[0]
    assert second.status is PostStatus.PUBLICADO
    assert not graph.writes
    assert len(uploads) == 1


def test_facebook_adapter_returns_media_without_comment_mutation(tmp_path):
    from socialctl.adapters.facebook import FacebookAdapter
    _, brand, graph, client, _ = service(tmp_path)
    requests = []
    def transport(request):
        requests.append(request.url.path)
        if request.url.path == "/v26.0/123/feed":
            return httpx.Response(200, json={"id": "789"})
        raise AssertionError("Adapter performed a second remote mutation")
    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        result = FacebookAdapter().publish(PlatformPost(platform=Platform.FACEBOOK,
            body="Body", first_comment="Exact supplied text", media=[]), brand, http)
    assert result.platform_id == "789"
    assert result.status is PostStatus.PUBLICADO
    assert requests == ["/v26.0/123/feed"]


@pytest.mark.parametrize("failure", ["identity", "permission", "persistence", "brand_account"])
def test_resumed_comment_failure_preserves_confirmed_media_and_occurrence(tmp_path, monkeypatch, failure):
    from socialctl.publication_steps import PublicationStore, CommentError
    brand, graph, client, post, uploads = setup_publication(tmp_path, monkeypatch)
    occurrence = "00000000-0000-0000-0000-000000000010"
    def stop_comment(result): raise OSError("disk")
    first = publicar(post, brand, occurrence_ids={Platform.FACEBOOK: occurrence}, on_media_result=stop_comment)[0]
    assert first.status is PostStatus.PUBLICADO
    if failure == "identity": graph.identity = "999"
    if failure == "permission": graph.failure = "read_permission"
    if failure == "persistence":
        def fail_save(*args): raise CommentError("SECRET")
        monkeypatch.setattr(PublicationStore, "write_json", fail_save)
    if failure == "brand_account": brand.cuentas["facebook"]["page_id"] = "999"
    resumed = publicar(post, brand, occurrence_ids={Platform.FACEBOOK: occurrence})[0]
    assert resumed.status is PostStatus.PUBLICADO
    assert resumed.platform_id == "789"
    assert resumed.publication_id == occurrence
    assert resumed.first_comment_status == "manual_review"
    assert "SECRET" not in str(resumed)
    assert len(uploads) == 1
    assert not graph.writes


@pytest.mark.parametrize("uncertain", [False, True])
def test_resume_retains_definite_or_uncertain_upload_failure(tmp_path, monkeypatch, uncertain):
    brand, graph, client, post, uploads = setup_publication(tmp_path, monkeypatch)
    class Failed(Adapter):
        platform = Platform.FACEBOOK
        def publish(self, *args):
            uploads.append("failure")
            return PostResult(platform=Platform.FACEBOOK, status=PostStatus.ERROR,
                              error="Upload failure", riesgo_duplicado=uncertain)
    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, Failed)
    occurrence = "00000000-0000-0000-0000-000000000011"
    first = publicar(post, brand, occurrence_ids={Platform.FACEBOOK: occurrence})[0]
    resumed = publicar(post, brand, occurrence_ids={Platform.FACEBOOK: occurrence})[0]
    assert resumed.status is PostStatus.ERROR
    assert resumed.riesgo_duplicado is uncertain
    assert resumed.publication_id == first.publication_id == occurrence
    assert resumed.error == "Upload failure"
    assert len(uploads) == 1


def _retry_inputs(brand, post, *, result=True):
    import yaml
    (brand.raiz / "accounts.yml").write_text(yaml.safe_dump(brand.cuentas))
    folder = brand.dir_posts / post.slug
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "post.yml").write_text(yaml.safe_dump({"slug": post.slug,
        "campaign": post.campaign.value, "platforms": {"facebook": {"body": "Body"}}}))
    if result:
        (folder / "resultado.json").write_text(json.dumps({"resultados": [{
            "platform": "facebook", "status": "error", "error": "old safe failure",
            "riesgo_duplicado": False}]}))


@pytest.mark.parametrize("state", ["published", "uploading", "uncertain", "missing_result", "malformed_journal"])
def test_actual_retry_checks_durable_history_before_new_upload(tmp_path, monkeypatch, state):
    from typer.testing import CliRunner
    from socialctl.cli import app
    from socialctl.publication_steps import PublicationStore
    brand, graph, client, post, uploads = setup_publication(tmp_path, monkeypatch)
    post.platforms[Platform.FACEBOOK].first_comment = None
    first = publicar(post, brand)[0]
    store = PublicationStore(brand.raiz)
    data = store.load(first.publication_id)
    if state == "uploading":
        data["media"], data["state"] = None, "uploading"
        store.write_json(first.publication_id, data)
    if state == "uncertain":
        data["media"].update(status="error", platform_id=None, riesgo_duplicado=True)
        data["state"] = "upload_result"
        store.write_json(first.publication_id, data)
    if state == "malformed_journal":
        store.path_for(first.publication_id).write_text("{invalid-json")
    _retry_inputs(brand, post, result=state != "missing_result")
    result = CliRunner().invoke(app, ["retry", "clip", "--brand", "Example", "--root", str(tmp_path), "--yes"])
    assert result.exit_code != 0, result.output
    assert len(uploads) == 1
    assert 'journal' in result.output.lower()
    if state != "malformed_journal":
        assert first.publication_id in result.output


def test_retry_ignores_another_exact_slug_but_explicit_publish_can_repeat(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from socialctl.cli import app
    brand, graph, client, post, uploads = setup_publication(tmp_path, monkeypatch)
    post.platforms[Platform.FACEBOOK].first_comment = None
    post.slug = "other-clip"
    publicar(post, brand)
    post.slug = "clip"
    _retry_inputs(brand, post)
    runner = CliRunner()
    args = ["clip", "--brand", "Example", "--root", str(tmp_path), "--yes"]
    assert runner.invoke(app, ["retry", *args]).exit_code == 0
    assert len(uploads) == 2
    assert runner.invoke(app, ["publish", *args]).exit_code == 0
    assert len(uploads) == 3


def test_retry_guard_rechecks_after_preview_before_uploader(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    import socialctl.cli as cli
    brand, graph, client, post, uploads = setup_publication(tmp_path, monkeypatch)
    post.platforms[Platform.FACEBOOK].first_comment = None
    _retry_inputs(brand, post)
    render = cli.render_preview
    def race(*args, **kwargs):
        value = render(*args, **kwargs)
        publicar(post, brand)
        return value
    monkeypatch.setattr(cli, "render_preview", race)
    result = CliRunner().invoke(cli.app, ["retry", "clip", "--brand", "Example", "--root", str(tmp_path), "--yes"])
    assert result.exit_code != 0
    assert len(uploads) == 1


def test_actual_retry_fails_closed_if_publication_directory_is_unreadable(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from socialctl.cli import app
    import socialctl.publication_steps as steps
    brand, graph, client, post, uploads = setup_publication(tmp_path, monkeypatch)
    _retry_inputs(brand, post)
    path = steps.PublicationStore(brand.raiz).root
    scandir = steps.os.scandir
    def unreadable(directory):
        if directory == path:
            raise PermissionError("SECRET")
        return scandir(directory)
    monkeypatch.setattr(steps.os, "scandir", unreadable)
    result = CliRunner().invoke(app, ["retry", "clip", "--brand", "Example", "--root", str(tmp_path), "--yes"])
    assert result.exit_code != 0
    assert "unreadable" in result.output
    assert "SECRET" not in result.output
    assert not uploads


def test_actual_retry_does_not_read_another_brand_same_slug_journal(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from socialctl.cli import app
    from socialctl.brands import crear_brand
    brand, graph, client, post, uploads = setup_publication(tmp_path, monkeypatch)
    post.platforms[Platform.FACEBOOK].first_comment = None
    publicar(post, brand)
    other = crear_brand(tmp_path, "Other")
    other.cuentas = brand.cuentas.copy()
    post.brand = "Other"
    _retry_inputs(other, post)
    result = CliRunner().invoke(app, ["retry", "clip", "--brand", "Other", "--root", str(tmp_path), "--yes"])
    assert result.exit_code == 0, result.output
    assert len(uploads) == 2


def test_actual_retry_never_guesses_order_of_different_occurrences(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from socialctl.cli import app
    from socialctl.publisher import guardar_resultado
    brand, graph, client, post, uploads = setup_publication(tmp_path, monkeypatch)
    post.platforms[Platform.FACEBOOK].first_comment = None
    published = publicar(post, brand)[0]
    class Failed(Adapter):
        platform = Platform.FACEBOOK
        def publish(self, *args):
            uploads.append("failure")
            return PostResult(platform=self.platform, status=PostStatus.ERROR, error="Rejected")
    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, Failed)
    failed = publicar(post, brand)[0]
    _retry_inputs(brand, post)
    guardar_resultado(post, brand, [failed])
    result = CliRunner().invoke(app, ["retry", "clip", "--brand", "Example", "--root", str(tmp_path), "--yes"])
    assert result.exit_code != 0
    assert published.publication_id in result.output
    assert len(uploads) == 2


def test_stable_occurrence_cannot_be_reused_for_another_post(tmp_path, monkeypatch):
    brand, graph, client, post, uploads = setup_publication(tmp_path, monkeypatch)
    post.platforms[Platform.FACEBOOK].first_comment = None
    occurrence = "00000000-0000-0000-0000-000000000012"
    publicar(post, brand, occurrence_ids={Platform.FACEBOOK: occurrence})
    post.slug = "different-post"
    result = publicar(post, brand, occurrence_ids={Platform.FACEBOOK: occurrence})[0]
    assert result.status is PostStatus.ERROR
    assert result.riesgo_duplicado is True
    assert not result.platform_id
    assert len(uploads) == 1
