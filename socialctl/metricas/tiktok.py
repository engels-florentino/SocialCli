"""Read TikTok Display API metrics using user.info.stats/video.list. Preserve absent fields as None and inspect error bodies before HTTP status; sanitize any reflected token values."""

from __future__ import annotations

from datetime import date, datetime, timezone

import httpx

from socialctl.adapters.errores import mensaje_de_error
from socialctl.auth import obtener_token
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
# Misma clave y mismo mecanismo de señal que YouTube y Meta, importada del
# mismo sitio en vez de reescrita: ver `CLAVE_ENRIQUECIMIENTO_FALLIDO` en
# `socialctl/metricas/youtube.py`.
from socialctl.metricas.youtube import CLAVE_ENRIQUECIMIENTO_FALLIDO
from socialctl.models import Platform

USER_INFO = "https://open.tiktokapis.com/v2/user/info/"
VIDEO_LIST = "https://open.tiktokapis.com/v2/video/list/"

CAMPOS_USUARIO = "follower_count,likes_count,video_count"
CAMPOS_VIDEO = (
    "id,title,video_description,create_time,share_url,"
    "view_count,like_count,comment_count,share_count"
)

#: Tope de páginas de `video/list`. Existe para que un `has_more` que nunca
#: se apague (o un cursor que no avance) no deje el comando girando para
#: siempre: 20 páginas de 20 son 400 vídeos, muy por encima de cualquier
#: marca de este proyecto.
MAX_PAGINAS = 20


class TikTokLector(Lector):
    platform = Platform.TIKTOK

    def leer(
        self, brand: Brand, client: httpx.Client, desde: date | None
    ) -> LecturaRed:
        token = obtener_token(brand, self.platform, client)
        cabeceras = {"Authorization": f"Bearer {token}"}

        usuario = self._get(
            client, USER_INFO, cabeceras, {"fields": CAMPOS_USUARIO}, "user.info.stats"
        )["data"].get("user", {})
        cuenta = Cuenta(
            seguidores=usuario.get("follower_count"),
            total_piezas=usuario.get("video_count"),
            total_vistas=None,  # TikTok no da vistas totales de cuenta
        )

        piezas: list[Pieza] = []
        cursor = None
        for numero_de_pagina in range(1, MAX_PAGINAS + 1):
            cuerpo: dict = {"max_count": 20}
            if cursor is not None:
                cuerpo["cursor"] = cursor
            try:
                datos = self._post(
                    client, VIDEO_LIST, cabeceras, {"fields": CAMPOS_VIDEO}, cuerpo,
                    "video.list",
                )["data"]
            except SinPermiso:
                # Falta un scope: no es una lectura parcial, es una que no
                # puede hacerse. Sube tal cual para que el usuario vea el
                # permiso que falta y el `auth` que lo arregla.
                raise
            except Exception as exc:  # noqa: BLE001 - degradar, no perderlo todo
                if not piezas:
                    # La primera página falló: no hay nada leído que salvar y
                    # esto es un fallo de la red entera, no una lectura
                    # parcial. Sube y `leer_red` lo convierte en `error`.
                    raise
                # Hay páginas ya leídas: conservarlas es mejor que tirarlas.
                # Antes de este arreglo, un fallo en la página 3 perdía las
                # piezas de la 1 y la 2 -TikTok era el único lector que no
                # degradaba: YouTube aísla el enriquecimiento pieza a pieza y
                # Meta reintenta sin insights-. Se marca cada pieza
                # conservada con `CLAVE_ENRIQUECIMIENTO_FALLIDO`, el mismo
                # mecanismo que usan las otras dos: una clave que solo existe
                # cuando algo falló, para que su sola presencia sea la señal
                # y `resumen.md` la muestre en su columna "Aviso".
                #
                # El texto no puede arrastrar el token: los errores de este
                # lector se construyen con `mensaje_de_error` (ver
                # `_comprobar`), y el token de TikTok viaja en la cabecera
                # `Authorization`, nunca en la URL que lleva un
                # `HTTPStatusError`.
                motivo = (
                    f"partial read: page {numero_de_pagina} of "
                    f"video/list failed ({exc}); preserving the "
                    f"{len(piezas)} items already read; older items are missing"
                )
                for pieza_leida in piezas:
                    pieza_leida.especificas[CLAVE_ENRIQUECIMIENTO_FALLIDO] = motivo
                break

            for video in datos.get("videos", []):
                publicado = datetime.fromtimestamp(
                    video["create_time"], tz=timezone.utc
                ).replace(tzinfo=None)
                if desde is not None and publicado.date() < desde:
                    continue
                piezas.append(Pieza(
                    id=video["id"],
                    url=video.get("share_url", ""),
                    titulo=(video.get("title") or video.get("video_description") or "")[:120],
                    publicado_el=publicado,
                    tipo=TipoPieza.VERTICAL,  # en TikTok todo es vertical
                    acumulado=Metricas(
                        vistas=video.get("view_count"),
                        likes=video.get("like_count"),
                        comentarios=video.get("comment_count"),
                        compartidos=video.get("share_count"),
                    ),
                ))

            if not datos.get("has_more"):
                break
            cursor = datos.get("cursor")

        return LecturaRed(estado=EstadoLectura.OK, cuenta=cuenta, piezas=piezas)

    # --- peticiones -----------------------------------------------------

    def _comprobar(self, r: httpx.Response, datos: dict, scope: str, token: str) -> dict:
        error = datos.get("error") or {}
        codigo = error.get("code", "ok")
        if codigo != "ok":
            mensaje = str(error.get("message", ""))
            if "scope" in codigo or "scope" in mensaje.lower():
                raise SinPermiso(self.platform, scope)
            # El texto sale de `mensaje_de_error` y no del `message` crudo:
            # la forma del error de TikTok es la misma `{"error": {"message":
            # ...}}` que ese helper ya entiende, y además redacta el token
            # por si un intermediario reflejó la petición en el cuerpo. Un
            # `error` de este lector acaba en el snapshot, que se versiona.
            raise RuntimeError(
                f"TikTok returned '{codigo}': {mensaje_de_error(r, token)}"
            )
        return datos

    def _respuesta(self, r: httpx.Response, scope: str, token: str) -> dict:
        """Inspect TikTok JSON error body before HTTP status so missing scopes become actionable permission errors even on 401/403; non-JSON bodies fall back to normal HTTP handling."""
        try:
            datos = r.json()
        except ValueError:
            datos = None

        if isinstance(datos, dict):
            self._comprobar(r, datos, scope, token)

        r.raise_for_status()

        if not isinstance(datos, dict):
            raise RuntimeError(
                f"TikTok returned {r.status_code} with a non-JSON body"
            )
        return datos

    def _token(self, cabeceras: dict) -> str:
        """Token used in this request, for redaction from errors."""
        return str(cabeceras.get("Authorization", "")).removeprefix("Bearer ")

    def _get(
        self, client: httpx.Client, url: str, cabeceras: dict, params: dict, scope: str
    ) -> dict:
        r = client.get(url, headers=cabeceras, params=params)
        return self._respuesta(r, scope, self._token(cabeceras))

    def _post(
        self, client: httpx.Client, url: str, cabeceras: dict,
        params: dict, cuerpo: dict, scope: str,
    ) -> dict:
        r = client.post(url, headers=cabeceras, params=params, json=cuerpo)
        return self._respuesta(r, scope, self._token(cabeceras))
