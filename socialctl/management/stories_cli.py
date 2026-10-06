"""CLI seguro y auditable para preparar, aplicar y verificar Stories."""
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
    help="Stories durables de Instagram y handoff explícito para Facebook Page Stories."
)


def make_http_client() -> httpx.Client:
    return httpx.Client(timeout=30.0, follow_redirects=False)


def _json(value) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def render_story_preview(change) -> str:
    delivery = (
        "Instagram: se enviará media_type=STORIES; el texto es referencia editorial y no se envía. "
        "La API pública está documentada, pero el grant de escritura de esta cuenta no está verificado."
        if change.story.platform is Platform.INSTAGRAM
        else "Facebook: Page Stories tiene API pública, pero socialctl aún no implementa su flujo distinto; apply devolverá handoff_required sin POST."
    )
    return (
        "PREVIEW COMPLETO — Story independiente\n"
        + _json(change)
        + f"\n{delivery}"
        + "\nLa media local no se modifica, genera ni reencodea."
        + f"\nHuella exacta para aprobar: {change.fingerprint}"
    )


def _expiry(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc) + timedelta(hours=24)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise StoryError("--expires-at debe ser una fecha ISO 8601 con zona horaria") from None
    if parsed.utcoffset() is None:
        raise StoryError("--expires-at debe incluir zona horaria")
    return parsed


@story_app.command("prepare")
def prepare(
    platform: Platform = typer.Option(..., "--platform", help="instagram o facebook."),
    media: str = typer.Option(..., "--media", help="Ruta relativa dentro de <Marca>/media/."),
    media_url: str | None = typer.Option(None, "--media-url", help="URL pública HTTPS exacta; obligatoria para Instagram."),
    text: str | None = typer.Option(None, "--text", help="Texto editorial opcional; Instagram no lo recibe en F2."),
    expires_at: str | None = typer.Option(None, "--expires-at", help="Caducidad ISO 8601; por defecto, 24 horas."),
    source_video_id: str | None = typer.Option(None, "--source-video-id", "--youtube-video-id", help="ID opcional del largo de YouTube relacionado."),
    brand: str = typer.Option(..., "--brand", help="Marca propietaria; nunca se infiere."),
    root: Path = typer.Option(DEFAULT_ROOT, "--root", help="Raíz del proyecto Social."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Prepara y muestra el ChangeSet local; nunca escribe en Meta."),
) -> None:
    """Valida y persiste una propuesta local; no hace ninguna petición remota."""
    selected = _brand(root, brand)
    try:
        path = validar_ruta_relativa(
            selected.raiz / "media", media, StoryError, "media de Story"
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
    typer.echo("DRY-RUN remoto: propuesta local persistida; ninguna escritura en Meta.")
    typer.echo(render_story_preview(change))


@story_app.command("status")
def status(
    change_id: str,
    brand: str = typer.Option(..., "--brand", help="Marca propietaria; nunca se infiere."),
    root: Path = typer.Option(DEFAULT_ROOT, "--root", help="Raíz del proyecto Social."),
) -> None:
    """Muestra el ChangeSet y su diario sin credenciales ni llamadas remotas."""
    selected = _brand(root, brand)
    try:
        change = StoryStore(selected.raiz).load(change_id)
    except ChangeError as exc:
        _fail(str(exc))
    typer.echo(_json(change))


@story_app.command("apply")
def apply(
    change_id: str,
    approval_digest: str | None = typer.Option(None, "--approval-digest", help="Huella exacta mostrada por prepare."),
    brand: str = typer.Option(..., "--brand", help="Marca propietaria; nunca se infiere."),
    root: Path = typer.Option(DEFAULT_ROOT, "--root", help="Raíz del proyecto Social."),
) -> None:
    """Exige la huella exacta y aplica sin repetir ningún POST incierto."""
    selected = _brand(root, brand)
    store = StoryStore(selected.raiz)
    try:
        prepared = store.load(change_id)
        typer.echo(render_story_preview(prepared))
        digest = approval_digest or typer.prompt("Escribe la huella exacta para aprobar")
        if digest != prepared.fingerprint:
            raise StoryError("la aprobación no coincide con la huella exacta de la Story")
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
    brand: str = typer.Option(..., "--brand", help="Marca propietaria; nunca se infiere."),
    root: Path = typer.Option(DEFAULT_ROOT, "--root", help="Raíz del proyecto Social."),
) -> None:
    """Hace solo GET para verificar un ID remoto ya persistido; nunca publica."""
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
