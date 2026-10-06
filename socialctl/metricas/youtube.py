"""Lectura de métricas de YouTube: Analytics API + Data API.

Dos APIs porque miden cosas distintas: la Data API da el catálogo (qué
vídeos hay, título, fecha, duración y estadísticas acumuladas) y la
Analytics API da el comportamiento (minutos vistos, porcentaje visto,
suscriptores ganados, de dónde vino la gente y en qué minuto se fue).

Las dos que de verdad explican el resultado son la **curva de retención**
-dónde abandona el espectador- y las **fuentes de tráfico** -si los cortes
traen gente al largo o no-. Sin ellas solo se cuenta; con ellas se entiende.

Lo que NO da la API y seguirá siendo dato manual: impresiones y CTR de
miniatura, que solo existen en YouTube Studio.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

import httpx

from socialctl.adapters.errores import mensaje_de_error
from socialctl.auth import obtener_token
from socialctl.brands import Brand
from socialctl.metricas.base import Lector, SinPermiso
from socialctl.metricas.modelos import (
    Audiencia,
    Cuenta,
    EstadoLectura,
    LecturaRed,
    Metricas,
    Pieza,
    TipoPieza,
)
from socialctl.models import Platform

ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"
CANALES = "https://www.googleapis.com/youtube/v3/channels"
VIDEOS = "https://www.googleapis.com/youtube/v3/videos"

#: Fecha lo bastante temprana como para cubrir la vida del canal.
INICIO_DE_LOS_TIEMPOS = "2005-02-14"  # YouTube no existía antes

#: La Data API acepta como mucho 50 ids por petición de `videos.list`.
LOTE = 50

#: Tope que impone la propia API al informe de "top videos": «Top videos
#: reports require a sort parameter and a maxResults value of 200 or less»
#: (developers.google.com/youtube/analytics/channel_reports). Sin `maxResults`
#: y sin `sort`, la petición responde 400 y no llega ninguna pieza.
#:
#: **Consecuencia declarada:** hoy no se pagina. Un canal con más de 200
#: vídeos no se lee entero: se leen **los 200 más vistos** (por `sort=-views`)
#: y los demás no aparecen en el snapshot ni en `piezas.yml`. Ninguna marca de
#: este proyecto se acerca a esa cifra; cuando alguna lo haga, la solución es
#: paginar con `startIndex` (1, 201, 401…) hasta agotar los resultados, no
#: subir este número.
MAX_VIDEOS = 200

#: **Coste de cuota, declarado.** La curva de retención y las fuentes de
#: tráfico se piden **una petición por pieza cada una** (ver `_retencion` y
#: `_trafico`): con el techo de `MAX_VIDEOS`, una ejecución puede hacer hasta
#: 2 × 200 = 400 peticiones a Analytics, además de las del catálogo. Es el
#: precio de las dos únicas lecturas que **explican** el resultado en vez de
#: contarlo, y se paga a propósito; lo que el spec (§9) pide -no gastar cuota
#: de más- se cumple pidiendo solo las métricas necesarias y agrupando
#: `videos.list` en lotes de 50, no renunciando a la retención.
#:
#: **Qué pasa si la cuota se agota antes del bucle** (canal, informe por
#: vídeo, catálogo): sigue tumbando la lectura entera -sin esas tres
#: llamadas no hay nada que devolver-, exactamente igual que cualquier otro
#: fallo ahí. `leer_red` lo convierte en `estado: error` **solo para
#: YouTube**; las otras tres redes se leen igual y el snapshot del día se
#: escribe. El remedio es esperar al siguiente día de cuota o acotar con
#: `--desde`.
#:
#: **Qué pasa si se agota DENTRO del bucle (degradar, no perder).** Cada
#: pieza ya trae sus métricas base -las del informe por vídeo, sin coste
#: adicional- antes de intentar su retención y su tráfico: perder esas dos
#: peticiones no tiene por qué tirar lo que ya se pagó. `leer()` aísla el
#: enriquecimiento de cada pieza (ver `_enriquecer`): si falla, esa pieza se
#: queda con `curva_retencion: []` y `fuentes_trafico: {}` -nunca
#: inventadas, la regla de oro del módulo- y el bucle sigue con la
#: siguiente. Si el fallo es justo cuota agotada -`_pedir` ya distingue hoy
#: «sin permiso» (`SinPermiso`) de «cualquier otro 403», y ese «cualquier
#: otro 403» es, por descarte, cuota- se deja de pedir retención y tráfico
#: para el resto de piezas pendientes en vez de insistir 250 veces más:
#: `_CuotaAgotada` (ver su docstring) reutiliza ese mismo criterio, no uno
#: nuevo. Ninguna pieza se pierde; solo se queda sin las dos métricas que
#: no se pudieron pagar.
#:
#: **Cómo se entera el usuario de que la lectura fue parcial.**
#: `LecturaRed.estado` se queda en `ok` -de verdad se leyeron la cuenta y
#: todas las piezas, con sus métricas base intactas- y por eso
#: `LecturaRed.error` NO es el sitio: ese campo solo se muestra cuando el
#: estado no es `ok` (ver el resumen legible de una tarea posterior), así
#: que colgar aquí el aviso con `estado: ok` lo dejaría invisible. La señal
#: vive en la propia pieza afectada: `especificas[CLAVE_ENRIQUECIMIENTO_FALLIDO]`
#: aparece **solo** en las piezas cuyo enriquecimiento falló o se saltó por
#: cuota, con el motivo -ya redactado, vía `mensaje_de_error`- como valor;
#: una pieza sana ni siquiera tiene esa clave, así que su sola presencia ya
#: es la señal de que la lectura no fue completa. Queda en el JSON del
#: snapshot del día para quien lo inspeccione a mano, y es la clave con la
#: que una tarea futura (`resumen.md`, `piezas.yml`) puede saber cuántas
#: piezas se quedaron sin retención sin tener que asumir que una curva
#: vacía siempre significa "sin datos": aquí puede significar "no se pudo
#: pedir". (Lo que este arreglo no cubre: si la cuota se agota justo en la
#: llamada de audiencia, que ocurre después del bucle y es una única
#: petición sin piezas parciales que salvar, esa sí tumba la lectura
#: entera -no es el patrón de "una petición por pieza" que motiva este
#: arreglo-.)
CLAVE_ENRIQUECIMIENTO_FALLIDO = "enriquecimiento_fallido"

_DURACION = re.compile(
    r"^P(?:(?P<d>\d+)D)?T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?$"
)


def _segundos(iso8601: str) -> int | None:
    """Convierte una duración ISO 8601 de YouTube (`PT16M20S`) a segundos."""
    m = _DURACION.match(iso8601 or "")
    if not m:
        return None
    partes = {k: int(v) for k, v in m.groupdict(default="0").items()}
    return partes["d"] * 86400 + partes["h"] * 3600 + partes["m"] * 60 + partes["s"]


def _entero(valor: object) -> int | None:
    """Convierte a int lo que venga, o `None` si no hay dato.

    La Data API devuelve las estadísticas como cadenas, y omite la clave
    entera cuando el creador ha ocultado esa métrica: ahí `None` es la
    respuesta correcta, no cero.
    """
    if valor is None:
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _filas(respuesta: dict) -> list[list]:
    return respuesta.get("rows") or []


class _CuotaAgotada(RuntimeError):
    """`_pedir` la lanza, en vez de `RuntimeError`, cuando el 403 no es de scope.

    Sigue siendo un `RuntimeError` -mismo texto, mismo mensaje ya redactado-,
    así que nada que ya capture `RuntimeError` deja de capturarla: el único
    efecto es que `leer()` puede reconocer, sin repetir el criterio de
    `_pedir`, el único caso que de verdad justifica dejar de insistir con el
    resto de piezas. Reutiliza exactamente la distinción que `_pedir` ya
    hace hoy entre «sin permiso» (`SinPermiso`, se arregla reautenticando) y
    «cualquier otro 403» (aquí: por descarte, la cuota diaria agotada, que
    no se arregla insistiendo).
    """


class YouTubeLector(Lector):
    platform = Platform.YOUTUBE

    def leer(
        self, brand: Brand, client: httpx.Client, desde: date | None
    ) -> LecturaRed:
        token = obtener_token(brand, self.platform, client)
        cabeceras = {"Authorization": f"Bearer {token}"}
        hoy = date.today()

        canal = self._canal(client, cabeceras)
        cuenta = Cuenta(
            seguidores=_entero(canal["statistics"].get("subscriberCount")),
            total_piezas=_entero(canal["statistics"].get("videoCount")),
            total_vistas=_entero(canal["statistics"].get("viewCount")),
        )

        por_video = self._analytics(
            client, cabeceras, hoy,
            dimensions="video",
            metrics=(
                "views,estimatedMinutesWatched,averageViewDuration,"
                "averageViewPercentage,subscribersGained,likes,comments,shares"
            ),
            # Obligatorios en el informe por vídeo, y solo en él: sin los dos
            # la API responde 400 (ver MAX_VIDEOS). Los otros tres informes
            # de este lector -retención, tráfico, audiencia- no los admiten
            # como requisito y no se los mandamos.
            maxResults=str(MAX_VIDEOS),
            sort="-views",
        )
        ids = [fila[0] for fila in _filas(por_video)]
        catalogo = self._videos(client, cabeceras, ids)

        piezas = []
        # Una vez agotada la cuota, ninguna petición más de este tipo va a
        # funcionar: se deja de intentar (ni siquiera se llama a `_pedir`)
        # para el resto de piezas pendientes, que se conservan igual con sus
        # métricas base. Ver el bloque de MAX_VIDEOS para el porqué.
        cuota_agotada = False
        for fila in _filas(por_video):
            pieza = self._pieza(fila, catalogo)
            if pieza is None:
                continue
            if desde is not None and pieza.publicado_el.date() < desde:
                continue

            if cuota_agotada:
                pieza.especificas["curva_retencion"] = []
                pieza.especificas["fuentes_trafico"] = {}
                pieza.especificas[CLAVE_ENRIQUECIMIENTO_FALLIDO] = (
                    "no se pidió: la cuota de Analytics ya se agotó al "
                    "enriquecer una pieza anterior"
                )
            else:
                try:
                    self._enriquecer(client, cabeceras, hoy, pieza)
                except _CuotaAgotada:
                    cuota_agotada = True

            piezas.append(pieza)

        # Reporting reports arrive as immutable CSV imports. They are a
        # separate, delayed source; no report means unknown, never zero.
        from socialctl.metricas.analytics_ingest import ObservationStore
        from socialctl.metricas.reach import attach_reach
        from socialctl.management.youtube_resources import ResourceError
        authenticated_channel = canal.get('id')
        configured_channel = (brand.cuentas.get('youtube') or {}).get('channel_id')
        if configured_channel and configured_channel != authenticated_channel:
            raise ValueError('YouTube Reporting account differs from authenticated channel')
        observations = ObservationStore(brand.raiz, authenticated_channel)
        if observations.root.exists():
            try:
                attach_reach(piezas, observations.active(), hoy)
            except (ResourceError, ValueError, KeyError, TypeError):
                for pieza in piezas:
                    pieza.especificas['thumbnail_reach_error'] = (
                        'informe de alcance importado ilegible; impresiones/CTR desconocidos')

        return LecturaRed(
            estado=EstadoLectura.OK,
            cuenta=cuenta,
            piezas=piezas,
            audiencia=self._audiencia(client, cabeceras, hoy),
        )

    # --- peticiones -----------------------------------------------------

    def _pedir(self, client: httpx.Client, url: str, cabeceras: dict, params: dict) -> dict:
        """Hace la petición y traduce cualquier respuesta que no sea de éxito.

        Dos traducciones, no una:

        - **Scope insuficiente** → `SinPermiso`, con el permiso exacto que
          falta y el comando `auth` que lo arregla.
        - **Cualquier otro fallo** (el caso que importa: la cuota diaria de
          10.000 unidades agotada) → `RuntimeError` **con el mensaje de la
          API dentro**. `raise_for_status()` a secas descartaría el cuerpo y
          dejaría en el snapshot un `Client error '403 Forbidden' for url …`
          que no dice nada; el spec (§9) pide el texto de la API. Cuando ese
          «cualquier otro fallo» es además un 403 -por descarte, cuota
          agotada, ya que el scope insuficiente se distingue arriba- se
          lanza `_CuotaAgotada`, una subclase de `RuntimeError` con el mismo
          mensaje: quien llama puede reconocer ese caso concreto sin que
          cambie nada para quien solo espera un `RuntimeError`.

        El mensaje se extrae con `mensaje_de_error` de
        `socialctl/adapters/errores.py` -el mismo helper que usan los cuatro
        adaptadores de publicación- en vez de con un criterio nuevo: además
        de no divergir, es el que ya **redacta el token** de cualquier texto
        que devuelva, por si un proxy intermedio refleja la petición fallida
        en su página de error. Un `error` del snapshot va a `piezas.yml` y a
        git: nunca puede arrastrar un secreto.
        """
        r = client.get(url, headers=cabeceras, params=params)
        if r.is_success:
            return r.json()

        token = cabeceras.get("Authorization", "").removeprefix("Bearer ")

        if r.status_code == 403:
            # Google devuelve 403 tanto por scope insuficiente como por
            # cuota agotada; solo el primero se arregla reautenticando, así
            # que se distinguen por el texto antes de decirle al usuario qué
            # hacer.
            cuerpo = r.text
            if "insufficient" in cuerpo.lower() or "PERMISSION_DENIED" in cuerpo:
                scope = (
                    "yt-analytics.readonly" if url == ANALYTICS else "youtube.readonly"
                )
                raise SinPermiso(self.platform, scope)

            # Cualquier otro 403 es, por descarte, el caso que importa: la
            # cuota diaria agotada. `_CuotaAgotada` es una `RuntimeError`
            # más -mismo mensaje, mismo comportamiento para quien no la
            # distinga-, pero permite que `leer()` deje de insistir con el
            # resto de piezas sin repetir aquí ningún criterio nuevo.
            raise _CuotaAgotada(
                f"YouTube respondió {r.status_code} a {url}: "
                f"{mensaje_de_error(r, token)}"
            )

        raise RuntimeError(
            f"YouTube respondió {r.status_code} a {url}: "
            f"{mensaje_de_error(r, token)}"
        )

    def _canal(self, client: httpx.Client, cabeceras: dict) -> dict:
        datos = self._pedir(client, CANALES, cabeceras, {
            "part": "snippet,statistics,contentDetails",
            "mine": "true",
        })
        items = datos.get("items") or []
        if not items:
            raise RuntimeError(
                "la Data API no devolvió ningún canal para este token: "
                "comprueba que autorizaste la cuenta correcta"
            )
        return items[0]

    def _analytics(
        self, client: httpx.Client, cabeceras: dict, hoy: date, **extra: str
    ) -> dict:
        params = {
            "ids": "channel==MINE",
            "startDate": INICIO_DE_LOS_TIEMPOS,
            "endDate": hoy.isoformat(),
            **extra,
        }
        return self._pedir(client, ANALYTICS, cabeceras, params)

    def _videos(self, client: httpx.Client, cabeceras: dict, ids: list[str]) -> dict[str, dict]:
        catalogo: dict[str, dict] = {}
        for i in range(0, len(ids), LOTE):
            datos = self._pedir(client, VIDEOS, cabeceras, {
                "part": "snippet,statistics,contentDetails",
                "id": ",".join(ids[i : i + LOTE]),
            })
            for item in datos.get("items") or []:
                catalogo[item["id"]] = item
        return catalogo

    # --- composición ----------------------------------------------------

    def _pieza(self, fila: list, catalogo: dict[str, dict]) -> Pieza | None:
        id_video = fila[0]
        item = catalogo.get(id_video)
        if item is None:
            # El vídeo salió en Analytics pero no en el catálogo: borrado o
            # privado. No se inventa nada: se omite.
            return None

        duracion = _segundos(item["contentDetails"].get("duration", ""))
        estadisticas = item.get("statistics", {})
        publicado = datetime.fromisoformat(
            item["snippet"]["publishedAt"].replace("Z", "+00:00")
        ).astimezone(timezone.utc).replace(tzinfo=None)

        return Pieza(
            id=id_video,
            url=f"https://www.youtube.com/watch?v={id_video}",
            titulo=item["snippet"]["title"],
            publicado_el=publicado,
            tipo=(
                # 180 s y no 60: desde octubre de 2024 YouTube admite Shorts
                # de hasta **3 minutos**, así que el umbral de 60 s dejaría
                # clasificados como "largo" cortes que son Shorts a todos los
                # efectos. Un vídeo horizontal de dos minutos caería del lado
                # equivocado, pero la API no da la relación de aspecto en
                # `contentDetails`, y en este proyecto no se publican
                # horizontales tan cortos.
                TipoPieza.VERTICAL
                if duracion is not None and duracion <= 180
                else TipoPieza.LARGO
            ),
            duracion_seg=duracion,
            acumulado=Metricas(
                vistas=_entero(estadisticas.get("viewCount")),
                likes=_entero(estadisticas.get("likeCount")),
                comentarios=_entero(estadisticas.get("commentCount")),
                compartidos=_entero(fila[8]) if len(fila) > 8 else None,
            ),
            especificas={
                "minutos_vistos": _entero(fila[2]),
                "duracion_media_seg": _entero(fila[3]),
                "porcentaje_visto": fila[4] if len(fila) > 4 else None,
                "suscriptores_ganados": _entero(fila[5]) if len(fila) > 5 else None,
            },
        )

    def _retencion(
        self, client: httpx.Client, cabeceras: dict, hoy: date, id_video: str
    ) -> list[dict]:
        datos = self._analytics(
            client, cabeceras, hoy,
            dimensions="elapsedVideoTimeRatio",
            metrics="audienceWatchRatio",
            filters=f"video=={id_video};audienceType==ORGANIC",
        )
        return [{"posicion": fila[0], "ratio": fila[1]} for fila in _filas(datos)]

    def _trafico(
        self, client: httpx.Client, cabeceras: dict, hoy: date, id_video: str
    ) -> dict[str, int]:
        datos = self._analytics(
            client, cabeceras, hoy,
            dimensions="insightTrafficSourceType",
            metrics="views",
            filters=f"video=={id_video}",
        )
        return {fila[0]: _entero(fila[1]) for fila in _filas(datos)}

    def _enriquecer(
        self, client: httpx.Client, cabeceras: dict, hoy: date, pieza: Pieza
    ) -> None:
        """Añade la retención y el tráfico de `pieza`, o la deja sin ellos.

        Aísla las dos peticiones de **esta** pieza: si cualquiera de las dos
        falla, ninguna se da por buena a medias -`curva_retencion` y
        `fuentes_trafico` quedan vacías, nunca inventadas- y la pieza se
        marca con `CLAVE_ENRIQUECIMIENTO_FALLIDO` (ver el bloque de
        `MAX_VIDEOS` para el porqué de esa clave y no `LecturaRed.error`).
        Quien llama decide qué hacer con la pieza: esta función nunca la
        descarta.

        Vuelve a lanzar `_CuotaAgotada` -después de dejar la pieza en el
        estado de arriba- para que `leer()` sepa que debe dejar de intentarlo
        con el resto; cualquier otro fallo se resuelve aquí mismo, porque
        solo afecta a esta pieza.
        """
        try:
            pieza.especificas["curva_retencion"] = self._retencion(
                client, cabeceras, hoy, pieza.id
            )
            pieza.especificas["fuentes_trafico"] = self._trafico(
                client, cabeceras, hoy, pieza.id
            )
        except Exception as e:  # noqa: BLE001 - aísla esta pieza, no tumba la lectura
            pieza.especificas["curva_retencion"] = []
            pieza.especificas["fuentes_trafico"] = {}
            pieza.especificas[CLAVE_ENRIQUECIMIENTO_FALLIDO] = str(e)
            if isinstance(e, _CuotaAgotada):
                raise

    def _audiencia(self, client: httpx.Client, cabeceras: dict, hoy: date) -> Audiencia:
        paises_crudos = self._analytics(
            # `sort=-views` no es decorativo: el spec pide el **top 10** de
            # países, y sin ordenar serían "los 10 primeros que devuelva la
            # API", que es otra cosa.
            client, cabeceras, hoy, dimensions="country", metrics="views",
            sort="-views",
        )
        filas = _filas(paises_crudos)[:10]
        total = sum(fila[1] for fila in filas) or 0
        paises = (
            {fila[0]: round(fila[1] * 100 / total, 1) for fila in filas}
            if total
            else {}
        )

        edades_crudas = self._analytics(
            client, cabeceras, hoy, dimensions="ageGroup", metrics="viewerPercentage"
        )
        edades = {fila[0]: fila[1] for fila in _filas(edades_crudas)}

        return Audiencia(paises=paises, edades=edades)
