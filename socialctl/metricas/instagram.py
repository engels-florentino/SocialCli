"""Read Instagram Business metrics via Graph API, sharing Facebook token handling, insights parsing and sanitized errors."""

from __future__ import annotations

from datetime import date

import httpx

from socialctl.auth import obtener_token
from socialctl.authflow import GRAFO
from socialctl.brands import Brand
from socialctl.metricas.base import Lector
from socialctl.metricas.facebook import (
    CLAVE_ENRIQUECIMIENTO_FALLIDO,
    POR_PAGINA_META,
    comprobar_respuesta,
    fecha_meta,
    id_de_cuenta,
    pedir_listado_con_insights,
    valor_insight,
)
from socialctl.metricas.modelos import (
    Cuenta,
    EstadoLectura,
    LecturaRed,
    Metricas,
    Pieza,
    TipoPieza,
)
from socialctl.models import Platform

#: Las cinco comprobadas vivas el 2026-09-11 contra @histo.past en la Graph
#: API v26.0. `views` es la que cuenta reproducciones y aplica a todo tipo de
#: media, no solo a vídeo; va a `Metricas.vistas`, que es su sitio.
#:
#: Retiradas en v26.0, y por eso ausentes de esta lista: `impressions` -el
#: error lo dice literal: «The Media Insights API does not support the
#: impressions metric»- y `plays`, sustituida por `views`. A diferencia de
#: Facebook, aquí `reach` sigue vivo, así que Instagram **sí** conserva su
#: campo `alcance`.
METRICAS_MEDIA = "reach,saved,shares,total_interactions,views"

#: El resto de `fields` del listado de medias, sin el bloque de insights.
#: Igual que `CAMPOS_POST` en `socialctl/metricas/facebook.py`: ninguno de
#: estos depende de una métrica de insights, así que ninguno se pierde
#: cuando `pedir_listado_con_insights` reintenta sin ese bloque.
CAMPOS_MEDIA = "id,caption,media_type,timestamp,permalink,like_count,comments_count"


class InstagramLector(Lector):
    platform = Platform.INSTAGRAM

    def leer(
        self, brand: Brand, client: httpx.Client, desde: date | None
    ) -> LecturaRed:
        token = obtener_token(brand, self.platform, client)
        ig_id = id_de_cuenta(brand, "instagram", "ig_user_id")

        cuenta_cruda = self._pedir(
            client, f"{GRAFO}/{ig_id}",
            {"fields": "followers_count,media_count", "access_token": token},
        )
        cuenta = Cuenta(
            seguidores=cuenta_cruda.get("followers_count"),
            total_piezas=cuenta_cruda.get("media_count"),
        )

        # Mismo mecanismo que Facebook (ver `pedir_listado_con_insights` en
        # `socialctl/metricas/facebook.py`): si Meta retiró el nombre de
        # alguna métrica pedida, la petición del listado responde 400 y se
        # reintenta sin el bloque de insights en vez de perder el listado
        # entero. `motivo_degradacion` es `None` salvo que haya hecho falta
        # ese reintento.
        medias, motivo_degradacion = pedir_listado_con_insights(
            client, f"{GRAFO}/{ig_id}/media", CAMPOS_MEDIA, METRICAS_MEDIA,
            {"limit": POR_PAGINA_META}, self.platform, "instagram_manage_insights", token,
        )

        piezas = []
        for media in medias.get("data", []):
            publicado = fecha_meta(media["timestamp"])
            if desde is not None and publicado.date() < desde:
                continue
            insights = media.get("insights", {})
            pieza = Pieza(
                id=media["id"],
                url=media.get("permalink", ""),
                titulo=(media.get("caption") or "")[:120],
                publicado_el=publicado,
                tipo=(
                    # `media_type` solo vale IMAGE, VIDEO o CAROUSEL_ALBUM
                    # (documentado). Que un reel llegue con
                    # `media_type: "VIDEO"` es, en cambio, una SUPOSICIÓN
                    # heredada del plan original: no está entre los datos
                    # verificados el 2026-09-11 contra @histo.past -esa
                    # cuenta no tenía ningún reel que comprobar-, así que no
                    # se afirma como dato comprobado. Si aparece uno con otro
                    # `media_type`, esta es la primera línea sospechosa.
                    TipoPieza.VERTICAL
                    if media.get("media_type") == "VIDEO"
                    else TipoPieza.IMAGEN
                ),
                acumulado=Metricas(
                    vistas=valor_insight(insights, "views"),
                    likes=media.get("like_count"),
                    comentarios=media.get("comments_count"),
                    guardados=valor_insight(insights, "saved"),
                    compartidos=valor_insight(insights, "shares"),
                ),
                especificas={
                    "alcance": valor_insight(insights, "reach"),
                    "interacciones_totales": valor_insight(insights, "total_interactions"),
                },
            )
            if motivo_degradacion is not None:
                # Señal de lectura parcial, igual que en Facebook: ver
                # `CLAVE_ENRIQUECIMIENTO_FALLIDO` en
                # `socialctl/metricas/youtube.py`.
                pieza.especificas[CLAVE_ENRIQUECIMIENTO_FALLIDO] = motivo_degradacion
            piezas.append(pieza)

        return LecturaRed(estado=EstadoLectura.OK, cuenta=cuenta, piezas=piezas)

    def _pedir(self, client: httpx.Client, url: str, params: dict) -> dict:
        # Sin `raise_for_status()`, por lo mismo que en Facebook: el token
        # viaja en la URL y el mensaje de esa excepción la lleva entera.
        r = client.get(url, params=params)
        return comprobar_respuesta(
            r,
            self.platform,
            "instagram_manage_insights",
            str(params.get("access_token") or ""),
        )
