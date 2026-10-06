"""Publicación en YouTube (Data API v3, upload resumible).

Ningún fallo de esta red puede abortar la publicación en las demás: por
diseño, `YouTubeAdapter.publish()` jamás deja escapar una excepción. Todo
fallo previsto (de red, de E/S del fichero de vídeo o de formato de la
respuesta) se traduce en un `PostResult(status=PostStatus.ERROR, ...)` con un
mensaje en español que explica, de forma accionable, qué ha pasado. Como
resguardo de última instancia frente a cualquier fallo *no* previsto,
`publish()` envuelve además todo el proceso en un `except Exception` genérico
que también devuelve un `PostResult` de error, para que la regla se sostenga
incluso ante un caso que nadie anticipó hoy.

El token viaja SOLO en la cabecera `Authorization` (nunca en el cuerpo ni
como parámetro de consulta), así que esta red no tiene la superficie de fuga
que sí tienen Facebook e Instagram (ver `socialctl/adapters/errores.py`);
aun así, `mensaje_de_error` recibe el token igual que en las otras tres
redes, por si un intermediario llegara a reflejarlo por una vía que hoy no
se ha previsto.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from socialctl.adapters.base import ADAPTADORES, Adapter
from socialctl.adapters.errores import mensaje_de_error
from socialctl.auth import obtener_token
from socialctl.brands import Brand
from socialctl.formatter import (
    PLATFORM_SPECS,
    privacidad_efectiva,
    texto_publicado,
    youtube_tags,
)
from socialctl.models import Platform, PlatformPost, PostResult, PostStatus

URL_UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
URL_VIDEOS = "https://www.googleapis.com/youtube/v3/videos"

# MIME type declarado en la subida resumible. Este proyecto no detecta el
# tipo MIME real de cada fichero (los adaptadores no llevan una tabla de
# extensión→MIME); "video/*" es el valor de comodín que la propia
# documentación de Google da como ejemplo válido para
# X-Upload-Content-Type ("The MIME type of the file (e.g., 'video/*')",
# developers.google.com/youtube/v3/guides/using_resumable_upload_protocol),
# así que sirve sin necesidad de esa tabla. Se usa el mismo valor en el
# POST inicial y en el PUT final porque la documentación exige que
# coincidan ("both of which must match the values provided in the initial
# request").
_TIPO_MIME_VIDEO = "video/*"


class YouTubeAdapter(Adapter):
    """Sube un vídeo a YouTube mediante el upload resumible de la Data API v3.

    Flujo en dos pasos:
    1. `POST` con los metadatos (título, descripción, tags, privacidad) →
       YouTube devuelve la URL de subida en la cabecera `Location`.
    2. `PUT` de los bytes del vídeo a esa URL → YouTube devuelve el `id` del
       vídeo ya creado.

    El vídeo se sube en streaming (un objeto de fichero abierto en binario
    como `content`, nunca `path.read_bytes()`): los documentales que publica
    este proyecto pueden pesar varios GB y cargarlos enteros en memoria no es
    aceptable. Para que ese streaming no acabe usando
    `Transfer-Encoding: chunked` (que el endpoint resumible de Google no
    admite), la cabecera `Content-Length` se fija explícitamente con el
    tamaño real del fichero: `httpx` ignora `Transfer-Encoding` en cuanto
    `Content-Length` ya está presente en la petición (ver
    `Request._prepare` en `httpx._models`), así que basta con fijarla para
    garantizar el comportamiento sin importar si `httpx` habría podido
    calcular el tamaño por su cuenta.

    El paso 1 (POST) lleva además `X-Upload-Content-Length` y
    `X-Upload-Content-Type`, y el paso 2 (PUT) lleva `Content-Type`: las
    tres son cabeceras "Required" según la documentación del flujo
    resumible (`using_resumable_upload_protocol`, pasos 1 y 3) que antes
    faltaban.
    """

    platform = Platform.YOUTUBE

    def publish(self, post: PlatformPost, brand: Brand, client: httpx.Client) -> PostResult:
        """Publica el post. Nunca lanza: cualquier fallo vuelve como `PostResult` de error.

        Todos los pasos concretos viven en `_publicar()`, con manejo específico
        para cada fallo previsto (de red, de E/S del fichero o de formato de la
        respuesta). Este método es solo el resguardo de última instancia: si
        `_publicar()` deja escapar una excepción que nadie previó —hoy, o el
        día que alguien modifique este archivo y olvide mantener la regla—,
        aquí se atrapa igualmente, para que un fallo de YouTube nunca pueda
        abortar la publicación en las demás redes.
        """
        spec = PLATFORM_SPECS[self.platform]
        if len(post.media) > spec.max_media:
            return self._error(
                f"esta ruta admite como máximo {spec.max_media} archivo; "
                "no publica carruseles"
            )

        try:
            return self._publicar(post, brand, client)
        except Exception as exc:
            return self._error(
                f"ha ocurrido un error inesperado en YouTube "
                f"({type(exc).__name__}): {exc}"
            )

    def _publicar(self, post: PlatformPost, brand: Brand, client: httpx.Client) -> PostResult:
        """Cuerpo real de la publicación, con manejo específico de cada fallo previsto."""
        if not post.media:
            return self._error(
                "no hay ningún vídeo que subir: el post no tiene media adjunta"
            )

        video = post.media[0]

        try:
            token = obtener_token(brand, self.platform, client)
        except Exception as exc:
            return self._error(str(exc))

        # El tamaño real del fichero hace falta ya para el POST inicial (la
        # cabecera X-Upload-Content-Length, ver más abajo), no solo para el
        # PUT: por eso se calcula aquí, antes de tocar la red, con el mismo
        # manejo de errores de E/S que antes solo cubría el PUT. Como
        # beneficio adicional, un fichero ya borrado o ilegible se detecta
        # antes de gastar una petición de red en abrir una sesión de subida
        # que no se podría completar.
        try:
            tamano = video.path.stat().st_size
        except FileNotFoundError:
            return self._error(
                f"el archivo de vídeo ya no existe en disco: {video.path}"
            )
        except IsADirectoryError:
            return self._error(
                f"la ruta del vídeo apunta a un directorio, no a un archivo: {video.path}"
            )
        except PermissionError:
            return self._error(
                f"no hay permisos de lectura sobre el archivo de vídeo: {video.path}"
            )
        except OSError as exc:
            return self._error(
                f"no se pudo leer el archivo de vídeo en disco ({video.path}): {exc}"
            )

        # privacidad_efectiva() (socialctl/formatter.py) ya resuelve el valor
        # por defecto ("private") cuando post.privacy no se ha indicado: por
        # decisión explícita del proyecto, ningún vídeo sale público sin que
        # el usuario lo pida (el canal tiene ~5000 suscriptores). "public",
        # "private" y "unlisted" son los tres valores que admite
        # status.privacyStatus (confirmado con Context7, ver
        # PLATFORM_SPECS[Platform.YOUTUBE].valores_privacidad).
        requested_privacy = privacidad_efectiva(post)
        metadatos = {
            "snippet": {
                "title": post.title,
                "description": texto_publicado(post),
                "tags": youtube_tags(post),
            },
            "status": {"privacyStatus": requested_privacy},
        }

        try:
            inicio = client.post(
                URL_UPLOAD,
                params={"uploadType": "resumable", "part": "snippet,status"},
                headers={
                    "Authorization": f"Bearer {token}",
                    # Ambas son "Required" en la tabla de cabeceras del paso 1
                    # del flujo resumible (Step 1 - Start a resumable
                    # session): el tamaño total del fichero y su MIME type.
                    "X-Upload-Content-Length": str(tamano),
                    "X-Upload-Content-Type": _TIPO_MIME_VIDEO,
                },
                json=metadatos,
            )
        except httpx.TimeoutException:
            return self._error(
                "se agotó el tiempo de espera al contactar con YouTube para "
                "iniciar la subida"
            )
        except httpx.HTTPError:
            return self._error(
                "no se pudo conectar con YouTube para iniciar la subida"
            )

        if inicio.status_code != 200:
            return self._error(mensaje_de_error(inicio, token))

        destino = inicio.headers.get("Location")
        if not destino:
            return self._error("YouTube no devolvió la URL de subida")

        try:
            with video.path.open("rb") as fichero:
                subida = client.put(
                    destino,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Length": str(tamano),
                        # "Required" en el paso 3 del flujo resumible (Step 3
                        # - Upload the video file), y debe coincidir con el
                        # X-Upload-Content-Type enviado en el POST inicial.
                        "Content-Type": _TIPO_MIME_VIDEO,
                    },
                    content=fichero,
                    timeout=None,
                )
        except FileNotFoundError:
            return self._error(
                f"el archivo de vídeo ya no existe en disco: {video.path}"
            )
        except IsADirectoryError:
            return self._error(
                f"la ruta del vídeo apunta a un directorio, no a un archivo: {video.path}"
            )
        except PermissionError:
            return self._error(
                f"no hay permisos de lectura sobre el archivo de vídeo: {video.path}"
            )
        except OSError as exc:
            # Cualquier otro fallo de E/S al leer el fichero (disco dañado,
            # demasiados descriptores abiertos, etc.): no es ninguno de los
            # casos anteriores, pero tampoco debe escapar como excepción.
            return self._error(
                f"no se pudo leer el archivo de vídeo en disco ({video.path}): {exc}"
            )
        except httpx.InvalidURL as exc:
            # La URL de subida (`destino`) la devuelve YouTube en la cabecera
            # `Location` de la respuesta al POST inicial: no es un literal
            # nuestro, así que puede llegar mal formada (puerto no numérico,
            # longitud desmesurada, etc.) y `httpx` la rechaza al construir la
            # petición, antes de llegar a la red.
            return self._error(
                f"YouTube devolvió una URL de subida no válida: {exc}"
            )
        except httpx.TimeoutException:
            return self._error(
                "se agotó el tiempo de espera al subir el vídeo a YouTube"
            )
        except httpx.HTTPError:
            return self._error(
                "no se pudo conectar con YouTube para subir el vídeo"
            )

        if subida.status_code not in (200, 201):
            return self._error(mensaje_de_error(subida, token))

        try:
            recurso_subido = subida.json()
            video_id = recurso_subido["id"]
        except json.JSONDecodeError:
            return self._error(
                "YouTube respondió con un cuerpo que no es JSON válido tras "
                "subir el vídeo"
            )
        except KeyError:
            return self._error(
                "YouTube respondió sin el id del vídeo tras la subida"
            )

        if not isinstance(video_id, str) or not video_id:
            return self._error("YouTube respondió con un id de vídeo no válido tras la subida")

        result = PostResult(
            platform=self.platform,
            status=PostStatus.PUBLICADO,
            platform_id=video_id,
            url=f"https://www.youtube.com/watch?v={video_id}",
            requested_privacy=requested_privacy,
        )
        try:
            self._apply_observation(result, recurso_subido)
        except Exception:
            # The ID is already authoritative. Optional fields in the upload
            # response can be malformed, but can never turn that confirmed
            # upload back into a retryable error.
            result.warnings.append(
                "La subida quedó confirmada, pero no se pudo interpretar su "
                "estado inicial de visibilidad."
            )
        self._readback(result, token, client)
        return result

    @staticmethod
    def _observation(
        resource: object,
    ) -> tuple[str | None, str | None, str | None, bool] | None:
        if not isinstance(resource, dict):
            return None
        status = resource.get("status")
        processing = resource.get("processingDetails")
        status = status if isinstance(status, dict) else {}
        processing = processing if isinstance(processing, dict) else {}
        privacy = status.get("privacyStatus")
        publish_at = status.get("publishAt")
        processing_status = processing.get("processingStatus")
        privacy = privacy if isinstance(privacy, str) and privacy else None
        valid_visibility = privacy is not None
        if isinstance(publish_at, str) and publish_at:
            try:
                parsed_publish_at = datetime.fromisoformat(
                    publish_at.replace("Z", "+00:00")
                )
            except ValueError:
                publish_at = None
            else:
                if parsed_publish_at.utcoffset() is None:
                    publish_at = None
        elif publish_at is not None:
            publish_at = None
        else:
            publish_at = None
        processing_status = (
            processing_status
            if isinstance(processing_status, str) and processing_status
            else None
        )
        if not valid_visibility and processing_status is None:
            return None
        return privacy, publish_at, processing_status, valid_visibility

    @classmethod
    def _apply_observation(cls, result: PostResult, resource: object) -> bool:
        observation = cls._observation(resource)
        if observation is None:
            return False
        privacy, publish_at, processing_status, valid_visibility = observation
        if valid_visibility:
            # `privacyStatus` anchors a complete visibility snapshot. Its
            # absence in a processing-only readback must not erase a status
            # returned by the upload or move that older status to a new time.
            result.observed_privacy = privacy
            result.observed_publish_at = publish_at
            result.visibility_observed_at = datetime.now(timezone.utc).isoformat()
        if processing_status is not None:
            # Processing has no separate timestamp in PostResult. Preserve
            # the existing visibility timestamp when this is the only field
            # returned, rather than attributing old privacy data to this GET.
            result.observed_processing_status = processing_status
        return True

    def _readback(self, result: PostResult, token: str, client: httpx.Client) -> None:
        """Read one resource snapshot; upload success remains authoritative."""
        try:
            response = client.get(
                URL_VIDEOS,
                params={
                    "id": result.platform_id,
                    "part": "status,processingDetails",
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status_code != 200:
                result.warnings.append(
                    "La subida quedó confirmada, pero no se pudo verificar su "
                    f"visibilidad (YouTube respondió HTTP {response.status_code})."
                )
                return
            payload = response.json()
            items = payload.get("items") if isinstance(payload, dict) else None
            resource = items[0] if isinstance(items, list) and items else None
            if resource is None or not self._apply_observation(result, resource):
                result.warnings.append(
                    "La subida quedó confirmada, pero YouTube no devolvió una "
                    "observación de visibilidad utilizable."
                )
        except Exception:
            result.warnings.append(
                "La subida quedó confirmada, pero no se pudo verificar su visibilidad."
            )

    def _error(self, mensaje: str) -> PostResult:
        return PostResult(platform=self.platform, status=PostStatus.ERROR, error=mensaje)


ADAPTADORES[Platform.YOUTUBE] = YouTubeAdapter
