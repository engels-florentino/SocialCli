"""CLI de socialctl.

Contiene el único punto de parada de todo el proyecto: el usuario ve el
preview con el texto de las cuatro redes y qué archivo va a cada una,
aprueba una sola vez (o pasa ``--yes`` para saltarse esa pregunta de forma
explícita), y a partir de ahí se publica en todas sin volver a preguntar
red por red. Si hay problemas de validación, o si el guardado del
resultado falla, no se publica nada (o se avisa con toda claridad de lo
que sí llegó a publicarse pese a ello); publicar algo que no cumple es
peor que no publicar.
"""

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

app = typer.Typer(help="Publica contenido ya producido en YouTube, Facebook, Instagram y TikTok.")
brand_app = typer.Typer(help="Gestión de marcas.")
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
    "<title>socialctl</title></head><body>"
    "<h1>Listo. Puedes cerrar esta pestaña.</h1>"
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
    "<title>socialctl</title></head><body>"
    "<h1>La autorización no se completó.</h1>"
    "<p>Vuelve a la terminal para ver el detalle.</p>"
    "</body></html>"
)
_PAGINA_ESTADO_INVALIDO = (
    "<!doctype html><html><head><meta charset='utf-8'>"
    "<title>socialctl</title></head><body>"
    "<h1>Algo fue mal con esta autorización y no se ha usado.</h1>"
    "<p>Vuelve a la terminal y ejecuta de nuevo el comando 'auth'.</p>"
    "</body></html>"
)


def _pagina_para(recibido: dict[str, str], estado_esperado: str | None) -> str:
    """Elige, entre las tres páginas estáticas de arriba, la que corresponde
    al resultado de la redirección.

    No hace la verificación anti-CSRF de verdad -esa sigue viviendo, sin
    cambios, en `verificar_state` y sigue siendo la que decide si se canjea
    el código-: esto es solo lo que ve el usuario en la pestaña del
    navegador, así que una comparación simple basta. `estado_esperado=None`
    (nadie pidió distinguir el `state`) se salta esa comprobación y muestra
    éxito salvo que la redirección trajera un `error`.
    """
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
        " (Instagram puede tardar varios minutos comprobando que el vídeo "
        "ya está procesado; no te preocupes si tarda)"
    ),
}


def _mostrar_progreso(platform: Platform) -> None:
    """Avisa por stdout de en qué red se está trabajando antes de publicar.

    Es el callback `on_progreso` de `publicar()` (`socialctl/publisher.py`):
    sin él, publicar en las cuatro redes de forma secuencial -y esperar el
    sondeo de Instagram en medio- no daba ninguna señal de vida al usuario.
    """
    typer.echo(f"Publicando en {platform.value}...{_AVISO_ESPERA_LARGA.get(platform, '')}")


def _esperar_codigo(
    puerto: int,
    estado_esperado: str | None = None,
    timeout_s: float = TIMEOUT_CALLBACK_S,
) -> dict[str, str]:
    """Levanta un servidor local de un solo uso en localhost:`puerto` y
    devuelve los parámetros de consulta de la primera petición que reciba
    (típicamente `code` y `state`, o `error`/`error_description` si el
    usuario canceló la autorización).

    `estado_esperado` es el `state` generado al iniciar este intento (ver
    `generar_state` en `socialctl/authflow.py`); se usa únicamente para
    elegir qué página estática mostrar (`_pagina_para`), no para la
    verificación real -esa la sigue haciendo `verificar_state` en el
    llamador, después de que esta función devuelva-. `None` (el valor por
    defecto) se salta esa distinción y muestra éxito salvo que la
    redirección traiga un `error`.

    Propiedades de seguridad y robustez, todas deliberadas:

    - De un solo uso: `handle_request()` atiende como máximo UNA petición y
      termina; nunca se queda escuchando una segunda.
    - Nunca se queda escuchando indefinidamente: `servidor.timeout` hace que
      la propia espera de una conexión expire a los `timeout_s` segundos (5
      minutos por defecto) en vez de bloquear para siempre si el usuario
      nunca completa el login en el navegador.
    - Se cierra siempre: `server_close()` vive en un `finally`, así que el
      puerto queda libre tanto si llega la petición, como si expira el
      plazo, como si `handle_request()` lanza algo inesperado.
    - Puerto ya ocupado: si otro proceso ya está escuchando en `puerto`,
      `HTTPServer(...)` lanza `OSError` al construirse (antes de intentar
      atender nada); se convierte aquí en un `RuntimeError` con un mensaje
      accionable en vez de dejar escapar la traza cruda de sockets.
    - Las páginas que puede ver el usuario al terminar (`_pagina_para`) son
      estáticas y ninguna contiene el código, el `state`, el `error` ni
      ningún otro dato de la petición: nunca reflejan de vuelta lo que han
      recibido.
    """
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
            f"no se pudo abrir el puerto {puerto} en localhost ({exc}). "
            "¿Hay otro proceso usándolo? Ciérralo e inténtalo de nuevo."
        ) from None

    servidor.timeout = timeout_s
    try:
        servidor.handle_request()
    finally:
        servidor.server_close()

    return recibido


def _fallar(mensaje: str) -> None:
    """Imprime ``mensaje`` por stdout y termina el comando con código 1."""
    typer.echo(mensaje)
    raise typer.Exit(1)


def _confirmar(mensaje: str) -> bool:
    """Pide una confirmación de sí/no en español.

    No se usa ``typer.confirm`` (que es en realidad ``click.confirm``)
    porque su wording está fijo en inglés («[y/N]», y «Error: invalid
    input» ante una respuesta no reconocida) y no se puede localizar; este
    proyecto exige mensajes de cara al usuario en español. Sin respuesta
    (EOF) se interpreta como "no", igual que una entrada vacía: nunca se
    publica nada sin un "sí" explícito.
    """
    typer.echo(f"{mensaje} [s/N]: ", nl=False)
    while True:
        try:
            valor = input().strip().lower()
        except EOFError:
            valor = "n"
        if valor in ("s", "si", "sí"):
            return True
        if valor in ("n", "no", ""):
            return False
        typer.echo("Respuesta no reconocida (responde 's' o 'n'): ", nl=False)


def _cargar_marca(root: Path, nombre: str) -> Brand:
    """Carga una marca o termina el comando con un mensaje claro."""
    try:
        return cargar_brand(root, nombre)
    except _EXCEPCIONES_MARCA as exc:
        _fallar(str(exc))


def _cargar_post_con_avisos(brand: Brand, slug: str, *, avisos: list[str] | None = None) -> Post:
    """Carga un post mostrando cualquier aviso emitido al hacerlo.

    ``cargar_post`` emite un ``UserWarning`` cuando el 'slug' declarado
    dentro de post.yml no coincide con el nombre real de la carpeta. El
    filtro de avisos por defecto de Python deduplica cada aviso dentro de
    un mismo proceso y lo manda a stderr, así que en un flujo de preview
    seguido de publicación -todo en el mismo proceso del CLI- es fácil que
    el usuario no llegue a verlo nunca. Aquí se captura explícitamente,
    con un filtro que nunca lo deduplica, y se muestra por stdout junto al
    resto del preview para que no pase inadvertido.
    """
    with warnings.catch_warnings(record=True) as capturados:
        warnings.simplefilter("always")
        try:
            post = cargar_post(brand, slug)
        except _EXCEPCIONES_POST as exc:
            for aviso in capturados:
                typer.echo(f"AVISO: {aviso.message}")
            typer.echo(str(exc))
            raise typer.Exit(1)

    for aviso in capturados:
        if avisos is None:
            typer.echo(f"AVISO: {aviso.message}")
        else:
            avisos.append(f"AVISO: {aviso.message}")
    return post


def _mostrar_comentario(result):
    if result.publication_id:
        typer.echo(f"Intento durable: {result.publication_id}")
    if result.first_comment_status:
        typer.echo(f"Primer comentario: {result.first_comment_status} (medio: {result.platform_id or 'sin confirmar'})")
    if result.first_comment_change_id:
        typer.echo(f"ChangeSet comentario: {result.first_comment_change_id}")
    for warning in result.warnings:
        typer.echo(f"AVISO: {warning}")


def _publicar_impl(
    post: Post,
    brand: Brand,
    *,
    dry_run: bool,
    yes: bool,
    only: list[Platform] | None,
    motivo_exclusion: str = "no solicitada con --only",
    retry_guard: bool = False,
) -> None:
    """Lógica compartida por `publish` y `retry` una vez que post y brand ya
    están cargados (y sus posibles avisos, ya mostrados).

    No pide confirmación más que una vez, y solo si hace falta: `publish` y
    `retry` son las dos únicas puertas de entrada a esta función, así que el
    punto de parada único del proyecto sigue viviendo aquí, no duplicado en
    cada comando.

    Hallazgo de revisión (I3): `--only` prometía en `SKILL.md` dejar publicar
    en las redes que sí cumplen aunque otra falle su validación, pero el
    código de antes validaba TODO el post y abortaba si cualquier red tenía
    errores, sin mirar `only` -así que `--only` no servía para lo que
    promete: un post con una red que no cumple quedaba sin ninguna salida,
    ni siquiera para publicar en la que sí cumple. Ahora los errores que
    bloquean son solo los de las redes pedidas (`destinos`): una red no
    solicitada puede tener problemas de validación sin impedir publicar en
    las demás, pero la garantía de fondo sigue intacta -nunca se publica una
    red que SÍ está en `destinos` y tiene errores-. El preview sigue
    mostrando todas las redes del post, íntegras (fidelidad), pero marca con
    claridad cuáles quedan fuera y por qué (ver `render_preview`).

    `motivo_exclusion` se reenvía tal cual a `render_preview`: `publish` usa
    el valor por defecto ("no solicitada con --only", el motivo real de su
    filtro), pero `retry` reutiliza `destinos` con un motivo distinto -una
    red queda fuera de un reintento porque ya se publicó antes o porque
    quedó marcada `riesgo_duplicado`, nunca porque el usuario haya escrito
    `--only`- y pasa aquí su propio texto para que la marca del preview siga
    siendo cierta también en ese caso.
    """
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
            "ninguna de las redes pedidas está en este post; no hay nada que publicar."
        )

    errores_bloqueantes = {p: es for p, es in errores.items() if p in destinos}
    if any(errores_bloqueantes.values()):
        typer.echo("\nNo se publica nada: corrige los problemas de arriba.")
        raise typer.Exit(1)

    if dry_run:
        typer.echo("\n--dry-run: no se ha publicado nada.")
        raise typer.Exit(0)

    if not yes:
        try:
            confirmado = _confirmar(f"\n¿Publicar en las {len(destinos)} redes?")
        except KeyboardInterrupt:
            # Typer/Click convierten un Ctrl+C no atrapado en el código de
            # salida 130 sin imprimir nada (ver `_main` en typer/core.py):
            # correcto -no se publica nada-, pero deja al usuario sin
            # confirmación visual, a diferencia de cualquier otro camino de
            # cancelación de este comando, que sí dice "Cancelado". Aquí se
            # informa y se relanza tal cual (nunca se traga la
            # interrupción), para que typer siga terminando con 130 como
            # siempre.
            typer.echo("\nCancelado. No se ha publicado nada.")
            raise
        if not confirmado:
            typer.echo("Cancelado. No se ha publicado nada.")
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
            "Los resultados de arriba SÍ se han intentado publicar; "
            "solo ha fallado guardarlos en disco."
        )
        raise typer.Exit(1)

    if any(r.status is PostStatus.ERROR for r in resultados):
        raise typer.Exit(1)


@brand_app.command("new")
def brand_new(
    nombre: str,
    root: Path = typer.Option(RAIZ_POR_DEFECTO, help="Carpeta Social/ que contiene las marcas."),
) -> None:
    """Crea la carpeta de una marca nueva a partir de las plantillas."""
    try:
        brand = crear_brand(root, nombre)
    except (BrandYaExiste, NombreDeMarcaInvalido) as exc:
        _fallar(str(exc))

    typer.echo(f"Marca creada en {brand.raiz}")
    typer.echo("Siguiente paso: rellena brand.md y accounts.yml, y autentica cada red.")


def _auth_meta(brand: Brand, platform: Platform) -> None:
    """Autentica Facebook o Instagram: ambos publican con el token de la
    misma Página de Facebook (ver `accounts.yml`, donde `instagram.ig_user_id`
    debe ser una cuenta Business vinculada a esa Página), así que ambos
    flujos son idénticos salvo por dónde se guarda el resultado.

    A diferencia del brief original (pegar directamente un token de PÁGINA
    ya de larga duración), aquí se pide un token de USUARIO -el que el
    Explorador de la API de Graph genera por defecto, de corta duración- y
    el propio comando hace el intercambio en dos pasos (ver
    `socialctl/authflow.py` para la documentación citada): más fácil de
    obtener a mano y de menor privilegio mientras vive sin canjear.
    """
    typer.echo(AYUDA_META)

    client_id = typer.prompt("App ID de la app de Meta")
    client_secret = typer.prompt("App secret de la app de Meta", hide_input=True)
    token_usuario = typer.prompt(
        "Pega aquí el token de USUARIO obtenido en el Explorador de la API de Graph",
        hide_input=True,
    )

    page_id = (brand.cuentas.get("facebook") or {}).get("page_id")
    if not page_id:
        _fallar(
            f"falta facebook.page_id en {brand.raiz / 'accounts.yml'}: "
            "configúralo antes de autenticar Facebook o Instagram (ambos "
            "publican con el token de esa Página)."
        )

    typer.echo("Canjeando el token con Meta (puede tardar unos segundos)...")
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
        ) or "ninguna"
        _fallar(
            f"la Página con id {page_id!r} (configurada en accounts.yml) no "
            "está entre las páginas administradas por el usuario que ha "
            f"autorizado el token. Páginas disponibles: {disponibles}."
        )

    token_pagina = pagina.get("access_token")
    if not isinstance(token_pagina, str) or not token_pagina:
        _fallar("Meta no ha devuelto un token de página válido para esa Página.")

    brand.guardar_secreto(platform, {"access_token": token_pagina})
    typer.echo(f"Credenciales de {platform.value} guardadas para {brand.nombre}.")


def _auth_oauth_redireccion(
    brand: Brand, platform: Platform, *, youtube_management: bool = False
) -> None:
    """Autentica YouTube o TikTok mediante redirección a un servidor local.

    Genera `state` (CSRF) y el par PKCE (`code_verifier`/`code_challenge`)
    de nuevo en cada intento -nunca se reutilizan-, abre el navegador en la
    URL de autorización, espera la redirección en un servidor de un solo
    uso (`_esperar_codigo`), verifica que el `state` recibido coincide
    exactamente con el generado (si no, aborta sin canjear nada: posible
    CSRF) y canjea el código por tokens.
    """
    if platform is Platform.YOUTUBE:
        client_id = typer.prompt("client_id de Google Cloud")
        client_secret = typer.prompt("client_secret de Google Cloud", hide_input=True)
        credenciales_app = {"client_id": client_id, "client_secret": client_secret}
        identificador = client_id
    else:
        client_key = typer.prompt("client_key de TikTok")
        client_secret = typer.prompt("client_secret de TikTok", hide_input=True)
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
    typer.echo(f"Abriendo el navegador para autorizar {platform.value}...")
    typer.echo(url)
    webbrowser.open(url)

    try:
        recibido = _esperar_codigo(PUERTO_CALLBACK, estado)
    except RuntimeError as exc:
        _fallar(str(exc))

    if "error" in recibido:
        detalle = recibido.get("error_description") or recibido["error"]
        _fallar(f"{platform.value} rechazó la autorización: {detalle}")

    try:
        verificar_state(estado, recibido.get("state"))
    except EstadoInvalido as exc:
        _fallar(str(exc))

    codigo = recibido.get("code")
    if not codigo:
        _fallar(
            "no se recibió el código de autorización (¿se cerró la pestaña "
            "antes de completar el login?). Vuelve a intentarlo."
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
    typer.echo(f"Credenciales de {platform.value} guardadas para {brand.nombre}.")


def _guardar_open_id_tiktok(brand: Brand, secreto: dict) -> None:
    """Extrae el `open_id` del `secreto` recién canjeado y lo guarda en accounts.yml.

    `canjear_codigo` ya deja el `open_id` en `secreto` cuando la respuesta
    de TikTok lo incluye -que es el caso normal: confirmado con Context7
    que `/v2/oauth/token/` lo trae directamente, sin falta de una llamada
    aparte a `/v2/user/info/` (ver el docstring de `canjear_codigo`)-. Esta
    función lo saca de ahí (no se duplica en `.secrets/tiktok.json`: vive
    solo en `accounts.yml`, igual que `page_id` o `ig_user_id`) y lo
    escribe con `Brand.guardar_open_id_tiktok`, que conserva los
    comentarios del fichero.

    Ningún fallo de este paso accesorio puede hacer fracasar la
    autenticación: el `access_token` ya se obtuvo y es lo importante, así
    que cualquier problema aquí (accounts.yml con una forma inesperada,
    error de E/S, o que la respuesta de TikTok simplemente no trajera el
    `open_id`) se degrada a un aviso por pantalla con instrucciones para
    rellenarlo a mano, nunca a una excepción que impida guardar el token.
    """
    open_id = secreto.pop("open_id", None)
    if not open_id:
        typer.echo(
            "Aviso: la respuesta de TikTok no incluyó el open_id de la cuenta. "
            "El token de acceso sí se ha guardado. Añade tiktok.open_id a mano "
            "en accounts.yml (ver el comentario de esa sección, o SETUP.md)."
        )
        return

    try:
        anterior = brand.guardar_open_id_tiktok(open_id)
    except Exception:
        typer.echo(
            "Aviso: no se pudo guardar el open_id automáticamente en "
            f"{brand.raiz / 'accounts.yml'} (revisa que la sección 'tiktok:' "
            "tenga la forma esperada). El token de acceso sí se ha guardado; "
            "añade tiktok.open_id a mano."
        )
        return

    if anterior is not None:
        typer.echo(
            f"Aviso: el open_id de TikTok en accounts.yml cambia de "
            f"'{anterior}' a '{open_id}'."
        )
    else:
        typer.echo(f"open_id de TikTok guardado en accounts.yml: {open_id}")


@app.command()
def auth(
    red: str,
    brand_nombre: str = typer.Option(..., "--brand", help="Marca a autenticar."),
    root: Path = typer.Option(RAIZ_POR_DEFECTO, help="Carpeta Social/ que contiene las marcas."),
    management: bool = typer.Option(
        False,
        "--management",
        help="YouTube: pide también youtube.force-ssl para editar metadatos.",
    ),
    status_platform: Platform | None = typer.Option(None, "--platform", help="Red explícita para auth status."),
    json_output: bool = typer.Option(False, "--json", help="auth status: salida versionada."),
) -> None:
    """Obtiene y guarda las credenciales iniciales de una red para una marca.

    YouTube y TikTok: abre el navegador para autorizar la app y recibe el
    código en un servidor local de un solo uso (ver `_auth_oauth_redireccion`).
    Facebook e Instagram: pide pegar un token de usuario del Explorador de la
    API de Graph y lo canjea por el token de página que hace falta (ver
    `_auth_meta`). Ningún secreto pegado o recibido se muestra en pantalla ni
    se filtra en un mensaje de error.
    """
    if red == "status":
        from socialctl.inventory_cli import show_auth_status
        show_auth_status(root, brand_nombre, status_platform, json_output)
        return
    brand = _cargar_marca(root, brand_nombre)

    try:
        platform = Platform(red)
    except ValueError:
        validas = ", ".join(p.value for p in Platform)
        _fallar(f"red desconocida: {red!r}. Redes válidas: {validas}")

    if management and platform is not Platform.YOUTUBE:
        _fallar("--management solo está disponible para YouTube")

    if platform in (Platform.FACEBOOK, Platform.INSTAGRAM):
        _auth_meta(brand, platform)
    else:
        _auth_oauth_redireccion(
            brand, platform, youtube_management=management
        )


@app.command()
def publish(
    slug: str,
    brand_nombre: str = typer.Option(..., "--brand", help="Marca a publicar."),
    root: Path = typer.Option(RAIZ_POR_DEFECTO, help="Carpeta Social/ que contiene las marcas."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Muestra el preview y no publica."),
    yes: bool = typer.Option(False, "--yes", help="No pedir confirmación."),
    only: list[str] = typer.Option(None, "--only", help="Publicar solo en estas redes."),
) -> None:
    """Valida todas las redes del post, muestra el preview y publica.

    Reglas: si hay problemas de validación en una red que se va a publicar
    se aborta sin publicar nada (código 1) y se listan; con `--only`, solo
    cuentan los problemas de las redes pedidas -una red no solicitada puede
    tener problemas sin bloquear las demás, pero nunca se publica una red
    solicitada que sí los tenga-. Con `--dry-run` termina justo después del
    preview, sin publicar (código 0). Sin `--yes` pide confirmación una
    única vez para las redes implicadas; a partir de ahí se publica en
    todas sin volver a preguntar. El código de salida es 1 si alguna red
    terminó en error.
    """
    brand = _cargar_marca(root, brand_nombre)
    post = _cargar_post_con_avisos(brand, slug)

    destinos: list[Platform] | None = None
    if only:
        try:
            destinos = [Platform(r) for r in only]
        except ValueError:
            validas = ", ".join(p.value for p in Platform)
            _fallar(f"--only con una red desconocida. Redes válidas: {validas}")

    _publicar_impl(post, brand, dry_run=dry_run, yes=yes, only=destinos)


@app.command("schedule")
def schedule(
    slug: str,
    at: str = typer.Option(..., "--at", help="Instante ISO 8601 con zona horaria."),
    brand_nombre: str = typer.Option(..., "--brand", help="Marca a programar."),
    root: Path = typer.Option(RAIZ_POR_DEFECTO, help="Carpeta Social/ que contiene las marcas."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Muestra el preview y no guarda la cola."),
    yes: bool = typer.Option(False, "--yes", help="Aprueba la cola sin volver a preguntar."),
    only: list[str] = typer.Option(None, "--only", help="Programar solo estas redes."),
    approval_digest: str | None = typer.Option(None, "--approval-digest", help="Digest del preview aprobado previamente."),
    preview_json: bool = typer.Option(False, "--preview-json", hidden=True),
) -> None:
    """Valida y añade un post de Facebook/Instagram a la cola local."""
    if preview_json and (not dry_run or yes or approval_digest):
        _fallar("--preview-json exige --dry-run y no admite aprobación")
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
            _fallar("--only solo admite youtube, facebook, instagram o tiktok")
    else:
        destinos = list(post.platforms)
    destinos = [p for p in destinos if p in (Platform.FACEBOOK, Platform.INSTAGRAM)]
    if not destinos:
        _fallar("schedule solo admite Facebook e Instagram y el post no contiene esas redes")
    if any(p not in post.platforms for p in destinos):
        _fallar("el post no contiene todas las redes solicitadas")
    try:
        displayed_hashes = {p.value: approval_hash(post, brand, p) for p in destinos}
        displayed_digest = schedule_digest(post.slug, displayed_hashes, scheduled_at)
    except ScheduleError as exc:
        _fallar(str(exc))
    errores = validar_todo(post, brand)
    preview = render_preview(post, errores, destinos=destinos, motivo_exclusion="no solicitada para esta programación")
    bloqueantes = [p for p in destinos if errores.get(p)]
    if preview_json:
        from socialctl.schedule_remote import SchedulePreview
        try:
            response = SchedulePreview(protocol="socialctl.schedule-preview.v1",
                                       valid=not bloqueantes, preview="\n".join([*preview_warnings, preview]),
                                       digest=displayed_digest, scheduled_at=scheduled_at.isoformat())
        except ValueError:
            _fallar("preview no seguro; revisa el contenido localmente antes de continuar")
        typer.echo(response.model_dump_json())
        raise typer.Exit(1 if bloqueantes else 0)
    typer.echo(preview)
    typer.echo(f"Approval digest: {displayed_digest}")
    if bloqueantes:
        typer.echo("\nNo se programa nada: corrige los problemas de arriba.")
        raise typer.Exit(1)
    if dry_run:
        typer.echo(f"\n--dry-run: se programaría para {scheduled_at.isoformat()}.")
        raise typer.Exit(0)
    if approval_digest and not hmac.compare_digest(approval_digest, displayed_digest):
        _fallar("el digest no coincide con el preview aprobado; ejecuta --dry-run y aprueba el contenido actual")
    if not yes and not _confirmar(f"\n¿Guardar {len(destinos)} programación(es) en la cola local?"):
        typer.echo("Cancelado. No se ha guardado ninguna programación.")
        raise typer.Exit(0)
    # Re-read disk/account changes as well as media bytes after confirmation.
    current_brand = _cargar_marca(root, brand_nombre)
    current_post = _cargar_post_con_avisos(current_brand, slug)
    try:
        if ({p.value: approval_hash(current_post, current_brand, p) for p in destinos} != displayed_hashes
                or {p.value: approval_hash(post, brand, p) for p in destinos} != displayed_hashes):
            _fallar("el contenido cambió después del preview; vuelve a ejecutar --dry-run")
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
    typer.echo(f"Cola actualizada: {len(destinos)} entrada(s).")


@app.command("schedule-status")
def schedule_status(
    brand_nombre: str = typer.Option(..., "--brand", help="Marca cuya cola consultar."),
    root: Path = typer.Option(RAIZ_POR_DEFECTO, help="Carpeta Social/ que contiene las marcas."),
) -> None:
    """Muestra la cola de programación de una marca."""
    brand = _cargar_marca(root, brand_nombre)
    if is_remote(brand.raiz):
        typer.echo(REMOTE_NOTICE)
        return
    entries = ScheduleStore(brand.raiz).load()
    if not entries:
        typer.echo("La cola está vacía.")
        return
    for entry in entries:
        error = " — error registrado (detalle omitido)" if entry.last_error else ""
        typer.echo(f"{entry.id} | {entry.scheduled_at.isoformat()} | {entry.status} | intentos={entry.attempts}{error}")


@app.command("schedule-batch")
def schedule_batch(
    fichero: Path,
    brand_nombre: str = typer.Option(..., "--brand", help="Marca a programar."),
    root: Path = typer.Option(RAIZ_POR_DEFECTO, help="Carpeta Social/ que contiene las marcas."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Muestra los previews y no guarda la cola."),
    yes: bool = typer.Option(False, "--yes", help="Omite la pregunta después de mostrar los previews."),
) -> None:
    """Programa varios posts desde un YAML, de forma atómica."""
    brand = _cargar_marca(root, brand_nombre)
    if is_remote(brand.raiz):
        _fallar(REMOTE_NOTICE)
    try:
        datos = yaml.safe_load(fichero.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        _fallar(f"no se pudo leer el lote {fichero}: {exc}")
    if not isinstance(datos, list) or not datos:
        _fallar("el lote debe ser una lista YAML no vacía")
    nuevas: list[ScheduleEntry] = []
    previews: list[
        tuple[int, Post, list[Platform], datetime, dict[Platform, list]]
    ] = []
    aprobaciones: list[tuple[Post, Platform, str]] = []
    problemas: list[str] = []
    now = now_utc()
    for index, item in enumerate(datos, start=1):
        if not isinstance(item, dict) or not isinstance(item.get("slug"), str) or not isinstance(item.get("at"), str):
            _fallar(f"entrada {index} del lote necesita 'slug' y 'at'")
        try:
            at = parse_scheduled_at(item["at"])
            post = _cargar_post_con_avisos(brand, item["slug"])
        except ScheduleError as exc:
            _fallar(f"entrada {index}: {exc}")
        only = item.get("only", ["facebook", "instagram"])
        if not isinstance(only, list):
            _fallar(f"entrada {index}: 'only' debe ser una lista")
        try:
            plataformas = [Platform(value) for value in only]
        except ValueError:
            _fallar(f"entrada {index}: red desconocida en 'only'")
        plataformas = [p for p in plataformas if p in (Platform.FACEBOOK, Platform.INSTAGRAM) and p in post.platforms]
        errores = validar_todo(post, brand)
        if any(errores.get(p) for p in plataformas):
            problemas.append(
                f"entrada {index} ({post.slug}): hay problemas de validación"
            )
        previews.append((index, post, plataformas, at, errores))
        for platform in plataformas:
            try:
                content_hash = approval_hash(post, brand, platform)
            except ScheduleError as exc:
                _fallar(f"entrada {index} ({post.slug}/{platform.value}): {exc}")
            nuevas.append(ScheduleEntry(
                id=f"{post.slug}/{platform.value}", brand=brand.nombre,
                slug=post.slug, platform=platform.value, scheduled_at=at,
                status="approved", created_at=now, updated_at=now,
                content_hash=content_hash,
            ))
            aprobaciones.append((post, platform, content_hash))
    for index, post, plataformas, at, errores in previews:
        typer.echo(
            f"\n=== Entrada {index}: {post.slug} → {at.isoformat()} ==="
        )
        typer.echo(
            render_preview(
                post,
                errores,
                destinos=plataformas,
                motivo_exclusion="no incluida en esta entrada del lote",
            )
        )
    typer.echo(f"Entradas válidas: {len(nuevas)}")
    for entry in sorted(nuevas, key=lambda value: value.scheduled_at):
        typer.echo(f"  {entry.id} → {entry.scheduled_at.isoformat()}")
    if problemas:
        typer.echo("\nNo se programa nada: corrige los problemas de arriba.")
        _fallar("; ".join(problemas))
    if dry_run:
        typer.echo("--dry-run: no se ha guardado la cola.")
        raise typer.Exit(0)
    if not yes and not _confirmar("¿Guardar este lote en la cola local?"):
        typer.echo("Cancelado. No se ha guardado ninguna programación.")
        raise typer.Exit(0)
    # Las huellas se toman antes de renderizar. Se vuelven a calcular tras la
    # aprobación para que ni ``--yes`` ni una mutación concurrente de la media
    # puedan convertir en aprobada una carga distinta de la que se mostró.
    for post, platform, displayed_hash in aprobaciones:
        try:
            current_hash = approval_hash(post, brand, platform)
        except ScheduleError as exc:
            _fallar(
                f"{post.slug}/{platform.value}: no se pudo verificar de nuevo "
                f"la aprobación después del preview: {exc}"
            )
        if not hmac.compare_digest(current_hash, displayed_hash):
            _fallar(
                f"{post.slug}/{platform.value}: el contenido, la media o la cuenta "
                "cambió después del preview; vuelve a ejecutar --dry-run y aprueba "
                "el nuevo contenido"
            )
    try:
        ScheduleStore(brand.raiz).add_many(nuevas)
    except ScheduleError as exc:
        _fallar(str(exc))
    typer.echo(f"Cola actualizada: {len(nuevas)} entrada(s).")


@app.command("schedule-cancel")
def schedule_cancel(
    entry_id: str,
    brand_nombre: str = typer.Option(..., "--brand", help="Marca cuya programación cancelar."),
    root: Path = typer.Option(RAIZ_POR_DEFECTO, help="Carpeta Social/ que contiene las marcas."),
) -> None:
    """Cancela una programación que aún no se ha ejecutado."""
    brand = _cargar_marca(root, brand_nombre)
    store = ScheduleStore(brand.raiz)
    try:
        store.cancel(entry_id, now_utc())
    except ScheduleError as exc:
        _fallar(str(exc))
    typer.echo(f"Cancelado: {entry_id}")


@app.command("schedule-reschedule")
def schedule_reschedule(
    entry_id: str,
    at: str = typer.Option(..., "--at", help="Nuevo instante ISO 8601 con zona horaria."),
    brand_nombre: str = typer.Option(..., "--brand", help="Marca cuya programación cambiar."),
    root: Path = typer.Option(RAIZ_POR_DEFECTO, help="Carpeta Social/ que contiene las marcas."),
) -> None:
    """Cambia la hora de una programación pendiente."""
    brand = _cargar_marca(root, brand_nombre)
    try:
        scheduled_at = parse_scheduled_at(at)
        store = ScheduleStore(brand.raiz)
        store.reschedule(entry_id, scheduled_at, now_utc())
    except ScheduleError as exc:
        _fallar(str(exc))
    typer.echo(f"Reprogramado: {entry_id} → {scheduled_at.isoformat()}")


@app.command("run-due")
def run_due(
    brand_nombre: str = typer.Option(..., "--brand", help="Marca cuya cola ejecutar."),
    root: Path = typer.Option(RAIZ_POR_DEFECTO, help="Carpeta Social/ que contiene las marcas."),
) -> None:
    """Ejecuta una vez las publicaciones aprobadas cuya hora ya llegó."""
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
    """Procesa una cola mientras el llamador conserva el lock del executor."""
    store.assert_ready()
    recuperadas = store.recover_stale()
    if recuperadas:
        typer.echo(
            f"Se aislaron {recuperadas} ejecución(es) interrumpida(s) para revisión manual."
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
                typer.echo(f"{entry.id}: entrada anterior: origen no verificado")
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
                typer.echo(f"{entry.id}: error de validación — {error}")
                continue
            if platform is Platform.INSTAGRAM:
                from socialctl.hosted_media import verify_scheduled_instagram
                verify_scheduled_instagram(post, brand, entry=entry)
            typer.echo(f"Publicando {entry.id}...")
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
                f"ejecución interrumpida ({type(exc).__name__}): confirma el "
                "resultado remoto antes de reintentar"
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
    """Estado durable del ejecutor; stale indica más de tres minutos sin latido."""
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
    brand_nombre: str = typer.Option(..., "--brand", help="Marca para instalar el agente."),
    root: Path = typer.Option(RAIZ_POR_DEFECTO, help="Carpeta Social/ que contiene las marcas."),
) -> None:
    """Instala el ejecutor periódico de la cola como agente launchd de macOS."""
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
    uv_path = shutil.which("uv")
    if uv_path:
        program = [uv_path, "run", "--project", str(root.resolve()), "socialctl"]
    else:
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
            typer.echo("Aviso: el plist se guardó, pero launchd no pudo cargarlo; revisa el log del sistema.")
    typer.echo(f"Agente instalado: {plist_path}")
    typer.echo("launchd lo ejecutará cada 60 segundos; la Mac debe estar encendida y despierta.")


@app.command("schedule-uninstall")
def schedule_uninstall(
    brand_nombre: str = typer.Option(..., "--brand", help="Marca cuyo agente quitar."),
) -> None:
    """Quita el agente launchd local de una marca."""
    label = f"com.socialctl.{brand_nombre.lower()}.run-due"
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    if not plist_path.exists():
        typer.echo(f"No existe el agente: {plist_path}")
        return
    launchctl = shutil.which("launchctl")
    if launchctl:
        subprocess.run([launchctl, "bootout", f"gui/{os.getuid()}/{label}"], check=False, capture_output=True)
    plist_path.unlink()
    typer.echo(f"Agente eliminado: {plist_path}")


@app.command()
def retry(
    slug: str,
    brand_nombre: str = typer.Option(..., "--brand", help="Marca a republicar."),
    root: Path = typer.Option(RAIZ_POR_DEFECTO, help="Carpeta Social/ que contiene las marcas."),
    yes: bool = typer.Option(False, "--yes", help="No pedir confirmación."),
) -> None:
    """Republica solo las redes que quedaron en error en el intento anterior.

    Antes de reintentar nada, vuelve a cargar post.yml -puede haber
    cambiado desde el intento fallido- y, a través de `_publicar_impl`,
    vuelve a validar TODO el post, pero (desde que se arregló `--only`)
    solo bloquean los problemas de las redes que de verdad se van a
    reintentar (`only=[seguras]`, más abajo): un error de validación en una
    red que no se reintenta en este intento -porque ya quedó publicada, o
    porque está marcada `riesgo_duplicado`- no aborta el reintento de las
    demás. Si una red que antes falló ya no figura en post.yml, se avisa y
    no se reintenta (probablemente se quitó a propósito).

    Si una red quedó en ERROR con `riesgo_duplicado=True` en resultado.json
    -el campo que marcan explícitamente Instagram y TikTok cuando la
    respuesta de la red llega en un estado ambiguo después de haber
    aceptado el contenido, ver `PostResult` en `socialctl/models.py`-,
    retry NUNCA la reintenta en automático: republicarla a ciegas es
    exactamente el escenario que ese campo advierte. Exige comprobar la
    cuenta a mano y, con conocimiento de causa, usar
    `publish --only <red> --yes` para forzarlo.

    Esta decisión depende del campo estructurado, nunca del texto de
    `error`: una versión anterior de este comando buscaba la palabra
    "duplicar" dentro del mensaje, lo que un adaptador futuro que
    expresara el mismo riesgo con otras palabras no dispararía -un fallo
    silencioso y grave, porque el resultado sería publicar dos veces-. Un
    resultado.json antiguo puede no incluir ese campo. Para Meta se consultan
    además los diarios durables de esta marca, slug y plataforma antes de elegir
    redes y de nuevo bajo lock antes del uploader: cualquier medio confirmado o
    incierto bloquea retry aunque resultado.json falte o contenga un error viejo.
    No se infiere cronología de varios intentos por sus nombres de fichero.
    Un publish explícito sigue siendo una publicación nueva intencionada.
    """
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
            _fallar("resultado.json ausente; el diario durable requiere reconciliación, no una nueva subida.")
        _fallar(f"no hay un intento previo en {fichero}; nada que reintentar.")

    try:
        contenido = json.loads(fichero.read_text(encoding="utf-8"))
        if not isinstance(contenido, dict):
            raise ValueError("la raíz del JSON no es un objeto")
        entradas = contenido["resultados"]
        if not isinstance(entradas, list):
            raise ValueError("'resultados' no es una lista")
        previos: dict[str, dict] = {}
        for entrada in entradas:
            if not isinstance(entrada, dict) or not isinstance(entrada.get("platform"), str):
                raise ValueError("una entrada de 'resultados' no tiene la forma esperada")
            previos[entrada["platform"]] = entrada
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        _fallar(f"no se pudo interpretar {fichero}: {exc}")

    fallidas = {
        plataforma for plataforma, entrada in previos.items()
        if entrada.get("status") == PostStatus.ERROR.value
    }
    if not fallidas:
        typer.echo("No hay redes fallidas que reintentar.")
        raise typer.Exit(1 if protected else 0)

    actuales = {p.value for p in post.platforms}
    faltantes = sorted(fallidas - actuales)
    vigentes = sorted(fallidas & actuales)

    if faltantes:
        typer.echo(
            f"Aviso: post.yml ya no incluye {', '.join(faltantes)} "
            "(estaban en error en el intento anterior); no se reintentan."
        )

    if not vigentes:
        typer.echo("No queda ninguna red fallida vigente que reintentar.")
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
            "\nATENCIÓN: estas redes no se reintentan en automático por "
            "riesgo de publicación duplicada (el intento anterior ya avisó "
            "de que el contenido puede haberse publicado):"
        )
        for p in arriesgadas:
            typer.echo(f"  - {p}: {previos[p].get('error')}")
        typer.echo(
            "Comprueba la cuenta a mano antes de volver a publicar en ellas "
            "(por ejemplo con: socialctl publish ... --only <red> --yes)."
        )

    if not seguras:
        raise typer.Exit(1)

    typer.echo(f"\nReintentando: {', '.join(seguras)}")

    codigo_publish = 0
    try:
        _publicar_impl(
            post, brand, dry_run=False, yes=yes,
            only=[Platform(p) for p in seguras],
            motivo_exclusion="no se reintenta en este intento",
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
    """Redes de un snapshot que quedaron en `error` o `sin_credenciales`.

    `sin_permiso` no cuenta a propósito: ese caso ya se explica entero en cada
    ejecución, con el scope que falta y el comando `auth`. El aviso existe
    para el fallo que pasa desapercibido, no para el que ya grita.
    """
    return {
        platform
        for platform, lectura in snapshot.redes.items()
        if lectura.estado
        in (EstadoLectura.ERROR, EstadoLectura.SIN_CREDENCIALES)
    }


def _avisar_de_fallos_persistentes(marca: Brand, snapshot: Snapshot) -> None:
    """Avisa de una red que lleva tres días seguidos sin leerse (spec §5).

    La «rutina» del spec no es otra cosa que este comando ejecutado a diario,
    así que el aviso lo da él: mira los dos snapshots anteriores al de hoy y,
    si una red falló en los tres, lo dice por pantalla. Cuesta dos lecturas de
    disco y ninguna petición más, y es la diferencia entre enterarse hoy de
    que un token caducó o dentro de tres semanas, con el histórico ya roto.

    Se mira el snapshot **anterior por fecha**, no «ayer»: si la rutina no
    corrió un día, tres snapshots seguidos en fallo siguen significando lo
    mismo. Con menos de dos snapshots previos no hay nada que afirmar y no se
    avisa.
    """
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
            f"AVISO: {platform.value} lleva {DIAS_DE_FALLO_PARA_AVISAR} días "
            f"seguidos sin leerse (también el {anterior.fecha.isoformat()} y "
            f"el {trasanterior.fecha.isoformat()}). Casi siempre es un token "
            f"caducado: ejecuta socialctl auth {platform.value} "
            f"--brand {marca.nombre}"
        )


def _limpiar_snapshots_y_avisar(marca: Brand) -> None:
    """Corre la retención de un año con cada `stats` y dice qué se ha borrado.

    La decisión y el mecanismo -qué es un año, qué fichero queda fuera,
    cómo se borra por nombre y no por `mtime`- viven en
    `limpiar_snapshots_antiguos` (`socialctl/metricas/almacen.py`); lo que
    hace este envoltorio es lo que le toca a `cli.py`: decidir que se llama
    en cada ejecución de `stats` (spec §5) y convertir el resultado en algo
    que el usuario lea. «Nada se pierde en silencio» es un principio ya
    establecido del proyecto (ver `_guardar_lo_leido`): si se borra algo,
    se dice, aunque sea rutina y no un error.

    Un fallo al borrar (permisos, por ejemplo) se avisa igual que un
    borrado, pero nunca aborta `stats`: para entonces el snapshot de hoy ya
    está en disco, y un fichero viejo que no se puede borrar es un
    problema de espacio, no de datos perdidos -al contrario, seguir sin
    poder borrarlo es la forma en que no se pierde nada-.
    """
    borrados, fallidos = limpiar_snapshots_antiguos(marca)
    if borrados:
        nombres = ", ".join(ruta.name for ruta in borrados)
        typer.echo(
            f"Limpieza: borrados {len(borrados)} snapshot(s) de más de "
            f"{RETENCION_SNAPSHOTS_DIAS} días ({nombres})."
        )
    for ruta, exc in fallidos:
        typer.echo(
            f"AVISO: no se pudo borrar el snapshot antiguo {ruta} ({exc}). "
            "Sigue en disco; bórralo a mano si quieres liberar el espacio."
        )


def _fusionar_snapshot_del_dia(marca: Brand, nuevo: Snapshot) -> Snapshot:
    """Con `--only`, fusiona la lectura de hoy con el snapshot que ya
    hubiera del día en vez de reemplazarlo entero.

    Hallazgo al ejecutar contra cuentas reales: un `stats --brand X` leía
    las cuatro redes y guardaba el snapshot del día; un `stats --brand X
    --only instagram` posterior, el MISMO día, sobrescribía ese fichero
    entero y dejaba solo instagram, tirando las otras tres -con su cuota de
    API ya gastada, que no se recupera hasta el día siguiente-. El spec
    (§4) decía sin matices que un segundo `stats` el mismo día
    "sobrescribe", y eso es correcto para una lectura completa (se quiere
    el dato más fresco, no dos ficheros), pero destructivo con `--only`:
    pedir UNA red no puede significar perder las otras tres.

    Dónde vive la fusión: aquí, en el comando, no en
    `almacen.guardar_snapshot` (que no cambia de firma: sigue escribiendo
    exactamente lo que se le pasa, y `cargar_snapshot` tampoco cambia).
    `guardar_snapshot`/`cargar_snapshot` son primitivas de lectura/escritura
    que ya usan otras tareas (`snapshot_anterior`, el resumen...) y no
    tienen por qué conocer `--only`, que es un concepto del CLI, no del
    almacén; meter esta política ahí dentro obligaría a cualquier llamador
    futuro que sí quiera sobrescribir sin matices a esquivarla. `stats` ya
    sabe si se pidió `--only` -es quien decide `destinos`-, así que es quien
    debe decidir si fusiona. Por eso esta función solo se llama cuando
    `only` está presente; una lectura completa nunca pasa por aquí y sigue
    sobrescribiendo tal cual, sin cambios.

    Casos:

    - Sin snapshot previo hoy: nada que fusionar, se guarda `nuevo` tal
      cual (fusionar con `{}` es un no-op).
    - Una red no pedida hoy: se conserva intacta la entrada de `anterior`
      (ni se toca su `estado`, ni sus piezas).
    - Una red pedida hoy que hoy FALLA pero el snapshot de hoy ya tenía un
      dato bueno (`OK`) para ella: se conserva ese dato bueno (`cuenta`,
      `piezas`, `audiencia`) -una lectura fallida no es un dato mejor que
      uno bueno de hace una hora-, pero `estado` y `error` son los de HOY,
      nunca los de antes: no se puede fingir que la red va bien cuando el
      intento de hoy ha fallado. Con esto, el resumen y `piezas.yml` siguen
      viendo los últimos números buenos en vez de un hueco, y el aviso de
      fallos persistentes (`_avisar_de_fallos_persistentes`, que mira
      `estado`) sigue detectando el fallo de hoy con normalidad -no queda
      tapado por el dato bueno que se conserva-.
    - La `fecha` del snapshot fusionado es la de `nuevo` (la de hoy),
      siempre.
    """
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
    """Escribe los tres ficheros del día y, si uno falla, dice cuál y qué quedó escrito.

    Los tres son independientes y se escriben en este orden: el snapshot
    (el dato crudo), `piezas.yml` (lo editorial) y `resumen.md` (la vista
    legible, que se regenera entera a partir de los dos anteriores). Cada
    uno se escribe de forma atómica (ver `escribir_atomico` en
    `socialctl/metricas/almacen.py`), así que un fallo nunca deja un fichero
    a medias; lo que sí puede pasar es que se escriban unos y otros no, y
    eso es justo lo que este envoltorio cuenta.

    Sin esto, un `piezas.yml` ilegible o un disco lleno salían como una
    traza de Python que no le decía al usuario ni qué fichero mirar ni qué
    se había guardado ya. Aquí sale en español y con las dos cosas.
    """
    pasos = (
        ("el snapshot del día", lambda: guardar_snapshot(marca, snapshot)),
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
                f"no se pudo actualizar piezas.yml: {exc}",
                "Ese fichero se ha dejado intacto a propósito: lo editorial "
                "que contiene no se puede volver a pedir a ninguna API.",
            )
        except OSError as exc:
            _fallar_guardando(
                escritos, pendientes,
                f"no se pudo escribir {nombre}: {exc}",
                "Ninguno de los ficheros queda a medias: se escriben de "
                "forma atómica, así que lo que no se guardó conserva su "
                "contenido anterior entero.",
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
    """Termina `stats` diciendo en español qué se guardó y qué no."""
    lineas = [f"ERROR: {motivo}"]
    lineas.append(
        "Sí se guardó: " + "; ".join(escritos) + "."
        if escritos
        else "No se guardó ninguno de los ficheros de esta ejecución."
    )
    if pendientes:
        lineas.append("No se guardó: " + ", ".join(pendientes) + ".")
    if coletilla:
        lineas.append(coletilla)
    _fallar("\n".join(lineas))


@app.command()
def stats(
    brand_nombre: str = typer.Option(..., "--brand", help="Marca de la que leer métricas."),
    root: Path = typer.Option(RAIZ_POR_DEFECTO, help="Carpeta Social/ que contiene las marcas."),
    only: list[str] = typer.Option(None, "--only", help="Leer solo estas redes."),
    desde: str = typer.Option(
        None, "--desde", help="Solo piezas publicadas desde esta fecha (AAAA-MM-DD)."
    ),
) -> None:
    """Lee las métricas de las redes y guarda un snapshot de la marca.

    Solo lectura: no publica, no borra y no modifica nada en ninguna red. Una
    red que falle no impide leer las demás; su motivo queda en el snapshot y
    se imprime aquí.

    Sin `--only` (lectura completa), el snapshot de hoy se sobrescribe
    entero: se quiere el dato más fresco de las cuatro redes, no arrastrar
    nada de antes. Con `--only`, el snapshot de hoy se FUSIONA con el que ya
    hubiera -las redes pedidas se actualizan, las demás se conservan tal
    cual- en vez de perderlas (ver `_fusionar_snapshot_del_dia`).

    Cada ejecución borra además los snapshots de más de un año -nunca
    `piezas.yml` ni `resumen.md`- y dice qué ha borrado (ver
    `limpiar_snapshots_antiguos` en `socialctl/metricas/almacen.py`).
    """
    marca = _cargar_marca(root, brand_nombre)

    fecha_desde = None
    if desde is not None:
        try:
            fecha_desde = date.fromisoformat(desde)
        except ValueError:
            _fallar(f"'--desde {desde}' no es una fecha AAAA-MM-DD válida.")

    # Mismo patrón que `publish` (`socialctl/cli.py:638-643`): la opción se
    # declara como texto y se convierte aquí, para que una red desconocida dé
    # un mensaje en español en vez del error de click, que sale en inglés.
    destinos: list[Platform]
    if only:
        try:
            destinos = [Platform(r) for r in only]
        except ValueError:
            validas = ", ".join(p.value for p in Platform)
            _fallar(f"--only con una red desconocida. Redes válidas: {validas}")
    else:
        destinos = list(LECTORES)

    redes = {}
    with httpx.Client(timeout=60.0) as client:
        for platform in destinos:
            typer.echo(f"Leyendo {platform.value}...")
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
            typer.echo(f"{platform.value}: ok, {len(lectura.piezas)} piezas")
        else:
            typer.echo(f"{platform.value}: {lectura.estado.value} — {lectura.error}")

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
