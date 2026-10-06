import json
import os
import shlex
import subprocess
import threading

import pytest
from typer.testing import CliRunner

from socialctl.cli import app
from tests.test_media_ingress import setup, ingress
from tests.test_media_registry import supplied, registry
from tests.test_youtube_metadata_parts import isolated

runner = CliRunner()


def install_process(monkeypatch, handler):
    """Fake process with real bounded OS pipe; never launches a subprocess."""
    processes = []
    class Process:
        def __init__(self, argv, **kwargs):
            assert kwargs["stdout"] == subprocess.PIPE
            assert kwargs["stderr"] == subprocess.DEVNULL
            read_fd, write_fd = os.pipe()
            self.stdout = os.fdopen(read_fd, "rb", buffering=0)
            self.returncode = None
            self.killed = False
            self.error = None
            def run():
                try:
                    with os.fdopen(write_fd, "wb", buffering=0) as output:
                        self.returncode = handler(argv, **{**kwargs, "stdout": output}).returncode
                except BrokenPipeError:
                    self.returncode = -9
                except BaseException as exc:
                    self.error = exc
                    self.returncode = 1
            self.thread = threading.Thread(target=run)
            processes.append(self)
            self.thread.start()

        def poll(self):
            return self.returncode

        def kill(self):
            self.killed = True
            self.stdout.close()

        def wait(self, timeout=None):
            self.thread.join(timeout)
            if self.thread.is_alive():
                raise subprocess.TimeoutExpired("fake", timeout)
            if self.error:
                raise self.error
            return self.returncode
    monkeypatch.setattr(subprocess, "Popen", Process)
    return processes


def test_cli_stage_exposes_local_readiness_and_explicit_brand(tmp_path):
    brand = supplied(tmp_path)
    result = runner.invoke(app, ["media", "stage", "clip", "--brand", "MarcaA", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    result = json.loads(result.output)
    assert result["local_ready"] is True and result["private_ready"] is False
    assert result["public_ready"] is False
    missing = runner.invoke(app, ["media", "stage", "clip", "--root", str(tmp_path)])
    assert missing.exit_code != 0


def remote(local):
    config = dict(host="server.example", port=2222, user="publisher", root="/srv/state",
                  executable="/srv/runtime/.venv/bin/socialctl")
    (local.raiz / ".socialctl/remote-executor.json").write_text(json.dumps(config))
    return config


def test_cli_transfer_uses_fixed_ssh_receiver_and_real_binary_ingestion(tmp_path, monkeypatch):
    local, manifest, server, public = setup(tmp_path)
    config = remote(local)
    def transport(argv, **kwargs):
        assert argv[:2] == ["ssh", "-T"] and "StrictHostKeyChecking=yes" in argv
        assert shlex.split(argv[-1]) == [config["executable"], "media", "receive", "--brand", "MarcaA", "--root", config["root"]]
        assert not kwargs.get("shell", False)
        result = ingress().receive(server, kwargs["stdin"])
        kwargs["stdout"].write(json.dumps(result).encode())
        return subprocess.CompletedProcess(argv, 0)
    install_process(monkeypatch, transport)
    result = runner.invoke(app, ["media", "transfer", manifest["digest"], "--brand", "MarcaA", "--root", str(local.raiz.parent)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["private_ready"] is True
    assert (server.dir_posts / "clip/post.yml").exists()


@pytest.mark.parametrize("payload", [b"password=SECRET", b'{"protocol":"unknown","token":"SECRET"}', b'x' * (300 * 1024)], ids=["secret", "unknown", "oversize"])
def test_remote_failure_or_untrusted_stdout_never_leaks_secrets(tmp_path, monkeypatch, payload):
    brand = supplied(tmp_path)
    manifest = registry().stage(brand, "clip")
    remote(brand)
    def transport(argv, **kwargs):
        kwargs["stdout"].write(payload)
        return subprocess.CompletedProcess(argv, 0)
    install_process(monkeypatch, transport)
    result = runner.invoke(app, ["media", "transfer", manifest["digest"], "--brand", "MarcaA", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "SECRET" not in result.output and len(result.output) < 1000


def test_stage_errors_never_print_paths_containing_credentials(tmp_path):
    brand = supplied(tmp_path)
    post = brand.dir_posts / "clip/post.yml"
    post.write_text(post.read_text().replace("source.mp4", "../../password=SECRET.mp4"))
    result = runner.invoke(app, ["media", "stage", "clip", "--brand", "MarcaA", "--root", str(tmp_path)])
    assert result.exit_code == 1 and "SECRET" not in result.output


def test_remote_attestation_must_match_selected_supplied_assets(tmp_path, monkeypatch):
    from socialctl.scheduler import now_utc
    brand = supplied(tmp_path)
    manifest = registry().stage(brand, "clip")
    remote(brand)
    def transport(argv, **kwargs):
        result = dict(protocol="socialctl.media-readiness.v1", brand="MarcaA", bundle=manifest["digest"],
            local_ready=True, private_ready=True, hosted_ready=True, public_ready=True,
            checked_at=now_utc().isoformat(), attestations=[dict(url="https://media.example/unrelated.mp4",
                sha256="0" * 64, size=1, mime="video/mp4")])
        kwargs["stdout"].write(json.dumps(result).encode())
        return subprocess.CompletedProcess(argv, 0)
    install_process(monkeypatch, transport)
    result = runner.invoke(app, ["media", "verify", manifest["digest"], "--remote", "--public", "--brand", "MarcaA", "--root", str(tmp_path)])
    assert result.exit_code == 1, result.output


def test_cutover_remote_marker_allows_new_client_stage_and_transfer(tmp_path, monkeypatch):
    from socialctl.scheduler import ScheduleStore
    from tests.test_media_registry import DATA
    local, old_manifest, server, public = setup(tmp_path)
    remote(local)
    supplied_post = local.dir_posts / "clip/post.yml"
    supplied_post.write_text(supplied_post.read_text().replace("Supplied text", "Future supplied text"))
    original = supplied_post.read_bytes()
    result = runner.invoke(app, ["media", "stage", "clip", "--brand", "MarcaA", "--root", str(local.raiz.parent)])
    assert result.exit_code == 0, result.output
    bundle = json.loads(result.output)["bundle"]
    assert bundle != old_manifest["digest"]
    def transport(argv, **kwargs):
        result = ingress().receive(server, kwargs["stdin"])
        kwargs["stdout"].write(json.dumps(result).encode())
        return subprocess.CompletedProcess(argv, 0)
    install_process(monkeypatch, transport)
    result = runner.invoke(app, ["media", "transfer", bundle, "--brand", "MarcaA", "--root", str(local.raiz.parent)])
    assert result.exit_code == 0, result.output
    assert supplied_post.read_bytes() == original
    assert (local.raiz / "media/source.mp4").read_bytes() == DATA
    assert ScheduleStore(local.raiz).load() == []
    assert b"Future supplied text" in (server.dir_posts / "clip/post.yml").read_bytes()


def test_full_supported_draft_update_preview_is_not_truncated_by_header_cap(tmp_path, monkeypatch):
    local, manifest, server, public = setup(tmp_path)
    post = local.dir_posts / "clip/post.yml"
    post.write_text(post.read_text().replace("Supplied text", "new " * 40000))
    manifest = registry().stage(local, "clip")
    existing = server.dir_posts / "clip/post.yml"
    existing.parent.mkdir()
    existing.write_text("old " * 40000)
    remote(local)
    def transport(argv, **kwargs):
        result = ingress().receive(server, kwargs["stdin"], preview_update=True)
        kwargs["stdout"].write(json.dumps(result).encode())
        return subprocess.CompletedProcess(argv, 0)
    install_process(monkeypatch, transport)
    result = runner.invoke(app, ["media", "transfer", manifest["digest"], "--preview-update", "--brand", "MarcaA", "--root", str(local.raiz.parent)])
    assert result.exit_code == 0
    assert "old " * 40000 in json.loads(result.output)["preview"]
    assert "new " * 40000 in json.loads(result.output)["preview"]


@pytest.mark.parametrize("url", ["https://unrelated.example/MarcaB/wrong.mp4",
                                "https://media.example/MarcaB/{asset}",
                                "https://unrelated.example/MarcaA/{asset}",
                                "https://media.example/MarcaA/wrong.mp4"])
def test_public_attestation_binds_trusted_brand_base_and_immutable_path(tmp_path, url):
    from socialctl.media_cli import _response
    from socialctl.scheduler import now_utc
    brand = supplied(tmp_path)
    brand.cuentas["instagram"]["media_url_base"] = "https://media.example/MarcaA"
    manifest = registry().stage(brand, "clip")
    item = manifest["assets"][0]
    result = dict(protocol="socialctl.media-readiness.v1", brand="MarcaA", bundle=manifest["digest"],
        local_ready=True, private_ready=True, hosted_ready=True, public_ready=True,
        checked_at=now_utc().isoformat(), attestations=[dict(url=url.format(asset=item["relative_path"]),
            sha256=item["sha256"], size=item["size"], mime=item["mime"])])
    with pytest.raises(ValueError):
        _response(result, brand, manifest["digest"])
    result["attestations"][0]["url"] = "https://media.example/MarcaA/" + item["relative_path"]
    assert _response(result, brand, manifest["digest"])["public_ready"] is True


def test_ssh_response_limit_is_enforced_before_remote_finishes(tmp_path, monkeypatch):
    from socialctl import media_cli
    brand = supplied(tmp_path)
    manifest = registry().stage(brand, "clip")
    remote(brand)
    monkeypatch.setattr(media_cli, "MAX_RESPONSE", 1024)
    produced, completed = [], []
    def transport(argv, **kwargs):
        for _ in range(10000):
            kwargs["stdout"].write(b"x" * 4096)
            produced.append(4096)
        completed.append(True)
        return subprocess.CompletedProcess(argv, 0)
    processes = install_process(monkeypatch, transport)
    result = runner.invoke(app, ["media", "verify", manifest["digest"], "--remote", "--brand", "MarcaA", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert len(processes) == 1 and processes[0].killed
    assert not completed and sum(produced) < 128 * 1024
    assert not processes[0].thread.is_alive()


def test_ssh_timeout_kills_and_reaps_without_disclosing_output(tmp_path, monkeypatch):
    import io
    from socialctl import media_cli
    from socialctl.media_registry import MediaRegistryError
    release = threading.Event()
    def transport(argv, **kwargs):
        # Keep stdout open without producing bytes until the fake is killed.
        assert release.wait(5)
        return subprocess.CompletedProcess(argv, 0)
    processes = install_process(monkeypatch, transport)
    process_factory = subprocess.Popen
    def factory(*args, **kwargs):
        process = process_factory(*args, **kwargs)
        original_kill = process.kill
        def kill():
            original_kill()
            release.set()
        process.kill = kill
        return process
    monkeypatch.setattr(subprocess, "Popen", factory)
    try:
        with pytest.raises(MediaRegistryError, match='time'):
            media_cli._capture(["fake-ssh"], io.BytesIO(), timeout=0.01)
    finally:
        release.set()
    assert processes[0].killed and not processes[0].thread.is_alive()


def test_nonzero_ssh_exit_discards_untrusted_diagnostic_output(tmp_path, monkeypatch):
    import io
    from socialctl import media_cli
    from socialctl.media_registry import MediaRegistryError
    def transport(argv, **kwargs):
        kwargs["stdout"].write(b"password=SECRET")
        return subprocess.CompletedProcess(argv, 255)
    install_process(monkeypatch, transport)
    with pytest.raises(MediaRegistryError) as captured:
        media_cli._capture(["fake-ssh"], io.BytesIO())
    assert "SECRET" not in str(captured.value)
