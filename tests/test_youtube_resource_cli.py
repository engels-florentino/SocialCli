from tests.terminal import plain
import httpx
from typer.testing import CliRunner

from socialctl.cli import app
from socialctl.management import resource_cli
from socialctl.management.resource_changes import ResourceStore
from tests.test_youtube_resources import API, SRT, OLD, PNG
from tests.test_youtube_management import _brand


runner = CliRunner()


def setup(tmp_path, monkeypatch):
    brand = _brand(tmp_path)
    api = API()
    monkeypatch.setattr(resource_cli, "make_http_client", lambda: httpx.Client(transport=httpx.MockTransport(api.handler)))
    file = tmp_path / "supplied.srt"
    file.write_bytes(SRT)
    args = ["--brand", brand.nombre, "--root", str(tmp_path)]
    return brand, api, file, args


def test_resource_cli_requires_brand(tmp_path):
    result = runner.invoke(app, ["content", "youtube-assets", "captions-list", "video-1"])
    assert result.exit_code != 0 and "--brand" in plain(result.output)


def test_resource_cli_preview_exact_approval_status_and_reconcile(tmp_path, monkeypatch):
    brand, api, file, args = setup(tmp_path, monkeypatch)
    result = runner.invoke(app, ["content", "youtube-assets", "prepare", "video-1", "--action", "caption-insert",
        "--file", str(file), "--language", "en", "--name", "English", "--no-draft", "--dry-run", *args])
    assert result.exit_code == 0, result.output
    assert "DRY-RUN" in result.output and '"draft": false' in result.output
    assert '"target_account": "channel-a"' in result.output and "sha256" in result.output
    assert not api.writes
    store = ResourceStore(brand.raiz)
    change = store.load(next(store.root.glob("*.json")).stem)
    result = runner.invoke(app, ["content", "youtube-assets", "apply", change.id, *args], input="wrong\n")
    assert result.exit_code != 0 and not api.writes
    result = runner.invoke(app, ["content", "youtube-assets", "apply", change.id, *args], input=change.fingerprint + "\n")
    assert result.exit_code == 0, result.output
    assert "verified" in result.output and len(api.writes) == 1
    for cmd in ("status", "reconcile"):
        result = runner.invoke(app, ["content", "youtube-assets", cmd, change.id, *args])
        assert result.exit_code == 0 and "verified" in result.output
    assert len(api.writes) == 1


def test_caption_cli_lists_and_downloads_private_exact_bytes(tmp_path, monkeypatch):
    brand, api, file, args = setup(tmp_path, monkeypatch)
    result = runner.invoke(app, ["content", "youtube-assets", "captions-list", "video-1", *args])
    assert result.exit_code == 0 and "caption-1" in result.output
    target = tmp_path / "download.srt"
    result = runner.invoke(app, ["content", "youtube-assets", "captions-download", "video-1", "caption-1",
        "--output", str(target), *args])
    assert result.exit_code == 0, result.output
    assert target.read_bytes() == OLD and target.stat().st_mode & 0o777 == 0o600
    assert not api.writes


def test_cli_insert_requires_explicit_draft(tmp_path, monkeypatch):
    brand, api, file, args = setup(tmp_path, monkeypatch)
    result = runner.invoke(app, ["content", "youtube-assets", "prepare", "video-1", "--action", "caption-insert",
        "--file", str(file), "--language", "en", "--name", "English", "--dry-run", *args])
    assert result.exit_code != 0 and "draft" in result.output and not api.writes


def test_cli_apply_has_no_yes_bypass(tmp_path, monkeypatch):
    brand, api, file, args = setup(tmp_path, monkeypatch)
    result = runner.invoke(app, ["content", "youtube-assets", "apply", "x", "--yes", *args])
    assert result.exit_code != 0 and not api.writes


def test_documented_thumbnail_exit_status_and_readonly_followups(tmp_path, monkeypatch):
    brand, api, file, args = setup(tmp_path, monkeypatch)
    file = tmp_path / "supplied-thumbnail.png"
    file.write_bytes(PNG)
    result = runner.invoke(app, ["content", "youtube-assets", "prepare", "video-1",
        "--action", "thumbnail-set", "--file", str(file), "--dry-run", *args])
    assert result.exit_code == 0 and not api.writes
    store = ResourceStore(brand.raiz)
    change = store.load(next(store.root.glob("*.json")).stem)
    result = runner.invoke(app, ["content", "youtube-assets", "apply", change.id, *args],
        input=change.fingerprint + "\n")
    assert result.exit_code == 1 and '"status": "accepted_unverifiable"' in result.output
    assert '"verified": false' in result.output and len(api.writes) == 1
    for command in ("status", "reconcile"):
        result = runner.invoke(app, ["content", "youtube-assets", command, change.id, *args])
        assert result.exit_code == 0 and '"status": "accepted_unverifiable"' in result.output
    assert len(api.writes) == 1
