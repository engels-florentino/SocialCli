'Publish Instagram media through Graph API v26.0 container creation and delivery.\n\nThe provider fetches user-supplied files from a configured public HTTPS host.\nCreate a container, poll processing, then publish it and read its permalink.\nFailures after a potentially accepted publication report duplicate risk rather\nthan silently retrying. Unexpected IDs are rejected. Known access tokens are\nredacted even when a proxy reflects the failed request in a response.'

from __future__ import annotations

import json
import time
from urllib.parse import quote

import httpx

from socialctl.adapters.base import ADAPTADORES, Adapter
from socialctl.adapters.errores import mensaje_de_error
from socialctl.auth import obtener_token
from socialctl.brands import Brand
from socialctl.formatter import PLATFORM_SPECS, componer_caption
from socialctl.media import ruta_relativa_efectiva
from socialctl.models import (
    MediaKind,
    Platform,
    PlatformPost,
    PostResult,
    PostStatus,
    ValidationError,
)

GRAFO = "https://graph.facebook.com/v26.0"
# v21.0 (versión anterior) caduca el 21 de enero de 2027 ("API Version
# Deprecations", developers.facebook.com/docs/graph-api/changelog/version26.0,
# confirmado con Context7); v26.0 es la vigente (introducida el 29 de julio
# de 2026, según el mismo changelog). Los endpoints y campos que usa este
# módulo (POST /{ig_user_id}/media, GET /{id}?fields=status_code, POST
# /{ig_user_id}/media_publish, GET /{id}?fields=permalink) siguen
# documentados igual en v26.0: no hay cambios de forma, solo de número de
# versión en la URL.


class InstagramAdapter(Adapter):
    'Publish Instagram media using the Graph API container workflow.'

    platform = Platform.INSTAGRAM

    def __init__(self, espera_s: float = 60.0, intentos: int = 5) -> None:
        self.espera_s = espera_s
        self.intentos = intentos

    def validate(self, post: PlatformPost, brand: Brand) -> list[ValidationError]:
        'Add Instagram account and public media hosting checks.'
        errores = super().validate(post, brand)
        cuenta = brand.cuentas.get("instagram") or {}

        if not cuenta.get("ig_user_id"):
            errores.append(
                ValidationError(
                    platform=self.platform,
                    campo="cuenta",
                    motivo=f"missing ig_user_id in {brand.raiz / 'accounts.yml'}",
                )
            )

        if not cuenta.get("media_url_base"):
            errores.append(
                ValidationError(
                    platform=self.platform,
                    campo="cuenta",
                    motivo=(
                        'missing media_url_base in accounts.yml: Instagram requires '
                        'a public media URL (direct file upload is '
                        'not supported); specify the host serving '
                        "your files, for example 'https://cdn.example.com'"
                    ),
                )
            )

        return errores

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
                f"an unexpected Instagram error occurred ({type(exc).__name__})"
            )

    def _publicar(self, post: PlatformPost, brand: Brand, client: httpx.Client) -> PostResult:
        'Execute publication with explicit handling of expected failures.'
        cuenta = brand.cuentas.get("instagram") or {}

        ig_user_id = cuenta.get("ig_user_id")
        if not ig_user_id:
            return self._error(f"missing ig_user_id in {brand.raiz / 'accounts.yml'}")

        base = cuenta.get("media_url_base")
        if not base:
            return self._error(
                'missing media_url_base in accounts.yml: Instagram requires a '
                'public media URL (it does not support direct '
                'file upload); specify the host serving your files, '
                "for example 'https://cdn.example.com'"
            )

        if not post.media:
            return self._error(
                'no image or video to publish: the post has no media attached'
            )
        asset = post.media[0]

        try:
            token = obtener_token(brand, self.platform, client)
        except Exception as exc:
            return self._error(str(exc))

        # `ruta_relativa_efectiva` conserva la subcarpeta del archivo (p.
        # ej. "short/S2.mp4", si el usuario guarda sus verticales en
        # <Marca>/media/short/). Se separa por '/' y se codifica cada
        # SEGMENTO por su cuenta con `quote` (espacios, acentos, 'ñ'...);
        # las '/' que separan subcarpetas se reintroducen después de
        # codificar, así que nunca pasan por `quote` y no pueden acabar
        # codificadas como '%2F' -que convertiría "short/S2.mp4" en una URL
        # de un solo segmento que el host del usuario no serviría igual
        # que la carpeta real-.
        segmentos_media = ruta_relativa_efectiva(asset).split("/")
        ruta_codificada = "/".join(quote(segmento) for segmento in segmentos_media)
        url_media = f"{base.rstrip('/')}/{ruta_codificada}"

        # Instagram descarga la media desde esta URL en un proceso asíncrono
        # (el sondeo del paso 2): si el archivo aún no está subido al host,
        # el fallo llega minutos después y de forma opaca. Comprobarlo aquí
        # con un HEAD lo convierte en un error inmediato y accionable.
        try:
            sonda = client.head(url_media, follow_redirects=True, timeout=15)
        except httpx.InvalidURL:
            # La URL se construye con media_url_base (de accounts.yml) más
            # el nombre del fichero: si contiene un carácter no válido (p.
            # ej. un salto de línea colado por un error de copia/pega),
            # httpx la rechaza al construir la petición, antes de tocar la
            # red. Esta excepción NO hereda de httpx.HTTPError.
            return self._error(
                f"media_url_base produces an invalid media URL: {url_media!r}"
            )
        except httpx.TimeoutException:
            # Subclase de httpx.HTTPError: debe ir antes que ese except para
            # no quedar inalcanzable.
            return self._error(
                f"timed out checking whether {url_media} "
                'is accessible'
            )
        except httpx.HTTPError:
            return self._error(
                f"could not check whether {url_media} is accessible: check "
                'that the media_url_base host is running'
            )

        if sonda.status_code != 200:
            return self._error(
                f"{url_media} did not return HTTP 200 (returned "
                f"{sonda.status_code}); upload the file to that host before "
                'publishing to Instagram'
            )

        campo_url = "video_url" if asset.kind is MediaKind.VIDEO else "image_url"
        datos = {
            campo_url: url_media,
            "caption": componer_caption(post.body, post.hashtags),
            "access_token": token,
        }
        if asset.kind is MediaKind.VIDEO:
            # Sin media_type, la Graph API podría tratar el vídeo como un
            # post de feed clásico en vez de un Reel; se fija de forma
            # explícita para no depender de un valor por defecto que Meta
            # puede cambiar.
            datos["media_type"] = "REELS"

        try:
            creacion = client.post(f"{GRAFO}/{ig_user_id}/media", data=datos)
        except httpx.InvalidURL:
            # ig_user_id viene de accounts.yml: mismo razonamiento que con
            # media_url_base más arriba, pero aquí construye la URL del
            # propio Grafo.
            return self._error(
                f"the configured ig_user_id ({ig_user_id!r}) produces an invalid URL"
            )
        except httpx.TimeoutException:
            return self._error(
                'timed out creating the Instagram media container'
            )
        except httpx.HTTPError:
            return self._error(
                'could not connect to Instagram to create the media container'
            )

        if creacion.status_code != 200:
            return self._error(mensaje_de_error(creacion, token))

        try:
            contenedor_id = creacion.json()["id"]
        except json.JSONDecodeError:
            return self._error(
                'Instagram returned an invalid JSON response while '
                'creating the media container'
            )
        except KeyError:
            return self._error('Instagram returned no media container ID')

        # A diferencia del permalink (cosmético: un fallo ahí degrada a
        # `url=None` sin abortar), un `id` de contenedor que no sea una
        # cadena de texto SÍ es un fallo real: sin un id de contenedor
        # válido no hay forma de sondear su estado ni de publicarlo, así
        # que se aborta aquí con un mensaje accionable en vez de dejar que
        # ese valor siga circulando y explote más adelante -por ejemplo, al
        # construir el `PostResult` final con un `platform_id` de tipo
        # equivocado, que pydantic rechazaría con un `ValidationError` opaco
        # que solo atraparía el resguardo genérico de `publish()`-.
        if not isinstance(contenedor_id, str):
            return self._error(
                'Instagram returned an unexpected media container '
                'ID (expected a string, received '
                f"{type(contenedor_id).__name__}); cannot continue without "
                'a valid container ID'
            )

        error_de_sondeo = self._esperar_procesado(contenedor_id, token, client)
        if error_de_sondeo is not None:
            return error_de_sondeo

        # Hallazgo de revisión (I1): a partir de aquí, `media_publish` es el
        # punto sin retorno -exactamente como el último chunk en TikTok (ver
        # `socialctl/adapters/tiktok.py`)-. Un timeout o un fallo de conexión
        # AQUÍ no permiten saber si Instagram llegó a aceptar la petición
        # antes de que la respuesta se perdiera: el envío pudo completarse en
        # el servidor aunque el cliente nunca viera la confirmación. Antes,
        # solo el quinto camino posible (un `id` de publicación no textual,
        # más abajo) marcaba `riesgo_duplicado=True`; los otros cuatro -este
        # timeout, este fallo de conexión, y los dos de una respuesta HTTP
        # 200 ilegible que siguen debajo- son la MISMA situación (Instagram
        # ya aceptó el contenido) y se dejaban en `False`, así que `retry`
        # podía volver a llamar a `media_publish` sobre contenido ya
        # publicado y duplicarlo -el timeout, además, es el fallo más
        # probable de los cinco-.
        try:
            publicacion = client.post(
                f"{GRAFO}/{ig_user_id}/media_publish",
                data={"creation_id": contenedor_id, "access_token": token},
            )
        except httpx.TimeoutException:
            return self._error(
                'timed out publishing to Instagram: cannot '
                'confirm whether Instagram accepted the post '
                'before the response was lost. Do NOT retry without '
                'checking the account first, to avoid duplicating the '
                'post.',
                riesgo_duplicado=True,
            )
        except httpx.HTTPError:
            return self._error(
                'could not connect to Instagram to publish: cannot '
                'confirm whether Instagram accepted the post before '
                'the connection was lost. Do NOT retry without checking '
                'the account first, to avoid duplicating the '
                'post.',
                riesgo_duplicado=True,
            )

        if publicacion.status_code != 200:
            return self._error(mensaje_de_error(publicacion, token))

        try:
            post_id = publicacion.json()["id"]
        except json.JSONDecodeError:
            return self._error(
                'Instagram returned an invalid JSON response after '
                'publishing: the post was accepted (HTTP 200), but its ID '
                'cannot be confirmed or reported. Do NOT retry without '
                'checking the account first, to avoid duplicating the '
                'post.',
                riesgo_duplicado=True,
            )
        except KeyError:
            return self._error(
                'Instagram returned no post ID: the '
                'post was accepted (HTTP 200), but its ID cannot be '
                'confirmed or reported. Do NOT retry without checking '
                'the account first, to avoid duplicating the '
                'post.',
                riesgo_duplicado=True,
            )

        # Igual que con `contenedor_id` más arriba: un `id` de publicación
        # que no sea una cadena de texto es un fallo real, no cosmético
        # como el permalink. En este punto Instagram YA aceptó la
        # publicación (media_publish devolvió 200), así que no hay nada que
        # reintentar; pero sin un id de tipo correcto no se puede confirmar
        # ni reportar esa publicación, así que se aborta con un mensaje
        # accionable en vez de dejar que el valor llegue a `PostResult(
        # platform_id=post_id)` y reviente ahí con un `ValidationError` de
        # pydantic que el resguardo genérico de `publish()` convertiría en
        # el mensaje opaco "ha ocurrido un error inesperado en Instagram" -
        # perdiendo, además, el propio id que sí llegó a confirmarse.
        #
        # A diferencia de cualquier otro `ERROR` de este módulo, este caso
        # concreto NO es seguro de reintentar: la publicación ya existe en
        # Instagram (media_publish ya devolvió 200), el id solo hacía falta
        # para confirmarla y reportarla. `retry` (en `socialctl/cli.py`)
        # vuelve a publicar automáticamente todo lo que haya quedado en
        # `ERROR`; si llamara de nuevo a `media_publish` sobre contenido ya
        # publicado, crearía una segunda publicación duplicada y visible en
        # la cuenta. Por eso, además de marcar `riesgo_duplicado=True` -la
        # señal que `retry` de verdad consulta para negarse a reintentar
        # esta red en automático-, el mensaje no se limita a describir el
        # tipo inesperado: advierte de forma explícita de que la
        # publicación probablemente ya está hecha y de que reintentar puede
        # duplicarla, con una acción concreta (comprobar la cuenta antes de
        # volver a publicar) para quien lo lea.
        if not isinstance(post_id, str):
            return self._error(
                'Instagram returned an unexpected post '
                'ID (expected a string, received '
                f"{type(post_id).__name__}); the post was probably "
                'published on Instagram: media_publish accepted it and '
                'only its returned ID failed validation. Therefore '
                'do NOT retry: another publication could duplicate it. '
                'Check the account before publishing again.',
                riesgo_duplicado=True,
            )

        # La publicación ya ha tenido éxito en este punto (media_publish
        # devolvió 200 con un id): un fallo al pedir el permalink jamás
        # puede convertir este resultado en ERROR, así que `_obtener_permalink`
        # nunca lanza y, ante cualquier problema, devuelve `None` en vez de
        # propagar o de inventar una URL a mano.
        return PostResult(
            platform=self.platform,
            status=PostStatus.PUBLICADO,
            platform_id=post_id,
            url=self._obtener_permalink(post_id, token, client),
        )

    def _obtener_permalink(
        self, media_id: str, token: str, client: httpx.Client
    ) -> str | None:
        'Read the public permalink for published media, falling back to the ID URL.'
        try:
            respuesta = client.get(
                f"{GRAFO}/{media_id}",
                params={"fields": "permalink", "access_token": token},
            )
            if respuesta.status_code != 200:
                return None
            permalink = respuesta.json()["permalink"]
            return permalink if isinstance(permalink, str) else None
        except Exception:
            return None

    def _esperar_procesado(
        self, contenedor_id: str, token: str, client: httpx.Client
    ) -> PostResult | None:
        'Poll the media container until ready, failed, expired or timed out.\n\nFINISHED permits publication. ERROR and EXPIRED terminate with actionable\nerrors. Unknown or in-progress statuses keep polling up to the configured limit.\nNetwork and malformed-response failures return error text without throwing.'
        for intento in range(self.intentos):
            try:
                estado = client.get(
                    f"{GRAFO}/{contenedor_id}",
                    params={"fields": "status_code", "access_token": token},
                )
            except httpx.TimeoutException:
                return self._error(
                    'timed out checking the status of the '
                    'Instagram media container'
                )
            except httpx.HTTPError:
                return self._error(
                    'could not connect to Instagram to check the '
                    'media container status'
                )

            if estado.status_code != 200:
                return self._error(mensaje_de_error(estado, token))

            try:
                codigo = estado.json()["status_code"]
            except json.JSONDecodeError:
                return self._error(
                    'Instagram returned an invalid JSON response '
                    'when checking the media container'
                )
            except KeyError:
                return self._error(
                    'Instagram returned no status_code when checking the '
                    'media container'
                )

            if codigo == "FINISHED":
                return None
            if codigo == "ERROR":
                return self._error(
                    'Instagram could not process the media (the '
                    'container is in ERROR status)'
                )
            if codigo == "EXPIRED":
                # Terminal, igual que ERROR (ver docstring de este método):
                # el contenedor caduca a las 24h sin publicar y nunca va a
                # llegar a FINISHED, así que seguir sondeando solo agotaría
                # self.intentos sin motivo antes de fallar con un mensaje
                # genérico de timeout que ocultaría la causa real.
                return self._error(
                    'the Instagram media container expired '
                    '(status_code=EXPIRED) before publication: it was not '
                    'published within 24 hours; create a new container '
                    'to try again'
                )

            # No dormir tras el último intento agotado: no serviría de nada
            # y solo ralentizaría cada publicación que acaba fallando.
            if intento < self.intentos - 1:
                time.sleep(self.espera_s)

        return self._error(
            f"the Instagram container did not finish processing in time "
            f"after {self.intentos} attempts"
        )

    def _error(self, mensaje: str, *, riesgo_duplicado: bool = False) -> PostResult:
        return PostResult(
            platform=self.platform,
            status=PostStatus.ERROR,
            error=mensaje,
            riesgo_duplicado=riesgo_duplicado,
        )


ADAPTADORES[Platform.INSTAGRAM] = InstagramAdapter
