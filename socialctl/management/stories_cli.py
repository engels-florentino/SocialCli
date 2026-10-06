"""Safe, auditable CLI for preparing, applying and verifying Stories."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import typer
from pydantic import ValidationError

from socialctl.management.changes import ChangeError
from socialctl.management.cli import DEFAULT_ROOT, _brand, _fail
from socialctl.management.stories import (
    MetaStoryClient,
    StoryError,
    StoryStore,
    apply_story,
    prepare_story,
    verify_story,
)
from socialctl.media import MediaInvalida, MediaNoEncontrada, leer_media
from socialctl.models import Platform, StoryPost
from socialctl.rutas import validar_ruta_relativa


story_app = typer.Typer(
    help="Durable Instagram Stories and explicit handoff for Facebook Page Stories."
)


def make_http_client() -> httpx.Client:
    return httpx.Client(timeout=30.0, follow_redirects=False)


def _json(value) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def render_story_preview(change) -> str:
    delivery = (
        "Instagram: media_type=STORIES will be sent; text is editorial reference and is not sent. "
        "Public API is documented, but this account's write grant is unverified."
        if change.story.platform is Platform.INSTAGRAM
        else "Facebook: Page Stories has a public API, but socialcli does not implement its separate flow yet; apply returns handoff_required without POST."
    )
    return (
        "PREVIEW COMPLETO — Story independiente\n"
        + _json(change)
        + f"\n{delivery}"
        + "\nLocal media is not modified, generated or reencoded."
        + f"\nExact fingerprint for approval: {change.fingerprint}"
    )


def _expiry(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc) + timedelta(hours=24)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise StoryError("--expires-at must be an ISO 8601 timestamp with timezone") from None
    if parsed.utcoffset() is None:
        raise StoryError("--expires-at must include a timezone")
    return parsed


@story_app.command("prepare")
def prepare(
    platform: Platform = typer.Option(..., "--platform", help="instagram or facebook."),
    media: str = typer.Option(..., "--media", help="Relative path inside <Brand>/media/."),
    media_url: str | None = typer.Option(None, "--media-url", help="Exact public HTTPS URL; required for Instagram."),
    text: str | None = typer.Option(None, "--text", help="Optional editorial text; Instagram does not receive it in F2."),
    expires_at: str | None = typer.Option(None, "--expires-at", help="ISO 8601 expiry; default 24 hours."),
    source_video_id: str | None = typer.Option(None, "--source-video-id", "--youtube-video-id", help="Optional related long-form YouTube video ID."),
    brand: str = typer.Option(..., "--brand", help="Owning brand; never inferred."),
    root: Path = typer.Option(DEFAULT_ROOT, "--root", help="Social project root."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Prepare and display local ChangeSet; never write to Meta."),
) -> None:
    """Validate and persist local proposal without any remote request."""
    selected = _brand(root, brand)
    try:
        path = validar_ruta_relativa(
            selected.raiz / "media", media, StoryError, "Story media"
        )
        asset = leer_media(path, ruta_relativa=media)
        story = StoryPost(
            platform=platform,
            media=asset,
            public_url=media_url,
            text=text,
            expires_at=_expiry(expires_at),
            source_video_id=source_video_id,
        )
        change = prepare_story(selected, StoryStore(selected.raiz), story)
    except (ChangeError, MediaNoEncontrada, MediaInvalida, ValidationError) as exc:
        _fail(str(exc))
    typer.echo("Remote DRY-RUN: local proposal persisted; no Meta writes.")
    typer.echo(render_story_preview(change))


@story_app.command("status")
def status(
    change_id: str,
    brand: str = typer.Option(..., "--brand", help="Owning brand; never inferred."),
    root: Path = typer.Option(DEFAULT_ROOT, "--root", help="Social project root."),
) -> None:
    """Display ChangeSet and journal without credentials or remote calls."""
    selected = _brand(root, brand)
    try:
        change = StoryStore(selected.raiz).load(change_id)
    except ChangeError as exc:
        _fail(str(exc))
    typer.echo(_json(change))


@story_app.command("apply")
def apply(
    change_id: str,
    approval_digest: str | None = typer.Option(None, "--approval-digest", help="Exact fingerprint displayed by prepare."),
    brand: str = typer.Option(..., "--brand", help="Owning brand; never inferred."),
    root: Path = typer.Option(DEFAULT_ROOT, "--root", help="Social project root."),
) -> None:
    """Require exact fingerprint and apply without repeating uncertain POSTs."""
    selected = _brand(root, brand)
    store = StoryStore(selected.raiz)
    try:
        prepared = store.load(change_id)
        typer.echo(render_story_preview(prepared))
        digest = approval_digest or typer.prompt("Enter the exact fingerprint to approve")
        if digest != prepared.fingerprint:
            raise StoryError("approval does not match exact Story fingerprint")
        if prepared.story.platform is Platform.FACEBOOK:
            result = apply_story(None, store, change_id, digest, brand=selected)
        else:
            with make_http_client() as http:
                client = MetaStoryClient(selected, Platform.INSTAGRAM, http)
                result = apply_story(client, store, change_id, digest)
    except (ChangeError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(result))
    if result.status != "verified":
        raise typer.Exit(1)


@story_app.command("verify")
def verify(
    change_id: str,
    brand: str = typer.Option(..., "--brand", help="Owning brand; never inferred."),
    root: Path = typer.Option(DEFAULT_ROOT, "--root", help="Social project root."),
) -> None:
    """Use GET only to verify an already persisted remote ID; never publish."""
    selected = _brand(root, brand)
    store = StoryStore(selected.raiz)
    try:
        prepared = store.load(change_id)
        if prepared.story.platform is Platform.FACEBOOK:
            result = prepared
        else:
            with make_http_client() as http:
                result = verify_story(
                    MetaStoryClient(selected, Platform.INSTAGRAM, http), store, change_id
                )
    except (ChangeError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(result))
    if result.status != "verified":
        raise typer.Exit(1)
