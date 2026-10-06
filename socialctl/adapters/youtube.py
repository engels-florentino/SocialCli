'YouTube Data API v3 resumable video upload.\n\nThe adapter validates one user-supplied video, initializes a resumable upload,\nstreams the original bytes and records the returned resource ID. Upload success\nis distinct from visibility observations. Expected I/O, network and response\nfailures become PostResult errors rather than escaping the adapter boundary.\nKnown credentials are redacted from provider errors.'

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
    'Upload one video through the YouTube Data API v3 resumable protocol.'

    platform = Platform.YOUTUBE

    def publish(self, post: PlatformPost, brand: Brand, client: httpx.Client) -> PostResult:
        'Publish a validated post; convert every failure to an error PostResult.'
        spec = PLATFORM_SPECS[self.platform]
        if len(post.media) > spec.max_media:
            return self._error(
                f"this endpoint supports at most {spec.max_media} file; "
                'it does not publish carousels'
            )

        try:
            return self._publicar(post, brand, client)
        except Exception as exc:
            return self._error(
                f"an unexpected YouTube error occurred "
                f"({type(exc).__name__}): {exc}"
            )

    def _publicar(self, post: PlatformPost, brand: Brand, client: httpx.Client) -> PostResult:
        'Execute publication with explicit handling of expected failures.'
        if not post.media:
            return self._error(
                'no video to upload: the post has no media attached'
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
                f"the video file no longer exists on disk: {video.path}"
            )
        except IsADirectoryError:
            return self._error(
                f"the video path points to a directory, not a file: {video.path}"
            )
        except PermissionError:
            return self._error(
                f"the video file is not readable: {video.path}"
            )
        except OSError as exc:
            return self._error(
                f"could not read the video file from disk ({video.path}): {exc}"
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
                'timed out contacting YouTube to '
                'initialize the upload'
            )
        except httpx.HTTPError:
            return self._error(
                'could not connect to YouTube to initialize the upload'
            )

        if inicio.status_code != 200:
            return self._error(mensaje_de_error(inicio, token))

        destino = inicio.headers.get("Location")
        if not destino:
            return self._error('YouTube did not return an upload URL')

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
                f"the video file no longer exists on disk: {video.path}"
            )
        except IsADirectoryError:
            return self._error(
                f"the video path points to a directory, not a file: {video.path}"
            )
        except PermissionError:
            return self._error(
                f"the video file is not readable: {video.path}"
            )
        except OSError as exc:
            # Cualquier otro fallo de E/S al leer el fichero (disco dañado,
            # demasiados descriptores abiertos, etc.): no es ninguno de los
            # casos anteriores, pero tampoco debe escapar como excepción.
            return self._error(
                f"could not read the video file from disk ({video.path}): {exc}"
            )
        except httpx.InvalidURL as exc:
            # La URL de subida (`destino`) la devuelve YouTube en la cabecera
            # `Location` de la respuesta al POST inicial: no es un literal
            # nuestro, así que puede llegar mal formada (puerto no numérico,
            # longitud desmesurada, etc.) y `httpx` la rechaza al construir la
            # petición, antes de llegar a la red.
            return self._error(
                f"YouTube returned an invalid upload URL: {exc}"
            )
        except httpx.TimeoutException:
            return self._error(
                'timed out uploading the video to YouTube'
            )
        except httpx.HTTPError:
            return self._error(
                'could not connect to YouTube to upload the video'
            )

        if subida.status_code not in (200, 201):
            return self._error(mensaje_de_error(subida, token))

        try:
            recurso_subido = subida.json()
            video_id = recurso_subido["id"]
        except json.JSONDecodeError:
            return self._error(
                'YouTube returned an invalid JSON response after '
                'uploading the video'
            )
        except KeyError:
            return self._error(
                'YouTube returned no video ID after the upload'
            )

        if not isinstance(video_id, str) or not video_id:
            return self._error('YouTube returned an invalid video ID after the upload')

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
                'The upload was confirmed, but its '
                'initial visibility could not be interpreted.'
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
                    'The upload was confirmed, but its '
                    f"visibility could not be verified (YouTube returned HTTP {response.status_code})."
                )
                return
            payload = response.json()
            items = payload.get("items") if isinstance(payload, dict) else None
            resource = items[0] if isinstance(items, list) and items else None
            if resource is None or not self._apply_observation(result, resource):
                result.warnings.append(
                    'The upload was confirmed, but YouTube did not return a '
                    'usable visibility observation.'
                )
        except Exception:
            result.warnings.append(
                'The upload was confirmed, but its visibility could not be verified.'
            )

    def _error(self, mensaje: str) -> PostResult:
        return PostResult(platform=self.platform, status=PostStatus.ERROR, error=mensaje)


ADAPTADORES[Platform.YOUTUBE] = YouTubeAdapter
