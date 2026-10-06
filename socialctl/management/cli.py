"""Comandos CLI acotados para inspección y ChangeSets de metadatos."""

from __future__ import annotations

import difflib
import json
from pathlib import Path

import httpx
import typer

from socialctl.brands import (
    AccountsInvalido,
    Brand,
    BrandNoEncontrada,
    NombreDeMarcaInvalido,
    cargar_brand,
)
from socialctl.management.changes import (
    ChangeError,
    ChangeSet,
    ChangeStore,
    apply_youtube_change,
    load_edit_file,
    prepare_youtube_change,
)
from socialctl.management.youtube import YouTubeManagementClient, YouTubeManagementError
from socialctl.management.batches import BatchStore
from socialctl.management.batch_cli import apply_selected, register

from socialctl.workspace import default_root

DEFAULT_ROOT = default_root()

content_app = typer.Typer(help="Inspecciona y prepara cambios de contenido existente.")
changes_app = typer.Typer(help="Consulta y aplica ChangeSets previamente preparados.")


def make_http_client() -> httpx.Client:
    """Punto pequeño de construcción para poder probar el CLI sin red real."""
    return httpx.Client(timeout=30.0)


def _fail(message: str) -> None:
    typer.echo(message)
    raise typer.Exit(1)


def _brand(root: Path, name: str) -> Brand:
    try:
        return cargar_brand(root, name)
    except (BrandNoEncontrada, NombreDeMarcaInvalido, AccountsInvalido) as exc:
        _fail(str(exc))


def _json(data: object) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True)


def render_change_preview(change: ChangeSet) -> str:
    """Muestra el snippet final completo y un diff exacto antes de aprobar."""
    before = _json(change.before).splitlines()
    after = _json(change.after).splitlines()
    diff = "\n".join(
        difflib.unified_diff(
            before,
            after,
            fromfile="snippet actual",
            tofile="snippet propuesto",
            lineterm="",
        )
    ) or "(sin diferencias)"
    return (
        f"ChangeSet: {change.id}\n"
        f"Marca/canal objetivo: {change.target_account}\n"
        f"Vídeo: {change.video_id}\n"
        f"Huella exacta para aprobar: {change.fingerprint}\n\n"
        f"Snippet propuesto completo:\n{_json(change.after)}\n\n"
        f"Diff:\n{diff}\n\n"
        "Límites V1: un vídeo, solo snippet. Lotes y otras partes usan content edit-batch. "
        "Restauración propone un nuevo cambio con changes restore --dry-run."
    )


@content_app.command("show")
def content_show(
    video_id: str = typer.Argument(..., help="ID remoto del vídeo de YouTube."),
    brand: str = typer.Option(
        ..., "--brand", help="Marca propietaria; nunca se infiere."
    ),
    root: Path = typer.Option(DEFAULT_ROOT, "--root", help="Raíz del proyecto Social."),
) -> None:
    """Muestra el snippet remoto tras verificar canal autenticado y propiedad."""
    selected = _brand(root, brand)
    try:
        with make_http_client() as client:
            inspected = YouTubeManagementClient(selected, client).inspect(video_id)
    except (YouTubeManagementError, OSError) as exc:
        _fail(str(exc))
    typer.echo(
        _json(
            {
                "platform": "youtube",
                "video_id": inspected.video_id,
                "channel_id": inspected.channel_id,
                "etag": inspected.etag,
                "snippet": inspected.snippet,
            }
        )
    )


@content_app.command("edit")
def content_edit(
    file: Path = typer.Option(
        ..., "--file", help="YAML version 1 con video_id y patch."
    ),
    brand: str = typer.Option(
        ..., "--brand", help="Marca propietaria; nunca se infiere."
    ),
    root: Path = typer.Option(DEFAULT_ROOT, "--root", help="Raíz del proyecto Social."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Muestra propuesta; nunca escribe en YouTube."
    ),
) -> None:
    """Prepara una propuesta local inmutable para exactamente un vídeo."""
    selected = _brand(root, brand)
    try:
        edit = load_edit_file(file)
        with make_http_client() as client:
            change = prepare_youtube_change(
                YouTubeManagementClient(selected, client), ChangeStore(selected.raiz), edit
            )
    except (ChangeError, YouTubeManagementError, OSError) as exc:
        _fail(str(exc))
    if dry_run:
        typer.echo("DRY-RUN remoto: no se ha escrito nada en YouTube.")
    else:
        typer.echo("Propuesta preparada: este comando no escribe en YouTube.")
    typer.echo(render_change_preview(change))


@changes_app.command("status")
def changes_status(
    change_id: str = typer.Argument(..., help="UUID del ChangeSet."),
    brand: str = typer.Option(
        ..., "--brand", help="Marca propietaria; nunca se infiere."
    ),
    root: Path = typer.Option(DEFAULT_ROOT, "--root", help="Raíz del proyecto Social."),
) -> None:
    """Muestra propuesta, estado y diario local completos."""
    selected = _brand(root, brand)
    try:
        batch_store = BatchStore(selected.raiz)
        store = batch_store if batch_store.path_for(change_id).exists() else ChangeStore(selected.raiz)
        change = store.load(change_id)
    except ChangeError as exc:
        _fail(str(exc))
    typer.echo(_json(change.model_dump(mode="json")))


@changes_app.command("apply")
def changes_apply(
    change_id: str = typer.Argument(..., help="UUID del ChangeSet."),
    brand: str = typer.Option(
        ..., "--brand", help="Marca propietaria; nunca se infiere."
    ),
    root: Path = typer.Option(DEFAULT_ROOT, "--root", help="Raíz del proyecto Social."),
    yes: bool = typer.Option(
        False, "--yes", help="No sustituye la aprobación exacta de la huella."
    ),
) -> None:
    """Aplica un snippet tras escribir exactamente la huella mostrada."""
    selected = _brand(root, brand)
    store = ChangeStore(selected.raiz)
    try:
        if BatchStore(selected.raiz).path_for(change_id).exists():
            apply_selected(selected, change_id, yes)
            return
        change = store.load(change_id)
    except (ChangeError, YouTubeManagementError, OSError) as exc:
        _fail(str(exc))
    typer.echo(render_change_preview(change))
    if yes:
        typer.echo("AVISO: --yes no sustituye la aprobación exacta de este ChangeSet.")
    try:
        approval = typer.prompt("Escribe exactamente la huella para aprobar")
    except (EOFError, KeyboardInterrupt):
        _fail("Aplicación cancelada: no se recibió la aprobación exacta.")
    if approval != change.fingerprint:
        _fail("Aplicación cancelada: la aprobación no coincide con la huella exacta.")

    try:
        with make_http_client() as client:
            result = apply_youtube_change(
                YouTubeManagementClient(selected, client), store, change_id, approval
            )
    except (ChangeError, YouTubeManagementError, OSError) as exc:
        _fail(str(exc))
    typer.echo(
        f"ChangeSet {result.id} aplicado y verificado por relectura. Estado: {result.status}."
    )


register(content_app, changes_app, DEFAULT_ROOT)

from socialctl.management.resource_cli import register as register_resources

register_resources(content_app, DEFAULT_ROOT)

from socialctl.management.owned_cli import register as register_owned

register_owned(content_app, DEFAULT_ROOT)

from socialctl.management.community_cli import register as register_community

register_community(content_app, DEFAULT_ROOT)

from socialctl.metricas.analytics_cli import register as register_analytics

register_analytics(content_app, DEFAULT_ROOT)
