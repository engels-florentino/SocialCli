"""Interfaz común de los lectores de métricas.

Espejo de `socialctl/adapters/base.py`, pero de solo lectura. Se mantiene
aparte de los adaptadores de publicación a propósito: usan scopes distintos,
fallan de formas distintas y cambian a ritmos distintos, y el contrato de
`Adapter` gira alrededor de `publish`.
"""

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
    """Sustituye, en `texto`, el valor de cualquier parámetro de URL sensible.

    Espejo deliberado de `_redactar_secretos` en
    `socialctl/adapters/errores.py`, pero redactando por PATRÓN (el nombre
    del parámetro) en vez de por VALOR (una lista de secretos conocidos).
    Esa función no sirve aquí: la usan los adaptadores, que en el momento de
    construir el mensaje saben exactamente qué token pasaron en su propia
    petición. `leer_red`, en cambio, captura una `Exception` genérica de un
    lector que no ha escrito todavía (ver docstring de `leer_red`): no sabe
    de qué red viene ni cuál era su secreto, así que no hay ningún valor
    concreto que buscar y sustituir. Lo único que sí se puede saber de
    antemano, sin conocer la red ni la petición, es CÓMO se llaman los
    parámetros por los que las cuatro redes de este proyecto hacen viajar un
    secreto en una URL -ver `_NOMBRES_PARAMETRO_SENSIBLE`-, y sustituir
    cualquier valor que cuelgue de uno de ellos.

    Solo toca el valor, no el nombre del parámetro: `access_token=EAAG...`
    queda como `access_token=[TOKEN REDACTADO]`, para que el mensaje
    conserve la pista de qué tipo de dato faltaba sin conservar el dato.
    """
    return _PATRON_PARAMETRO_URL.sub(lambda m: f"{m.group(1)}={_MARCA_REDACCION}", texto)

LECTORES: dict[Platform, type[Lector]] = {}
"""Registro de clases de lector por plataforma.

Guarda **clases**, no instancias: se consume con `LECTORES[platform]()`, sin
argumentos, así que todo lector debe poder construirse sin ellos.
"""


class SinPermiso(Exception):
    """El token existe pero no tiene el scope necesario para leer.

    Se distingue de `AuthError` (no hay token) porque el remedio es distinto:
    aquí hay que añadir un permiso y volver a autenticar, no autenticar por
    primera vez. El mensaje lo dice con el comando exacto para que el usuario
    -o el gestor de redes- no tenga que deducirlo de un 403.
    """

    def __init__(self, platform: Platform, scope: str) -> None:
        self.platform = platform
        self.scope = scope
        super().__init__(
            f"el token de {platform.value} no tiene el permiso '{scope}'; "
            f"añádelo y vuelve a ejecutar: socialctl auth {platform.value} "
            f"--brand <Marca> (ver SETUP.md)"
        )


class Lector(ABC):
    """Lee las métricas de una red. Nunca publica, borra ni modifica nada."""

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
        """Devuelve la lectura de esta red para esta marca.

        `desde` acota las piezas por fecha de publicación; `None` significa
        todas. Puede lanzar `AuthError` o `SinPermiso`: `leer_red` los traduce
        a estados, así que un lector no necesita capturarlos.
        """


def leer_red(
    platform: Platform, brand: Brand, client: httpx.Client, desde: date | None
) -> LecturaRed:
    """Ejecuta el lector de una red y traduce cualquier fallo a un estado.

    Es lo que usa el CLI, no `Lector.leer` directamente: así el fallo de una
    red nunca aborta la lectura de las demás, igual que en `publicar()`.

    Por qué las tres ramas pasan su mensaje por `_redactar_url_por_patron`:
    `LecturaRed.error` no se queda en pantalla, acaba escrito en el JSON del
    snapshot (`<Marca>/metricas/AAAA-MM-DD.json`) y en `resumen.md`, y los
    dos se versionan en git -un secreto que caiga aquí no es un error de
    consola que se pierde al cerrar la terminal, es un commit-. La primera
    línea de defensa es que cada lector envuelva sus propios errores con
    `mensaje_de_error` (`socialctl/adapters/errores.py`), que sí conoce el
    token en juego y lo redacta por VALOR antes de que la excepción llegue
    hasta aquí. Esta función es la red de seguridad de SEGUNDO nivel para lo
    que a un lector se le escape sin envolver -típicamente, una
    `httpx.HTTPStatusError` sin capturar, cuyo mensaje por defecto incluye
    la URL completa de la petición que falló, con el token todavía en su
    parámetro de consulta si esa red lo manda así-. Como aquí no se sabe de
    qué red viene la excepción ni cuál era su secreto, no se puede redactar
    por valor como hace `mensaje_de_error`; solo por PATRÓN, sobre los
    nombres de parámetro que de verdad usan las cuatro redes (ver
    `_redactar_url_por_patron`). Se aplica a las tres ramas, no solo a la
    genérica, porque un `AuthError` o un `SinPermiso` que un lector futuro
    construya interpolando algo suyo también podría arrastrar una URL.
    """
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
