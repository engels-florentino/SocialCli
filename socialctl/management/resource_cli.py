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
    return ("DRY-RUN — PREVIEW COMPLETO del recurso suministrado\n"
        + _json(change.model_dump(mode="json"))
        + "\nSe enviarán los bytes exactos de asset.sha256, sin generación, sincronización ni conversión."
        "\nMiniatura: respuesta y URLs no prueban sustitución visual ni restauración de bytes originales."
        "\nSubtítulo: SRT/VTT/SBV/SUB/MPSUB/LRC/SMI/SAMI/RT/TTML/DFXP/SCC UTF-8: comprobación básica; "
        "CAP/TDS/CIN/STL/ASC: solo extensión/tamaño/huella, validación del proveedor pendiente."
        "\nBackup: conserva bytes de respuesta original de la API, que puede haberlos normalizado."
        "\ncaption-delete elimina exclusivamente el track ID indicado; no se recrea automáticamente."
        "\nResultados inciertos nunca repiten escrituras. Permisos efectivos desconocidos hasta la operación."
        f"\nHuella exacta para aprobar: {change.fingerprint}")


def register(content_app, default_root):
    from socialctl.management.cli import _brand, _fail

    assets = typer.Typer(help="Miniaturas y subtítulos suministrados de vídeos propios; aprobación durable.")
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
            "limitation": "captions.list no documenta paginación; permisos actuales no prueban permiso de escritura."}))

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
            "limitation": "Sin tfmt/tlang. El proveedor puede haber normalizado el original; no se sobrescriben archivos."}))

    @assets.command("prepare")
    def prepare(video_id: str, action: str = typer.Option(..., "--action", help="thumbnail-set, caption-insert, caption-update o caption-delete"),
                file: Path | None = typer.Option(None, "--file"),
                track_id: str | None = typer.Option(None, "--track-id"),
                language: str | None = typer.Option(None, "--language"),
                name: str | None = typer.Option(None, "--name"),
                draft: bool | None = typer.Option(None, "--draft/--no-draft", help="Obligatorio en insert; omitir en update conserva el valor."),
                brand: str = typer.Option(..., "--brand"),
                root: Path = typer.Option(default_root, "--root"),
                dry_run: bool = typer.Option(False, "--dry-run")):
        """Prepara y muestra siempre un dry-run completo, sin escritura remota."""
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
            approval = typer.prompt("Escribe la huella exacta para aprobar")
            if approval != change.fingerprint:
                _fail("aprobación no coincide con la huella exacta")
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
        """Solo relectura remota: nunca reenvía la subida, update ni delete."""
        selected = _brand(root, brand)
        try:
            with make_http_client() as http:
                result = reconcile_resource(YouTubeResourcesClient(selected, http), ResourceStore(selected.raiz), change_id)
        except (ChangeError, OSError) as exc:
            _fail(str(exc))
        typer.echo(_json(result.model_dump(mode="json")))
