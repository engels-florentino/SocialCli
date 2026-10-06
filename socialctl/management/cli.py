"""Bounded CLI commands for inspection and metadata ChangeSets."""

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

content_app = typer.Typer(help="Inspect and prepare changes to existing content.")
changes_app = typer.Typer(help="Inspect and apply previously prepared ChangeSets.")


def make_http_client() -> httpx.Client:
    """Small construction hook for testing CLI without live network."""
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
    """Display complete final snippet and exact diff before approval."""
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
    ) or "(no differences)"
    return (
        f"ChangeSet: {change.id}\n"
        f"Target brand/channel: {change.target_account}\n"
        f"Video: {change.video_id}\n"
        f"Exact fingerprint for approval: {change.fingerprint}\n\n"
        f"Complete proposed snippet:\n{_json(change.after)}\n\n"
        f"Diff:\n{diff}\n\n"
        "V1 limitations: one video, snippet only. Batches and other parts use content edit-batch. "
        "Restoration proposes a new change with changes restore --dry-run."
    )


@content_app.command("show")
def content_show(
    video_id: str = typer.Argument(..., help="Remote YouTube video ID."),
    brand: str = typer.Option(
        ..., "--brand", help="Owning brand; never inferred."
    ),
    root: Path = typer.Option(DEFAULT_ROOT, "--root", help="Social project root."),
) -> None:
    """Display remote snippet after verifying authenticated channel and ownership."""
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
        ..., "--file", help="Version 1 YAML with video_id and patch."
    ),
    brand: str = typer.Option(
        ..., "--brand", help="Owning brand; never inferred."
    ),
    root: Path = typer.Option(DEFAULT_ROOT, "--root", help="Social project root."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Display proposal; never write to YouTube."
    ),
) -> None:
    """Prepare immutable local proposal for exactly one video."""
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
        typer.echo("Remote DRY-RUN: no YouTube writes performed.")
    else:
        typer.echo("Proposal prepared: this command does not write to YouTube.")
    typer.echo(render_change_preview(change))


@changes_app.command("status")
def changes_status(
    change_id: str = typer.Argument(..., help="ChangeSet UUID."),
    brand: str = typer.Option(
        ..., "--brand", help="Owning brand; never inferred."
    ),
    root: Path = typer.Option(DEFAULT_ROOT, "--root", help="Social project root."),
) -> None:
    """Display complete local proposal, state and journal."""
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
    change_id: str = typer.Argument(..., help="ChangeSet UUID."),
    brand: str = typer.Option(
        ..., "--brand", help="Owning brand; never inferred."
    ),
    root: Path = typer.Option(DEFAULT_ROOT, "--root", help="Social project root."),
    yes: bool = typer.Option(
        False, "--yes", help="Does not replace exact fingerprint approval."
    ),
) -> None:
    """Apply snippet after entering exact displayed fingerprint."""
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
        typer.echo("NOTICE: --yes does not replace exact approval of this ChangeSet.")
    try:
        approval = typer.prompt("Enter the exact fingerprint to approve")
    except (EOFError, KeyboardInterrupt):
        _fail("Application cancelled: exact approval was not received.")
    if approval != change.fingerprint:
        _fail("Application cancelled: approval does not match exact fingerprint.")

    try:
        with make_http_client() as client:
            result = apply_youtube_change(
                YouTubeManagementClient(selected, client), store, change_id, approval
            )
    except (ChangeError, YouTubeManagementError, OSError) as exc:
        _fail(str(exc))
    typer.echo(
        f"ChangeSet {result.id} applied and verified by reread. Status: {result.status}."
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
