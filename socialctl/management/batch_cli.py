"""Full batch previews and the same exact-digest approval used by V1 commands."""
from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

import typer

from socialctl.management.batches import BatchStore, apply_batch, prepare_batch, prepare_restore
from socialctl.management.changes import ChangeError
from socialctl.management.youtube import YouTubeManagementError
from socialctl.management.youtube_metadata import YouTubeMetadataClient


def render_batch_preview(batch):
    def formatted(value):
        return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)
    lines = [f"Lote V2: {batch.id}", f"Marca: {batch.brand} ({batch.brand_root})",
             f"Canal: {batch.account}", f"Huella exacta para aprobar: {batch.fingerprint}",
             "Archivos vinculados:\n" + formatted(batch.source_files)]
    if batch.restored_from:
        lines.append(f"Restauración propuesta de: {batch.restored_from}")
    for number, op in enumerate(batch.operations, 1):
        before, after = formatted(op.before), formatted(op.after)
        lines.extend([f"\nOperación {number}: {op.video_id} / {op.kind} / {op.status}",
                      f"Huella operación: {op.fingerprint}",
                      "Patch explícito:\n" + formatted(op.patch),
                      "Antes:\n" + before, "Después (cuerpo completo por parte):\n" + after,
                      "Diff:\n" + ("\n".join(difflib.unified_diff(before.splitlines(), after.splitlines(),
                            fromfile="actual", tofile="propuesto", lineterm="")) or "(sin diferencias)")])
        snippet = op.after.get("snippet", op.observed["snippet"])
        visible = re.findall(r"(?<!\w)#[\w]+", snippet.get("title", "") + "\n" + snippet.get("description", ""))
        lines.append("Tags internos: " + formatted(snippet.get("tags", [])))
        lines.append("Hashtags visibles en título/descripción: " + formatted(visible))
        if op.resets:
            lines.append("Resets explícitos: " + formatted(op.resets))
        if op.kind == "schedule":
            lines.append("Programación nativa YouTube: privado + nunca publicado declarado por usuario; "
                         "la API decide elegibilidad histórica. No es la cola del servidor.")
        if op.kind == "chapters":
            lines.append("Capítulos: texto suministrado validado contra duración API; no prueba habilitación de la interfaz.")
    lines.extend(["Restauración no disponible: " + formatted(batch.unavailable),
                  "Límites: solo metadatos; no restaura eliminaciones ni subidas de media. "
                  "defaultAudioLanguage se preserva; su edición no está verificada."])
    return "\n".join(lines)


def apply_selected(selected, change_id, yes):
    from socialctl.management import cli
    store = BatchStore(selected.raiz)
    batch = store.load(change_id)
    typer.echo("DRY-RUN completo antes de aprobar; no se ha escrito en YouTube.")
    typer.echo(render_batch_preview(batch))
    if yes:
        typer.echo("--yes no sustituye la aprobación exacta del lote.")
    try:
        approval = typer.prompt("Escribe exactamente la huella del lote para aprobar")
    except (EOFError, KeyboardInterrupt):
        cli._fail("Aplicación cancelada: falta aprobación exacta")
    if approval != batch.fingerprint:
        cli._fail("Aplicación cancelada: huella incorrecta")
    with cli.make_http_client() as http:
        result = apply_batch(YouTubeMetadataClient(selected, http), store, change_id, approval)
    for op in result.operations:
        typer.echo(f"{op.video_id}: {op.status}")
    if any(op.status != "applied" for op in result.operations):
        typer.echo("Resultado parcial. Reanudar solo procesa pendientes; intentos inciertos solo se releen.")
        raise typer.Exit(1)


def register(content_app, changes_app, default_root):
    @content_app.command("edit-batch")
    def content_edit_batch(
        file: Path = typer.Option(..., "--file"),
        brand: str = typer.Option(..., "--brand"),
        root: Path = typer.Option(default_root, "--root"),
        dry_run: bool = typer.Option(False, "--dry-run"),
    ):
        """Prepara un lote V2 de IDs explícitos; una operación por vídeo."""
        from socialctl.management import cli
        if not dry_run:
            cli._fail("Preparar un lote requiere --dry-run y su preview completo")
        selected = cli._brand(root, brand)
        try:
            with cli.make_http_client() as http:
                batch = prepare_batch(YouTubeMetadataClient(selected, http), BatchStore(selected.raiz), file)
        except (ChangeError, YouTubeManagementError, OSError) as exc:
            cli._fail(str(exc))
        typer.echo("DRY-RUN: no se ha escrito en YouTube.")
        typer.echo(render_batch_preview(batch))

    @changes_app.command("restore")
    def changes_restore(
        change_id: str = typer.Argument(...),
        brand: str = typer.Option(..., "--brand"),
        root: Path = typer.Option(default_root, "--root"),
        dry_run: bool = typer.Option(False, "--dry-run"),
    ):
        """Propone restaurar campos guardados sobre una lectura remota nueva."""
        from socialctl.management import cli
        if not dry_run:
            cli._fail("Restauración requiere --dry-run; después se aprueba la nueva huella")
        selected = cli._brand(root, brand)
        try:
            with cli.make_http_client() as http:
                batch = prepare_restore(YouTubeMetadataClient(selected, http), BatchStore(selected.raiz), change_id)
        except (ChangeError, YouTubeManagementError, OSError) as exc:
            cli._fail(str(exc))
        typer.echo("DRY-RUN: nueva propuesta local, sin escritura remota.")
        typer.echo(render_batch_preview(batch))
