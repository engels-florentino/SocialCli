"""Initial platform OAuth authorization; errors must never contain credentials, authorization codes or tokens."""

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
    # The public TikTok authorization flow is inbox-only. Request only the
    # permissions submitted for review; analytics and Direct Post are deferred.
    Platform.TIKTOK: "user.info.basic,video.upload",
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
    "Facebook and Instagram use a pasted USER token rather than a local redirect. "
    "Obtain it from the Graph API Explorer (https://developers.facebook.com/tools/explorer/): "
    "select your app, choose 'Generate Access Token' and grant seven permissions: "
    "pages_show_list, pages_manage_posts, pages_read_engagement and instagram_content_publish "
    "for publication, plus read_insights, instagram_basic and instagram_manage_insights "
    "for reading metrics with `socialcli stats`. Granting them together avoids repeating "
    "this step. This command exchanges the token for a long-lived Page token for the "
    "Page configured in accounts.yml (facebook.page_id)."
)


class EstadoInvalido(Exception):
    """The received OAuth state does not match the generated state."""


def generar_state() -> str:
    """Generate a random state value for CSRF protection."""
    return secrets.token_urlsafe(32)


def generar_code_verifier() -> str:
    """Generate a valid PKCE verifier for Google and TikTok."""
    return secrets.token_urlsafe(64)


def _challenge_base64url(code_verifier: str) -> str:
    """Return the standard RFC 7636 PKCE challenge used by Google."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _challenge_hex(code_verifier: str) -> str:
    """Return TikTok's hexadecimal SHA-256 PKCE challenge."""
    return hashlib.sha256(code_verifier.encode("ascii")).hexdigest()


_GENERADOR_CHALLENGE = {
    Platform.YOUTUBE: _challenge_base64url,
    Platform.TIKTOK: _challenge_hex,
}


def calcular_code_challenge(platform: Platform, code_verifier: str) -> str:
    """Derive the code challenge using the platform's required encoding."""
    if platform not in _GENERADOR_CHALLENGE:
        raise ValueError(AYUDA_META)
    return _GENERADOR_CHALLENGE[platform](code_verifier)


def verificar_state(esperado: str, recibido: str | None) -> None:
    """Abort unless the received state exactly matches the expected state."""
    if recibido is None or not secrets.compare_digest(esperado, recibido):
        raise EstadoInvalido(
            "received 'state' does not match the value generated when authorization started (possible CSRF); aborting without exchanging any code. Run 'socialcli auth' again."
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
    """Build the URL where the user authorizes their own app."""
    if platform not in URL_AUTORIZACION:
        raise ValueError(AYUDA_META)
    if youtube_management and platform is not Platform.YOUTUBE:
        raise ValueError("the --management permission is only available for YouTube")

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
    """Exchange an authorization code for tokens and return credentials to persist."""
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
            f'could not contact {platform.value} to exchange the authorization code: the configured URL is invalid. Run the auth command again.'
        ) from None
    except httpx.TimeoutException:
        # Subclase de httpx.HTTPError: debe ir antes que ese except para no
        # quedar inalcanzable.
        raise RuntimeError(
            f'timed out while contacting {platform.value} to exchange the authorization code. Run the auth command again.'
        ) from None
    except httpx.HTTPError:
        raise RuntimeError(
            f'could not connect to {platform.value} to exchange the authorization code. Check your internet connection and run the auth command again.'
        ) from None
    except Exception:
        # Resguardo final para cualquier fallo imprevisto que no sea de las
        # familias anteriores: nunca se interpola el propio error (podría
        # arrastrar la petición, con el client_secret y el code_verifier).
        raise RuntimeError(
            f'an unexpected error occurred while exchanging the authorization code with {platform.value}. Run the auth command again.'
        ) from None

    if respuesta.status_code != 200:
        raise RuntimeError(
            f"{platform.value} rejected the authorization code "
            f"(HTTP {respuesta.status_code}). Run the auth command again."
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
            f'{platform.value} returned an unexpected format while exchanging the authorization code'
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
    """Exchange a Meta USER token for a long-lived token of approximately 60 days."""
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
            'could not contact Meta to exchange the token: the configured URL is invalid. Run the auth command again.'
        ) from None
    except httpx.TimeoutException:
        # Subclase de httpx.HTTPError: debe ir antes que ese except para no
        # quedar inalcanzable.
        raise RuntimeError(
            'contacting Meta to exchange the token timed out. Run the auth command again.'
        ) from None
    except httpx.HTTPError:
        raise RuntimeError(
            'could not connect to Meta to exchange the token. Check your internet connection and run the auth command again.'
        ) from None
    except Exception:
        # Resguardo final: nunca se interpola el propio error, podría
        # arrastrar la petición con el App secret o el token pegado.
        raise RuntimeError(
            'an unexpected error occurred while exchanging the token with Meta. Run the auth command again.'
        ) from None

    if respuesta.status_code != 200:
        raise RuntimeError(
            f'Meta rejected the token exchange (HTTP {respuesta.status_code}). Check the app ID, app secret and whether the pasted token is valid and unexpired.'
        )
    try:
        return respuesta.json()["access_token"]
    except Exception:
        raise RuntimeError(
            "Meta returned an unexpected format while exchanging the token"
        ) from None


def obtener_paginas_meta(token_usuario: str, client: httpx.Client) -> list[dict]:
    """List Pages administered by the user, including their Page tokens."""
    try:
        respuesta = client.get(f"{GRAFO}/me/accounts", params={"access_token": token_usuario})
    except httpx.InvalidURL:
        # No hereda de httpx.HTTPError, así que necesita su propio except.
        raise RuntimeError(
            'could not contact Meta to list pages: the configured URL is invalid. Run the auth command again.'
        ) from None
    except httpx.TimeoutException:
        # Subclase de httpx.HTTPError: debe ir antes que ese except para no
        # quedar inalcanzable.
        raise RuntimeError(
            'contacting Meta to list pages timed out. Run the auth command again.'
        ) from None
    except httpx.HTTPError:
        raise RuntimeError(
            'could not connect to Meta to list pages. Check your internet connection and run the auth command again.'
        ) from None
    except Exception:
        # Resguardo final: nunca se interpola el propio error, podría
        # arrastrar la petición con el token de usuario de larga duración.
        raise RuntimeError(
            'an unexpected error occurred while listing Meta pages. Run the auth command again.'
        ) from None

    if respuesta.status_code != 200:
        raise RuntimeError(
            f"Meta rejected the pages request (HTTP {respuesta.status_code})."
        )
    try:
        paginas = respuesta.json()["data"]
    except Exception:
        raise RuntimeError(
            "Meta returned an unexpected format while listing pages"
        ) from None

    if not isinstance(paginas, list):
        raise RuntimeError(
            "Meta returned an unexpected format while listing pages"
        )
    return paginas
