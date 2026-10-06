"""CLI local para el handoff manual de publicaciones en Grupos de Facebook."""
from __future__ import annotations

import json
import os
import webbrowser
from datetime import datetime
from enum import Enum
from pathlib import Path

import typer

from socialctl.management.changes import ChangeError
from socialctl.management.cli import DEFAULT_ROOT, _brand, _fail
from socialctl.management.facebook_groups import (
    CONFIRMATION_PHRASE,
    GroupError,
    GroupStore,
    approve_handoff,
    confirm_group,
    export_queue,
    load_group,
    mark_handoff_opened,
    prepare_group,
    record_handoff_error,
)


group_app = typer.Typer(
    help="Prepara publicaciones manuales para Grupos; no usa ni emula la API retirada."
)


class ExportFormat(str, Enum):
    JSON = "json"
    TEXT = "text"


def _datetime(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise GroupError(f"{field} debe ser una fecha ISO 8601 con zona horaria") from None
    if parsed.utcoffset() is None:
        raise GroupError(f"{field} debe incluir zona horaria")
    return parsed


def _json(value) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def render_group_preview(change) -> str:
    if change.media:
        dimensions = f"{change.media.width}x{change.media.height}"
        duration = (
            f"{change.media.duration_s} s"
            if change.media.duration_s is not None
            else "no aplica"
        )
        media = (
            f"\n  Ruta: {change.media.relative_path}\n"
            f"  SHA-256: {change.media.sha256}\n"
            f"  Tamaño: {change.media.size_bytes} bytes\n"
            f"  Tipo: {change.media.kind}\n"
            f"  Dimensiones: {dimensions}\n"
            f"  Duración: {duration}"
        )
    else:
        media = " (sin media)"
    final_text = f"{change.copy_text}\n\n{change.youtube_url}"
    return (
        "PREVIEW COMPLETO — publicación manual en Grupo de Facebook\n"
        f"ChangeSet: {change.id}\n"
        f"Marca: {change.brand}\n"
        f"Grupo: {change.destination.name}\n"
        f"URL del Grupo: {change.destination.url}\n"
        f"Ventana sugerida: {change.suggested_window.start.isoformat()} → "
        f"{change.suggested_window.end.isoformat()}\n"
        f"Media existente:{media}\n"
        f"Estado: {change.status}\n\n"
        f"Texto final para copiar:\n{final_text}\n\n"
        "Límite: Meta retiró la API de Grupos. socialctl no hará ningún POST, "
        "scraping ni llamada a endpoints privados; el usuario publica manualmente.\n"
        f"Huella exacta para aprobar: {change.fingerprint}"
    )


def render_handoff(change) -> str:
    media = change.media.relative_path if change.media else "(sin media)"
    return (
        "HANDOFF DE NAVEGADOR — no confirma publicación\n"
        f"1. Abre: {change.destination.url}\n"
        f"2. Copia exactamente:\n{change.copy_text}\n\n{change.youtube_url}\n"
        f"3. Adjunta media existente: {media}\n"
        "4. Publica manualmente y guarda la URL del post o una captura/PDF.\n"
        f"5. Registra el resultado con: socialctl group confirm {change.id} "
        "--post-url URL --brand MARCA\n"
        "Hasta esa confirmación el estado no representa una publicación."
    )


def _render_text(items: list[dict]) -> str:
    if not items:
        return "Cola de Grupos vacía."
    chunks = []
    for item in items:
        media = (item.get("media") or {}).get("relative_path") or "(sin media)"
        confirmation = item.get("confirmation") or {}
        chunks.append(
            f"[{item['status']}] {item['group']['name']} — {item['id']}\n"
            f"Grupo: {item['group']['url']}\n"
            f"Ventana: {item['suggested_window']['start']} → {item['suggested_window']['end']}\n"
            f"Media: {media}\n"
            f"Copy:\n{item['copy']}\n\n{item['youtube_url']}\n"
            f"Confirmación aportada: {confirmation.get('post_url') or '(ninguna)'}\n"
            f"Recordatorio: {item['reminder']}"
        )
    return "\n\n".join(chunks)


def _write_new(path: Path, content: str) -> None:
    if ".secrets" in path.parts:
        raise GroupError("el export no puede escribirse dentro de .secrets")
    if not path.parent.exists() or not path.parent.is_dir():
        raise GroupError("la carpeta de destino del export no existe")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            if not content.endswith("\n"):
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        raise GroupError("el archivo de export ya existe; no se sobrescribe") from None
    except OSError as exc:
        raise GroupError(f"no se pudo escribir el export: {exc}") from None


@group_app.command("prepare")
def prepare(
    group_name: str = typer.Option(..., "--group-name", help="Nombre visible del Grupo destino."),
    group_url: str = typer.Option(..., "--group-url", help="URL HTTPS exacta /groups/GRUPO."),
    copy: str = typer.Option(..., "--copy", help="Texto exacto aportado para el post."),
    youtube_url: str = typer.Option(..., "--youtube-url", help="Enlace al documental largo."),
    window_start: str = typer.Option(..., "--window-start", help="Inicio ISO 8601 con zona horaria."),
    window_end: str = typer.Option(..., "--window-end", help="Fin ISO 8601 con zona horaria."),
    media: str | None = typer.Option(None, "--media", help="Ruta opcional dentro de <Marca>/media/."),
    brand: str = typer.Option(..., "--brand", help="Marca propietaria; nunca se infiere."),
    root: Path = typer.Option(DEFAULT_ROOT, "--root", help="Raíz del proyecto Social."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Persiste y muestra propuesta; nunca publica."),
) -> None:
    """Valida y persiste una propuesta local sin abrir navegador ni usar red."""
    selected = _brand(root, brand)
    try:
        change = prepare_group(
            selected,
            GroupStore(selected.raiz),
            group_name=group_name,
            group_url=group_url,
            copy=copy,
            youtube_url=youtube_url,
            window_start=_datetime(window_start, "window_start"),
            window_end=_datetime(window_end, "window_end"),
            media=media,
        )
    except (ChangeError, OSError, ValueError) as exc:
        _fail(str(exc))
    typer.echo("DRY-RUN remoto: propuesta local persistida; ninguna publicación en Facebook.")
    typer.echo(render_group_preview(change))


@group_app.command("status")
def status(
    change_id: str,
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
) -> None:
    """Muestra el ChangeSet y el diario local sin red."""
    selected = _brand(root, brand)
    try:
        change = load_group(selected, GroupStore(selected.raiz), change_id)
    except (ChangeError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(change))


@group_app.command("export")
def export(
    change_id: str | None = typer.Argument(None, help="UUID opcional; sin él exporta la cola completa."),
    format: ExportFormat = typer.Option(ExportFormat.TEXT, "--format"),
    output: Path | None = typer.Option(None, "--output", help="Archivo nuevo opcional; nunca se sobrescribe."),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
) -> None:
    """Exporta una vista JSON o legible, siempre desde el almacenamiento local."""
    selected = _brand(root, brand)
    try:
        items = export_queue(selected, GroupStore(selected.raiz), change_id)
        content = _json({"version": 1, "authority": "local_manual_handoff", "items": items}) if format is ExportFormat.JSON else _render_text(items)
        if output is not None:
            _write_new(output, content)
        else:
            typer.echo(content)
    except (ChangeError, OSError) as exc:
        _fail(str(exc))
    if output is not None:
        typer.echo(f"Export local escrito: {output}")


@group_app.command("handoff")
def handoff(
    change_id: str,
    approval_digest: str | None = typer.Option(None, "--approval-digest", help="Huella exacta de prepare."),
    open_browser: bool = typer.Option(False, "--open-browser", help="Abre una pestaña; no publica ni rellena formularios."),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
) -> None:
    """Aprueba el paquete y lo entrega al usuario para publicación manual."""
    selected = _brand(root, brand)
    store = GroupStore(selected.raiz)
    try:
        prepared = load_group(selected, store, change_id)
        typer.echo(render_group_preview(prepared))
        digest = approval_digest or typer.prompt("Escribe la huella exacta para aprobar")
        change = approve_handoff(selected, store, change_id, digest)
        typer.echo(render_handoff(change))
        if open_browser and change.status == "handoff_ready":
            try:
                opened = webbrowser.open_new_tab(change.destination.url)
            except OSError as exc:
                record_handoff_error(selected, store, change_id, f"no se pudo abrir el navegador: {exc}")
                raise GroupError("no se pudo abrir el navegador; el handoff sigue listo") from None
            if not opened:
                record_handoff_error(selected, store, change_id, "el navegador no confirmó la apertura")
                raise GroupError("el navegador no confirmó la apertura; el handoff sigue listo")
            change = mark_handoff_opened(selected, store, change_id)
            typer.echo("Pestaña abierta. socialctl no ha publicado ni rellenado el formulario.")
        elif open_browser:
            typer.echo("El handoff ya se abrió o ya fue confirmado; no se abre otra pestaña.")
    except (ChangeError, OSError) as exc:
        _fail(str(exc))


@group_app.command("confirm")
def confirm(
    change_id: str,
    post_url: str | None = typer.Option(None, "--post-url", help="URL del post en el mismo Grupo."),
    evidence: str | None = typer.Option(None, "--evidence", help="JPG/PNG/WebP/PDF existente, relativo a la marca."),
    confirmation: str | None = typer.Option(None, "--confirmation", help="Frase exacta de confirmación explícita."),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
) -> None:
    """Registra evidencia aportada por el usuario; nunca consulta ni publica en Facebook."""
    selected = _brand(root, brand)
    try:
        phrase = confirmation or typer.prompt(f"Escribe exactamente {CONFIRMATION_PHRASE}")
        change = confirm_group(
            selected,
            GroupStore(selected.raiz),
            change_id,
            confirmation_phrase=phrase,
            post_url=post_url,
            evidence=evidence,
        )
    except (ChangeError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(change))
    typer.echo("Confirmación manual registrada; autoridad: declaración/evidencia aportada por el usuario.")
