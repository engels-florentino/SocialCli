"""Read Facebook Page metrics via Graph API, degrading gracefully when removed insight names require fallback."""

from __future__ import annotations

import warnings
from datetime import date, datetime, timezone

import httpx

from socialctl.adapters.errores import mensaje_de_error
from socialctl.auth import obtener_token
from socialctl.authflow import GRAFO
from socialctl.brands import Brand
from socialctl.metricas.base import Lector, SinPermiso
from socialctl.metricas.modelos import (
    Cuenta,
    EstadoLectura,
    LecturaRed,
    Metricas,
    Pieza,
    TipoPieza,
)
from socialctl.models import Platform

# Mismo nombre de clave, mismo mecanismo de señal e importado del mismo
# sitio en vez de vuelto a escribir: `socialctl/metricas/youtube.py` ya
# resolvió cómo avisar de una lectura parcial sin tocar `estado` ni los
# modelos (ver su docstring, junto a `MAX_VIDEOS`). Reutilizar el símbolo,
# no solo el nombre, hace imposible que las dos claves diverjan en
# silencio -el mismo problema que tenía `VERSION_GRAFO_VERIFICADA` más
# abajo antes de este arreglo-.
from socialctl.metricas.youtube import CLAVE_ENRIQUECIMIENTO_FALLIDO

#: Fecha y versión contra las que se verificaron los nombres de métrica de
#: este módulo, llamando a la Página real -no leyendo documentación-. Si estás
#: leyendo esto mucho después, asume que algún nombre ya murió y compruébalo
#: antes de fiarte (Task 4, Step 1).
VERIFICADO_EL = "2026-09-11"
#: Derivada de `GRAFO` (no repetida a mano) para que esta constante y la
#: versión real que usan las peticiones no puedan divergir en silencio: si
#: `socialctl/authflow.py` sube de versión, esta cadena sube sola con ella
#: en vez de quedarse describiendo una v26.0 que ya nadie pide.
VERSION_GRAFO_VERIFICADA = GRAFO.rsplit("/", 1)[-1]

#: Nombres que en v26.0 ya NO existen: los ocho devuelven
#: `(#100) The value must be a valid insights metric` y tumban la petición
#: entera, no solo su propio campo. Están escritos aquí, y no borrados sin
#: más, para que nadie los reintente dentro de seis meses creyendo que fue un
#: olvido. El primero es el que este módulo usaba para `alcance`.
METRICAS_MUERTAS = (
    "post_impressions_unique",
    "post_impressions",
    "post_engaged_users",
    "post_reach",
    "post_unique_impressions",
    "post_impressions_organic_unique",
    "post_video_views_unique",
    "post_activity",
)

#: Métricas de publicación que pedimos, todas comprobadas vivas el
#: 2026-09-11 contra la Página real en la Graph API v26.0. Si Meta retira
#: alguna, su valor queda `None` y el resto de la lectura sigue igual.
#:
#: **No hay alcance.** En v26.0 no existe ninguna métrica de alcance por
#: publicación en Facebook: los ocho nombres de `METRICAS_MUERTAS` lo cubrían
#: y todos fallan. No se sustituye por otra parecida -`post_clicks` o
#: `post_activity_by_action_type` están vivas, pero no son alcance-: el campo
#: `alcance` sencillamente no está en las `especificas` de esta red.
#:
#: Lo que sí hay, y sirve mejor a la meta del proyecto -saber qué funciona-,
#: es el tiempo visto: `post_video_avg_time_watched` es **retención**, que es
#: exactamente lo que mide un objetivo de "que vean el contenido", y el
#: alcance nunca lo midió.
#:
#: Las tres de vídeo solo tienen sentido en una publicación de vídeo: en una
#: foto no vienen, y ahí `None` es la respuesta correcta -"esta métrica no
#: aplica"-, nunca un cero que se leería como "nadie lo vio".
METRICAS_POST = (
    "post_reactions_by_type_total,"
    "post_video_views,"
    "post_video_view_time,"
    "post_video_avg_time_watched"
)

#: El resto de `fields` del listado de publicaciones, sin el bloque de
#: insights: id, texto, fecha, enlace, comentarios, compartidos y el adjunto
#: que distingue foto de vídeo. Ninguno de estos depende de una métrica de
#: insights, así que ninguno se pierde cuando Meta retira un nombre y
#: `pedir_listado_con_insights` reintenta sin ese bloque (ver más abajo).
CAMPOS_POST = (
    "id,message,created_time,permalink_url,"
    "comments.summary(true),shares,"
    # `attachments{media_type}` es lo que distingue una foto de un vídeo en
    # una publicación de Página ("photo", "video", "album", "link"...). Sin
    # pedirlo, todo entraría como imagen. `target{id}` da el id del vídeo,
    # con el que se consulta su duración.
    "attachments{media_type,target}"
)

#: Cuántas publicaciones (o medias de Instagram) pide cada página del
#: listado. 100 es el máximo que acepta la Graph API para `limit`.
POR_PAGINA_META = 100

#: Tope de páginas que se siguen por `paging.next`. No es el tope de
#: publicaciones que se leen -eso lo decide la propia API, que deja de
#: mandar `paging.next` cuando no queda nada-, sino la red de seguridad
#: contra un `next` que no avanza: 50 × 100 son 5000 publicaciones, muy por
#: encima de cualquier marca de este proyecto.
#:
#: **Por qué paginar y no declarar un tope**, que es lo que hacen YouTube
#: (`MAX_VIDEOS`) y TikTok (`MAX_PAGINAS`): en esas dos, paginar cuesta algo
#: real -cuota de Analytics por pieza en YouTube, una API de cursor que
#: puede no avanzar en TikTok-. Aquí no: `paging.next` viene en la misma
#: respuesta, ya firmado, y seguirlo es una petición más sin coste de
#: insights. Con un tope, las publicaciones más antiguas desaparecían del
#: snapshot y de `piezas.yml` sin que nada lo dijera, y `piezas.yml` no
#: borra lo que ya tenía pero tampoco lo refresca: el histórico se quedaba
#: congelado en silencio. Si alguna vez se llega a este tope, se avisa (ver
#: `_seguir_paginas`), que es la diferencia con el silencio de antes.
MAX_PAGINAS_META = 50

#: Mismo umbral que YouTube (ver `socialctl/metricas/youtube.py`): un vídeo de
#: hasta 3 minutos se clasifica como vertical. Vive aquí duplicado a
#: propósito, como constante nombrada, para que cambiar el criterio de una red
#: no cambie el de la otra sin querer.
VERTICAL_HASTA_SEG = 180


def valor_insight(insights: dict, nombre: str) -> object | None:
    """Extract Graph API insight value, preserving missing metrics as None rather than zero."""
    for entrada in (insights or {}).get("data", []):
        if entrada.get("name") == nombre:
            valores = entrada.get("values") or []
            return valores[0].get("value") if valores else None
    return None


def segundos(milisegundos: object | None) -> float | None:
    """Convert Graph API milliseconds to seconds with one decimal place."""
    if milisegundos is None:
        return None
    try:
        return round(float(milisegundos) / 1000, 1)
    except (TypeError, ValueError):
        return None


def comprobar_permiso(
    respuesta: httpx.Response, platform: Platform, scope: str
) -> None:
    """Translate Meta 403/#10 to SinPermiso with exact missing scope; handle non-JSON responses safely."""
    if respuesta.status_code not in (400, 403):
        return
    try:
        error = respuesta.json().get("error") or {}
    except (ValueError, AttributeError):
        return
    if not isinstance(error, dict):
        return
    if error.get("code") in (10, 200) or "permission" in str(
        error.get("message", "")
    ).lower():
        raise SinPermiso(platform, scope)


def comprobar_respuesta(
    respuesta: httpx.Response, platform: Platform, scope: str, token: str
) -> dict:
    """Parse Graph API JSON without leaking query-string tokens; avoid raise_for_status because its URL could expose credentials."""
    comprobar_permiso(respuesta, platform, scope)
    if not respuesta.is_success:
        raise RuntimeError(
            f"{platform.value} returned {respuesta.status_code}: "
            f"{mensaje_de_error(respuesta, token)}"
        )
    return respuesta.json()


def es_metrica_invalida(respuesta: httpx.Response) -> bool:
    """Identify 400 responses caused by a retired insights metric name."""
    if respuesta.status_code != 400:
        return False
    try:
        error = respuesta.json().get("error") or {}
    except (ValueError, AttributeError):
        return False
    if not isinstance(error, dict):
        return False
    return error.get("code") == 100 and "must be a valid insights metric" in str(
        error.get("message", "")
    )


def pedir_listado_con_insights(
    client: httpx.Client,
    url: str,
    campos_base: str,
    metricas: str,
    extra: dict,
    platform: Platform,
    scope: str,
    token: str,
) -> tuple[dict, str | None]:
    """Request listings with insights, falling back when Meta rejects retired metrics."""
    params_con_insights = {
        **extra,
        "fields": f"{campos_base},insights.metric({metricas})",
        "access_token": token,
    }
    r = client.get(url, params=params_con_insights)
    if platform is Platform.FACEBOOK and "comments.summary(true)" in campos_base and r.status_code in (400, 403):
        try:
            error = r.json().get("error") or {}
        except (ValueError, AttributeError):
            error = {}
        if isinstance(error, dict) and error.get("code") in (10, 200) and "pages_read_user_content" in str(error.get("message", "")):
            # Comment counts need a separate permission; preserve authorized insights.
            reduced_fields = ",".join(field for field in campos_base.split(",") if field != "comments.summary(true)")
            data, other_reason = pedir_listado_con_insights(client, url, reduced_fields, metricas, extra, platform, scope, token)
            reason = "Comment counts unavailable: pages_read_user_content permission not granted."
            return data, reason + (" " + other_reason if other_reason else "")
    if r.status_code == 400 and es_metrica_invalida(r):
        motivo = mensaje_de_error(r, token)
        params_sin_insights = {**extra, "fields": campos_base, "access_token": token}
        r2 = client.get(url, params=params_sin_insights)
        datos = comprobar_respuesta(r2, platform, scope, token)
        return _seguir_paginas(client, datos, platform, scope, token), motivo
    datos = comprobar_respuesta(r, platform, scope, token)
    return _seguir_paginas(client, datos, platform, scope, token), None


def _seguir_paginas(
    client: httpx.Client,
    primera: dict,
    platform: Platform,
    scope: str,
    token: str,
) -> dict:
    """Combine bounded Graph API pages into one data list; prevent unbounded quota use and warn on truncation."""
    acumulado = list(primera.get("data") or [])
    siguiente = (primera.get("paging") or {}).get("next")
    paginas = 1
    while siguiente and paginas < MAX_PAGINAS_META:
        r = client.get(siguiente)
        pagina = comprobar_respuesta(r, platform, scope, token)
        acumulado.extend(pagina.get("data") or [])
        siguiente = (pagina.get("paging") or {}).get("next")
        paginas += 1
    if siguiente:
        warnings.warn(
            f"{platform.value}: listing exceeds {MAX_PAGINAS_META} "
            f"pages of {POR_PAGINA_META}; reading the "
            f"{MAX_PAGINAS_META * POR_PAGINA_META} most recent posts "
            "and older posts are excluded from this snapshot.",
            stacklevel=3,
        )
    return {**primera, "data": acumulado}


def id_de_cuenta(brand: Brand, red: str, campo: str) -> str:
    """Return configured account ID or report exact missing field."""
    valor = (brand.cuentas.get(red) or {}).get(campo) or ""
    if not str(valor).strip():
        raise RuntimeError(
            f"brand '{brand.nombre}' has no '{red}.{campo}' configured in "
            f"{brand.raiz / 'accounts.yml'}: fill it in before reading metrics "
            f"for {red} (see SETUP.md)"
        )
    return str(valor).strip()


def fecha_meta(texto: str) -> datetime:
    """Convert offset-aware Meta timestamp to naive UTC, preserving the actual instant."""
    return (
        datetime.strptime(texto, "%Y-%m-%dT%H:%M:%S%z")
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )


class FacebookLector(Lector):
    platform = Platform.FACEBOOK

    def leer(
        self, brand: Brand, client: httpx.Client, desde: date | None
    ) -> LecturaRed:
        token = obtener_token(brand, self.platform, client)
        page_id = id_de_cuenta(brand, "facebook", "page_id")

        cuenta_cruda = self._pedir(
            client, f"{GRAFO}/{page_id}",
            {"fields": "followers_count", "access_token": token},
        )
        cuenta = Cuenta(seguidores=cuenta_cruda.get("followers_count"))

        # `pedir_listado_con_insights` pide primero con el bloque de
        # insights, tal cual antes de este arreglo; si Meta ha retirado el
        # nombre de alguna métrica pedida, reintenta sin ese bloque en vez
        # de perder el listado de publicaciones entero (ver su docstring y
        # `es_metrica_invalida`). `motivo_degradacion` es `None` salvo que
        # haya hecho falta ese reintento.
        posts, motivo_degradacion = pedir_listado_con_insights(
            client, f"{GRAFO}/{page_id}/posts", CAMPOS_POST, METRICAS_POST,
            {"limit": POR_PAGINA_META}, self.platform, "read_insights", token,
        )

        piezas = []
        for post in posts.get("data", []):
            publicado = fecha_meta(post["created_time"])
            if desde is not None and publicado.date() < desde:
                continue
            pieza = self._pieza(post, publicado, client, token)
            if motivo_degradacion is not None:
                # Señal de lectura parcial, no un cambio de `estado`: ver el
                # docstring de `pedir_listado_con_insights` y el de
                # `CLAVE_ENRIQUECIMIENTO_FALLIDO` en
                # `socialctl/metricas/youtube.py`.
                pieza.especificas[CLAVE_ENRIQUECIMIENTO_FALLIDO] = motivo_degradacion
            piezas.append(pieza)

        return LecturaRed(estado=EstadoLectura.OK, cuenta=cuenta, piezas=piezas)

    def _pedir(self, client: httpx.Client, url: str, params: dict) -> dict:
        # El token sale de los propios `params` porque es donde la Graph API
        # lo exige: pasárselo a `comprobar_respuesta` es lo que permite
        # redactarlo del mensaje de error.
        r = client.get(url, params=params)
        return comprobar_respuesta(
            r, self.platform, "read_insights", str(params.get("access_token") or "")
        )

    def _adjunto(self, post: dict) -> dict:
        adjuntos = (post.get("attachments") or {}).get("data") or []
        return adjuntos[0] if adjuntos else {}

    def _duracion_video(
        self, client: httpx.Client, token: str, id_video: str
    ) -> int | None:
        """Read attached video duration in seconds; unavailable duration does not abort Page metrics."""
        try:
            datos = self._pedir(
                client, f"{GRAFO}/{id_video}",
                {"fields": "length", "access_token": token},
            )
            longitud = datos.get("length")
            return int(longitud) if longitud is not None else None
        except SinPermiso:
            raise
        except Exception:  # noqa: BLE001 - la duración es opcional
            return None

    def _pieza(
        self, post: dict, publicado: datetime, client: httpx.Client, token: str
    ) -> Pieza:
        insights = post.get("insights", {})
        reacciones = valor_insight(insights, "post_reactions_by_type_total")

        adjunto = self._adjunto(post)
        es_video = str(adjunto.get("media_type", "")).lower() == "video"

        duracion = None
        if es_video:
            id_video = ((adjunto.get("target") or {}).get("id")) or ""
            if id_video:
                duracion = self._duracion_video(client, token, id_video)

        if not es_video:
            tipo = TipoPieza.IMAGEN
        elif duracion is not None and duracion <= VERTICAL_HASTA_SEG:
            tipo = TipoPieza.VERTICAL
        else:
            # Vídeo del que no se conoce la duración (o que la pasa): se
            # clasifica como largo. Es una suposición, pero explícita y en un
            # solo sitio; lo que no vale es marcar todo como imagen.
            tipo = TipoPieza.LARGO

        return Pieza(
            id=post["id"],
            url=post.get("permalink_url", ""),
            titulo=(post.get("message") or "")[:120],
            publicado_el=publicado,
            tipo=tipo,
            duracion_seg=duracion,
            acumulado=Metricas(
                # Solo un vídeo tiene vistas. El `if es_video` es explícito a
                # propósito: en una foto la métrica no viene y `valor_insight`
                # ya devolvería `None`, pero dicho aquí queda claro que el
                # `None` de una foto significa "no aplica" y no "Meta no lo
                # mandó". Cero no vale: se leería como "nadie lo vio".
                vistas=valor_insight(insights, "post_video_views") if es_video else None,
                # Igual de protegido que `shares` justo debajo: la Graph API
                # puede devolver `"comments": null` explícito (no solo omitir
                # la clave), y un `.get("comments", {})` a secas no cubre ese
                # caso -el valor por defecto de `.get` solo se usa cuando la
                # clave FALTA, no cuando está y vale `None`-, así que sin el
                # `or {}` de aquí un `None` explícito reventaría con
                # `AttributeError` al llamar a `.get("summary")` sobre él.
                comentarios=((post.get("comments") or {}).get("summary") or {}).get(
                    "total_count"
                ),
                compartidos=(post.get("shares") or {}).get("count"),
            ),
            especificas={
                # No hay `alcance`: en v26.0 Facebook no da ninguna métrica de
                # alcance por publicación (ver `METRICAS_MUERTAS`). El hueco es
                # deliberado; no se rellena con otra métrica parecida.
                "reacciones": sum(reacciones.values()) if isinstance(reacciones, dict) else None,
                # Los dos en segundos, no en los milisegundos que manda Meta.
                "tiempo_visto_seg": segundos(
                    valor_insight(insights, "post_video_view_time") if es_video else None
                ),
                # Retención: lo que de verdad mide "que vean el contenido".
                "retencion_media_seg": segundos(
                    valor_insight(insights, "post_video_avg_time_watched") if es_video else None
                ),
            },
        )
