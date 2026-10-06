"""Versioned read-only inventory/status commands; never interactive authorization."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import httpx
import typer

from socialctl.brands import cargar_brand
from socialctl.identity import SUPPORTED, ReadError, auth_status, configured_account
from socialctl.inventory import InventoryStore, sync_content
from socialctl.media_cli import _capture as capture_remote
from socialctl.media_registry import safe
from socialctl.models import Platform
from socialctl.schedule_remote import RemoteConfig, is_remote

from socialctl.workspace import default_root

DEFAULT_ROOT = default_root()


def make_http_client():
    return httpx.Client(timeout=30)


def emit(data, json_output):
    if json_output:
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


def guarded(operation, json_output):
    try:
        data = operation()
    except ReadError as exc:
        data = {"version": 1, "failure_class": exc.code}
    except Exception:
        data = {"version": 1, "failure_class": "local_state_invalid"}
    emit(data, json_output)
    if data.get("failure_class"):
        raise typer.Exit(1)


def show_auth_status(root, brand, platform, json_output):
    def operation():
        if platform is None:
            raise ReadError("platform_required")
        selected = cargar_brand(root, brand)
        with make_http_client() as client:
            return auth_status(selected, platform, client)
    guarded(operation, json_output)


def remote_health(brand):
    """Only the fixed server health read. Never local queue state as a proxy."""
    if not is_remote(brand.raiz):
        return {"authority": "local", "configured": False, "state": "not_probed"}
    result = {"authority": "remote", "configured": True, "state": "unknown"}
    try:
        path = safe(brand.raiz / ".socialctl/remote-executor.json")
        config = RemoteConfig.model_validate_json(path.read_bytes())
        command = shlex.join([config.executable, "schedule-health", "--brand", brand.nombre,
                              "--root", config.root])
        response = capture_remote(["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=15", "-p", str(config.port), "--", f"{config.user}@{config.host}", command],
            subprocess.DEVNULL, timeout=30)
        data = json.loads(response)
        if data.get("state") not in {"unknown", "started", "finished", "error"} or type(data.get("stale")) is not bool or data.get("backend") not in {"json", "sqlite"}:
            raise ReadError("remote_health_invalid")
        result.update({"state": data["state"], "stale": data["stale"], "backend": data["backend"], "failure_class": None})
    except ReadError as exc:
        result["failure_class"] = exc.code
    except Exception:
        result["failure_class"] = "remote_health_unavailable"
    return result


def register(app, content_app):
    @content_app.command("sync")
    def sync(platform: Platform = typer.Option(..., "--platform"),
             brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(DEFAULT_ROOT, "--root"),
             max_pages: int = typer.Option(20, "--max-pages", min=1, max=100),
             resume: bool = typer.Option(False, "--resume"), json_output: bool = typer.Option(False, "--json")):
        """Read owned content and save complete pages under verified identity."""
        def operation():
            selected = cargar_brand(root, brand)
            with make_http_client() as client:
                return sync_content(selected, platform, client, max_pages=max_pages, resume=resume)
        guarded(operation, json_output)

    def listing(root, brand, platform, account):
        selected = cargar_brand(root, brand)
        configured = configured_account(selected, platform)
        selected_account = account or configured
        store = InventoryStore(selected, platform, selected_account)
        state = store.load()
        run = state.get("sync") or {}
        return {"version": 1, "brand": brand, "platform": platform.value, "account_id": selected_account,
                "configured_account_id": configured, "historical_account": selected_account != configured,
                "authority": "cached_observation", "sync": {k: run.get(k) for k in (
                    "id", "complete", "failure_class", "updated_at", "pages", "source", "missing_resource_ids")},
                "items": store.public_items(state), "failure_class": None}

    @content_app.command("list")
    def list_content(platform: Platform = typer.Option(..., "--platform"),
                     brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(DEFAULT_ROOT, "--root"),
                     account: str | None = typer.Option(None, "--account", help="Explicit historical ID; never reassigned."),
                     json_output: bool = typer.Option(False, "--json")):
        """List latest local observations without network access."""
        guarded(lambda: listing(root, brand, platform, account), json_output)

    @content_app.command("export")
    def export(platform: Platform = typer.Option(..., "--platform"),
               brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(DEFAULT_ROOT, "--root"),
               account: str | None = typer.Option(None, "--account"),
               json_output: bool = typer.Option(False, "--json")):
        """Export public inventory fields to stdout; private raw data stays local."""
        guarded(lambda: listing(root, brand, platform, account), json_output)

    @app.command("doctor")
    def doctor(brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(DEFAULT_ROOT, "--root"),
               platform: Platform | None = typer.Option(None, "--platform"),
               json_output: bool = typer.Option(False, "--json")):
        """Check configuration, local media, identity and configured remote executor health."""
        def operation():
            selected = cargar_brand(root, brand)
            media = safe(selected.raiz / "media")
            media_status = {"directory_exists": media.is_dir(), "readable": os.access(media, os.R_OK),
                            "ffprobe_available": shutil.which("ffprobe") is not None,
                            "file_integrity": "not_checked"}
            platforms = [platform] if platform else [p for p in SUPPORTED if selected.cuentas.get(p.value)]
            with make_http_client() as client:
                statuses = [auth_status(selected, p, client) for p in platforms]
            queue = remote_health(selected)
            failed = not media_status["directory_exists"] or not media_status["readable"] or any(r["failure_class"] for r in statuses) or queue.get("failure_class") or queue.get("stale") or queue["state"] == "error"
            return {"version": 1, "brand": brand, "configuration": "loaded", "media": media_status,
                    "accounts": statuses, "queue": queue, "failure_class": "doctor_unhealthy" if failed else None}
        guarded(operation, json_output)

    @app.command("capabilities")
    def capabilities(brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(DEFAULT_ROOT, "--root"),
                     platform: str | None = typer.Option(None, "--platform"),
                     json_output: bool = typer.Option(False, "--json")):
        """Documented classification; does not indicate granted permissions or account availability."""
        from socialctl.capabilities import capability_report
        def operation():
            selected = cargar_brand(root, brand)
            return {**capability_report(platform), "brand": selected.nombre,
                    "account_eligibility": "not_probed", "failure_class": None}
        guarded(operation, json_output)
