import json

import httpx
from typer.testing import CliRunner

from socialctl.cli import app
from socialctl.management import owned_cli
from socialctl.management.owned_changes import OwnedStore
from tests.test_youtube_management import _brand
from tests.test_youtube_owned import API


runner = CliRunner()


def test_channel_show_default_cli_parts_match_documented_client_defaults(tmp_path, monkeypatch):
    from socialctl.management.youtube_owned import PARTS
    brand, api = _brand(tmp_path), API()
    monkeypatch.setattr(owned_cli, "make_http_client", lambda: httpx.Client(transport=httpx.MockTransport(api.handler)))
    result = runner.invoke(app, ["content", "youtube-owned", "channel-show", "channel-a", "--brand", brand.nombre, "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["id"] == "channel-a"
    requests = [r for r in api.requests if r.url.path == "/youtube/v3/channels" and r.url.params.get("part") != "id"]
    assert len(requests) == 1
    assert set(requests[0].url.params["part"].split(",")) == set(PARTS["channels"].split(","))
    assert "invideoBranding" not in requests[0].url.params["part"]
    assert requests[0].url.params["id"] == "channel-a"
    assert not api.writes


def test_owned_cli_requires_brand_and_has_no_approval_bypass():
    result = runner.invoke(app, ["content", "youtube-owned", "playlists-list"])
    assert result.exit_code != 0 and "--brand" in result.output
    result = runner.invoke(app, ["content", "youtube-owned", "apply", "uuid", "--yes"])
    assert result.exit_code != 0


def test_owned_cli_full_preview_exact_apply_and_readonly_reconcile(tmp_path, monkeypatch):
    brand, api = _brand(tmp_path), API()
    monkeypatch.setattr(owned_cli, "make_http_client", lambda: httpx.Client(transport=httpx.MockTransport(api.handler)))
    args = ["--brand", brand.nombre, "--root", str(tmp_path)]
    file = tmp_path / "edit.json"
    file.write_text(json.dumps({"version": 1, "action": "playlist-update", "playlist_id": "playlist-1", "patch": {"snippet": {"title": "New title"}}}))
    result = runner.invoke(app, ["content", "youtube-owned", "prepare", "--file", str(file), "--dry-run", *args])
    assert result.exit_code == 0, result.output
    store = OwnedStore(brand.raiz)
    change = store.load(next(store.root.glob("*.json")).stem)
    assert owned_cli.render_owned_preview(change) in result.output
    assert not api.writes
    result = runner.invoke(app, ["content", "youtube-owned", "apply", change.id, *args], input="wrong\n")
    assert result.exit_code != 0 and not api.writes
    result = runner.invoke(app, ["content", "youtube-owned", "apply", change.id, *args], input=change.fingerprint + "\n")
    assert result.exit_code == 0, result.output
    assert owned_cli.render_owned_preview(change) in result.output
    for command in ("status", "reconcile"):
        result = runner.invoke(app, ["content", "youtube-owned", command, change.id, *args])
        assert result.exit_code == 0 and '"status": "verified"' in result.output
    assert len(api.writes) == 1


def test_owned_cli_named_reads_and_strict_unknown_schema(tmp_path, monkeypatch):
    brand, api = _brand(tmp_path), API()
    monkeypatch.setattr(owned_cli, "make_http_client", lambda: httpx.Client(transport=httpx.MockTransport(api.handler)))
    args = ["--brand", brand.nombre, "--root", str(tmp_path)]
    for command in (["playlists-list"], ["items-list", "playlist-1"], ["sections-list"], ["images-list", "playlist-1"],
                    ["channel-show", "channel-a", "--parts", "brandingSettings,status"], ["video-show", "video-1", "--parts", "fileDetails,processingDetails"]):
        result = runner.invoke(app, ["content", "youtube-owned", *command, *args])
        assert result.exit_code == 0, result.output
    file = tmp_path / "unsafe.json"
    file.write_text(json.dumps({"action": "playlist-update", "playlist_id": "playlist-1", "patch": {"snippet": {"title": "Title"}}, "endpoint": "https://evil.invalid"}))
    result = runner.invoke(app, ["content", "youtube-owned", "prepare", "--file", str(file), *args])
    assert result.exit_code != 0 and not api.writes
