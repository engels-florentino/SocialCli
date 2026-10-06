"""Explicit media staging/SSH operations; ingestion never invokes a publisher."""
from __future__ import annotations

import json
import os
import re
import selectors
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import typer

from socialctl.brands import cargar_brand
from socialctl.media_ingress import receive as ingest, verify as readiness, write_transfer
from socialctl.media_registry import MAX_HEADER, MAX_POST, MediaRegistryError, component, load_bundle, parse_json, relative, stage as stage_bundle
from socialctl.schedule_remote import DEFAULT_ROOT, RemoteConfig

media_app = typer.Typer(help="Copia y transfiere media suministrada; no publica ni aprueba programación.")
MAX_RESPONSE = 12 * MAX_POST + MAX_HEADER  # Both full texts, worst-case JSON escaping.
SECRET = re.compile(r"(?i)(?:[\"']?(?:access[_-]?token|refresh[_-]?token|client[_-]?secret|password|authorization|token)[\"']?\s*[:=]|\bbearer\s+\S+)")


def _response(value, brand, ident):
    """Only bounded typed readiness/preview data may be printed from SSH."""
    if not isinstance(value, dict) or value.get("bundle") != ident:
        raise MediaRegistryError("respuesta remota inválida")
    if value.get("protocol") == "socialctl.media-update.v1":
        if (set(value) != {"protocol", "bundle", "preview", "update_digest"}
                or not isinstance(value["preview"], str) or SECRET.search(value["preview"])
                or not re.fullmatch(r"[a-f0-9]{64}", value["update_digest"])):
            raise MediaRegistryError("preview remoto inválido")
    elif value.get("protocol") == "socialctl.media-readiness.v1":
        if (set(value) != {"protocol", "brand", "bundle", "local_ready", "private_ready", "hosted_ready", "public_ready", "checked_at", "attestations"}
                or value["brand"] != brand.nombre
                or any(type(value[key]) is not bool for key in ("local_ready", "private_ready", "hosted_ready", "public_ready"))
                or not isinstance(value["attestations"], list)
                or SECRET.search(json.dumps(value))):
            raise MediaRegistryError("estado remoto inválido")
        from socialctl.scheduler import parse_scheduled_at
        from socialctl.hosted_media import MIMES, public_url, validate_url
        parse_scheduled_at(value["checked_at"])
        for item in value["attestations"]:
            if (set(item) != {"url", "sha256", "size", "mime"}
                    or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"])
                    or type(item["size"]) is not int or item["size"] <= 0 or item["mime"] not in MIMES.values()):
                raise MediaRegistryError("atestación remota inválida")
            validate_url(item["url"])
        manifest, _ = load_bundle(brand, ident)
        if value["public_ready"]:
            # The expected destination comes from trusted local brand config,
            # never from the remote response being authenticated.
            base = (brand.cuentas.get("instagram") or {}).get("media_url_base", "")
            expected = {(public_url(base, a["relative_path"]), a["sha256"], a["size"], a["mime"])
                        for a in manifest["assets"]}
            actual = {(a["url"], a["sha256"], a["size"], a["mime"]) for a in value["attestations"]}
            if actual != expected or len(value["attestations"]) != len(manifest["assets"]):
                raise MediaRegistryError("atestación no coincide con assets seleccionados")
            if not (value["local_ready"] and value["private_ready"] and value["hosted_ready"]):
                raise MediaRegistryError("estado remoto inconsistente")
        elif value["attestations"]:
            raise MediaRegistryError("estado remoto inconsistente")
    else:
        raise MediaRegistryError("protocolo remoto no admitido")
    return value


def _capture(argv, incoming, *, timeout=300):
    """Drain a bounded stdout pipe and kill/reap SSH on overflow or deadline."""
    process = subprocess.Popen(argv, stdin=incoming, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + timeout
    raw = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MediaRegistryError("respuesta remota excede tiempo límite")
                if not selector.select(remaining):
                    continue
                chunk = os.read(process.stdout.fileno(), min(65536, MAX_RESPONSE + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > MAX_RESPONSE:
                    raise MediaRegistryError("respuesta remota excede límite")
        if process.wait(timeout=max(0, deadline - time.monotonic())):
            raise MediaRegistryError("ingestión remota fallida; verifica antes de reintentar")
        return bytes(raw)
    finally:
        # Diagnostics are discarded, never buffered or reflected to the caller.
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()


def _remote(brand, ident, *, operation, preview_update=False, update_digest=None, public=False):
    if not re.fullmatch(r"[a-f0-9]{64}", ident):
        raise MediaRegistryError("bundle inválido")
    config = RemoteConfig.model_validate_json(relative(brand.raiz, ".socialctl/remote-executor.json").read_bytes())
    component(brand.nombre)
    args = ["media", operation]
    if operation == "verify":
        args += [ident]
        if public:
            args += ["--public"]
    elif operation == "receive":
        if preview_update:
            args += ["--preview-update"]
        if update_digest:
            if not re.fullmatch(r"[a-f0-9]{64}", update_digest) or preview_update:
                raise MediaRegistryError("digest de actualización inválido")
            args += ["--update-digest", update_digest]
    else:
        raise MediaRegistryError("operación no admitida")
    command = shlex.join([config.executable, *args, "--brand", brand.nombre, "--root", config.root])
    argv = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=15", "-p", str(config.port), "--", f"{config.user}@{config.host}", command]
    # Disk-backed bounded input; output is read incrementally into a capped buffer.
    with tempfile.TemporaryFile() as incoming:
        if operation == "receive":
            write_transfer(brand, ident, incoming)
            incoming.seek(0)
        raw = _capture(argv, incoming)
        return _response(parse_json(raw, limit=MAX_RESPONSE), brand, ident)


def _run(operation):
    try:
        result = operation()
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    except MediaRegistryError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from None
    except Exception:
        # Even parse errors and remote exceptions can contain supplied paths/secrets.
        typer.echo("Operación de media rechazada: revisa fuentes, manifest, política, espacio y conflictos de posts. Los conflictos exigen --preview-update y su digest; posts activos requieren cancelar/reconciliar la cola primero.")
        raise typer.Exit(1) from None


@media_app.command("stage")
def stage(slug: str, brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(DEFAULT_ROOT)):
    def operation():
        selected = cargar_brand(root, brand)
        manifest = stage_bundle(selected, slug)
        return readiness(selected, manifest["digest"])
    _run(operation)


@media_app.command("transfer")
def transfer(bundle: str, brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(DEFAULT_ROOT),
             preview_update: bool = typer.Option(False, "--preview-update"),
             update_digest: str | None = typer.Option(None, "--update-digest")):
    _run(lambda: _remote(cargar_brand(root, brand), bundle, operation="receive",
                        preview_update=preview_update, update_digest=update_digest))


@media_app.command("receive", hidden=True)
def receive(brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(DEFAULT_ROOT),
            preview_update: bool = typer.Option(False, "--preview-update"),
            update_digest: str | None = typer.Option(None, "--update-digest")):
    _run(lambda: ingest(cargar_brand(root, brand), sys.stdin.buffer,
                        preview_update=preview_update, update_digest=update_digest))


@media_app.command("verify")
def verify(bundle: str, brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(DEFAULT_ROOT),
           remote: bool = typer.Option(False, "--remote"), public: bool = typer.Option(False, "--public")):
    def operation():
        selected = cargar_brand(root, brand)
        if remote:
            return _remote(selected, bundle, operation="verify", public=public)
        return readiness(selected, bundle, public=public)
    _run(operation)
