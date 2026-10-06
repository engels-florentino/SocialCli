"""Supplied-byte staging contracts; all brands and transports are synthetic."""
import hashlib
import importlib
import json
from pathlib import Path

import pytest

from socialctl.brands import crear_brand
from tests.test_youtube_metadata_parts import isolated

DATA = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isomSUPPLIED"


def registry():
    assert importlib.util.find_spec("socialctl.media_registry") is not None, "supplied media registry missing"
    return importlib.import_module("socialctl.media_registry")


def supplied(tmp_path, name="MarcaA"):
    brand = crear_brand(tmp_path, name)
    (brand.raiz / "media/source.mp4").write_bytes(DATA)
    post = brand.dir_posts / "clip/post.yml"
    post.parent.mkdir()
    post.write_text("# supplied copy\ncampaign: clip-vertical\nplatforms:\n  instagram:\n    body: Supplied text\n    media: [source.mp4]\n")
    return brand


def test_stage_copies_bytes_preserves_originals_and_records_uses(tmp_path):
    module = registry()
    brand = supplied(tmp_path)
    original = (brand.dir_posts / "clip/post.yml").read_bytes()
    manifest = module.stage(brand, "clip")
    asset = manifest["assets"][0]
    assert asset["sha256"] == hashlib.sha256(DATA).hexdigest()
    assert asset["size"] == len(DATA)
    assert asset["source"] == "media/source.mp4"
    assert asset["uses"] == ["clip/instagram"]
    assert asset["relative_path"] == f"approved/sha256/{hashlib.sha256(DATA).hexdigest()}.mp4"
    assert (brand.raiz / "media" / asset["relative_path"]).read_bytes() == DATA
    assert (brand.dir_posts / "clip/post.yml").read_bytes() == original
    assert (brand.raiz / "media/source.mp4").read_bytes() == DATA
    assert manifest["brand"] == "MarcaA"
    assert module.stage(brand, "clip") == manifest
    assert not (brand.raiz / ".socialctl/schedule.json").exists()


@pytest.mark.parametrize("failure", ["missing", "symlink", "traversal", "mime", "space", "mismatch"])
def test_stage_rejects_unsafe_or_unavailable_supplied_bytes(tmp_path, monkeypatch, failure):
    module = registry()
    brand = supplied(tmp_path)
    source = brand.raiz / "media/source.mp4"
    if failure == "missing":
        source.unlink()
    elif failure == "symlink":
        target = brand.raiz / "media/real.mp4"
        source.rename(target)
        source.symlink_to(target)
    elif failure == "traversal":
        post = brand.dir_posts / "clip/post.yml"
        post.write_text(post.read_text().replace("source.mp4", "../.secrets/secret.mp4"))
    elif failure == "mime":
        source.write_bytes(b"this is not MP4")
    elif failure == "space":
        monkeypatch.setattr(module.shutil, "disk_usage", lambda path: type("Space", (), {"free": 0})())
    else:
        target = brand.raiz / f"media/approved/sha256/{hashlib.sha256(DATA).hexdigest()}.mp4"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"existing immutable mismatch")
    with pytest.raises(ValueError):
        module.stage(brand, "clip")
    assert not list((brand.raiz / ".socialctl/media-bundles").glob("*.json"))


def test_manifest_integrity_and_brand_binding(tmp_path):
    module = registry()
    a = supplied(tmp_path)
    b = supplied(tmp_path, "MarcaB")
    manifest = module.stage(a, "clip")
    with pytest.raises(ValueError):
        module.validate_manifest(b, manifest)
    manifest["assets"][0]["source"] = "media/other.mp4"
    with pytest.raises(ValueError):
        module.validate_manifest(a, manifest)


def test_growing_source_is_bounded_and_never_promoted(tmp_path, monkeypatch):
    module = registry()
    brand = supplied(tmp_path)
    original_check = module.space
    def mutate(path, size):
        original_check(path, size)
        (brand.raiz / "media/source.mp4").write_bytes(DATA + b"unexpected more bytes")
    monkeypatch.setattr(module, "space", mutate)
    with pytest.raises(ValueError):
        module.stage(brand, "clip")
    assert not list((brand.raiz / "media/approved").rglob("*.mp4"))


def test_copy_refuses_to_read_more_than_declared_size(tmp_path):
    module = registry()
    class Source:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, count):
            assert count <= len(DATA) + 1, "copy is not bounded by declared asset size"
            return DATA + b"extra"
    source = tmp_path / "source.mp4"
    source.write_bytes(DATA)
    item = dict(size=len(DATA), sha256=hashlib.sha256(DATA).hexdigest(), mime="video/mp4", relative_path="file.mp4")
    original_open = Path.open
    from unittest.mock import patch
    with patch.object(Path, "open", lambda path, *args, **kw: Source() if path == source else original_open(path, *args, **kw)):
        with pytest.raises(ValueError):
            module.promote(source, tmp_path / "target.mp4", item)


def test_oversize_supplied_file_rejected_before_hash_read(tmp_path, monkeypatch):
    module = registry()
    brand = supplied(tmp_path)
    monkeypatch.setattr(module, "MAX_BYTES", 16)
    monkeypatch.setattr(module, "file_digest", lambda path: pytest.fail("oversize source reached full hash read"))
    with pytest.raises(ValueError):
        module.stage(brand, "clip")
