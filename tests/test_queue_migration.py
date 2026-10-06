import hashlib
import json

import httpx
import pytest

from socialctl.brands import cargar_brand, crear_brand
from socialctl.models import Platform
from socialctl.postfile import cargar_post
from socialctl.queue_migration import prepare, stage, apply, rollback, load_proposal
from socialctl.scheduler import ScheduleError, ScheduleStore, legacy_approval_hash
from tests.test_scheduler import _entry


@pytest.fixture
def migration(tmp_path, monkeypatch):
    monkeypatch.setattr("socialctl.media._ffprobe", lambda path: {"streams": [{"width": 1080, "height": 1920}], "format": {"duration": "30"}})
    brand = crear_brand(tmp_path, "One")
    crear_brand(tmp_path, "Two")
    (brand.raiz / "accounts.yml").write_text("instagram:\n  ig_user_id: '123'\n  media_url_base: https://media.test\nfacebook:\n  page_id: '456'\n")
    brand = cargar_brand(tmp_path, "One")
    (brand.raiz / "media" / "original.mp4").write_bytes(b"supplied bytes")
    folder = brand.dir_posts / "clip"
    folder.mkdir()
    (folder / "post.yml").write_text("# preserve me\ncampaign: clip-vertical\nplatforms:\n  instagram:\n    body: Original text\n    media: ['original.mp4'] # keep comment\n  facebook:\n    body: Other text\n    media: [original.mp4]\n")
    entries = []
    post = cargar_post(brand, "clip")
    for platform in (Platform.INSTAGRAM, Platform.FACEBOOK):
        entry = _entry("clip/" + platform.value, "2020-01-01T00:00:00Z")
        entry.brand = "One"
        entry.platform = platform.value
        entry.content_hash = legacy_approval_hash(post, platform)
        entries.append(entry)
    ScheduleStore(brand.raiz).save(entries)
    return brand, entries


def public_client(body=b"supplied bytes"):
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body, headers={"content-type": "video/mp4"})))


def test_migration_preserves_everything_except_media_and_hash(migration, tmp_path):
    brand, entries = migration
    original = (brand.dir_posts / "clip/post.yml").read_bytes()
    proposal = prepare(brand, backend="sqlite")
    assert "Original text" in proposal["preview"] and "Other text" in proposal["preview"]
    manifest = stage(brand, proposal["id"], tmp_path / "transfer")
    assert len(manifest["files"]) == 1
    assert (tmp_path / "transfer" / manifest["files"][0]["relative_path"]).read_bytes() == b"supplied bytes"
    with public_client() as client:
        apply(brand, proposal["id"], proposal["digest"], acknowledge_legacy_limitations=True, client=client)
    after = ScheduleStore(brand.raiz).load()
    for old, new in zip(entries, after):
        assert old.model_dump(exclude={"content_hash", "approval_migration"}) == new.model_dump(exclude={"content_hash", "approval_migration"})
        assert new.content_hash.startswith("v2:")
        assert new.approval_migration == proposal["id"]
    assert ScheduleStore(tmp_path / "Two").load() == []
    assert (brand.raiz / "media/original.mp4").read_bytes() == b"supplied bytes"
    rewritten = (brand.dir_posts / "clip/post.yml").read_bytes()
    assert b"# preserve me" in rewritten and b"# keep comment" in rewritten
    rollback(brand, proposal["id"])
    assert (brand.dir_posts / "clip/post.yml").read_bytes() == original
    assert ScheduleStore(brand.raiz).load() == entries


def test_stage_blocks_remote_marker_before_any_media_or_manifest_write(migration, tmp_path):
    brand, _ = migration
    proposal = prepare(brand)
    (brand.raiz / ".socialctl/remote-executor.json").write_text("{}")
    with pytest.raises(ScheduleError, match='remote'):
        stage(brand, proposal["id"], tmp_path / "transfer")
    assert not (tmp_path / "transfer").exists()
    assert not (brand.raiz / "media/approved").exists()


def test_stage_cannot_race_an_authority_handoff_holding_executor_lock(migration, tmp_path):
    brand, _ = migration
    proposal = prepare(brand)
    with ScheduleStore(brand.raiz).executor_lock():
        with pytest.raises(ScheduleError, match="another executor"):
            stage(brand, proposal["id"], tmp_path / "transfer")
    assert not (tmp_path / "transfer").exists()
    assert not (brand.raiz / "media/approved").exists()


@pytest.mark.parametrize("tamper", ["post", "account", "source", "queue", "public", "staged"])
def test_apply_revalidates_all_evidence(migration, tmp_path, tamper):
    brand, entries = migration
    proposal = prepare(brand)
    manifest = stage(brand, proposal["id"], tmp_path / "transfer")
    paths = {"post": brand.dir_posts / "clip/post.yml", "account": brand.raiz / "accounts.yml",
             "source": brand.raiz / "media/original.mp4", "queue": ScheduleStore(brand.raiz).path,
             "staged": brand.raiz / "media" / manifest["files"][0]["relative_path"]}
    if tamper in paths:
        with paths[tamper].open("ab") as handle:
            handle.write(b"changed")
    with public_client(b"changed" if tamper == "public" else b"supplied bytes") as client:
        with pytest.raises((ScheduleError, ValueError)):
            apply(brand, proposal["id"], proposal["digest"], acknowledge_legacy_limitations=True, client=client)


def test_interrupted_apply_blocks_execution_and_explicit_rollback_recovers(migration, tmp_path, monkeypatch):
    brand, entries = migration
    original = (brand.dir_posts / "clip/post.yml").read_bytes()
    proposal = prepare(brand)
    stage(brand, proposal["id"], tmp_path / "transfer")
    import socialctl.queue_migration as module
    save = ScheduleStore._save_unlocked
    monkeypatch.setattr(ScheduleStore, "_save_unlocked", lambda *a: (_ for _ in ()).throw(OSError("crash")))
    with public_client() as client, pytest.raises(OSError, match="crash"):
        apply(brand, proposal["id"], proposal["digest"], acknowledge_legacy_limitations=True, client=client)
    with pytest.raises(ScheduleError, match='migration'):
        ScheduleStore(brand.raiz).claim_due(entries[0].id, entries[0].scheduled_at)
    monkeypatch.setattr(ScheduleStore, "_save_unlocked", save)
    rollback(brand, proposal["id"])
    assert (brand.dir_posts / "clip/post.yml").read_bytes() == original
    assert ScheduleStore(brand.raiz).load() == entries


@pytest.mark.parametrize("bad", ["missing", "hash", "running", "attempted", "brand", "duplicate", "alias"])
def test_prepare_rejects_ambiguous_legacy_approval(migration, bad):
    brand, entries = migration
    if bad == "missing": entries[0].content_hash = None
    if bad == "hash": entries[0].content_hash = "0" * 64
    if bad == "running": entries[0].status = "running"
    if bad == "attempted": entries[0].attempts = 1
    if bad == "brand": entries[0].brand = "Two"
    if bad == "duplicate": entries.append(entries[0])
    if bad == "alias":
        path = brand.dir_posts / "clip/post.yml"
        path.write_text(path.read_text().replace("['original.mp4']", "&media ['original.mp4']"))
    ScheduleStore(brand.raiz).save(entries)
    with pytest.raises((ScheduleError, ValueError)):
        prepare(brand)


def test_intent_without_guard_still_blocks_executor(migration, tmp_path, monkeypatch):
    brand, entries = migration
    proposal = prepare(brand)
    stage(brand, proposal["id"], tmp_path / "transfer")
    import socialctl.queue_migration as module
    original = module.write_json
    def crash(path, data):
        if path.name == "migration-active.json":
            raise OSError("power loss")
        return original(path, data)
    monkeypatch.setattr(module, "write_json", crash)
    with public_client() as client, pytest.raises(OSError, match="power loss"):
        apply(brand, proposal["id"], proposal["digest"], acknowledge_legacy_limitations=True, client=client)
    with pytest.raises(ScheduleError, match='migration'):
        with ScheduleStore(brand.raiz).executor_lock():
            pass
    monkeypatch.setattr(module, "write_json", original)
    rollback(brand, proposal["id"])
    assert ScheduleStore(brand.raiz).load() == entries


def test_historical_repeated_ids_and_other_brand_preserved(migration, tmp_path):
    brand, entries = migration
    old = entries[0].model_copy(update={"status": "published", "platform_id": "remote-old", "attempts": 2})
    store = ScheduleStore(brand.raiz)
    store.save([old, *entries])
    other = ScheduleStore(tmp_path / "Two")
    other.save([old.model_copy(update={"brand": "Two"})])
    untouched = other.path.read_bytes()
    proposal = prepare(brand, backend="sqlite")
    stage(brand, proposal["id"], tmp_path / "transfer")
    with public_client() as client:
        apply(brand, proposal["id"], proposal["digest"], acknowledge_legacy_limitations=True, client=client)
    assert store.load()[0] == old
    assert other.path.read_bytes() == untouched


def test_rollback_refuses_advanced_queue(migration, tmp_path):
    brand, entries = migration
    proposal = prepare(brand)
    stage(brand, proposal["id"], tmp_path / "transfer")
    with public_client() as client:
        apply(brand, proposal["id"], proposal["digest"], acknowledge_legacy_limitations=True, client=client)
    store = ScheduleStore(brand.raiz)
    store.claim_due(entries[0].id, entries[0].scheduled_at)
    with pytest.raises(ScheduleError, match='advanced'):
        rollback(brand, proposal["id"])


def test_existing_sqlite_queue_migrates_and_restores_consistent_backup(migration, tmp_path):
    brand, entries = migration
    store = ScheduleStore(brand.raiz, backend="sqlite")
    proposal = prepare(brand)
    stage(brand, proposal["id"], tmp_path / "transfer")
    with public_client() as client:
        apply(brand, proposal["id"], proposal["digest"], acknowledge_legacy_limitations=True, client=client)
    assert store.load()[0].content_hash.startswith("v2:")
    rollback(brand, proposal["id"])
    assert store.load() == entries


def test_apply_cannot_bind_changed_staged_bytes_as_new_approval(migration, tmp_path, monkeypatch):
    brand, entries = migration
    proposal = prepare(brand)
    manifest = stage(brand, proposal["id"], tmp_path / "transfer")
    import socialctl.queue_migration as module
    original = module.durable_write
    def tamper(path, data):
        original(path, data)
        if path == brand.dir_posts / "clip/post.yml":
            (brand.raiz / "media" / manifest["files"][0]["relative_path"]).write_bytes(b"changed bytes")
    monkeypatch.setattr(module, "durable_write", tamper)
    with public_client() as client, pytest.raises(ScheduleError, match="changed"):
        apply(brand, proposal["id"], proposal["digest"], acknowledge_legacy_limitations=True, client=client)
    assert ScheduleStore(brand.raiz).load() == entries


def test_rollback_recovers_crash_after_terminal_record_before_guard_removal(migration, tmp_path, monkeypatch):
    brand, entries = migration
    proposal = prepare(brand)
    stage(brand, proposal["id"], tmp_path / "transfer")
    with public_client() as client:
        apply(brand, proposal["id"], proposal["digest"], acknowledge_legacy_limitations=True, client=client)
    import socialctl.queue_migration as module
    original = module.write_json
    def crash(path, data):
        original(path, data)
        if data.get("status") == "rolled_back":
            raise OSError("terminal record crash")
    monkeypatch.setattr(module, "write_json", crash)
    with pytest.raises(OSError, match="terminal record crash"):
        rollback(brand, proposal["id"])
    monkeypatch.setattr(module, "write_json", original)
    rollback(brand, proposal["id"])
    with ScheduleStore(brand.raiz).executor_lock():
        assert ScheduleStore(brand.raiz).load() == entries


def test_unbound_draft_proposal_rejected_before_any_application(migration, tmp_path):
    from socialctl.queue_migration import _digest
    brand, entries = migration
    proposal = prepare(brand)
    stage(brand, proposal["id"], tmp_path / "transfer")
    for item in proposal["approvals"]:
        item.pop("approval_migration")
    proposal["digest"] = _digest(proposal)
    directory = brand.raiz / ".socialctl/migrations" / proposal["id"]
    (directory / "proposal.json").write_text(json.dumps(proposal))
    with public_client() as client, pytest.raises(ScheduleError, match="linkage"):
        apply(brand, proposal["id"], proposal["digest"], acknowledge_legacy_limitations=True, client=client)
    assert not (directory / "intent.json").exists()
    assert ScheduleStore(brand.raiz).load() == entries


@pytest.mark.parametrize("at", ["first_post", "committed_intent", "attestations", "rollback_post"])
def test_crash_boundaries_require_and_allow_rollback(migration, tmp_path, monkeypatch, at):
    brand, entries = migration
    proposal = prepare(brand, backend="sqlite")
    stage(brand, proposal["id"], tmp_path / "transfer")
    import socialctl.queue_migration as module
    original_json, original_write = module.write_json, module.durable_write
    def json_crash(path, data):
        original_json(path, data)
        if (at == "committed_intent" and data.get("status") == "committed") or (at == "attestations" and path.name == "media-attestations.json"):
            raise OSError("crash boundary")
    def write_crash(path, data):
        original_write(path, data)
        if at == "first_post" and path == brand.dir_posts / "clip/post.yml":
            raise OSError("crash boundary")
    monkeypatch.setattr(module, "write_json", json_crash)
    monkeypatch.setattr(module, "durable_write", write_crash)
    with public_client() as client:
        if at != "rollback_post":
            with pytest.raises(OSError, match="crash boundary"):
                apply(brand, proposal["id"], proposal["digest"], acknowledge_legacy_limitations=True, client=client)
        else:
            apply(brand, proposal["id"], proposal["digest"], acknowledge_legacy_limitations=True, client=client)
    monkeypatch.setattr(module, "write_json", original_json)
    monkeypatch.setattr(module, "durable_write", original_write)
    if at == "rollback_post":
        def rollback_crash(path, data):
            original_write(path, data)
            if path == brand.dir_posts / "clip/post.yml": raise OSError("rollback interrupted")
        monkeypatch.setattr(module, "durable_write", rollback_crash)
        with pytest.raises(OSError, match="rollback interrupted"):
            rollback(brand, proposal["id"])
        monkeypatch.setattr(module, "durable_write", original_write)
    with pytest.raises(ScheduleError, match='migration'):
        ScheduleStore(brand.raiz).save(entries)
    rollback(brand, proposal["id"])
    assert ScheduleStore(brand.raiz).load() == entries
