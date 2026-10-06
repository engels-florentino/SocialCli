"""Supplied YouTube thumbnail/caption commands, exact durable approval only."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import typer

from socialctl.management.changes import ChangeError
from socialctl.management.resource_changes import (
    ResourceStore, apply_resource, digest, prepare_resource, reconcile_resource, save_download,
)
from socialctl.management.youtube_resources import YouTubeResourcesClient


def make_http_client():
    return httpx.Client(timeout=60.0, follow_redirects=False)


def _json(value):
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True)


def render_resource_preview(change):
    return ("DRY-RUN — FULL PREVIEW of supplied resource\n"
        + _json(change.model_dump(mode="json"))
        + "\nExact asset.sha256 bytes will be sent without generation, synchronization or conversion."
        "\nThumbnail: response and URLs do not prove visual replacement or restoration of original bytes."
        "\nCaptions: supplied UTF-8 SRT/VTT/SBV/SUB/MPSUB/LRC/SMI/SAMI/RT/TTML/DFXP/SCC: basic validation; "
        "CAP/TDS/CIN/STL/ASC: extension/size/fingerprint only, provider validation pending."
        "\nBackup: preserves original API response bytes, which may have been normalized."
        "\ncaption-delete removes only specified track ID; it is not recreated automatically."
        "\nUncertain outcomes never repeat writes. Effective permissions unknown until operation."
        f"\nExact fingerprint for approval: {change.fingerprint}")


def register(content_app, default_root):
    from socialctl.management.cli import _brand, _fail

    assets = typer.Typer(help="Supplied thumbnails and captions for owned videos; durable approval.")
    content_app.add_typer(assets, name="youtube-assets")

    @assets.command("captions-list")
    def captions_list(video_id: str, brand: str = typer.Option(..., "--brand"),
                      root: Path = typer.Option(default_root, "--root")):
        selected = _brand(root, brand)
        try:
            with make_http_client() as http:
                rows = YouTubeResourcesClient(selected, http).list_captions(video_id)
        except (ChangeError, OSError) as exc:
            _fail(str(exc))
        typer.echo(_json({"items": rows, "absence_proven": False,
            "limitation": "captions.list does not document pagination; current permissions do not prove write permission."}))

    @assets.command("captions-download")
    def captions_download(video_id: str, track_id: str,
                          output: Path = typer.Option(..., "--output"),
                          brand: str = typer.Option(..., "--brand"),
                          root: Path = typer.Option(default_root, "--root")):
        selected = _brand(root, brand)
        try:
            with make_http_client() as http:
                data = YouTubeResourcesClient(selected, http).download_caption(video_id, track_id)
            save_download(output, data)
        except (ChangeError, OSError) as exc:
            _fail(str(exc))
        typer.echo(_json({"path": str(output.absolute()), "sha256": digest(data), "size": len(data),
            "video_id": video_id, "track_id": track_id, "fidelity": "api_original_response_bytes",
            "limitation": "Without tfmt/tlang. Provider may have normalized original; files are not overwritten."}))

    @assets.command("prepare")
    def prepare(video_id: str, action: str = typer.Option(..., "--action", help="thumbnail-set, caption-insert, caption-update or caption-delete"),
                file: Path | None = typer.Option(None, "--file"),
                track_id: str | None = typer.Option(None, "--track-id"),
                language: str | None = typer.Option(None, "--language"),
                name: str | None = typer.Option(None, "--name"),
                draft: bool | None = typer.Option(None, "--draft/--no-draft", help="Required for insert; omission on update preserves value."),
                brand: str = typer.Option(..., "--brand"),
                root: Path = typer.Option(default_root, "--root"),
                dry_run: bool = typer.Option(False, "--dry-run")):
        """Prepare and display complete dry-run without remote writes."""
        selected = _brand(root, brand)
        try:
            with make_http_client() as http:
                change = prepare_resource(YouTubeResourcesClient(selected, http), ResourceStore(selected.raiz),
                    action=action, video_id=video_id, file=file, track_id=track_id,
                    language=language, name=name, draft=draft)
        except (ChangeError, OSError) as exc:
            _fail(str(exc))
        typer.echo(render_resource_preview(change))

    @assets.command("status")
    def status(change_id: str, brand: str = typer.Option(..., "--brand"),
               root: Path = typer.Option(default_root, "--root")):
        selected = _brand(root, brand)
        try:
            change = ResourceStore(selected.raiz).load(change_id)
        except ChangeError as exc:
            _fail(str(exc))
        typer.echo(_json(change.model_dump(mode="json")))

    @assets.command("apply")
    def apply(change_id: str, brand: str = typer.Option(..., "--brand"),
              root: Path = typer.Option(default_root, "--root")):
        selected = _brand(root, brand)
        store = ResourceStore(selected.raiz)
        try:
            change = store.load(change_id)
            typer.echo(render_resource_preview(change))
            approval = typer.prompt("Enter the exact fingerprint to approve")
            if approval != change.fingerprint:
                _fail("approval does not match exact fingerprint")
            with make_http_client() as http:
                result = apply_resource(YouTubeResourcesClient(selected, http), store, change_id, approval)
        except (ChangeError, OSError) as exc:
            _fail(str(exc))
        typer.echo(_json(result.model_dump(mode="json")))
        if result.status != "verified":
            raise typer.Exit(1)

    @assets.command("reconcile")
    def reconcile(change_id: str, brand: str = typer.Option(..., "--brand"),
                  root: Path = typer.Option(default_root, "--root")):
        """Remote rereads only; never resend upload, update or delete."""
        selected = _brand(root, brand)
        try:
            with make_http_client() as http:
                result = reconcile_resource(YouTubeResourcesClient(selected, http), ResourceStore(selected.raiz), change_id)
        except (ChangeError, OSError) as exc:
            _fail(str(exc))
        typer.echo(_json(result.model_dump(mode="json")))
