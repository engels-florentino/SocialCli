"""Publicación en una Página de Facebook (Graph API v26.0).

Ningún fallo de esta red puede abortar la publicación en las demás: por
diseño, `FacebookAdapter.publish()` jamás deja escapar una excepción. Todo
fallo previsto (de red, de E/S del fichero de media o de formato de la
respuesta) se traduce en un `PostResult(status=PostStatus.ERROR, ...)` con un
mensaje en español que explica, de forma accionable, qué ha pasado. Como
resguardo de última instancia frente a cualquier fallo *no* previsto,
`publish()` envuelve además todo el proceso en un `except Exception` genérico
que también devuelve un `PostResult` de error, para que la regla se sostenga
incluso ante un caso que nadie anticipó hoy. Ese resguardo marca
`riesgo_duplicado=True`, igual que los fallos sin respuesta tras iniciar el
POST: solo un rechazo remoto explícito o un fallo conocido anterior al envío se
considera retryable.

Aviso de seguridad propio de este adaptador: el token de página viaja en el
CUERPO de la petición (`access_token`, dentro de `data=`), no en una cabecera
como en YouTube. Eso lo hace más fácil de filtrar por accidente: cualquier
mensaje de error que interpolase a lo bruto el texto de una excepción de red
podría arrastrar ese cuerpo. Por eso el resguardo genérico de `publish()`
nunca interpola `str(exc)` -solo el nombre del tipo de excepción-, y los
`except` de red de `_publicar()` usan siempre mensajes fijos. El único texto
libre que se interpola en cualquier mensaje de error de este módulo procede
del CUERPO DE LA RESPUESTA de Facebook (vía `mensaje_de_error`, en
`socialctl/adapters/errores.py`), nunca de la petición ni de sus cabeceras.
Eso por sí solo no bastaría si un intermediario reflejase la petición que
falló en su respuesta de error (el hallazgo crítico de la revisión final,
reproducido para Instagram pero con el mismo riesgo aquí, ya que el token
también viaja en el cuerpo de la petición); por eso `mensaje_de_error`
recibe también el token y lo redacta explícitamente de cualquier texto que
fuera a devolver (ver `socialctl/adapters/errores.py`).
"""

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
    """Publica en una Página de Facebook, eligiendo endpoint según el contenido.

    Facebook es la única de las cuatro redes que admite un post de solo
    texto, y la única donde un enlace no penaliza el alcance; por eso el
    endpoint depende de lo que lleve el post:

    - Solo texto → ``POST /{page_id}/feed`` con ``message``.
    - Imagen     → ``POST /{page_id}/photos`` con ``caption`` y el fichero en
      ``source``.
    - Vídeo      → ``POST /{page_id}/videos`` con ``description`` y el
      fichero en ``source``.

    El fichero de media (cuando lo hay) se sube en streaming: se abre con
    ``Path.open("rb")`` y se pasa el objeto de fichero directamente en
    ``files={"source": fichero}`` -nunca ``Path.read_bytes()``-, porque este
    proyecto publica vídeos de documentales que pueden pesar varios GB y no
    deben cargarse enteros en RAM. El fichero se abre con un ``with``, así
    que se cierra siempre, tanto si la petición tiene éxito como si falla a
    mitad.
    """

    platform = Platform.FACEBOOK

    def validate(self, post: PlatformPost, brand: Brand) -> list[ValidationError]:
        """Añade, a los del base, la falta de `page_id` en la cuenta.

        Hallazgo de revisión (I4): antes solo se comprobaba al publicar
        (`_publicar`, más abajo), así que un post con la cuenta sin
        configurar pasaba el preview limpio. No hace red (lee
        `brand.cuentas`, ya cargado en memoria), así que encaja en
        `validate()`. Se mantiene TAMBIÉN en `_publicar`: `publish()` no
        puede confiar en que alguien haya llamado a `validate()` antes.
        """
        errores = super().validate(post, brand)
        page_id = (brand.cuentas.get("facebook") or {}).get("page_id")
        if not page_id:
            errores.append(
                ValidationError(
                    platform=self.platform,
                    campo="cuenta",
                    motivo=f"falta page_id en {brand.raiz / 'accounts.yml'}",
                )
            )
        return errores

    def publish(self, post: PlatformPost, brand: Brand, client: httpx.Client) -> PostResult:
        """Publica el post. Nunca lanza: cualquier fallo vuelve como `PostResult` de error.

        Todos los pasos concretos viven en `_publicar()`, con manejo específico
        para cada fallo previsto. Este método es solo el resguardo de última
        instancia: si `_publicar()` deja escapar una excepción que nadie
        previó -hoy, o el día que alguien modifique este archivo y olvide
        mantener la regla-, aquí se atrapa igualmente, para que un fallo de
        Facebook nunca pueda abortar la publicación en las demás redes.

        El mensaje de este resguardo genérico NUNCA interpola `str(exc)`:
        solo el nombre del tipo de excepción. El token viaja en el cuerpo de
        la petición, así que una excepción verdaderamente no prevista podría,
        en teoría, arrastrar ese cuerpo en su propio mensaje (por ejemplo, una
        excepción de una librería de transporte que volcara la petición que
        falló); no interpolar su texto es lo único que lo garantiza también
        en este caso límite, no solo en los previstos.

        Como tampoco permite saber hasta qué punto avanzó la operación remota,
        el resultado conserva explícitamente `riesgo_duplicado=True`.
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
                f"ha ocurrido un error inesperado en Facebook ({type(exc).__name__})",
                riesgo_duplicado=True,
            )

    def _publicar(self, post: PlatformPost, brand: Brand, client: httpx.Client) -> PostResult:
        """Cuerpo real de la publicación, con manejo específico de cada fallo previsto."""
        page_id = (brand.cuentas.get("facebook") or {}).get("page_id")
        if not page_id:
            return self._error(f"falta page_id en {brand.raiz / 'accounts.yml'}")

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
            return self._error(f"el archivo ya no existe en disco: {asset.path}")
        except IsADirectoryError:
            return self._error(
                f"la ruta del archivo apunta a un directorio, no a un archivo: {asset.path}"
            )
        except PermissionError:
            return self._error(f"no hay permisos de lectura sobre el archivo: {asset.path}")
        except OSError as exc:
            # Cualquier otro fallo de E/S al leer el fichero (disco dañado,
            # demasiados descriptores abiertos, etc.): no es ninguno de los
            # casos anteriores, pero tampoco debe escapar como excepción.
            return self._error(f"no se pudo leer el archivo en disco ({asset.path}): {exc}")
        except httpx.InvalidURL:
            # La URL se construye con el page_id de accounts.yml: si contiene
            # un carácter no válido (p. ej. un salto de línea colado por un
            # error de copia/pega), httpx la rechaza al construir la petición,
            # antes de llegar a la red.
            return self._error(
                f"el page_id configurado ({page_id!r}) produce una URL no válida"
            )
        except httpx.TimeoutException:
            # Subclase de httpx.HTTPError: debe ir antes que ese except para
            # no quedar inalcanzable.
            return self._error(
                "se agotó el tiempo de espera al contactar con Facebook",
                riesgo_duplicado=True,
            )
        except httpx.HTTPError:
            return self._error(
                "no se pudo conectar con Facebook para publicar",
                riesgo_duplicado=True,
            )

        if respuesta.status_code != 200:
            return self._error(mensaje_de_error(respuesta, token))

        try:
            post_id = respuesta.json()["id"]
        except json.JSONDecodeError:
            return self._error(
                "Facebook respondió con un cuerpo que no es JSON válido tras publicar",
                riesgo_duplicado=True,
            )
        except KeyError:
            return self._error(
                "Facebook respondió sin el id de la publicación",
                riesgo_duplicado=True,
            )
        if not isinstance(post_id, str) or not post_id.strip():
            return self._error(
                "Facebook respondió con un identificador de publicación inesperado "
                "(se esperaba una cadena no vacía)",
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
