import json

import httpx

from typer.testing import CliRunner

from socialctl.cli import app
from tests.test_youtube_batches import proposal
from tests.test_youtube_metadata_parts import isolated


def wire(monkeypatch, client, api):
    from socialctl.management import cli
    monkeypatch.setattr(cli, "_brand", lambda root, name: client.brand)
    monkeypatch.setattr(cli, "make_http_client", lambda: httpx.Client(transport=httpx.MockTransport(api.handler)))


def test_complete_batch_dryrun_and_apply_share_exact_digest(tmp_path, monkeypatch):
    module, api, client, store, original, file = proposal(tmp_path)
    wire(monkeypatch, client, api)
    runner = CliRunner()
    preview = runner.invoke(app, ["content", "edit-batch", "--file", str(file), "--brand", "MarcaA", "--dry-run"])
    assert preview.exit_code == 0, preview.output
    records = [store.load(path.stem) for path in store.root.glob("*.json") if path.stem != original.id]
    batch = records[0]
    for ident in ("video-1", "video-2", "video-3"):
        assert ident in preview.output
    assert batch.fingerprint in preview.output
    assert "internal" in preview.output and "Visible hashtags" in preview.output
    assert "Before" in preview.output and "After" in preview.output
    assert api.writes == []
    denied = runner.invoke(app, ["changes", "apply", batch.id, "--brand", "MarcaA", "--yes"], input="yes\n")
    assert denied.exit_code == 1 and api.writes == []
    api.failures["video-2"] = 412
    result = runner.invoke(app, ["changes", "apply", batch.id, "--brand", "MarcaA"], input=batch.fingerprint + "\n")
    assert result.exit_code == 1
    assert "conflict" in result.output and "applied" in result.output
    assert [ident for ident, *_ in api.writes] == ["video-1", "video-2", "video-3"]


def test_restore_cli_demands_dryrun_then_new_exact_approval(tmp_path, monkeypatch):
    module, api, client, store, batch, _ = proposal(tmp_path, names=("video-1",))
    wire(monkeypatch, client, api)
    runner = CliRunner()
    denied = runner.invoke(app, ["changes", "restore", batch.id, "--brand", "MarcaA"])
    assert denied.exit_code == 1 and "--dry-run" in denied.output
    result = runner.invoke(app, ["changes", "restore", batch.id, "--brand", "MarcaA", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "restoration" in result.output
    assert 'does not restore' in result.output
    assert api.writes == []
    ids = [path.stem for path in store.root.glob("*.json") if path.stem != batch.id]
    status = runner.invoke(app, ["changes", "status", ids[0], "--brand", "MarcaA"])
    assert status.exit_code == 0
    assert json.loads(status.output)["restored_from"] == batch.id
