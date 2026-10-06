import copy
import hashlib
import importlib
import io
import json
import os
import struct

import httpx
import pytest

from socialctl.brands import crear_brand
from socialctl.scheduler import ScheduleEntry, ScheduleStore, now_utc
from tests.test_media_registry import DATA, registry, supplied
from tests.test_youtube_metadata_parts import isolated


def ingress():
    assert importlib.util.find_spec("socialctl.media_ingress") is not None, "bounded media ingress missing"
    return importlib.import_module("socialctl.media_ingress")


def setup(tmp_path):
    local = supplied(tmp_path / "local")
    manifest = registry().stage(local, "clip")
    server = crear_brand(tmp_path / "server", "MarcaA")
    public = tmp_path / "hosted/MarcaA"
    public.mkdir(parents=True)
    public.chmod(0o755)
    policy = dict(version=1, brand="MarcaA", private_root=str(server.raiz / "media"),
                  public_root=str(public), public_url_base="https://media.example/MarcaA")
    server.cuentas["instagram"]["media_url_base"] = policy["public_url_base"]
    path = server.raiz / ".socialctl/media-policy.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(policy))
    return local, manifest, server, public


def wire(local, manifest):
    stream = io.BytesIO()
    ingress().write_transfer(local, manifest["digest"], stream)
    stream.seek(0)
    return stream


def test_complete_transfer_copies_only_selected_bytes_and_preserves_posts(tmp_path):
    local, manifest, server, public = setup(tmp_path)
    unrelated = server.dir_posts / "draft/post.yml"
    unrelated.parent.mkdir()
    unrelated.write_bytes(b"original unrelated draft")
    result = ingress().receive(server, wire(local, manifest))
    relative = manifest["assets"][0]["relative_path"]
    assert result["private_ready"] is True
    assert result["hosted_ready"] is True
    assert result["public_ready"] is False
    assert (server.raiz / "media" / relative).read_bytes() == DATA
    assert (public / relative).read_bytes() == DATA
    assert (public / relative).stat().st_mode & 0o444 == 0o444
    assert b"Supplied text" in (server.dir_posts / "clip/post.yml").read_bytes()
    assert unrelated.read_bytes() == b"original unrelated draft"
    inode = (public / relative).stat().st_ino
    ingress().receive(server, wire(local, manifest))
    assert (public / relative).stat().st_ino == inode
    assert ScheduleStore(server.raiz).load() == []
    assert not (server.raiz / ".socialctl/publications").exists()


@pytest.mark.parametrize("failure", ["truncated_header", "truncated_asset", "trailing", "hash", "mime", "duplicate", "brand", "destination", "fsync", "oversize", "version"])
def test_bad_or_interrupted_transfer_never_promotes_assets_or_posts(tmp_path, monkeypatch, failure):
    local, manifest, server, public = setup(tmp_path)
    module = ingress()
    raw = wire(local, manifest).getvalue()
    if failure == "truncated_header":
        raw = raw[:10]
    elif failure == "truncated_asset":
        raw = raw[:-1]
    elif failure == "trailing":
        raw += b"extra frame"
    elif failure == "hash":
        raw = raw[:-1] + b"X"
    elif failure in {"mime", "duplicate", "brand", "destination", "oversize", "version"}:
        length = struct.unpack("!I", raw[:4])[0]
        header = json.loads(raw[4:4+length])
        if failure == "mime":
            header["assets"][0]["mime"] = "text/plain"
        elif failure == "duplicate":
            header["assets"].append(header["assets"][0])
        elif failure == "brand":
            header["brand"] = "MarcaB"
        elif failure == "destination":
            header["public_root"] = "/arbitrary/destination"
        elif failure == "oversize":
            header["assets"][0]["size"] = 2**50
        else:
            header["version"] = 2
        header["digest"] = registry().digest(registry().canonical({k: v for k, v in header.items() if k != "digest"}))
        encoded = json.dumps(header).encode()
        raw = struct.pack("!I", len(encoded)) + encoded + raw[4+length:]
    else:
        monkeypatch.setattr(os, "fsync", lambda *args: (_ for _ in ()).throw(OSError("fsync failed")))
    with pytest.raises((ValueError, OSError)):
        module.receive(server, io.BytesIO(raw))
    assert not list(public.rglob("*.mp4"))
    assert not (server.dir_posts / "clip/post.yml").exists()
    assert not list((server.raiz / ".socialctl/media-bundles").glob("*.json"))


def test_mismatched_hosted_destination_is_preserved(tmp_path):
    local, manifest, server, public = setup(tmp_path)
    target = public / manifest["assets"][0]["relative_path"]
    target.parent.mkdir(parents=True)
    target.write_bytes(b"existing mismatch")
    with pytest.raises(ValueError):
        ingress().receive(server, wire(local, manifest))
    assert target.read_bytes() == b"existing mismatch"
    assert not (server.dir_posts / "clip/post.yml").exists()


def test_conflicting_draft_requires_exact_preview_digest_and_keeps_backup(tmp_path):
    local, manifest, server, public = setup(tmp_path)
    path = server.dir_posts / "clip/post.yml"
    path.parent.mkdir()
    path.write_bytes(b"old supplied post\n")
    with pytest.raises(ValueError, match="preview"):
        ingress().receive(server, wire(local, manifest))
    preview = ingress().receive(server, wire(local, manifest), preview_update=True)
    assert "old supplied post" in preview["preview"] and "Supplied text" in preview["preview"]
    assert path.read_bytes() == b"old supplied post\n"
    assert not list(public.rglob("*.mp4"))
    with pytest.raises(ValueError):
        ingress().receive(server, wire(local, manifest), update_digest="0" * 64)
    ingress().receive(server, wire(local, manifest), update_digest=preview["update_digest"])
    assert b"Supplied text" in path.read_bytes()
    backups = list((server.raiz / ".socialctl/media-post-backups").rglob("*.yml"))
    assert len(backups) == 1 and backups[0].read_bytes() == b"old supplied post\n"


@pytest.mark.parametrize("state", ["approved", "running", "uncertain", "manual_review"])
def test_queue_bound_posts_are_never_overwritten(tmp_path, state):
    local, manifest, server, public = setup(tmp_path)
    path = server.dir_posts / "clip/post.yml"
    path.parent.mkdir()
    path.write_bytes(b"approved bytes")
    now = now_utc()
    ScheduleStore(server.raiz).save([ScheduleEntry(id="clip/instagram", brand=server.nombre,
        slug="clip", platform="instagram", status=state, scheduled_at=now, created_at=now, updated_at=now)])
    with pytest.raises(ValueError, match="queue"):
        ingress().receive(server, wire(local, manifest), preview_update=True)
    assert path.read_bytes() == b"approved bytes"


def test_public_verification_persists_exact_attestation_without_publication(tmp_path):
    local, manifest, server, public = setup(tmp_path)
    module = ingress()
    module.receive(server, wire(local, manifest))
    def handler(request):
        assert request.method == "GET" and request.url.host == "media.example"
        assert "authorization" not in request.headers
        return httpx.Response(200, content=DATA, headers={"Content-Type": "video/mp4"})
    result = module.verify(server, manifest["digest"], public=True,
                           client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert result["public_ready"] is True
    assert result["attestations"] == [dict(url="https://media.example/MarcaA/" + manifest["assets"][0]["relative_path"],
        sha256=hashlib.sha256(DATA).hexdigest(), size=len(DATA), mime="video/mp4")]
    saved = json.loads((server.raiz / f".socialctl/media-readiness/{manifest['digest']}.json").read_bytes())
    assert saved == result
    assert ScheduleStore(server.raiz).load() == []


def test_existing_bundle_tamper_is_rejected_without_overwriting_registry(tmp_path):
    local, manifest, server, public = setup(tmp_path)
    ingress().receive(server, wire(local, manifest))
    path = server.raiz / f".socialctl/media-bundles/{manifest['digest']}.json"
    path.write_bytes(b"tampered registry")
    with pytest.raises(ValueError):
        ingress().receive(server, wire(local, manifest))
    assert path.read_bytes() == b"tampered registry"


@pytest.mark.parametrize("target", ["private", "public", "post", "policy", "parent"])
def test_server_rejects_symlink_paths_without_touching_target(tmp_path, target):
    local, manifest, server, public = setup(tmp_path)
    relative = manifest["assets"][0]["relative_path"]
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(DATA)
    if target == "private":
        path = server.raiz / "media" / relative
    elif target == "public":
        path = public / relative
    elif target == "post":
        path = server.dir_posts / "clip/post.yml"
    elif target == "policy":
        path = server.raiz / ".socialctl/media-policy.json"
        original = path.read_bytes()
        path.unlink()
        outside.write_bytes(original)
    else:
        path = public / "approved"
        outside = tmp_path / "outside-dir"
        outside.mkdir()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(outside)
    with pytest.raises(ValueError):
        ingress().receive(server, wire(local, manifest))
    assert path.is_symlink()


def test_stale_update_digest_preserves_latest_draft(tmp_path):
    local, manifest, server, public = setup(tmp_path)
    path = server.dir_posts / "clip/post.yml"
    path.parent.mkdir()
    path.write_bytes(b"old draft")
    preview = ingress().receive(server, wire(local, manifest), preview_update=True)
    path.write_bytes(b"edited meanwhile")
    with pytest.raises(ValueError):
        ingress().receive(server, wire(local, manifest), update_digest=preview["update_digest"])
    assert path.read_bytes() == b"edited meanwhile"


def test_identical_active_post_can_reuse_assets_without_queue_changes(tmp_path):
    local, manifest, server, public = setup(tmp_path)
    ingress().receive(server, wire(local, manifest))
    now = now_utc()
    store = ScheduleStore(server.raiz)
    entry = ScheduleEntry(id="clip/instagram", brand=server.nombre, slug="clip", platform="instagram",
                         scheduled_at=now, created_at=now, updated_at=now)
    store.save([entry])
    ingress().receive(server, wire(local, manifest))
    assert store.load() == [entry]


def test_duplicate_header_keys_are_rejected(tmp_path):
    local, manifest, server, public = setup(tmp_path)
    raw = wire(local, manifest).getvalue()
    length = struct.unpack("!I", raw[:4])[0]
    header = raw[4:4+length]
    header = b'{"version":2,' + header[1:]
    with pytest.raises(ValueError):
        ingress().receive(server, io.BytesIO(struct.pack("!I", len(header)) + header + raw[4+length:]))
    assert not list(public.rglob("*.mp4"))


def test_missing_post_with_active_queue_cannot_be_replaced_by_ingress(tmp_path):
    local, manifest, server, public = setup(tmp_path)
    now = now_utc()
    store = ScheduleStore(server.raiz)
    entry = ScheduleEntry(id="clip/instagram", brand=server.nombre, slug="clip", platform="instagram",
                         scheduled_at=now, created_at=now, updated_at=now)
    store.save([entry])
    with pytest.raises(ValueError, match="queue"):
        ingress().receive(server, wire(local, manifest))
    assert not (server.dir_posts / "clip/post.yml").exists()
    assert store.load() == [entry]


@pytest.mark.parametrize("state,platform_id", [("published", "published-id"), ("published", None),
                                               ("error", "published-id"), ("cancelled", "published-id")])
def test_legacy_queue_publication_evidence_blocks_draft_update(tmp_path, state, platform_id):
    local, manifest, server, public = setup(tmp_path)
    path = server.dir_posts / "clip/post.yml"
    path.parent.mkdir()
    path.write_bytes(b"legacy published text")
    preview = ingress().receive(server, wire(local, manifest), preview_update=True)
    now = now_utc()
    store = ScheduleStore(server.raiz)
    entry = ScheduleEntry(id="clip/youtube", brand=server.nombre, slug="clip", platform="youtube",
        status=state, platform_id=platform_id, scheduled_at=now, created_at=now, updated_at=now)
    store.save([entry])
    for options in ({"preview_update": True}, {"update_digest": preview["update_digest"]}):
        with pytest.raises(ValueError, match='publication'):
            ingress().receive(server, wire(local, manifest), **options)
        assert path.read_bytes() == b"legacy published text"
        assert store.load() == [entry]
