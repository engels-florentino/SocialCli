'Publish creator-owned content to a Facebook Page through Graph API v26.0.\n\nThe content type selects the feed, photo or video endpoint. Missing account\nconfiguration and expected I/O/network failures produce actionable results.\nMalformed responses after acceptance carry duplicate risk: check the remote\naccount before retrying. Never interpolate request headers or credentials into\nerrors; known secrets are redacted from provider response text.'

from __future__ import annotations

import json

import httpx

from socialctl.adapters.base import ADAPTADORES, Adapter
from socialctl.adapters.errores import mensaje_de_error
from socialctl.auth import obtener_token
from socialctl.brands import Brand
from socialctl.formatter import PLATFORM_SPECS, componer_caption
from socialctl.models import (
    MediaAsset,
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
# módulo (POST /{page_id}/feed con message, POST /{page_id}/photos con
# caption+source, POST /{page_id}/videos con description+source) siguen
# documentados igual en v26.0 (confirmado contra los ejemplos vigentes de
# video-uploads y event/videos, que ya usan v26.0 en su propia URL de
# ejemplo): no hay cambios de forma, solo de número de versión en la URL.


class FacebookAdapter(Adapter):
    'Publish to a Facebook Page using the endpoint matching the content type.'

    platform = Platform.FACEBOOK

    def validate(self, post: PlatformPost, brand: Brand) -> list[ValidationError]:
        'Add the required Page ID check to platform validation.'
        errores = super().validate(post, brand)
        page_id = (brand.cuentas.get("facebook") or {}).get("page_id")
        if not page_id:
            errores.append(
                ValidationError(
                    platform=self.platform,
                    campo="cuenta",
                    motivo=f"missing page_id in {brand.raiz / 'accounts.yml'}",
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
                f"an unexpected Facebook error occurred ({type(exc).__name__})",
                riesgo_duplicado=True,
            )

    def _publicar(self, post: PlatformPost, brand: Brand, client: httpx.Client) -> PostResult:
        'Execute publication with explicit handling of expected failures.'
        page_id = (brand.cuentas.get("facebook") or {}).get("page_id")
        if not page_id:
            return self._error(f"missing page_id in {brand.raiz / 'accounts.yml'}")

        try:
            token = obtener_token(brand, self.platform, client)
        except Exception as exc:
            return self._error(str(exc))

        texto = componer_caption(post.body, post.hashtags)
        if post.link:
            texto = f"{texto}\n\n{post.link}"

        asset: MediaAsset | None = post.media[0] if post.media else None

        try:
            if asset is None:
                respuesta = client.post(
                    f"{GRAFO}/{page_id}/feed",
                    data={"message": texto, "access_token": token},
                )
            else:
                es_video = asset.kind is MediaKind.VIDEO
                endpoint = "videos" if es_video else "photos"
                campo_texto = "description" if es_video else "caption"
                with asset.path.open("rb") as fichero:
                    respuesta = client.post(
                        f"{GRAFO}/{page_id}/{endpoint}",
                        data={campo_texto: texto, "access_token": token},
                        files={"source": fichero},
                        timeout=None,
                    )
        except FileNotFoundError:
            return self._error(f"the file no longer exists on disk: {asset.path}")
        except IsADirectoryError:
            return self._error(
                f"the file path points to a directory, not a file: {asset.path}"
            )
        except PermissionError:
            return self._error(f"the file is not readable: {asset.path}")
        except OSError as exc:
            # Cualquier otro fallo de E/S al leer el fichero (disco dañado,
            # demasiados descriptores abiertos, etc.): no es ninguno de los
            # casos anteriores, pero tampoco debe escapar como excepción.
            return self._error(f"could not read the file from disk ({asset.path}): {exc}")
        except httpx.InvalidURL:
            # La URL se construye con el page_id de accounts.yml: si contiene
            # un carácter no válido (p. ej. un salto de línea colado por un
            # error de copia/pega), httpx la rechaza al construir la petición,
            # antes de llegar a la red.
            return self._error(
                f"the configured page_id ({page_id!r}) produces an invalid URL"
            )
        except httpx.TimeoutException:
            # Subclase de httpx.HTTPError: debe ir antes que ese except para
            # no quedar inalcanzable.
            return self._error(
                'timed out contacting Facebook',
                riesgo_duplicado=True,
            )
        except httpx.HTTPError:
            return self._error(
                'could not connect to Facebook to publish',
                riesgo_duplicado=True,
            )

        if respuesta.status_code != 200:
            return self._error(mensaje_de_error(respuesta, token))

        try:
            post_id = respuesta.json()["id"]
        except json.JSONDecodeError:
            return self._error(
                'Facebook returned an invalid JSON response after publishing',
                riesgo_duplicado=True,
            )
        except KeyError:
            return self._error(
                'Facebook returned no post ID',
                riesgo_duplicado=True,
            )
        if not isinstance(post_id, str) or not post_id.strip():
            return self._error(
                'Facebook returned an unexpected post ID '
                '(expected a nonempty string)',
                riesgo_duplicado=True,
            )

        # The publisher durably saves this media ID before the independent
        # first-comment step. Adapters must never perform that second mutation.
        return PostResult(
            platform=self.platform,
            status=PostStatus.PUBLICADO,
            platform_id=post_id,
            url=f"https://www.facebook.com/{post_id}",
        )

    def _error(self, mensaje: str, *, riesgo_duplicado: bool = False) -> PostResult:
        return PostResult(
            platform=self.platform,
            status=PostStatus.ERROR,
            error=mensaje,
            riesgo_duplicado=riesgo_duplicado,
        )


ADAPTADORES[Platform.FACEBOOK] = FacebookAdapter
