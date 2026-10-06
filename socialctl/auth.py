"""OAuth token retrieval and refresh for explicit brands and platforms."""

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
    """Authentication is missing or no longer valid."""


def _pedir_auth(platform: Platform, motivo: str) -> AuthError:
    return AuthError(
        f"{motivo}. Run: socialcli auth {platform.value} --brand <brand>"
    )


def _caducado(secreto: dict, platform: Platform) -> bool:
    """Return whether the saved token has expired or is about to expire."""
    expira = secreto.get("expira_en")
    if expira is None:
        return False
    try:
        expira = float(expira)
    except (TypeError, ValueError):
        raise _pedir_auth(
            platform, "the saved credential has an invalid expiration date"
        ) from None
    return time.time() >= expira - MARGEN_S


def _refrescar(
    brand: Brand, platform: Platform, secreto: dict, client: httpx.Client
) -> dict:
    """Refresh the platform token and persist the result without exposing secrets."""
    url = URL_REFRESCO.get(platform)
    if url is None:
        # Meta usa tokens de página de larga duración que no se refrescan así.
        raise _pedir_auth(platform, "the token has expired")

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
        raise _pedir_auth(platform, "the token refresh URL is invalid") from None
    except httpx.TimeoutException:
        # Subclase de httpx.HTTPError: debe ir antes que ese except para no
        # quedar inalcanzable.
        raise _pedir_auth(
            platform, "token refresh timed out"
        ) from None
    except httpx.HTTPError:
        raise _pedir_auth(platform, "could not connect to refresh the token") from None
    except Exception:
        # Resguardo final para cualquier fallo imprevisto que no sea de las
        # familias anteriores: nunca se interpola el propio error, podría
        # arrastrar la petición con el refresh_token o el client_secret.
        raise _pedir_auth(
            platform, "an unexpected error occurred while refreshing the token"
        ) from None

    if respuesta.status_code != 200:
        raise _pedir_auth(platform, "the token refresh was rejected")

    try:
        cuerpo = respuesta.json()
        secreto["access_token"] = cuerpo["access_token"]
    except (json.JSONDecodeError, KeyError, TypeError):
        raise _pedir_auth(
            platform, "the token refresh response is invalid"
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
    """Return a valid access token, refreshing it when necessary."""
    secreto = brand.leer_secreto(platform)
    if secreto.get("auth_mode") == "broker":
        from socialctl.connections.client import broker_access_token
        try:
            return broker_access_token(brand, platform, client)
        except Exception:
            raise AuthError("shared connection unavailable; run socialcli connections --brand <brand> and reconnect if needed") from None
    if not secreto.get("access_token"):
        raise _pedir_auth(
            platform, f"no credentials for {platform.value} for {brand.nombre}"
        )

    if not _caducado(secreto, platform):
        return secreto["access_token"]

    secreto = _refrescar(brand, platform, secreto, client)
    return secreto["access_token"]
