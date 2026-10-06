"""Flujo de autorización inicial (OAuth) por red, para el comando `auth`.

Ningún mensaje de error de este módulo puede contener un secreto: ni el
`client_secret`, ni el código de autorización, ni ningún `access_token` ni
`refresh_token`, ni el `code_verifier` de PKCE, ni el token de usuario que se
pega a mano para Meta. Los `except` de red de este módulo nunca interpolan el
cuerpo de la respuesta ni `str(exc)` de una excepción no prevista; solo
mensajes fijos y, como mucho, el código de estado HTTP.

YouTube y TikTok comparten el mismo patrón: redirección a un servidor local
(`socialctl/cli.py` levanta ese servidor; este módulo no toca la red del
navegador) con PKCE obligatorio para TikTok y recomendado -pero igualmente
aplicado aquí, por prudencia- para Google:

- TikTok exige PKCE en su flujo de escritorio: un `code_verifier` único por
  petición, su `code_challenge` (S256) en la URL de autorización, y el mismo
  `code_verifier` en el canje del código. Confirmado con Context7
  (developers.tiktok.com/docs/en/login-kit-overview, "Platform differences >
  Desktop": "Desktop authorization follows a similar redirect flow to web but
  requires the use of PKCE for security"; y
  developers.tiktok.com/docs/en/oauth-user-access-token-management, que lista
  `code_verifier` como campo del canje "required for mobile and desktop apps
  only"). El brief original de esta tarea NO incluía PKCE en absoluto: ese
  flujo habría sido rechazado por TikTok.
- Un detalle propio de TikTok, distinto del estándar RFC 7636 que usa Google:
  su `code_challenge` es SHA256 del verifier codificado en **hexadecimal**,
  no en base64url. Confirmado con Context7
  (developers.tiktok.com/docs/en/login-kit-desktop, "Generate code
  challenge": `CryptoJS.SHA256(code_verifier).toString(CryptoJS.enc.Hex)`,
  repetido igual en varias secciones de esa misma página). Usar la
  codificación base64url estándar aquí produciría un `code_challenge` que no
  coincide con el que TikTok recalcula, y el canje del código fallaría.
- Google trata PKCE como **recomendado, no obligatorio**, para apps de
  escritorio (confirmado con Context7,
  developers.google.com/identity/protocols/oauth2/native-app: "code_challenge
  ... Recommended", "code_challenge_method ... Recommended"; y
  developers.google.com/identity/protocols/oauth2/resources/best-practices:
  "For desktop applications, implementing PKCE is strongly recommended").
  Como no hace daño y Google lo soporta con la codificación estándar
  (`code_challenge = BASE64URL-ENCODE(SHA256(ASCII(code_verifier)))`, misma
  fuente), este módulo lo aplica también a YouTube, no solo a TikTok.

Facebook e Instagram no tienen este patrón de redirección: sus apps deben
estar en modo "Live" con revisión de Meta para usar un dominio HTTPS propio
como `redirect_uri`, y no hay documentación que confirme que acepten
`http://localhost` para una app de escritorio (a diferencia de Google y
TikTok, que sí lo documentan explícitamente); no verificado, así que este
módulo no lo ofrece. Lo que SÍ mejora respecto al brief original -que pedía
pegar directamente un token de PÁGINA de larga duración, algo que exige que
el usuario sepa hacer a mano el intercambio en el Explorador de la API de
Graph- es automatizar ese intercambio: el usuario pega un token de
**usuario** (el que el Explorador genera por defecto, de corta duración) y
`intercambiar_token_meta` + `obtener_paginas_meta` hacen aquí el resto:
canjearlo por uno de usuario de larga duración (~60 días) y, con ese,
obtener el token de PÁGINA -que Meta documenta sin fecha de caducidad-
correspondiente a la Página configurada en accounts.yml. Confirmado con
Context7 (developers.facebook.com/docs/facebook-login/guides/access-tokens/
get-long-lived): `GET /oauth/access_token` con `grant_type=fb_exchange_token`
para el primer paso, y `GET /{user-id}/accounts` (aquí, `/me/accounts` con el
token de usuario de larga duración) para el segundo, cuyo campo
`access_token` por página "do not have an expiration date and only expire or
are invalidated under certain conditions".
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

import httpx

from socialctl.models import Platform

GRAFO = "https://graph.facebook.com/v26.0"

SCOPES = {
    # A los scopes de subida se suman los de lectura, que usa `socialctl
    # stats`: `yt-analytics.readonly` para los informes de la YouTube
    # Analytics API (vistas, minutos vistos, retención, fuentes de tráfico) y
    # `youtube.readonly` para el catálogo de vídeos de la Data API. Se piden
    # junto al de subida para que un mismo token sirva para publicar y para
    # leer, sin tener que autenticar dos veces. Los dos añadidos son de solo
    # lectura: no permiten modificar ni borrar nada del canal. El scope de
    # gestión es opt-in y se añade en `construir_url_autorizacion` cuando el
    # CLI recibe `auth youtube --management`.
    # El separador de Google es un espacio, no una coma (a diferencia de
    # TikTok, más abajo).
    Platform.YOUTUBE: (
        "https://www.googleapis.com/auth/youtube.upload"
        " https://www.googleapis.com/auth/yt-analytics.readonly"
        " https://www.googleapis.com/auth/youtube.readonly"
    ),
    # video.publish permite publicar directamente (mode: direct, con la
    # app auditada); video.upload es lo mínimo para subir al buzón del
    # creador (mode: inbox). Se piden ambos para que un mismo secreto sirva
    # con cualquiera de los dos modos de tiktok.py sin tener que reautenticar
    # al cambiar de uno a otro. user.info.basic es el scope asociado a
    # consultar el perfil (GET /v2/user/info/) y, según la documentación,
    # el `open_id` de la cuenta viaja de todos modos en la propia respuesta
    # del canje (ver `canjear_codigo`); se pide igualmente aquí porque es
    # el scope declarado para ese dato y porque sin él una llamada futura a
    # /v2/user/info/ (por ejemplo, para volver a obtener el open_id de un
    # token ya existente) sería rechazada. El separador es una coma sin
    # espacios, igual que `video.upload,video.publish` ya usaba este
    # proyecto: confirmado con Context7
    # (developers.tiktok.com/docs/en/silent-login y /docs/en/minis-oauth,
    # cuyo campo `scope` de respuesta llega como "user.info.basic,video.list";
    # y los propios ejemplos de las SDKs nativas -Android, iOS- que listan
    # varios scopes separados por coma).
    # A los de subida se suman los de lectura, que usa `socialctl stats`:
    # `user.info.stats` da seguidores, likes totales y numero de videos;
    # `video.list` da los videos con sus vistas, likes, comentarios y
    # compartidos. Los dos son de la Display API -no de la Research API, que
    # exige aprobacion como investigador- y se piden junto a los de subida
    # para no autenticar dos veces.
    Platform.TIKTOK: "user.info.basic,user.info.stats,video.upload,video.publish,video.list",
}

YOUTUBE_MANAGEMENT_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"

URL_AUTORIZACION = {
    Platform.YOUTUBE: "https://accounts.google.com/o/oauth2/v2/auth",
    Platform.TIKTOK: "https://www.tiktok.com/v2/auth/authorize/",
}

URL_TOKEN = {
    Platform.YOUTUBE: "https://oauth2.googleapis.com/token",
    Platform.TIKTOK: "https://open.tiktokapis.com/v2/oauth/token/",
}

AYUDA_META = (
    "Facebook e Instagram no usan una redirección local: no hay documentación "
    "que confirme que Meta acepte http://localhost como redirect_uri para una "
    "app de escritorio. En su lugar, pega aquí un token de USUARIO (no de "
    "página) obtenido en el Explorador de la API de Graph "
    "(https://developers.facebook.com/tools/explorer/): elige tu app, pulsa "
    "'Generate Access Token' y concede SIETE permisos: pages_show_list, "
    "pages_manage_posts, pages_read_engagement e instagram_content_publish "
    "para publicar, y read_insights, instagram_basic e "
    "instagram_manage_insights para leer metricas con `socialctl stats`. "
    "Concederlos todos de una vez evita tener que repetir este paso. "
    "Ese token puede ser de corta duración: este comando lo canjea por uno de "
    "página de larga duración automáticamente, para la Página configurada en "
    "accounts.yml (facebook.page_id)."
)


class EstadoInvalido(Exception):
    """El parámetro `state` recibido en la redirección no coincide con el generado.

    Es la comprobación anti-CSRF del flujo: si no coincide, hay que abortar
    sin canjear ningún código, porque no hay garantía de que la respuesta
    corresponda a la petición de autorización que este proceso inició.
    """


def generar_state() -> str:
    """Genera el valor aleatorio de `state` para protección CSRF.

    Debe generarse de nuevo en cada intento de autorización y verificarse
    con `verificar_state` al recibir la redirección; nunca reutilizarse.
    """
    return secrets.token_urlsafe(32)


def generar_code_verifier() -> str:
    """Genera un `code_verifier` de PKCE válido para Google y para TikTok.

    Ambas redes exigen una cadena de 43 a 128 caracteres del alfabeto no
    reservado ``[A-Za-z0-9\\-._~]`` (confirmado con Context7 en
    developers.google.com/identity/protocols/oauth2/native-app y en
    developers.tiktok.com/docs/en/login-kit-desktop, con idéntica
    redacción en ambas). `secrets.token_urlsafe` genera únicamente del
    subconjunto ``[A-Za-z0-9\\-_]`` (sin ``.`` ni ``~``, que tampoco hacen
    falta), que ya cae dentro de ese alfabeto en ambos casos.
    `secrets.token_urlsafe(64)` produce siempre una cadena de 86
    caracteres: dentro del rango exigido y con 512 bits de entropía
    aleatoria, muy por encima del mínimo que pide la especificación.
    """
    return secrets.token_urlsafe(64)


def _challenge_base64url(code_verifier: str) -> str:
    """PKCE estándar (RFC 7636), el que usa Google.

    ``code_challenge = BASE64URL-ENCODE(SHA256(ASCII(code_verifier)))``,
    sin relleno ``=`` (confirmado con Context7,
    developers.google.com/identity/protocols/oauth2/native-app).
    """
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _challenge_hex(code_verifier: str) -> str:
    """El `code_challenge` propio de TikTok: SHA256 en hexadecimal, no base64url.

    Ver el docstring del módulo para la cita exacta de la documentación.
    """
    return hashlib.sha256(code_verifier.encode("ascii")).hexdigest()


_GENERADOR_CHALLENGE = {
    Platform.YOUTUBE: _challenge_base64url,
    Platform.TIKTOK: _challenge_hex,
}


def calcular_code_challenge(platform: Platform, code_verifier: str) -> str:
    """Deriva el `code_challenge` de `code_verifier` con la codificación de `platform`."""
    if platform not in _GENERADOR_CHALLENGE:
        raise ValueError(AYUDA_META)
    return _GENERADOR_CHALLENGE[platform](code_verifier)


def verificar_state(esperado: str, recibido: str | None) -> None:
    """Aborta si `recibido` no coincide exactamente con `esperado`.

    Comparación en tiempo constante (`secrets.compare_digest`): el `state`
    no es un secreto de alto valor como un token, pero es la única defensa
    anti-CSRF de todo el intercambio, así que se compara con el mismo
    cuidado. `recibido=None` (la redirección no traía `state` en absoluto)
    se trata igual que un valor que no coincide: nunca se asume que "sin
    dato" equivale a "válido".
    """
    if recibido is None or not secrets.compare_digest(esperado, recibido):
        raise EstadoInvalido(
            "el parámetro 'state' recibido no coincide con el generado al "
            "iniciar la autorización (posible CSRF); abortando sin canjear "
            "ningún código. Vuelve a ejecutar 'socialctl auth'."
        )


def construir_url_autorizacion(
    platform: Platform,
    client_id: str,
    redirect_uri: str,
    *,
    state: str,
    code_verifier: str,
    youtube_management: bool = False,
) -> str:
    """URL a la que enviar al usuario para que autorice la app.

    `state` y `code_verifier` son obligatorios y deben venir de
    `generar_state()` y `generar_code_verifier()` respectivamente, generados
    de nuevo para cada intento de autorización (nunca reutilizados entre
    intentos): son la defensa anti-CSRF y la de PKCE, y ambas dependen de
    que el valor sea impredecible y de un solo uso.
    """
    if platform not in URL_AUTORIZACION:
        raise ValueError(AYUDA_META)
    if youtube_management and platform is not Platform.YOUTUBE:
        raise ValueError("el permiso --management solo está disponible para YouTube")

    code_challenge = calcular_code_challenge(platform, code_verifier)

    if platform is Platform.YOUTUBE:
        parametros = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": (
                f"{SCOPES[platform]} {YOUTUBE_MANAGEMENT_SCOPE}"
                if youtube_management
                else SCOPES[platform]
            ),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    else:
        parametros = {
            "client_key": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPES[platform],
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

    return f"{URL_AUTORIZACION[platform]}?{urlencode(parametros)}"


def canjear_codigo(
    platform: Platform,
    codigo: str,
    credenciales_app: dict,
    redirect_uri: str,
    client: httpx.Client,
    *,
    code_verifier: str,
) -> dict:
    """Canjea el código de autorización por tokens y devuelve el secreto a guardar.

    `code_verifier` debe ser el MISMO que generó el `code_challenge` enviado
    en `construir_url_autorizacion` para este mismo intento: es la prueba de
    posesión que exige PKCE. Sin él (o con uno distinto), TikTok y -si Google
    llegara a exigirlo también en el futuro- Google rechazan el canje.

    Un fallo de red real al hacer la petición (host inalcanzable, timeout,
    DNS) se traduce aquí en `RuntimeError` en vez de escapar como excepción
    cruda; ver el docstring del módulo para el detalle de qué familias de
    `httpx` se cubren y por qué ningún mensaje interpola el error original.
    """
    if platform not in URL_TOKEN:
        raise ValueError(AYUDA_META)

    if platform is Platform.YOUTUBE:
        datos = {
            "grant_type": "authorization_code",
            "code": codigo,
            "redirect_uri": redirect_uri,
            "client_id": credenciales_app["client_id"],
            "client_secret": credenciales_app["client_secret"],
            "code_verifier": code_verifier,
        }
    else:
        datos = {
            "grant_type": "authorization_code",
            "code": codigo,
            "redirect_uri": redirect_uri,
            "client_key": credenciales_app["client_key"],
            "client_secret": credenciales_app["client_secret"],
            "code_verifier": code_verifier,
        }

    try:
        respuesta = client.post(URL_TOKEN[platform], data=datos)
    except httpx.InvalidURL:
        # No hereda de httpx.HTTPError, así que necesita su propio except; la
        # URL es una constante de este módulo (URL_TOKEN), no algo escrito a
        # mano por el usuario, pero se cubre por si acaso, igual que en los
        # adaptadores de este proyecto.
        raise RuntimeError(
            f"no se pudo contactar con {platform.value} para canjear el "
            "código de autorización: la URL configurada no es válida. "
            "Vuelve a ejecutar el comando auth."
        ) from None
    except httpx.TimeoutException:
        # Subclase de httpx.HTTPError: debe ir antes que ese except para no
        # quedar inalcanzable.
        raise RuntimeError(
            f"se agotó el tiempo de espera al contactar con {platform.value} "
            "para canjear el código de autorización. Vuelve a ejecutar el "
            "comando auth."
        ) from None
    except httpx.HTTPError:
        raise RuntimeError(
            f"no se pudo conectar con {platform.value} para canjear el "
            "código de autorización. Comprueba tu conexión a internet y "
            "vuelve a ejecutar el comando auth."
        ) from None
    except Exception:
        # Resguardo final para cualquier fallo imprevisto que no sea de las
        # familias anteriores: nunca se interpola el propio error (podría
        # arrastrar la petición, con el client_secret y el code_verifier).
        raise RuntimeError(
            f"ocurrió un error inesperado al canjear el código de "
            f"autorización con {platform.value}. Vuelve a ejecutar el "
            "comando auth."
        ) from None

    if respuesta.status_code != 200:
        raise RuntimeError(
            f"{platform.value} rechazó el código de autorización "
            f"(HTTP {respuesta.status_code}). Vuelve a ejecutar el comando auth."
        )

    try:
        cuerpo = respuesta.json()
        access_token = cuerpo["access_token"]
    except Exception:
        # Cubre JSON mal formado, una raíz que no es un objeto, o un objeto
        # sin 'access_token'. No se interpola el cuerpo de la respuesta en
        # el mensaje: en teoría podría arrastrar algún dato sensible del
        # intercambio.
        raise RuntimeError(
            f"{platform.value} respondió con un formato inesperado al "
            "canjear el código de autorización"
        ) from None

    secreto = dict(credenciales_app)
    from socialctl.identity import record_granted_scopes
    record_granted_scopes(secreto, cuerpo)
    secreto["access_token"] = access_token
    if isinstance(cuerpo.get("refresh_token"), str):
        secreto["refresh_token"] = cuerpo["refresh_token"]
    # TikTok incluye el `open_id` de la cuenta directamente en esta misma
    # respuesta (confirmado con Context7: developers.tiktok.com/docs/en/
    # oauth-user-access-token-management -"these values, along with the
    # user's unique open_id and authorized scopes, should be stored"- y los
    # ejemplos de /docs/en/silent-login y /docs/en/minis-oauth, cuyo JSON de
    # respuesta trae "open_id" junto a "access_token"). No hace falta una
    # llamada aparte a /v2/user/info/ para obtenerlo: quien llama a esta
    # función (`_auth_oauth_redireccion` en cli.py) lo toma de aquí. Google
    # no documenta este campo, así que en YouTube simplemente no aparecerá.
    if isinstance(cuerpo.get("open_id"), str) and cuerpo["open_id"]:
        secreto["open_id"] = cuerpo["open_id"]
    secreto["expira_en"] = time.time() + float(cuerpo["expires_in"]) if "expires_in" in cuerpo else None
    return secreto


def intercambiar_token_meta(
    client_id: str, client_secret: str, token_corto: str, client: httpx.Client
) -> str:
    """Canjea un token de USUARIO de Meta por uno de larga duración (~60 días).

    Confirmado con Context7 (developers.facebook.com/docs/facebook-login/
    guides/access-tokens/get-long-lived): `GET /oauth/access_token` con
    `grant_type=fb_exchange_token`, `client_id`, `client_secret` (el App ID
    y App secret de la app de Meta, no un secreto de la marca) y
    `fb_exchange_token=<token pegado>`.

    Un fallo de red real al hacer la petición se traduce en `RuntimeError`
    en vez de escapar como excepción cruda (ver el docstring del módulo).
    """
    try:
        respuesta = client.get(
            f"{GRAFO}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "fb_exchange_token": token_corto,
            },
        )
    except httpx.InvalidURL:
        # No hereda de httpx.HTTPError, así que necesita su propio except.
        raise RuntimeError(
            "no se pudo contactar con Meta para intercambiar el token: la "
            "URL configurada no es válida. Vuelve a ejecutar el comando auth."
        ) from None
    except httpx.TimeoutException:
        # Subclase de httpx.HTTPError: debe ir antes que ese except para no
        # quedar inalcanzable.
        raise RuntimeError(
            "se agotó el tiempo de espera al contactar con Meta para "
            "intercambiar el token. Vuelve a ejecutar el comando auth."
        ) from None
    except httpx.HTTPError:
        raise RuntimeError(
            "no se pudo conectar con Meta para intercambiar el token. "
            "Comprueba tu conexión a internet y vuelve a ejecutar el "
            "comando auth."
        ) from None
    except Exception:
        # Resguardo final: nunca se interpola el propio error, podría
        # arrastrar la petición con el App secret o el token pegado.
        raise RuntimeError(
            "ocurrió un error inesperado al intercambiar el token con Meta. "
            "Vuelve a ejecutar el comando auth."
        ) from None

    if respuesta.status_code != 200:
        raise RuntimeError(
            f"Meta rechazó el intercambio de token (HTTP {respuesta.status_code}). "
            "Revisa el App ID, el App secret y que el token pegado sea válido "
            "y no haya caducado."
        )
    try:
        return respuesta.json()["access_token"]
    except Exception:
        raise RuntimeError(
            "Meta respondió con un formato inesperado al intercambiar el token"
        ) from None


def obtener_paginas_meta(token_usuario: str, client: httpx.Client) -> list[dict]:
    """Lista las páginas administradas por el usuario, con su token de página.

    Confirmado con Context7 (misma fuente que `intercambiar_token_meta`):
    `GET /me/accounts` con el token de usuario de larga duración devuelve,
    por cada página, `id`, `name` y `access_token` -este último ya es un
    token de PÁGINA de larga duración ("do not have an expiration date"),
    sin ningún paso adicional.

    Un fallo de red real al hacer la petición se traduce en `RuntimeError`
    en vez de escapar como excepción cruda (ver el docstring del módulo).
    """
    try:
        respuesta = client.get(f"{GRAFO}/me/accounts", params={"access_token": token_usuario})
    except httpx.InvalidURL:
        # No hereda de httpx.HTTPError, así que necesita su propio except.
        raise RuntimeError(
            "no se pudo contactar con Meta para listar las páginas: la URL "
            "configurada no es válida. Vuelve a ejecutar el comando auth."
        ) from None
    except httpx.TimeoutException:
        # Subclase de httpx.HTTPError: debe ir antes que ese except para no
        # quedar inalcanzable.
        raise RuntimeError(
            "se agotó el tiempo de espera al contactar con Meta para "
            "listar las páginas. Vuelve a ejecutar el comando auth."
        ) from None
    except httpx.HTTPError:
        raise RuntimeError(
            "no se pudo conectar con Meta para listar las páginas. "
            "Comprueba tu conexión a internet y vuelve a ejecutar el "
            "comando auth."
        ) from None
    except Exception:
        # Resguardo final: nunca se interpola el propio error, podría
        # arrastrar la petición con el token de usuario de larga duración.
        raise RuntimeError(
            "ocurrió un error inesperado al listar las páginas de Meta. "
            "Vuelve a ejecutar el comando auth."
        ) from None

    if respuesta.status_code != 200:
        raise RuntimeError(
            f"Meta rechazó la petición de páginas (HTTP {respuesta.status_code})."
        )
    try:
        paginas = respuesta.json()["data"]
    except Exception:
        raise RuntimeError(
            "Meta respondió con un formato inesperado al listar las páginas"
        ) from None

    if not isinstance(paginas, list):
        raise RuntimeError(
            "Meta respondió con un formato inesperado al listar las páginas"
        )
    return paginas
