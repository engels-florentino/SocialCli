"""Durable Meta management commands with exact approval and explicit reconciliation."""
from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from pathlib import Path

import httpx
import typer

from socialctl.management.cli import DEFAULT_ROOT, _brand, _fail
from socialctl.management.meta_changes import MetaStore, apply_meta, prepare_meta, reconcile_meta
from socialctl.management.meta_client import MetaClient
from socialctl.management.meta_schema import MetaError, load_edit
from socialctl.management.changes import ChangeError
from socialctl.models import Platform
from socialctl.management.meta_webhooks import WebhookStore, verify_challenge

meta_app = typer.Typer(help="Gestiona cambios explícitos de Meta con estado durable y huella exacta.")


def make_http_client():
    return httpx.Client(timeout=30.0, follow_redirects=False)


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def render_preview(change):
    return (
        "PREVIEW COMPLETO (meta-management)\n" + _json(change.model_dump(mode="json")) +
        "\nHuella exacta para aprobar: " + change.fingerprint
    )


@meta_app.command("prepare")
def prepare(
    edit_file: Path = typer.Option(..., "--file", help="YAML con acción Meta y campos requeridos."),
    platform: Platform = typer.Option(..., "--platform"),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Solo prepara y muestra la propuesta; no escribe en Meta."),
) -> None:
    """Prepara un cambio sin mutación remota y guarda un ChangeSet durable."""
    selected = _brand(root, brand)
    try:
        with make_http_client() as http:
            change = prepare_meta(MetaClient(selected, platform, http), MetaStore(selected.raiz),
                                  load_edit(edit_file))
    except (ChangeError, MetaError, OSError) as exc:
        _fail(str(exc))
    typer.echo("DRY-RUN remoto: propuesta local; ninguna escritura en Meta." if dry_run else "Propuesta preparada: la edición sigue sin enviar." )
    typer.echo(render_preview(change))


@meta_app.command("status")
def status(
    change_id: str = typer.Argument(...),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
) -> None:
    """Muestra propuesta y diario completo sin escribir."""
    selected = _brand(root, brand)
    try:
        change = MetaStore(selected.raiz).load(change_id)
    except (ChangeError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(change.model_dump(mode="json")))


@meta_app.command("apply")
def apply(
    change_id: str = typer.Argument(...),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
    yes: bool = typer.Option(False, "--yes", help="No sustituye la aprobación exacta."),
) -> None:
    """Requiere huella exacta; un write incierto nunca se reprocesa a ciegas."""
    selected = _brand(root, brand)
    store = MetaStore(selected.raiz)
    try:
        change = store.load(change_id)
        typer.echo(render_preview(change))
        if yes:
            typer.echo("AVISO: --yes no sustituye la aprobación exacta de este ChangeSet.")
        digest = typer.prompt("Escribe la huella exacta para aprobar")
        if not digest == change.fingerprint:
            _fail("aprobación no coincide con la huella exacta")
        with make_http_client() as http:
            result = apply_meta(MetaClient(selected, Platform(change.platform), http), store, change_id, digest)
    except (ChangeError, OSError, MetaError) as exc:
        _fail(str(exc))
    typer.echo(_json(result.model_dump(mode="json")))


@meta_app.command("reconcile")
def reconcile(
    change_id: str = typer.Argument(...),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
) -> None:
    """Solo lectura para reintento seguro tras cambios inciertos."""
    selected = _brand(root, brand)
    store = MetaStore(selected.raiz)
    try:
        change = store.load(change_id)
        with make_http_client() as http:
            result = reconcile_meta(MetaClient(selected, Platform(change.platform), http), store, change_id)
    except (ChangeError, OSError, MetaError) as exc:
        _fail(str(exc))
    typer.echo(_json(result.model_dump(mode="json")))


@meta_app.command("webhook-verify")
def webhook_verify(
    mode: str = typer.Option(..., "--mode", help="hub.mode recibido del servicio de verificación"),
    challenge: str = typer.Option(..., "--challenge", help="hub.challenge recibido"),
    expected_token: str = typer.Option(..., "--expected-token", help="token esperado configurado en Meta"),
    provided_token: str = typer.Option(..., "--provided-token", help="token recibido en la verificación"),
) -> None:
    """Verifica challenge y devuelve el mismo valor si es válido."""
    try:
        result = verify_challenge(mode=mode, provided_token=provided_token, challenge=challenge,
                                 expected_token=expected_token)
    except MetaError as exc:
        _fail(str(exc))
    typer.echo(result)


@meta_app.command("webhook-ingest")
def webhook_ingest(
    body_path: Path = typer.Option(..., "--body", help="Ruta al payload JSON crudo recibido por webhook (bytes exactos)"),
    platform: Platform = typer.Option(..., "--platform"),
    signature: str = typer.Option(..., "--signature", help="X-Hub-Signature-256: valor sin espacio"),
    app_secret: str = typer.Option(..., "--app-secret", help="Secreto de la app Meta"),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
) -> None:
    """Ingiere un payload webhook validado por HMAC; no escribe en Meta."""
    selected = _brand(root, brand)
    try:
        raw = body_path.read_bytes()
        store = WebhookStore(selected, platform.value)
        counts = store.ingest(raw=raw, signature=signature, app_secret=app_secret)
    except (ChangeError, MetaError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(counts))


@meta_app.command("webhook-events")
def webhook_events(
    platform: Platform = typer.Option(..., "--platform"),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """Lista eventos webhook persistidos localmente."""
    selected = _brand(root, brand)
    try:
        store = WebhookStore(selected, platform.value)
        rows = store.events(limit=limit)
    except (ChangeError, MetaError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(rows))


@meta_app.command("webhook-reconcile")
def webhook_reconcile(
    event_id: str = typer.Argument(..., help="ID del evento persistido (sha256 canónico)"),
    platform: Platform = typer.Option(..., "--platform"),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
) -> None:
    """Reconciliación de un evento por GET remoto; jamás realiza escrituras."""
    selected = _brand(root, brand)
    try:
        store = WebhookStore(selected, platform.value)
        with make_http_client() as http:
            result = store.reconcile(MetaClient(selected, platform, http), event_id)
    except (ChangeError, MetaError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(result))


@meta_app.command("identity")
def identity(
    platform: Platform = typer.Option(..., "--platform"),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
) -> None:
    """Muestra la identidad y cuenta vinculada tras validación de propiedad."""
    selected = _brand(root, brand)
    try:
        with make_http_client() as http:
            result = MetaClient(selected, platform, http).identity()
    except (ChangeError, MetaError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(result))


@meta_app.command("profile")
def profile(
    platform: Platform = typer.Option(..., "--platform"),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
    fields: str = typer.Option(None, "--fields", help="Campos separados por coma"),
) -> None:
    """Lee el perfil del nodo activo. Sin escrituras."""
    selected = _brand(root, brand)
    try:
        requested = None if fields is None else [part.strip() for part in fields.split(",") if part.strip()]
        with make_http_client() as http:
            result = MetaClient(selected, platform, http).profile(fields=requested)
    except (ChangeError, MetaError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(result))


@meta_app.command("content")
def content(
    target_id: str = typer.Argument(..., help="ID remoto (post/video/media)."),
    platform: Platform = typer.Option(..., "--platform"),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
    kind: str = typer.Option(
        None,
        "--kind",
        help="facebook: post|video, instagram: media",
    ),
) -> None:
    """Lee un contenido propio y verifica firma de propiedad remota."""
    selected = _brand(root, brand)
    try:
        with make_http_client() as http:
            result = MetaClient(selected, platform, http).content(target_id, kind=kind or None)
    except (ChangeError, MetaError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(result))


@meta_app.command("reactions")
def reactions(
    target_id: str = typer.Argument(..., help="ID remoto del contenido Facebook al que se leen reacciones."),
    platform: Platform = typer.Option(..., "--platform"),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
    max_pages: int = typer.Option(10, "--max-pages", min=1, max=50),
) -> None:
    """Lee reacciones de una publicación propia (solo Meta/Facebook)."""
    selected = _brand(root, brand)
    try:
        with make_http_client() as http:
            result = MetaClient(selected, platform, http).reactions(target_id, max_pages=max_pages)
    except (ChangeError, MetaError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(result))


@meta_app.command("content-list")
def content_list(
    platform: Platform = typer.Option(..., "--platform"),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
    edge: str = typer.Option(None, "--edge", help="feed|photos|posts|videos|video_reels|stories|scheduled_posts|media"),
    max_pages: int = typer.Option(10, "--max-pages"),
) -> None:
    """Lee un listado propio protegido por paginación acotada."""
    selected = _brand(root, brand)
    try:
        with make_http_client() as http:
            result = MetaClient(selected, platform, http).content_list(edge=edge or None, max_pages=max_pages)
    except (ChangeError, MetaError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(result))


@meta_app.command("insights")
def insights(
    target_id: str,
    metric: list[str] = typer.Option(..., "--metric", help="Métrica de contenido; repetir para varias."),
    platform: Platform = typer.Option(..., "--platform"),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
    period: str | None = typer.Option(None, "--period"),
    since: str | None = typer.Option(None, "--since", help="Fecha UTC AAAA-MM-DD."),
    until: str | None = typer.Option(None, "--until", help="Fecha UTC AAAA-MM-DD, exclusiva."),
    breakdown: list[str] = typer.Option(None, "--breakdown"),
    max_pages: int = typer.Option(10, "--max-pages", min=1, max=50),
) -> None:
    """Lee insights de contenido propio con métricas explícitas y paginación acotada."""
    def timestamp(value, label):
        if value is None:
            return None
        try:
            return int(datetime.combine(date.fromisoformat(value), time.min, tzinfo=timezone.utc).timestamp())
        except ValueError:
            _fail(f"--{label} debe ser una fecha AAAA-MM-DD válida")

    selected = _brand(root, brand)
    try:
        with make_http_client() as http:
            result = MetaClient(selected, platform, http).insights(
                target_id, metric, period=period,
                since=timestamp(since, "since"), until=timestamp(until, "until"),
                breakdown=breakdown or None, max_pages=max_pages,
            )
    except (ChangeError, MetaError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(result))
    if result.get("complete") is False:
        raise typer.Exit(1)


@meta_app.command("publishing-limit")
def publishing_limit(
    platform: Platform = typer.Option(..., "--platform"),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
) -> None:
    """Lee el límite de publicación documentado, sin escrituras."""
    selected = _brand(root, brand)
    try:
        with make_http_client() as http:
            result = MetaClient(selected, platform, http).content_publishing_limit()
    except (ChangeError, MetaError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(result))
