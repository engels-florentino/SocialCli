"""Named owned YouTube operations; preparation always shows the entire dry-run."""
import json
from pathlib import Path

import httpx
import typer

from socialctl.management.changes import ChangeError
from socialctl.management.owned_schema import load_owned_edit
from socialctl.management.owned_changes import OwnedStore, prepare_owned, apply_owned, reconcile_owned
from socialctl.management.youtube_owned import PARTS, YouTubeOwnedClient


def make_http_client():
    return httpx.Client(timeout=60.0, follow_redirects=False)


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def render_owned_preview(change):
    return ("DRY-RUN — FULL PREVIEW of owned YouTube resource\n"
        + _json(change.model_dump(mode="json"))
        + "\nApproval covers complete before, after, effects, plan and bytes with their SHA256."
        + "\nUncertain outcomes are not repeated with another UUID. Reconcile only rereads; does not restore or continue effects."
        + f"\nExact fingerprint for approval: {change.fingerprint}")


def register(content_app, default_root):
    from socialctl.management.cli import _brand, _fail

    owned = typer.Typer(help="Playlists, channel, sections and owned resources; strict proposals and durable approval.")
    content_app.add_typer(owned, name="youtube-owned")

    def read(root, brand, callback):
        selected = _brand(root, brand)
        try:
            with make_http_client() as http:
                result = callback(YouTubeOwnedClient(selected, http))
        except (ChangeError, OSError) as exc:
            _fail(str(exc))
        typer.echo(_json(result))

    @owned.command("playlists-list")
    def playlists_list(brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root"),
                       max_pages: int = typer.Option(100, "--max-pages", min=1, max=100)):
        read(root, brand, lambda client: client.list_playlists(max_pages=max_pages))

    @owned.command("items-list")
    def items_list(playlist_id: str, brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root"),
                   max_pages: int = typer.Option(100, "--max-pages", min=1, max=100)):
        read(root, brand, lambda client: client.list_items(playlist_id, max_pages=max_pages))

    @owned.command("sections-list")
    def sections_list(brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root")):
        read(root, brand, lambda client: client.list_sections())

    @owned.command("images-list")
    def images_list(playlist_id: str, brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root"),
                    max_pages: int = typer.Option(100, "--max-pages", min=1, max=100)):
        read(root, brand, lambda client: client.list_images(playlist_id, max_pages=max_pages))

    @owned.command("channel-show")
    def channel_show(channel_id: str, parts: str = typer.Option(PARTS["channels"], "--parts"),
                     brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root")):
        read(root, brand, lambda client: client.inspect_channel(channel_id, parts=parts.split(",")))

    @owned.command("video-show")
    def video_show(video_id: str, parts: str = typer.Option("snippet,recordingDetails", "--parts"),
                   brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root")):
        read(root, brand, lambda client: client.inspect_video(video_id, parts=parts.split(",")))

    @owned.command("prepare")
    def prepare(file: Path = typer.Option(..., "--file", help="YAML version1, action and specific fields; never arbitrary endpoint/payload."),
                brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root"),
                dry_run: bool = typer.Option(False, "--dry-run")):
        """Prepare locally and always display full dry-run without writing to YouTube."""
        selected = _brand(root, brand)
        try:
            edit = load_owned_edit(file)
            with make_http_client() as http:
                change = prepare_owned(YouTubeOwnedClient(selected, http), OwnedStore(selected.raiz), edit)
        except (ChangeError, OSError) as exc:
            _fail(str(exc))
        typer.echo(render_owned_preview(change))

    @owned.command("status")
    def status(change_id: str, brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root")):
        selected = _brand(root, brand)
        try:
            change = OwnedStore(selected.raiz).load(change_id)
        except ChangeError as exc:
            _fail(str(exc))
        typer.echo(_json(change.model_dump(mode="json")))

    @owned.command("apply")
    def apply(change_id: str, brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root")):
        selected = _brand(root, brand)
        store = OwnedStore(selected.raiz)
        try:
            change = store.load(change_id)
            typer.echo(render_owned_preview(change))
            approval = typer.prompt("Enter the exact fingerprint to approve")
            if approval != change.fingerprint:
                _fail("approval does not match exact fingerprint")
            with make_http_client() as http:
                result = apply_owned(YouTubeOwnedClient(selected, http), store, change_id, approval)
        except (ChangeError, OSError) as exc:
            _fail(str(exc))
        typer.echo(_json(result.model_dump(mode="json")))
        if result.status != "verified":
            raise typer.Exit(1)

    @owned.command("reconcile")
    def reconcile(change_id: str, brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root")):
        """Remote reads only; never resend or continue writes."""
        selected = _brand(root, brand)
        try:
            with make_http_client() as http:
                result = reconcile_owned(YouTubeOwnedClient(selected, http), OwnedStore(selected.raiz), change_id)
        except (ChangeError, OSError) as exc:
            _fail(str(exc))
        typer.echo(_json(result.model_dump(mode="json")))
