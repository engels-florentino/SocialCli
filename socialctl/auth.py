"""OAuth: obtención y refresco de tokens por marca y por red.

Ningún mensaje de error de este módulo puede contener un token.
"""

from __future__ import annotations

import json
import time

import httpx

from socialctl.brands import Brand
from socialctl.models import Platform

MARGEN_S = 60  # se refresca un poco antes de que caduque

URL_REFRESCO = {
    Platform.YOUTUBE: "https://oauth2.googleapis.com/token",
    Platform.TIKTOK: "https://open.tiktokapis.com/v2/oauth/token/",
}


class AuthError(Exception):
    """Falta autenticación o ha dejado de ser válida."""


def _pedir_auth(platform: Platform, motivo: str) -> AuthError:
    return AuthError(
        f"{motivo}. Ejecuta: socialctl auth {platform.value} --brand <marca>"
    )


def _caducado(secreto: dict, platform: Platform) -> bool:
    """Indica si el token guardado ha caducado (o está a punto de hacerlo).

    Si ``expira_en`` no es ``None`` pero tampoco es convertible a ``float``
    (un fichero de secretos manipulado o corrupto), no se propaga el
    ``ValueError`` crudo: se levanta ``AuthError`` pidiendo reautenticar,
    igual que el resto de errores de este módulo.
    """
    expira = secreto.get("expira_en")
    if expira is None:
        return False
    try:
        expira = float(expira)
    except (TypeError, ValueError):
        raise _pedir_auth(
            platform, "el secreto guardado tiene una fecha de caducidad corrupta"
        ) from None
    return time.time() >= expira - MARGEN_S


def _refrescar(
    brand: Brand, platform: Platform, secreto: dict, client: httpx.Client
) -> dict:
    """Llama al endpoint de refresco de ``platform`` y persiste el resultado.

    Nunca incluye el token, el refresh token ni el client secret en un
    mensaje de error: si el refresco falla, solo se informa de que hay que
    reautenticar y con qué comando, nunca del valor ni de la causa exacta.
    Esto cubre también una respuesta 200 cuyo cuerpo no es JSON válido o no
    trae ``access_token``: el cuerpo de esa respuesta puede contener
    credenciales, así que tampoco se interpola en el mensaje de error.

    Si la respuesta 200 no incluye ``expires_in``, no hay forma de saber
    cuándo caduca el ``access_token`` recién obtenido: se deja ``expira_en``
    a ``None`` (que ``_caducado`` interpreta como "sin fecha de caducidad
    conocida") en vez de conservar el valor antiguo, ya caducado, que
    forzaría un refresco en cada llamada posterior aunque el token nuevo
    siga siendo válido.

    Un fallo de red real (host inalcanzable, timeout, DNS) al llamar a
    ``client.post`` también se traduce aquí en ``AuthError`` en vez de
    escapar como excepción cruda: ``httpx.TimeoutException`` se comprueba
    antes que ``httpx.HTTPError`` (del que es subclase, así que iría
    inalcanzable si se comprobara después), ``httpx.InvalidURL`` se nombra
    aparte porque no hereda de ``httpx.HTTPError``, y un ``except Exception``
    final cubre cualquier otro fallo imprevisto. Ninguno de los mensajes
    interpola el error original: podría arrastrar la petición, con el
    refresh_token o el client_secret dentro.
    """
    url = URL_REFRESCO.get(platform)
    if url is None:
        # Meta usa tokens de página de larga duración que no se refrescan así.
        raise _pedir_auth(platform, "el token ha caducado")

    if platform is Platform.YOUTUBE:
        datos = {
            "grant_type": "refresh_token",
            "refresh_token": secreto.get("refresh_token", ""),
            "client_id": secreto.get("client_id", ""),
            "client_secret": secreto.get("client_secret", ""),
        }
    else:
        datos = {
            "grant_type": "refresh_token",
            "refresh_token": secreto.get("refresh_token", ""),
            "client_key": secreto.get("client_key", ""),
            "client_secret": secreto.get("client_secret", ""),
        }

    try:
        respuesta = client.post(url, data=datos)
    except httpx.InvalidURL:
        # No hereda de httpx.HTTPError, así que necesita su propio except; la
        # URL es una constante de este módulo (URL_REFRESCO), pero se cubre
        # por si acaso, igual que en los adaptadores de este proyecto.
        raise _pedir_auth(platform, "la URL de refresco de token no es válida") from None
    except httpx.TimeoutException:
        # Subclase de httpx.HTTPError: debe ir antes que ese except para no
        # quedar inalcanzable.
        raise _pedir_auth(
            platform, "se agotó el tiempo de espera al refrescar el token"
        ) from None
    except httpx.HTTPError:
        raise _pedir_auth(platform, "no se pudo conectar para refrescar el token") from None
    except Exception:
        # Resguardo final para cualquier fallo imprevisto que no sea de las
        # familias anteriores: nunca se interpola el propio error, podría
        # arrastrar la petición con el refresh_token o el client_secret.
        raise _pedir_auth(
            platform, "ocurrió un error inesperado al refrescar el token"
        ) from None

    if respuesta.status_code != 200:
        raise _pedir_auth(platform, "el refresco del token ha sido rechazado")

    try:
        cuerpo = respuesta.json()
        secreto["access_token"] = cuerpo["access_token"]
    except (json.JSONDecodeError, KeyError, TypeError):
        raise _pedir_auth(
            platform, "la respuesta del refresco de token no es válida"
        ) from None

    if "refresh_token" in cuerpo:
        secreto["refresh_token"] = cuerpo["refresh_token"]
    from socialctl.identity import record_granted_scopes
    record_granted_scopes(secreto, cuerpo)
    secreto["expira_en"] = (
        time.time() + float(cuerpo["expires_in"]) if "expires_in" in cuerpo else None
    )

    brand.guardar_secreto(platform, secreto)
    return secreto


def obtener_token(brand: Brand, platform: Platform, client: httpx.Client) -> str:
    """Devuelve un access token válido, refrescándolo si hace falta."""
    secreto = brand.leer_secreto(platform)
    if not secreto.get("access_token"):
        raise _pedir_auth(
            platform, f"no hay credenciales de {platform.value} para {brand.nombre}"
        )

    if not _caducado(secreto, platform):
        return secreto["access_token"]

    secreto = _refrescar(brand, platform, secreto, client)
    return secreto["access_token"]
