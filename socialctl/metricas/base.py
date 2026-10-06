"""Common read-only metrics reader interface, separate from publishing adapters and their scopes."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import date

import httpx

from socialctl.auth import AuthError
from socialctl.brands import Brand
from socialctl.metricas.modelos import EstadoLectura, LecturaRed
from socialctl.models import Platform

# Misma marca que usa `socialctl/adapters/errores.py` para lo que redacta:
# así un token censurado se ve igual en un `PostResult.error` (publicación)
# que en una `LecturaRed.error` (lectura), sin acoplar este módulo a aquel
# -ver el docstring de cabecera sobre por qué se mantienen separados-.
_MARCA_REDACCION = "[TOKEN REDACTADO]"

# Nombres de parámetro que, en el código real de `socialctl/adapters/` y de
# `socialctl/authflow.py`, viajan como parte de una URL (query string) y
# pueden identificar o autenticar a una marca:
#
# - `access_token`: el token de página/usuario de Meta, como parámetro de
#   consulta al sondear un contenedor de Instagram
#   (`socialctl/adapters/instagram.py`, `GET .../{id}?...&access_token=...`)
#   y al canjear o listar páginas en `socialctl/authflow.py`
#   (`intercambiar_token_meta`, `obtener_paginas_meta`).
# - `client_secret`: el secreto de la app de Meta, también como parámetro de
#   consulta en `socialctl/authflow.py:intercambiar_token_meta`
#   (`GET /oauth/access_token?...&client_secret=...`).
# - `refresh_token`: el token de refresco guardado en el secreto de la
#   marca (`socialctl/auth.py:_refrescar`); hoy viaja en el cuerpo de un
#   POST, no en una URL, pero es un secreto de la misma familia que
#   `access_token` y una `HTTPStatusError` futura podría reflejarlo en una
#   URL igual que hace hoy con el token de acceso.
# - `code`: el código de autorización que Google/TikTok añaden como
#   parámetro de consulta al redirigir al callback local
#   (`socialctl/cli.py:_esperar_codigo`, que lo lee con
#   `urllib.parse.parse_qsl`); es de un solo uso, pero canjeable por tokens
#   mientras no haya caducado.
# - `client_key` / `key`: el identificador de la app de TikTok viaja como
#   parámetro de consulta en la URL de autorización
#   (`socialctl/authflow.py:construir_url_autorizacion`,
#   `urlencode({"client_key": ..., ...})`). No es tan sensible como un
#   secreto -es un identificador, no una prueba de posesión-, pero se cubre
#   igual: es barato de redactar y evita tener que juzgar, mensaje a
#   mensaje, si de verdad no importa que se vea.
#
# Nótese lo que NO cubre esta lista: `Authorization: Bearer <token>`. Es una
# CABECERA, no un parámetro de URL, y ninguna red de este proyecto la
# interpola hoy en un mensaje de error -de hecho, YouTube y TikTok mandan el
# token SOLO por esa cabecera, nunca por la URL (ver el docstring de
# `socialctl/adapters/errores.py`)-. Se decide no cubrirla aquí: un patrón
# que persiga pares `Cabecera: valor` sueltos, sin la estructura de una URL
# que los acote, tiene muchas más formas de disparar en falso sobre un
# mensaje legítimo (cualquier texto con dos puntos y una palabra después).
# Si algún lector futuro empezara a interpolar cabeceras crudas, el arreglo
# correcto es que ESE lector la redacte con `mensaje_de_error` -que ya sabe
# qué secreto tiene en ámbito-, no ensanchar esta red de patrones genérica.
_NOMBRES_PARAMETRO_SENSIBLE = (
    "access_token",
    "refresh_token",
    "client_secret",
    "client_key",
    "code",
    "key",
)

_PATRON_PARAMETRO_URL = re.compile(
    r"(?<=[?&])(" + "|".join(_NOMBRES_PARAMETRO_SENSIBLE) + r")=[^&\s'\")\]]+",
    re.IGNORECASE,
)


def _redactar_url_por_patron(texto: str) -> str:
    """Redact sensitive URL parameter values by parameter-name pattern when the reader's exact token is unknown; preserve names as diagnostic evidence."""
    return _PATRON_PARAMETRO_URL.sub(lambda m: f"{m.group(1)}={_MARCA_REDACCION}", texto)

LECTORES: dict[Platform, type[Lector]] = {}
"""Registry of platform reader classes; every reader must support construction without arguments."""


class SinPermiso(Exception):
    """Existing token lacks required read scope; add permission and reauthorize rather than first-time authentication."""

    def __init__(self, platform: Platform, scope: str) -> None:
        self.platform = platform
        self.scope = scope
        super().__init__(
            f"token for {platform.value} lacks permission '{scope}'; "
            f"add it and rerun: socialcli auth {platform.value} "
            f"--brand <Brand> (see SETUP.md)"
        )


class Lector(ABC):
    """Read platform metrics; never publish, delete or modify content."""

    platform: Platform

    def __init_subclass__(cls, **kwargs: object) -> None:
        # Registra la subclase en el momento de definirla, pero SOLO si
        # declara `platform` en su cuerpo. Una subclase que no lo declare
        # (el caso de las clases falsas de los tests, que asignan
        # `platform` después) no entra en `LECTORES`: así un test puede
        # sustituir una entrada con `monkeypatch.setitem` y que el teardown
        # restaure la clase real, en vez de dejar la falsa registrada para
        # el resto de la sesión.
        super().__init_subclass__(**kwargs)
        platform = getattr(cls, "platform", None)
        if isinstance(platform, Platform):
            LECTORES[platform] = cls

    @abstractmethod
    def leer(
        self, brand: Brand, client: httpx.Client, desde: date | None
    ) -> LecturaRed:
        """Return platform metrics for brand, optionally filtered by publication date; caller converts AuthError/SinPermiso into read states."""


def leer_red(
    platform: Platform, brand: Brand, client: httpx.Client, desde: date | None
) -> LecturaRed:
    """Run platform reader and convert failures to states without aborting other reads. Redact sensitive URL parameters in every error branch before storing snapshots or Markdown."""
    try:
        return LECTORES[platform]().leer(brand, client, desde)
    except AuthError as e:
        return LecturaRed(
            estado=EstadoLectura.SIN_CREDENCIALES,
            error=_redactar_url_por_patron(str(e)),
        )
    except SinPermiso as e:
        return LecturaRed(
            estado=EstadoLectura.SIN_PERMISO,
            error=_redactar_url_por_patron(str(e)),
        )
    except Exception as e:  # noqa: BLE001 - un lector roto no tumba a los demás
        error = f"{type(e).__name__}: {e}"
        return LecturaRed(estado=EstadoLectura.ERROR, error=_redactar_url_por_patron(error))
