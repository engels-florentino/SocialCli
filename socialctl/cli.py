"""The socialcli command-line interface."""

from __future__ import annotations

import http.server
import hashlib
import hmac
import json
import os
import plistlib
import shutil
import subprocess
import sys
import urllib.parse
import warnings
import webbrowser
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import typer
import yaml

from socialctl.authflow import (
    AYUDA_META,
    EstadoInvalido,
    canjear_codigo,
    construir_url_autorizacion,
    generar_code_verifier,
    generar_state,
    intercambiar_token_meta,
    obtener_paginas_meta,
    verificar_state,
)
from socialctl.brands import (
    AccountsInvalido,
    Brand,
    BrandNoEncontrada,
    BrandYaExiste,
    NombreDeMarcaInvalido,
    cargar_brand,
    crear_brand,
)
from socialctl.media import MediaInvalida, MediaNoEncontrada
from socialctl.media_cli import media_app
from socialctl.management.cli import changes_app, content_app
from socialctl.management.comments_cli import comments_app
from socialctl.management.meta_cli import meta_app
from socialctl.management.stories_cli import story_app
from socialctl.management.groups_cli import group_app
from socialctl.migration_cli import migration_app
from socialctl.schedule_remote import remote_app, is_remote, REMOTE_NOTICE
from socialctl.native_schedule.cli import native_app
from socialctl.metricas import LECTORES, leer_red  # noqa: F401  (importarlo registra)
from socialctl.metricas.almacen import (
    RETENCION_SNAPSHOTS_DIAS,
    cargar_snapshot,
    guardar_snapshot,
    limpiar_snapshots_antiguos,
    snapshot_anterior,
)
from socialctl.metricas.modelos import EstadoLectura, Snapshot
from socialctl.metricas.piezas import PiezasIlegibles, actualizar_piezas
from socialctl.metricas.resumen import escribir_resumen
from socialctl.metricas.slugs import asignar_slugs
from socialctl.models import Platform, Post, PostStatus
from socialctl.postfile import PostNoEncontrado, cargar_post
from socialctl.publication_status import describe_result_with_observation
from socialctl.publisher import (
    PersistenciaError,
    anexar_historial,
    guardar_resultado,
    publicar,
    render_preview,
    validar_todo,
)
from socialctl.scheduler import (
    ScheduleEntry,
    ScheduleError,
    ScheduleStore,
    approval_hash,
    approval_review_reason,
    now_utc,
    parse_scheduled_at,
    schedule_digest,
)

app = typer.Typer(help="Publish already produced content on YouTube, Facebook, Instagram and TikTok.")
brand_app = typer.Typer(help="Brand management.")
app.add_typer(brand_app, name="brand")
app.add_typer(content_app, name="content")
app.add_typer(changes_app, name="changes")
app.add_typer(comments_app, name="comments")
app.add_typer(meta_app, name="meta")
app.add_typer(story_app, name="story")
app.add_typer(group_app, name="group")
app.add_typer(migration_app, name="queue-migration")
app.add_typer(remote_app, name="schedule-remote")
app.add_typer(native_app, name="native-schedule")
app.add_typer(media_app, name="media")

from socialctl.inventory_cli import register as register_inventory
register_inventory(app, content_app)

from socialctl.connections.cli import register as register_connections
register_connections(app)

from socialctl.workspace import default_root

RAIZ_POR_DEFECTO = default_root()

# Excepciones que cargar_brand puede lanzar y que el CLI debe convertir en un
# mensaje claro (nunca en una traza de Python).
_EXCEPCIONES_MARCA = (BrandNoEncontrada, NombreDeMarcaInvalido, AccountsInvalido)

# Excepciones que cargar_post puede lanzar. PostInvalido, SlugInvalido (la de
# postfile.py) y NombreDeMediaInvalido son subclases de ValueError -ver sus
# docstrings en socialctl/postfile.py-, así que capturar ValueError basta
# para las tres sin tener que importarlas una a una.
_EXCEPCIONES_POST = (PostNoEncontrado, MediaNoEncontrada, MediaInvalida, ValueError)

# YouTube y TikTok comparten el mismo puerto/redirect_uri de callback local:
# cada intento de `auth` levanta y cierra su propio servidor de un solo uso
# (ver `_esperar_codigo`), así que no hay conflicto entre ellos aunque se
# ejecuten en momentos distintos.
PUERTO_CALLBACK = 8723
REDIRECT_URI_OAUTH = f"http://localhost:{PUERTO_CALLBACK}/callback"
TIMEOUT_CALLBACK_S = 300.0  # 5 minutos: tiempo razonable para completar el login en el navegador

_PAGINA_LISTO = (
    "<!doctype html><html><head><meta charset='utf-8'>"
    "<title>socialcli</title></head><body>"
    "<h1>Done. You can close this tab.</h1>"
    "</body></html>"
)

# Hallazgo de revisión: la página que ve el usuario al volver del navegador
# decía siempre "Listo" aunque la redirección trajera un `error` (el usuario
# canceló la autorización o el proveedor la rechazó) o un `state` que no
# coincide (posible CSRF, o simplemente una pestaña vieja de un intento
# anterior). Las tres páginas son estáticas y fijas a propósito -nunca
# interpolan `error`, `error_description` ni `state`- porque ese contenido
# lo controla el proveedor (o quien sea que golpee el puerto del callback) y
# podría llevar algo inesperado; `_pagina_para` elige entre las tres sin
# volcar ningún dato de la petición.
_PAGINA_ERROR_AUTORIZACION = (
    "<!doctype html><html><head><meta charset='utf-8'>"
    "<title>socialcli</title></head><body>"
    "<h1>Authorization did not complete.</h1>"
    "<p>Return to the terminal for details.</p>"
    "</body></html>"
)
_PAGINA_ESTADO_INVALIDO = (
    "<!doctype html><html><head><meta charset='utf-8'>"
    "<title>socialcli</title></head><body>"
    "<h1>Something went wrong with this authorization and it was not used.</h1>"
    "<p>Return to the terminal and run the 'auth' command again.</p>"
    "</body></html>"
)


def _pagina_para(recibido: dict[str, str], estado_esperado: str | None) -> str:
    """Select the static OAuth callback page matching the result."""
    if "error" in recibido:
        return _PAGINA_ERROR_AUTORIZACION
    if estado_esperado is not None and recibido.get("state") != estado_esperado:
        return _PAGINA_ESTADO_INVALIDO
    return _PAGINA_LISTO


# Aviso adicional que se muestra justo antes de publicar en una red cuya
# publicación implica una espera larga y silenciosa si no se avisa (hallazgo
# de revisión: el CLI no daba ninguna señal de vida mientras Instagram
# sondeaba el procesado del vídeo, hasta 5 minutos según la cadencia que
# recomienda Meta -ver `InstagramAdapter` en `socialctl/adapters/
# instagram.py`-). Un diccionario y no un `if` disperso porque el mismo
# patrón podría hacer falta para otra red el día de mañana.
_AVISO_ESPERA_LARGA = {
    Platform.INSTAGRAM: (
        ' (Instagram may take several minutes to verify video processing; allow it time)'
    ),
}


def _mostrar_progreso(platform: Platform) -> None:
    """Report the active platform before publication."""
    typer.echo(f"Publishing on {platform.value}...{_AVISO_ESPERA_LARGA.get(platform, '')}")


def _esperar_codigo(
    puerto: int,
    estado_esperado: str | None = None,
    timeout_s: float = TIMEOUT_CALLBACK_S,
) -> dict[str, str]:
    """Run a single-use localhost callback server and return the authorization code."""
    recibido: dict[str, str] = {}

    class _Manejador(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            consulta = urllib.parse.urlparse(self.path).query
            recibido.update(dict(urllib.parse.parse_qsl(consulta)))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_pagina_para(recibido, estado_esperado).encode("utf-8"))

        def log_message(self, *args: object) -> None:  # silencia el log del servidor
            pass

    try:
        servidor = http.server.HTTPServer(("localhost", puerto), _Manejador)
    except OSError as exc:
        raise RuntimeError(
            f'could not open port {puerto} in localhost ({exc}). Is another process using it? Close it and try again.'
        ) from None

    servidor.timeout = timeout_s
    try:
        servidor.handle_request()
    finally:
        servidor.server_close()

    return recibido


def _fallar(mensaje: str) -> None:
    """Print an error and exit the command with code 1."""
    typer.echo(mensaje)
    raise typer.Exit(1)


def _confirmar(mensaje: str) -> bool:
    """Request explicit confirmation; empty input or EOF declines. English and legacy Spanish affirmative inputs are accepted."""
    typer.echo(f"{mensaje} [y/N]: ", nl=False)
    while True:
        try:
            valor = input().strip().lower()
        except EOFError:
            valor = "n"
        if valor in ("y", "yes", "s", "si", "sí"):
            return True
        if valor in ("n", "no", ""):
            return False
        typer.echo("Unrecognized answer (enter 'y' or 'n'): ", nl=False)


def _cargar_marca(root: Path, nombre: str) -> Brand:
    """Load the explicitly named brand or exit with a readable error."""
    try:
        return cargar_brand(root, nombre)
    except _EXCEPCIONES_MARCA as exc:
        _fallar(str(exc))


def _cargar_post_con_avisos(brand: Brand, slug: str, *, avisos: list[str] | None = None) -> Post:
    """Load a post and display any loading warnings."""
    with warnings.catch_warnings(record=True) as capturados:
        warnings.simplefilter("always")
        try:
            post = cargar_post(brand, slug)
        except _EXCEPCIONES_POST as exc:
            for aviso in capturados:
                typer.echo(f"WARNING: {aviso.message}")
            typer.echo(str(exc))
            raise typer.Exit(1)

    for aviso in capturados:
        if avisos is None:
            typer.echo(f"WARNING: {aviso.message}")
        else:
            avisos.append(f"WARNING: {aviso.message}")
    return post


def _mostrar_comentario(result):
    if result.publication_id:
        typer.echo(f'Durable attempt: {result.publication_id}')
    if result.first_comment_status:
        typer.echo(f"First comment: {result.first_comment_status} (media: {result.platform_id or 'unconfirmed'})")
    if result.first_comment_change_id:
        typer.echo(f'Comment ChangeSet: {result.first_comment_change_id}')
    for warning in result.warnings:
        typer.echo(f"WARNING: {warning}")


def _publicar_impl(
    post: Post,
    brand: Brand,
    *,
    dry_run: bool,
    yes: bool,
    only: list[Platform] | None,
    motivo_exclusion: str = "not requested with --only",
    retry_guard: bool = False,
) -> None:
    """Validate, preview and publish an already loaded post; shared by publish and retry."""
    if only is not None:
        destinos = [d for d in only if d in post.platforms]
    else:
        destinos = list(post.platforms)

    errores = validar_todo(post, brand)
    typer.echo(
        render_preview(
            post, errores,
            destinos=destinos if only is not None else None,
            motivo_exclusion=motivo_exclusion,
        )
    )

    if only is not None and not destinos:
        _fallar(
            "none of the requested platforms are in this post; there is nothing to publish."
        )

    errores_bloqueantes = {p: es for p, es in errores.items() if p in destinos}
    if any(errores_bloqueantes.values()):
        typer.echo("\nNothing will be published: fix the problems above.")
        raise typer.Exit(1)

    if dry_run:
        typer.echo("\n--dry-run: nothing was published.")
        raise typer.Exit(0)

    if not yes:
        try:
            confirmado = _confirmar(f"\nPublish on the {len(destinos)} platforms?")
        except KeyboardInterrupt:
            # Typer/Click convierten un Ctrl+C no atrapado en el código de
            # salida 130 sin imprimir nada (ver `_main` en typer/core.py):
            # correcto -no se publica nada-, pero deja al usuario sin
            # confirmación visual, a diferencia de cualquier otro camino de
            # cancelación de este comando, que sí dice "Cancelado". Aquí se
            # informa y se relanza tal cual (nunca se traga la
            # interrupción), para que typer siga terminando con 130 como
            # siempre.
            typer.echo("\nCanceled. Nothing was published.")
            raise
        if not confirmado:
            typer.echo("Canceled. Nothing was published.")
            raise typer.Exit(0)

    resultados = publicar(post, brand, solo=destinos, on_progreso=_mostrar_progreso,
                          retry_guard=retry_guard)

    typer.echo("")
    for r in resultados:
        estado = describe_result_with_observation(r)
        typer.echo(f"{r.platform.value}: {estado} {r.url or r.error or ''}".rstrip())
        _mostrar_comentario(r)

    try:
        guardar_resultado(post, brand, resultados)
        anexar_historial(post, brand, resultados)
    except PersistenciaError as exc:
        typer.echo(f"\n{exc}")
        typer.echo(
            'Publication WAS attempted for the results above; only saving them to disk failed.'
        )
        raise typer.Exit(1)

    if any(r.status is PostStatus.ERROR for r in resultados):
        raise typer.Exit(1)


@brand_app.command("new")
def brand_new(
    nombre: str = typer.Argument(..., metavar="NAME", help="Name of the new brand."),
    root: Path = typer.Option(RAIZ_POR_DEFECTO, help="Workspace directory containing brands."),
) -> None:
    """Create a new brand directory from templates."""
    try:
        brand = crear_brand(root, nombre)
    except (BrandYaExiste, NombreDeMarcaInvalido) as exc:
        _fallar(str(exc))

    typer.echo(f"Brand created at {brand.raiz}")
    typer.echo("Next: complete brand.md and accounts.yml, then authenticate each platform.")


def _auth_meta(brand: Brand, platform: Platform) -> None:
    """Authenticate Facebook or Instagram using the configured Page's token."""
    typer.echo(AYUDA_META)

    client_id = typer.prompt("Meta app ID")
    client_secret = typer.prompt("Meta app secret", hide_input=True)
    token_usuario = typer.prompt(
        "Paste the USER token obtained from the Graph API Explorer",
        hide_input=True,
    )

    page_id = (brand.cuentas.get("facebook") or {}).get("page_id")
    if not page_id:
        _fallar(
            f"facebook.page_id is missing in {brand.raiz / 'accounts.yml'}: configure it before authenticating Facebook or Instagram (both publish with that Page's token)."
        )

    typer.echo("Exchanging the token with Meta (this may take a few seconds)...")
    try:
        with httpx.Client(timeout=30) as client:
            token_larga_duracion = intercambiar_token_meta(
                client_id, client_secret, token_usuario, client
            )
            paginas = obtener_paginas_meta(token_larga_duracion, client)
    except RuntimeError as exc:
        _fallar(str(exc))

    pagina = next((p for p in paginas if p.get("id") == page_id), None)
    if pagina is None:
        disponibles = ", ".join(
            f"{p.get('name')!r} ({p.get('id')!r})" for p in paginas
        ) or 'none'
        _fallar(
            f'the Page with ID {page_id!r} (configured in accounts.yml) is not among the Pages administered by the user who authorized the token. Available Pages: {disponibles}.'
        )

    token_pagina = pagina.get("access_token")
    if not isinstance(token_pagina, str) or not token_pagina:
        _fallar("Meta did not return a valid page token for that Page.")

    brand.guardar_secreto(platform, {"access_token": token_pagina})
    typer.echo(f"Credentials for {platform.value} saved for {brand.nombre}.")


def _auth_oauth_redireccion(
    brand: Brand, platform: Platform, *, youtube_management: bool = False
) -> None:
    """Authenticate YouTube or TikTok through a localhost redirect."""
    if platform is Platform.YOUTUBE:
        client_id = typer.prompt("Google Cloud client_id")
        client_secret = typer.prompt("Google Cloud client_secret", hide_input=True)
        credenciales_app = {"client_id": client_id, "client_secret": client_secret}
        identificador = client_id
    else:
        client_key = typer.prompt("TikTok client_key")
        client_secret = typer.prompt("TikTok client_secret", hide_input=True)
        credenciales_app = {"client_key": client_key, "client_secret": client_secret}
        identificador = client_key

    estado = generar_state()
    code_verifier = generar_code_verifier()

    url = construir_url_autorizacion(
        platform,
        identificador,
        REDIRECT_URI_OAUTH,
        state=estado,
        code_verifier=code_verifier,
        youtube_management=youtube_management,
    )
    typer.echo(f"Opening the browser to authorize {platform.value}...")
    typer.echo(url)
    webbrowser.open(url)

    try:
        recibido = _esperar_codigo(PUERTO_CALLBACK, estado)
    except RuntimeError as exc:
        _fallar(str(exc))

    if "error" in recibido:
        detalle = recibido.get("error_description") or recibido["error"]
        _fallar(f"{platform.value} rejected authorization: {detalle}")

    try:
        verificar_state(estado, recibido.get("state"))
    except EstadoInvalido as exc:
        _fallar(str(exc))

    codigo = recibido.get("code")
    if not codigo:
        _fallar(
            'no authorization code was received (was the tab closed before login completed?). Try again.'
        )

    try:
        with httpx.Client(timeout=30) as client:
            secreto = canjear_codigo(
                platform, codigo, credenciales_app, REDIRECT_URI_OAUTH, client,
                code_verifier=code_verifier,
            )
    except RuntimeError as exc:
        _fallar(str(exc))

    if platform is Platform.TIKTOK:
        _guardar_open_id_tiktok(brand, secreto)

    brand.guardar_secreto(platform, secreto)
    typer.echo(f"Credentials for {platform.value} saved for {brand.nombre}.")


def _guardar_open_id_tiktok(brand: Brand, secreto: dict) -> None:
    """Save the newly exchanged TikTok open_id in accounts.yml when available."""
    open_id = secreto.pop("open_id", None)
    if not open_id:
        typer.echo(
            "Warning: TikTok did not return the account open_id. The access token was saved. Add tiktok.open_id manually in accounts.yml (see that section's comment or SETUP.md)."
        )
        return

    try:
        anterior = brand.guardar_open_id_tiktok(open_id)
    except Exception:
        typer.echo(
            f"Warning: could not save open_id automatically in {brand.raiz / 'accounts.yml'} (check the structure of the 'tiktok:' section). The access token was saved; add tiktok.open_id manually."
        )
        return

    if anterior is not None:
        typer.echo(
            f"Warning: TikTok open_id in accounts.yml changes from '{anterior}' to '{open_id}'."
        )
    else:
        typer.echo(f"TikTok open_id saved in accounts.yml: {open_id}")


@app.command()
def auth(
    red: str = typer.Argument(..., metavar="PLATFORM", help="Platform to authenticate, or status."),
    brand_nombre: str = typer.Option(..., "--brand", help="Brand to authenticate."),
    root: Path = typer.Option(RAIZ_POR_DEFECTO, help="Workspace directory containing brands."),
    management: bool = typer.Option(
        False,
        "--management",
        help="YouTube: also request youtube.force-ssl to edit metadata.",
    ),
    status_platform: Platform | None = typer.Option(None, "--platform", help="Explicit platform for auth status."),
    json_output: bool = typer.Option(False, "--json", help="auth status: versioned output."),
) -> None:
    """Obtain and save a platform's initial credentials for an explicit brand."""
    if red == "status":
        from socialctl.inventory_cli import show_auth_status
        show_auth_status(root, brand_nombre, status_platform, json_output)
        return
    brand = _cargar_marca(root, brand_nombre)

    try:
        platform = Platform(red)
    except ValueError:
        validas = ", ".join(p.value for p in Platform)
        _fallar(f'unknown platform: {red!r}. Valid platforms: {validas}')

    from socialctl.connections.cli import acquire_brand_lock
    try:
        lock_fd = acquire_brand_lock(brand)
    except ValueError as exc:
        _fallar(str(exc))
    try:
        if brand.leer_secreto(platform).get("auth_mode") == "broker":
            _fallar("a shared connection already exists; run socialcli disconnect for this platform before independent-app auth")

        if management and platform is not Platform.YOUTUBE:
            _fallar("--management is only available for YouTube")

        if platform in (Platform.FACEBOOK, Platform.INSTAGRAM):
            _auth_meta(brand, platform)
        else:
            _auth_oauth_redireccion(
                brand, platform, youtube_management=management
            )
    finally:
        import os
        os.close(lock_fd)


@app.command()
def publish(
    slug: str,
    brand_nombre: str = typer.Option(..., "--brand", help="Brand to publish."),
    root: Path = typer.Option(RAIZ_POR_DEFECTO, help="Workspace directory containing brands."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the preview without publishing."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
    only: list[str] = typer.Option(None, "--only", help="Publish only on these platforms."),
) -> None:
    """Validate every platform, display the complete preview, then publish approved content."""
    brand = _cargar_marca(root, brand_nombre)
    post = _cargar_post_con_avisos(brand, slug)

    destinos: list[Platform] | None = None
    if only:
        try:
            destinos = [Platform(r) for r in only]
        except ValueError:
            validas = ", ".join(p.value for p in Platform)
            _fallar(f"--only contains an unknown platform. Valid platforms: {validas}")

    _publicar_impl(post, brand, dry_run=dry_run, yes=yes, only=destinos)


@app.command("schedule")
def schedule(
    slug: str,
    at: str = typer.Option(..., "--at", help="ISO 8601 timestamp with timezone."),
    brand_nombre: str = typer.Option(..., "--brand", help="Brand to schedule."),
    root: Path = typer.Option(RAIZ_POR_DEFECTO, help="Workspace directory containing brands."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the preview without saving the queue."),
    yes: bool = typer.Option(False, "--yes", help="Approve the queue without another prompt."),
    only: list[str] = typer.Option(None, "--only", help="Schedule only these platforms."),
    approval_digest: str | None = typer.Option(None, "--approval-digest", help="Digest of the previously approved preview."),
    preview_json: bool = typer.Option(False, "--preview-json", hidden=True),
) -> None:
    """Validate and add a Facebook/Instagram post to the local queue."""
    if preview_json and (not dry_run or yes or approval_digest):
        _fallar("--preview-json requires --dry-run and does not support approval")
    brand = _cargar_marca(root, brand_nombre)
    if is_remote(brand.raiz):
        _fallar(REMOTE_NOTICE)
    preview_warnings: list[str] = []
    post = _cargar_post_con_avisos(brand, slug, avisos=preview_warnings if preview_json else None)
    try:
        scheduled_at = parse_scheduled_at(at)
    except ScheduleError as exc:
        _fallar(str(exc))
    if only:
        try:
            destinos = [Platform(item) for item in only]
        except ValueError:
            _fallar("--only solo allows youtube, facebook, instagram o tiktok")
    else:
        destinos = list(post.platforms)
    destinos = [p for p in destinos if p in (Platform.FACEBOOK, Platform.INSTAGRAM)]
    if not destinos:
        _fallar("schedule only supports Facebook and Instagram, and the post contains neither")
    if any(p not in post.platforms for p in destinos):
        _fallar("the post does not contain all requested platforms")
    try:
        displayed_hashes = {p.value: approval_hash(post, brand, p) for p in destinos}
        displayed_digest = schedule_digest(post.slug, displayed_hashes, scheduled_at)
    except ScheduleError as exc:
        _fallar(str(exc))
    errores = validar_todo(post, brand)
    preview = render_preview(post, errores, destinos=destinos, motivo_exclusion="not requested for this schedule")
    bloqueantes = [p for p in destinos if errores.get(p)]
    if preview_json:
        from socialctl.schedule_remote import SchedulePreview
        try:
            response = SchedulePreview(protocol="socialctl.schedule-preview.v1",
                                       valid=not bloqueantes, preview="\n".join([*preview_warnings, preview]),
                                       digest=displayed_digest, scheduled_at=scheduled_at.isoformat())
        except ValueError:
            _fallar("unsafe preview; review the content locally before continuing")
        typer.echo(response.model_dump_json())
        raise typer.Exit(1 if bloqueantes else 0)
    typer.echo(preview)
    typer.echo(f"Approval digest: {displayed_digest}")
    if bloqueantes:
        typer.echo("\nNothing will be scheduled: fix the problems above.")
        raise typer.Exit(1)
    if dry_run:
        typer.echo(f"\n--dry-run: would schedule for {scheduled_at.isoformat()}.")
        raise typer.Exit(0)
    if approval_digest and not hmac.compare_digest(approval_digest, displayed_digest):
        _fallar("digest does not match the approved preview; run --dry-run and approve the current content")
    if not yes and not _confirmar(f"\nSave {len(destinos)} schedule(s) to the local queue?"):
        typer.echo("Canceled. No schedule was saved.")
        raise typer.Exit(0)
    # Re-read disk/account changes as well as media bytes after confirmation.
    current_brand = _cargar_marca(root, brand_nombre)
    current_post = _cargar_post_con_avisos(current_brand, slug)
    try:
        if ({p.value: approval_hash(current_post, current_brand, p) for p in destinos} != displayed_hashes
                or {p.value: approval_hash(post, brand, p) for p in destinos} != displayed_hashes):
            _fallar("content changed after the preview; run --dry-run again")
    except ScheduleError as exc:
        _fallar(str(exc))
    store = ScheduleStore(brand.raiz)
    now = now_utc()
    nuevas = []
    for platform in destinos:
        entry = ScheduleEntry(
            id=f"{post.slug}/{platform.value}", brand=brand.nombre, slug=post.slug,
            platform=platform.value, scheduled_at=scheduled_at, status="approved",
            created_at=now, updated_at=now,
            content_hash=displayed_hashes[platform.value],
        )
        nuevas.append(entry)
    try:
        store.add_many(nuevas)
    except ScheduleError as exc:
        _fallar(str(exc))
    for entry in nuevas:
        typer.echo(f"Programado: {entry.id} → {scheduled_at.isoformat()}")
    typer.echo(f"Queue updated: {len(destinos)} entry/entries.")


@app.command("schedule-status")
def schedule_status(
    brand_nombre: str = typer.Option(..., "--brand", help="Brand whose queue to inspect."),
    root: Path = typer.Option(RAIZ_POR_DEFECTO, help="Workspace directory containing brands."),
) -> None:
    """Display the brand's scheduling queue."""
    brand = _cargar_marca(root, brand_nombre)
    if is_remote(brand.raiz):
        typer.echo(REMOTE_NOTICE)
        return
    entries = ScheduleStore(brand.raiz).load()
    if not entries:
        typer.echo("The queue is empty.")
        return
    for entry in entries:
        error = " — error recorded (details omitted)" if entry.last_error else ""
        typer.echo(f"{entry.id} | {entry.scheduled_at.isoformat()} | {entry.status} | intentos={entry.attempts}{error}")


@app.command("schedule-batch")
def schedule_batch(
    fichero: Path,
    brand_nombre: str = typer.Option(..., "--brand", help="Brand to schedule."),
    root: Path = typer.Option(RAIZ_POR_DEFECTO, help="Workspace directory containing brands."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show previews without saving the queue."),
    yes: bool = typer.Option(False, "--yes", help="Skip the prompt after showing previews."),
) -> None:
    """Schedule multiple posts atomically from a YAML batch."""
    brand = _cargar_marca(root, brand_nombre)
    if is_remote(brand.raiz):
        _fallar(REMOTE_NOTICE)
    try:
        datos = yaml.safe_load(fichero.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        _fallar(f"could not read the batch {fichero}: {exc}")
    if not isinstance(datos, list) or not datos:
        _fallar("batch must be a nonempty YAML list")
    nuevas: list[ScheduleEntry] = []
    previews: list[
        tuple[int, Post, list[Platform], datetime, dict[Platform, list]]
    ] = []
    aprobaciones: list[tuple[Post, Platform, str]] = []
    problemas: list[str] = []
    now = now_utc()
    for index, item in enumerate(datos, start=1):
        if not isinstance(item, dict) or not isinstance(item.get("slug"), str) or not isinstance(item.get("at"), str):
            _fallar(f"entry {index} in the batch requires 'slug' and 'at'")
        try:
            at = parse_scheduled_at(item["at"])
            post = _cargar_post_con_avisos(brand, item["slug"])
        except ScheduleError as exc:
            _fallar(f"entry {index}: {exc}")
        only = item.get("only", ["facebook", "instagram"])
        if not isinstance(only, list):
            _fallar(f"entry {index}: 'only' must be a list")
        try:
            plataformas = [Platform(value) for value in only]
        except ValueError:
            _fallar(f"entry {index}: unknown platform in 'only'")
        plataformas = [p for p in plataformas if p in (Platform.FACEBOOK, Platform.INSTAGRAM) and p in post.platforms]
        errores = validar_todo(post, brand)
        if any(errores.get(p) for p in plataformas):
            problemas.append(
                f"entry {index} ({post.slug}): validation problems found"
            )
        previews.append((index, post, plataformas, at, errores))
        for platform in plataformas:
            try:
                content_hash = approval_hash(post, brand, platform)
            except ScheduleError as exc:
                _fallar(f"entry {index} ({post.slug}/{platform.value}): {exc}")
            nuevas.append(ScheduleEntry(
                id=f"{post.slug}/{platform.value}", brand=brand.nombre,
                slug=post.slug, platform=platform.value, scheduled_at=at,
                status="approved", created_at=now, updated_at=now,
                content_hash=content_hash,
            ))
            aprobaciones.append((post, platform, content_hash))
    for index, post, plataformas, at, errores in previews:
        typer.echo(
            f"\n=== Entry {index}: {post.slug} → {at.isoformat()} ==="
        )
        typer.echo(
            render_preview(
                post,
                errores,
                destinos=plataformas,
                motivo_exclusion='not included in this batch entry',
            )
        )
    typer.echo(f"Valid entries: {len(nuevas)}")
    for entry in sorted(nuevas, key=lambda value: value.scheduled_at):
        typer.echo(f"  {entry.id} → {entry.scheduled_at.isoformat()}")
    if problemas:
        typer.echo("\nNothing will be scheduled: fix the problems above.")
        _fallar("; ".join(problemas))
    if dry_run:
        typer.echo("--dry-run: the queue was not saved.")
        raise typer.Exit(0)
    if not yes and not _confirmar("Save this batch to the local queue?"):
        typer.echo("Canceled. No schedule was saved.")
        raise typer.Exit(0)
    # Las huellas se toman antes de renderizar. Se vuelven a calcular tras la
    # aprobación para que ni ``--yes`` ni una mutación concurrente de la media
    # puedan convertir en aprobada una carga distinta de la que se mostró.
    for post, platform, displayed_hash in aprobaciones:
        try:
            current_hash = approval_hash(post, brand, platform)
        except ScheduleError as exc:
            _fallar(
                f"{post.slug}/{platform.value}: could not reverify "
                f"approval after the preview: {exc}"
            )
        if not hmac.compare_digest(current_hash, displayed_hash):
            _fallar(
                f"{post.slug}/{platform.value}: content, media or account "
                "changed after the preview; run --dry-run again and approve "
                "the new content"
            )
    try:
        ScheduleStore(brand.raiz).add_many(nuevas)
    except ScheduleError as exc:
        _fallar(str(exc))
    typer.echo(f"Queue updated: {len(nuevas)} entry/entries.")


@app.command("schedule-cancel")
def schedule_cancel(
    entry_id: str,
    brand_nombre: str = typer.Option(..., "--brand", help="Brand whose schedule to cancel."),
    root: Path = typer.Option(RAIZ_POR_DEFECTO, help="Workspace directory containing brands."),
) -> None:
    """Cancel a schedule that has not run."""
    brand = _cargar_marca(root, brand_nombre)
    store = ScheduleStore(brand.raiz)
    try:
        store.cancel(entry_id, now_utc())
    except ScheduleError as exc:
        _fallar(str(exc))
    typer.echo(f"Canceled: {entry_id}")


@app.command("schedule-reschedule")
def schedule_reschedule(
    entry_id: str,
    at: str = typer.Option(..., "--at", help="New ISO 8601 timestamp with timezone."),
    brand_nombre: str = typer.Option(..., "--brand", help="Brand whose schedule to change."),
    root: Path = typer.Option(RAIZ_POR_DEFECTO, help="Workspace directory containing brands."),
) -> None:
    """Change the time of a pending schedule."""
    brand = _cargar_marca(root, brand_nombre)
    try:
        scheduled_at = parse_scheduled_at(at)
        store = ScheduleStore(brand.raiz)
        store.reschedule(entry_id, scheduled_at, now_utc())
    except ScheduleError as exc:
        _fallar(str(exc))
    typer.echo(f"Rescheduled: {entry_id} → {scheduled_at.isoformat()}")


@app.command("run-due")
def run_due(
    brand_nombre: str = typer.Option(..., "--brand", help="Brand whose queue to run."),
    root: Path = typer.Option(RAIZ_POR_DEFECTO, help="Workspace directory containing brands."),
) -> None:
    """Run approved publications whose scheduled time has arrived, once."""
    brand = _cargar_marca(root, brand_nombre)
    store = ScheduleStore(brand.raiz)
    from socialctl.executor import heartbeat
    try:
        with store.executor_lock():
            heartbeat(store, "started", now_utc())
            try:
                _run_due_locked(brand, store)
            except BaseException:
                try:
                    heartbeat(store, "error", now_utc())
                except ScheduleError:
                    pass  # Preserve original failure; previous started heartbeat becomes stale.
                raise
            heartbeat(store, "finished", now_utc())
    except ScheduleError as exc:
        _fallar(str(exc))


def _run_due_locked(brand: Brand, store: ScheduleStore) -> None:
    """Process the queue while holding its executor lock."""
    store.assert_ready()
    recuperadas = store.recover_stale()
    if recuperadas:
        typer.echo(
            f"Isolated {recuperadas} interrupted execution(s) for manual review."
        )
    from socialctl.executor import admitted_groups
    due = (entry for group in admitted_groups(store, now_utc(), clock=now_utc) for entry in group)
    for candidate in due:
        entry = store.claim_due(candidate.id, now_utc(), expected=candidate)
        if entry is None:
            continue
        try:
            platform = Platform(entry.platform)
            post = _cargar_post_con_avisos(brand, entry.slug)
            review_reason = approval_review_reason(
                entry.content_hash, post, brand, platform
            )
            if review_reason is not None:
                store.transition(
                    entry.id,
                    {"running"},
                    status="manual_review",
                    moment=now_utc(),
                    last_error=review_reason,
                )
                typer.echo(f"{entry.id}: manual_review — {review_reason}")
                continue
            pp = post.platforms[platform]
            legacy_approved_platforms = frozenset()
            if pp.content_origin is None and pp.source_video_id is None:
                # The stored v2 hash above authorizes only this unchanged entry.
                legacy_approved_platforms = frozenset({platform})
                typer.echo(f"{entry.id}: legacy entry: source unverified")
            errores = validar_todo(
                post, brand, legacy_approved_platforms=legacy_approved_platforms
            ).get(platform, [])
            if errores:
                error = "; ".join(e.motivo for e in errores)
                store.transition(
                    entry.id,
                    {"running"},
                    status="error",
                    moment=now_utc(),
                    last_error=error,
                )
                typer.echo(f"{entry.id}: validation error — {error}")
                continue
            if platform is Platform.INSTAGRAM:
                from socialctl.hosted_media import verify_scheduled_instagram
                verify_scheduled_instagram(post, brand, entry=entry)
            typer.echo(f"Publishing {entry.id}...")
            def persist_media(result):
                store.transition(entry.id, {"running"}, status="running", moment=now_utc(),
                                 platform_id=result.platform_id)
                guardar_resultado(post, brand, [result])

            from socialctl.scheduler import occurrence_id
            occurrence = occurrence_id(brand.raiz, entry)
            resultados = publicar(post, brand, solo=[platform], on_progreso=_mostrar_progreso,
                on_media_result=persist_media, occurrence_ids={platform: occurrence},
                approval_provenance=f"schedule:{entry.id}:{entry.content_hash}",
                legacy_approved_platforms=legacy_approved_platforms)
            resultado = resultados[0]
            store.transition(
                entry.id,
                {"running"},
                status="running",
                moment=now_utc(),
                platform_id=resultado.platform_id,
            )
            guardar_resultado(post, brand, resultados)
            anexar_historial(post, brand, resultados)
            status = "published" if resultado.status is PostStatus.PUBLICADO else ("manual_review" if resultado.riesgo_duplicado else "error")
            store.transition(
                entry.id,
                {"running"},
                status=status,
                moment=now_utc(),
                last_error=resultado.error,
                platform_id=resultado.platform_id,
            )
            typer.echo(f"{entry.id}: {status}")
            _mostrar_comentario(resultado)
        except Exception as exc:
            error = (
                f"execution interrupted ({type(exc).__name__}): confirm the "
                "remote result before retrying"
            )
            store.transition(
                entry.id,
                {"running"},
                status="manual_review",
                moment=now_utc(),
                last_error=error,
            )
            typer.echo(f"{entry.id}: manual_review — {error}")


@app.command("schedule-health")
def schedule_health(
    brand_nombre: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(RAIZ_POR_DEFECTO),
) -> None:
    """Display durable executor health; stale means no heartbeat for more than three minutes."""
    from socialctl.executor import health
    brand = _cargar_marca(root, brand_nombre)
    if is_remote(brand.raiz):
        typer.echo(REMOTE_NOTICE)
        return
    try:
        typer.echo(json.dumps(health(ScheduleStore(brand.raiz), now_utc()), ensure_ascii=False))
    except ScheduleError as exc:
        _fallar(str(exc))


@app.command("schedule-install")
def schedule_install(
    brand_nombre: str = typer.Option(..., "--brand", help="Brand to install the agent for."),
    root: Path = typer.Option(RAIZ_POR_DEFECTO, help="Workspace directory containing brands."),
) -> None:
    """Install the periodic queue executor as a macOS launchd agent."""
    brand = _cargar_marca(root, brand_nombre)
    try:
        ScheduleStore(brand.raiz).assert_ready()
    except ScheduleError as exc:
        _fallar(str(exc))
    agent_dir = Path.home() / "Library" / "LaunchAgents"
    agent_dir.mkdir(parents=True, exist_ok=True)
    label = f"com.socialctl.{brand.nombre.lower()}.run-due"
    plist_path = agent_dir / f"{label}.plist"
    log_dir = brand.raiz / ".socialctl"
    log_dir.mkdir(parents=True, exist_ok=True)
    program = [sys.executable, "-m", "socialctl"]
    payload = {
        "Label": label,
        "ProgramArguments": program + ["run-due", "--brand", brand.nombre, "--root", str(root.resolve())],
        "WorkingDirectory": str(root.resolve()),
        "StartInterval": 60,
        "RunAtLoad": True,
        "StandardOutPath": str(log_dir / "run-due.log"),
        "StandardErrorPath": str(log_dir / "run-due.error.log"),
        "ProcessType": "Background",
    }
    plist_path.write_bytes(plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=False))
    launchctl = shutil.which("launchctl")
    if launchctl:
        domain = f"gui/{os.getuid()}"
        subprocess.run([launchctl, "bootout", f"{domain}/{label}"], check=False, capture_output=True)
        result = subprocess.run([launchctl, "bootstrap", domain, str(plist_path)], check=False, capture_output=True)
        if result.returncode != 0:
            typer.echo("Warning: the plist was saved, but launchd could not load it; check the system log.")
    typer.echo(f"Agent installed: {plist_path}")
    typer.echo("launchd will run it every 60 seconds; the Mac must be powered on and awake.")


@app.command("schedule-uninstall")
def schedule_uninstall(
    brand_nombre: str = typer.Option(..., "--brand", help="Brand whose agent to remove."),
) -> None:
    """Remove a brand's local launchd agent."""
    label = f"com.socialctl.{brand_nombre.lower()}.run-due"
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    if not plist_path.exists():
        typer.echo(f"Agent does not exist: {plist_path}")
        return
    launchctl = shutil.which("launchctl")
    if launchctl:
        subprocess.run([launchctl, "bootout", f"gui/{os.getuid()}/{label}"], check=False, capture_output=True)
    plist_path.unlink()
    typer.echo(f"Agent removed: {plist_path}")


@app.command()
def retry(
    slug: str,
    brand_nombre: str = typer.Option(..., "--brand", help="Brand to retry."),
    root: Path = typer.Option(RAIZ_POR_DEFECTO, help="Workspace directory containing brands."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
) -> None:
    """Retry failed platforms only after validation and preview, excluding outcomes that may duplicate publications."""
    brand = _cargar_marca(root, brand_nombre)
    post = _cargar_post_con_avisos(brand, slug)

    from socialctl.publication_steps import retry_media_blockers, CommentError
    try:
        protected = retry_media_blockers(brand, post.slug, post.platforms)
    except CommentError as exc:
        _fallar(str(exc))
    for platform, reasons in protected.items():
        for reason in reasons:
            typer.echo(f"{platform.value}: {reason}. Consulta comments publication-status / retry-first.")

    fichero = brand.dir_posts / post.slug / "resultado.json"
    if not fichero.exists():
        if protected:
            _fallar("resultado.json is missing; the durable journal requires reconciliation, not another upload.")
        _fallar(f'no previous attempt in {fichero}; nothing to retry.')

    try:
        contenido = json.loads(fichero.read_text(encoding="utf-8"))
        if not isinstance(contenido, dict):
            raise ValueError("JSON root is not an object")
        entradas = contenido["resultados"]
        if not isinstance(entradas, list):
            raise ValueError("'resultados' is not a list")
        previos: dict[str, dict] = {}
        for entrada in entradas:
            if not isinstance(entrada, dict) or not isinstance(entrada.get("platform"), str):
                raise ValueError("a 'resultados' entry does not have the expected structure")
            previos[entrada["platform"]] = entrada
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        _fallar(f"could not parse {fichero}: {exc}")

    fallidas = {
        plataforma for plataforma, entrada in previos.items()
        if entrada.get("status") == PostStatus.ERROR.value
    }
    if not fallidas:
        typer.echo("There are no failed platforms to retry.")
        raise typer.Exit(1 if protected else 0)

    actuales = {p.value for p in post.platforms}
    faltantes = sorted(fallidas - actuales)
    vigentes = sorted(fallidas & actuales)

    if faltantes:
        typer.echo(
            f"Warning: post.yml no longer includes {', '.join(faltantes)} "
            "(they failed in the previous attempt); they will not be retried."
        )

    if not vigentes:
        typer.echo("No failed platform remaining in this post can be retried.")
        raise typer.Exit(0)

    # Estrictamente `is True`, no `bool(...)`: un valor no booleano colado en
    # el JSON (p. ej. la cadena "false", que `bool("false")` evaluaría como
    # verdadera) nunca debe convertir una red en "arriesgada" por accidente.
    # Una entrada sin este campo -de un resultado.json anterior a que
    # existiera- también cae aquí, y por tanto no se considera arriesgada
    # (ver docstring de esta función para la justificación).
    arriesgadas = sorted(
        p for p in vigentes if previos[p].get("riesgo_duplicado") is True
    )
    seguras = [p for p in vigentes if p not in arriesgadas and Platform(p) not in protected]

    if arriesgadas:
        typer.echo(
            "\nWARNING: these platforms will not be retried automatically because of "
            "duplicate publication risk (the previous attempt warned "
            "that the content may have been published):"
        )
        for p in arriesgadas:
            typer.echo(f"  - {p}: {previos[p].get('error')}")
        typer.echo(
            "Check the account manually before publishing on these platforms again "
            "(for example: socialcli publish ... --only <platform> --yes)."
        )

    if not seguras:
        raise typer.Exit(1)

    typer.echo(f"\nRetrying: {', '.join(seguras)}")

    codigo_publish = 0
    try:
        _publicar_impl(
            post, brand, dry_run=False, yes=yes,
            only=[Platform(p) for p in seguras],
            motivo_exclusion="not retried in this attempt",
            retry_guard=True,
        )
    except typer.Exit as exc:
        codigo_publish = exc.exit_code or 0

    if arriesgadas or protected or codigo_publish:
        raise typer.Exit(1)


#: Días seguidos en fallo tras los que `stats` avisa por pantalla. Tres, como
#: pide el spec (§5): uno puede ser un corte de red, dos una coincidencia;
#: tres seguidos es un token caducado que lleva días comiéndose el histórico.
DIAS_DE_FALLO_PARA_AVISAR = 3


def _redes_en_fallo(snapshot: Snapshot) -> set[Platform]:
    """Return platforms with error or missing-credential read states."""
    return {
        platform
        for platform, lectura in snapshot.redes.items()
        if lectura.estado
        in (EstadoLectura.ERROR, EstadoLectura.SIN_CREDENCIALES)
    }


def _avisar_de_fallos_persistentes(marca: Brand, snapshot: Snapshot) -> None:
    """Warn when a platform fails in three consecutive snapshots."""
    anterior = snapshot_anterior(marca, antes_de=snapshot.fecha)
    if anterior is None:
        return
    trasanterior = snapshot_anterior(marca, antes_de=anterior.fecha)
    if trasanterior is None:
        return

    persistentes = (
        _redes_en_fallo(snapshot)
        & _redes_en_fallo(anterior)
        & _redes_en_fallo(trasanterior)
    )
    for platform in sorted(persistentes, key=lambda p: p.value):
        typer.echo(
            f"WARNING: {platform.value} has gone {DIAS_DE_FALLO_PARA_AVISAR} days "
            f"without a successful read (also on {anterior.fecha.isoformat()} and "
            f"on {trasanterior.fecha.isoformat()}). This usually means an "
            f"expired token: run socialcli auth {platform.value} "
            f"--brand {marca.nombre}"
        )


def _limpiar_snapshots_y_avisar(marca: Brand) -> None:
    """Apply one-year snapshot retention and report deletions or failures."""
    borrados, fallidos = limpiar_snapshots_antiguos(marca)
    if borrados:
        nombres = ", ".join(ruta.name for ruta in borrados)
        typer.echo(
            f"Cleanup: deleted {len(borrados)} snapshot(s) older than "
            f"{RETENCION_SNAPSHOTS_DIAS} days ({nombres})."
        )
    for ruta, exc in fallidos:
        typer.echo(
            f"WARNING: could not delete the old snapshot {ruta} ({exc}). "
            "It remains on disk; delete it manually to free the space."
        )


def _fusionar_snapshot_del_dia(marca: Brand, nuevo: Snapshot) -> Snapshot:
    """Merge selected-platform reads with today's snapshot, retaining other platforms and previous good data when a read fails."""
    anterior = cargar_snapshot(marca, nuevo.fecha)
    if anterior is None:
        return nuevo

    fusionadas = dict(anterior.redes)
    for platform, lectura in nuevo.redes.items():
        previa = anterior.redes.get(platform)
        if (
            lectura.estado is not EstadoLectura.OK
            and previa is not None
            and previa.estado is EstadoLectura.OK
        ):
            fusionadas[platform] = previa.model_copy(
                update={"estado": lectura.estado, "error": lectura.error}
            )
        else:
            fusionadas[platform] = lectura

    return Snapshot(fecha=nuevo.fecha, marca=nuevo.marca, redes=fusionadas)


def _guardar_lo_leido(marca: Brand, snapshot: Snapshot) -> Path:
    """Write the day's snapshot, editorial inventory and report atomically, reporting any incomplete set of writes."""
    pasos = (
        ("today's snapshot", lambda: guardar_snapshot(marca, snapshot)),
        ("piezas.yml", lambda: actualizar_piezas(marca, snapshot)),
        ("resumen.md", lambda: escribir_resumen(marca, snapshot)),
    )

    escritos: list[str] = []
    pendientes = [nombre for nombre, _ in pasos]
    ruta_del_snapshot: Path | None = None

    for nombre, escribir in pasos:
        try:
            ruta = escribir()
        except PiezasIlegibles as exc:
            _fallar_guardando(
                escritos, pendientes,
                f"could not update piezas.yml: {exc}",
                "That file was preserved: its editorial content "
                "cannot be retrieved again from an API.",
            )
        except OSError as exc:
            _fallar_guardando(
                escritos, pendientes,
                f"could not write {nombre}: {exc}",
                "Files are written "
                "atomically, so unsaved files retain their "
                "complete previous content.",
            )
        escritos.append(f"{nombre} ({ruta})")
        pendientes.remove(nombre)
        if ruta_del_snapshot is None:
            ruta_del_snapshot = ruta

    assert ruta_del_snapshot is not None  # el primer paso siempre lo fija
    return ruta_del_snapshot


def _fallar_guardando(
    escritos: list[str], pendientes: list[str], motivo: str, coletilla: str
) -> None:
    """Exit stats with a clear account of saved and unsaved files."""
    lineas = [f"ERROR: {motivo}"]
    lineas.append(
        "Saved: " + "; ".join(escritos) + "."
        if escritos
        else "None of this run's files were saved."
    )
    if pendientes:
        lineas.append("Not saved: " + ", ".join(pendientes) + ".")
    if coletilla:
        lineas.append(coletilla)
    _fallar("\n".join(lineas))


def _metric_read_label(state: str) -> str:
    """Render English metric-read labels while preserving stored state codes."""
    return {"sin_credenciales": "missing_credentials", "sin_permiso": "missing_permission",
            "no_disponible": "unavailable"}.get(state, state)


@app.command()
def stats(
    brand_nombre: str = typer.Option(..., "--brand", help="Brand whose metrics to read."),
    root: Path = typer.Option(RAIZ_POR_DEFECTO, help="Workspace directory containing brands."),
    only: list[str] = typer.Option(None, "--only", help="Read only these platforms."),
    desde: str = typer.Option(
        None, "--since", "--desde", help="Only content published since this date (YYYY-MM-DD)."
    ),
) -> None:
    """Read platform metrics and save a brand snapshot. Selected-platform reads merge with today's data; old snapshots expire after one year."""
    marca = _cargar_marca(root, brand_nombre)

    fecha_desde = None
    if desde is not None:
        try:
            fecha_desde = date.fromisoformat(desde)
        except ValueError:
            _fallar(f"'--desde {desde}' is not a valid YYYY-MM-DD date.")

    # Mismo patrón que `publish` (`socialctl/cli.py:638-643`): la opción se
    # declara como texto y se convierte aquí, para que una red desconocida dé
    # un mensaje en español en vez del error de click, que sale en inglés.
    destinos: list[Platform]
    if only:
        try:
            destinos = [Platform(r) for r in only]
        except ValueError:
            validas = ", ".join(p.value for p in Platform)
            _fallar(f"--only contains an unknown platform. Valid platforms: {validas}")
    else:
        destinos = list(LECTORES)

    redes = {}
    with httpx.Client(timeout=60.0) as client:
        for platform in destinos:
            typer.echo(f"Reading {platform.value}...")
            redes[platform] = leer_red(platform, marca, client, fecha_desde)

    snapshot = Snapshot(fecha=date.today(), marca=marca.nombre, redes=redes)

    # Con `--only`, fusiona con el snapshot que ya hubiera hoy en vez de
    # reemplazarlo (ver `_fusionar_snapshot_del_dia`); sin `--only` no se
    # toca nada aquí y la lectura completa sigue sobrescribiendo como
    # siempre. `redes` (usado más abajo para el resumen por pantalla y el
    # código de salida) sigue siendo la lectura de HOY, sin fusionar: lo que
    # se cuenta ahí es lo que ha pasado en ESTA ejecución.
    if only:
        snapshot = _fusionar_snapshot_del_dia(marca, snapshot)

    # ANTES de guardar y de actualizar `piezas.yml`: el slug enlaza cada
    # pieza con el post que la publicó, y tiene que llegar a los dos ficheros.
    asignar_slugs(marca, snapshot)

    ruta = _guardar_lo_leido(marca, snapshot)

    typer.echo("")
    for platform, lectura in redes.items():
        if lectura.estado is EstadoLectura.OK:
            typer.echo(f"{platform.value}: ok, {len(lectura.piezas)} content items")
        else:
            typer.echo(f"{platform.value}: {_metric_read_label(lectura.estado.value)} — {lectura.error}")

    typer.echo(f"\nSnapshot: {ruta}")

    # Retención (spec §3): corre con cada `stats`, después de guardar el
    # snapshot de hoy -nunca antes-, para que un fallo al borrar un fichero
    # viejo no se lleve por delante una lectura que ya ha costado cuota de
    # API en las cuatro redes.
    _limpiar_snapshots_y_avisar(marca)

    # El tercer punto de la cadencia del spec (§5). Va después del resumen por
    # red para que lo último que se lea sea lo que hay que hacer.
    _avisar_de_fallos_persistentes(marca, snapshot)

    if all(l.estado is not EstadoLectura.OK for l in redes.values()):
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
