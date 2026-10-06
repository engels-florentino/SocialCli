import json
import hashlib

import httpx
import pytest
from typer.testing import CliRunner

from socialctl.cli import app
from socialctl.scheduler import ScheduleStore
from tests.test_queue_migration import migration, public_client

runner = CliRunner()


def test_prepare_cli_dry_run_prints_complete_preview_and_no_proposal(migration):
    brand, entries = migration
    result = runner.invoke(app, ["queue-migration", "prepare", "--brand", "One", "--root", str(brand.raiz.parent), "--dry-run"])
    assert result.exit_code == 0, result.stdout
    assert "Original text" in result.stdout and "Other text" in result.stdout
    assert "original.mp4" in result.stdout and "approved/sha256/" in result.stdout
    assert not (brand.raiz / ".socialctl/migrations").exists()
    assert ScheduleStore(brand.raiz).load() == entries


def test_cli_prepare_stage_contract(migration, tmp_path):
    brand, entries = migration
    common = ["--brand", "One", "--root", str(brand.raiz.parent)]
    result = runner.invoke(app, ["queue-migration", "prepare", *common, "--backend", "sqlite"])
    assert result.exit_code == 0, result.stdout
    paths = list((brand.raiz / ".socialctl/migrations").glob("*/proposal.json"))
    proposal = json.loads(paths[0].read_bytes())
    result = runner.invoke(app, ["queue-migration", "stage", *common, "--proposal", proposal["id"], "--output", str(tmp_path / "transfer")])
    assert result.exit_code == 0, result.stdout
    assert (tmp_path / "transfer/transfer-manifest.json").exists()


def test_scheduled_instagram_verifies_public_bytes_before_adapter_writes(migration, tmp_path, monkeypatch):
    from socialctl.queue_migration import prepare, stage, apply
    import socialctl.cli as cli
    brand, entries = migration
    proposal = prepare(brand)
    stage(brand, proposal["id"], tmp_path / "transfer")
    with public_client() as client:
        apply(brand, proposal["id"], proposal["digest"], acknowledge_legacy_limitations=True, client=client)
    # Restrict test to the IG occurrence, preserving approved hash.
    store = ScheduleStore(brand.raiz)
    store.save([store.load()[0]])
    calls = []
    monkeypatch.setattr(cli, "publicar", lambda *a, **k: calls.append("WRITE"))
    def rejected(*args, **kwargs):
        calls.append("GET")
        raise ValueError("public bytes changed")
    monkeypatch.setattr("socialctl.hosted_media.attest_public_media", rejected)
    result = runner.invoke(app, ["run-due", "--brand", "One", "--root", str(brand.raiz.parent)])
    assert result.exit_code == 0, result.stdout
    assert calls == ["GET"]
    assert store.load()[0].status == "manual_review"


@pytest.fixture
def migrated(migration, tmp_path):
    from socialctl.queue_migration import prepare, stage, apply
    brand, _ = migration
    proposal = prepare(brand)
    stage(brand, proposal["id"], tmp_path / "transfer")
    with public_client() as client:
        apply(brand, proposal["id"], proposal["digest"], acknowledge_legacy_limitations=True, client=client)
    return brand, proposal


def _record_publication(monkeypatch, public_bytes):
    import socialctl.cli as cli
    import socialctl.hosted_media as hosted
    from socialctl.models import Platform, PostResult, PostStatus
    calls = []
    original = hosted.attest_public_media
    def respond(request):
        calls.append("GET")
        return httpx.Response(200, content=public_bytes, headers={"content-type": "video/mp4"})
    def preflight(path, url, **kwargs):
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            return original(path, url, client=client)
    def publish(*args, **kwargs):
        calls.append("WRITE")
        return [PostResult(platform=Platform.INSTAGRAM, status=PostStatus.PUBLICADO, platform_id="remote-new")]
    monkeypatch.setattr(hosted, "attest_public_media", preflight)
    monkeypatch.setattr(cli, "publicar", publish)
    return calls


def test_migration_does_not_block_later_new_v2_approved_asset(migrated, monkeypatch):
    brand, proposal = migrated
    store = ScheduleStore(brand.raiz)
    for entry in store.load():
        store.cancel(entry.id, entry.updated_at)
    raw = b"new independently supplied bytes"
    relative = f"approved/sha256/{hashlib.sha256(raw).hexdigest()}.mp4"
    (brand.raiz / "media" / relative).write_bytes(raw)
    folder = brand.dir_posts / "new"
    folder.mkdir()
    (folder / "post.yml").write_text(f"campaign: clip-vertical\nplatforms:\n  instagram:\n    body: Newly approved text\n    content_origin: standalone\n    media: ['{relative}']\n")
    common = ["--brand", "One", "--root", str(brand.raiz.parent)]
    scheduled = runner.invoke(app, ["schedule", "new", "--at", "2020-01-01T00:00:00Z", "--yes", *common])
    assert scheduled.exit_code == 0, scheduled.output
    calls = _record_publication(monkeypatch, raw)
    result = runner.invoke(app, ["run-due", *common])
    assert result.exit_code == 0, result.output
    assert calls == ["GET", "WRITE"]
    assert store.get("new/instagram").status == "published"
    assert store.get("new/instagram").approval_migration is None
    evidence = json.loads((brand.raiz / ".socialctl/last-media-preflight.json").read_bytes())
    assert evidence["slug"] == "new"
    assert evidence["entry_id"] == "new/instagram"
    assert evidence["content_hash"] == store.get("new/instagram").content_hash
    assert evidence["approval_migration"] is None
    assert evidence["attestations"][0]["sha256"] == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("bad", ["local", "public", "missing_attestations", "invalid_attestations", "missing_intent", "invalid_intent", "altered_provenance", "missing_proposal", "missing_all_history"])
def test_migrated_approval_requires_untampered_media_and_bound_history(migrated, monkeypatch, bad):
    brand, proposal = migrated
    store = ScheduleStore(brand.raiz)
    instagram = store.get("clip/instagram")
    store.save([instagram])
    intent_path = brand.raiz / ".socialctl/migrations" / proposal["id"] / "intent.json"
    attestations = brand.raiz / ".socialctl/media-attestations.json"
    if bad == "local":
        (brand.raiz / "media" / proposal["media"][0]["relative_path"]).write_bytes(b"changed local bytes")
    if bad == "missing_attestations": attestations.unlink()
    if bad == "invalid_attestations":
        data = json.loads(attestations.read_bytes())
        data["attestations"] = []
        attestations.write_text(json.dumps(data))
    if bad == "missing_intent": intent_path.unlink()
    if bad == "missing_proposal": (intent_path.parent / "proposal.json").unlink()
    if bad == "altered_provenance":
        data = json.loads(intent_path.read_bytes())
        data["approvals"][0]["old_hash"] = "0" * 64
        intent_path.write_text(json.dumps(data))
    if bad == "missing_all_history":
        intent_path.unlink()
        attestations.unlink()
    if bad == "invalid_intent":
        data = json.loads(intent_path.read_bytes())
        data["approvals"] = []
        intent_path.write_text(json.dumps(data))
    calls = _record_publication(monkeypatch, b"changed public bytes" if bad == "public" else b"supplied bytes")
    result = runner.invoke(app, ["run-due", "--brand", "One", "--root", str(brand.raiz.parent)])
    assert result.exit_code == 0, result.output
    assert "WRITE" not in calls
    assert calls == (["GET"] if bad == "public" else [])
    assert store.get("clip/instagram").status == "manual_review"
