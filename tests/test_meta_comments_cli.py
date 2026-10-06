import json
import subprocess

import httpx
import pytest
from typer.testing import CliRunner

from socialctl.cli import app
from tests.test_meta_comments import service

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    def blocked(*args, **kwargs): raise AssertionError("external process/HTTP prohibited")
    monkeypatch.setattr(subprocess, "Popen", blocked)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)


def test_comments_cli_registered_and_requires_brand():
    help_result = runner.invoke(app, ["comments", "--help"])
    assert help_result.exit_code == 0, help_result.output
    result = runner.invoke(app, ["comments", "prepare", "789", "--platform", "facebook", "--text", "hello"])
    assert result.exit_code != 0
    assert "--brand" in result.output


def test_complete_preview_and_exact_cli_approval(tmp_path, monkeypatch):
    import socialctl.management.comments_cli as cli
    mod, brand, graph, client, store = service(tmp_path)
    (brand.raiz / "accounts.yml").write_text("facebook:\n  page_id: '123'\ninstagram:\n  ig_user_id: '456'\n")
    factory = httpx.Client
    monkeypatch.setattr(cli, "make_http_client", lambda: factory(transport=httpx.MockTransport(graph)))
    common = ["--brand", "Example", "--root", str(tmp_path)]
    result = runner.invoke(app, ["comments", "prepare", "789", "--platform", "facebook", "--text", "Exact supplied text", "--dry-run", *common])
    assert result.exit_code == 0, result.output
    change = store.load(next(store.root.glob("*.json")).stem)
    for value in ["Exact supplied text", "789", "123", "Example", change.id, change.fingerprint, "add"]:
        assert value in result.output
    assert not graph.writes
    cancelled = runner.invoke(app, ["comments", "apply", change.id, *common], input="wrong\n")
    assert cancelled.exit_code != 0
    assert not graph.writes
    applied = runner.invoke(app, ["comments", "apply", change.id, *common], input=change.fingerprint + "\n")
    assert applied.exit_code == 0, applied.output
    assert store.load(change.id).status == "verified"
    assert len(graph.writes) == 1
    status = runner.invoke(app, ["comments", "status", change.id, *common])
    assert status.exit_code == 0
    assert '"remote_id": "900"' in status.output


def test_scheduled_first_comment_has_queue_media_id_before_post(tmp_path, monkeypatch):
    import socialctl.cli as cli
    from socialctl.scheduler import ScheduleStore
    from tests.test_scheduler_cli import _make_social, _schedule, _FacebookOK
    from socialctl.adapters.base import ADAPTADORES
    from socialctl.models import Platform
    from tests.test_meta_comments import Graph
    brand, path = _make_social(tmp_path)
    (brand.raiz / "accounts.yml").write_text("facebook:\n  page_id: '123'\n")
    brand.guardar_secreto(Platform.FACEBOOK, {"access_token": "SECRET"})
    _schedule(tmp_path)
    graph = Graph()
    original_graph = graph.__call__
    def graph_request(request):
        if request.url.path.endswith("/789"):
            return httpx.Response(200, json={"id": "789", "from": {"id": "123"}})
        return original_graph(request)
    class Upload(_FacebookOK):
        def publish(self, *args):
            result = super().publish(*args)
            result.platform_id = "789"
            return result
    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, Upload)
    factory = httpx.Client
    monkeypatch.setattr("socialctl.publisher.httpx.Client", lambda **kw: factory(transport=httpx.MockTransport(graph_request)))
    observations = []
    def inspect_queue(): observations.append(ScheduleStore(brand.raiz).get("clip/facebook").platform_id)
    graph.after_write = inspect_queue
    graph.failure = "lost-id"
    result = runner.invoke(app, ["run-due", "--brand", "Histopast", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert observations == ["789"]
    entry = ScheduleStore(brand.raiz).get("clip/facebook")
    assert entry.status == "published"
    assert entry.platform_id == "789"
    saved = json.loads((brand.dir_posts / "clip" / "resultado.json").read_text())
    assert saved["resultados"][0]["first_comment_status"] == "uncertain"
    assert "uncertain" in result.output
    assert saved["resultados"][0]["publication_id"] in result.output
