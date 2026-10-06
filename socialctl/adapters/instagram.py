"""Publicación en Instagram (Graph API v26.0, flujo de contenedor de tres pasos).

Instagram es la más frágil de las cuatro redes que publica este proyecto,
por dos razones que no se dan en las otras tres:

1. No admite subir el fichero de media directamente: exige una URL
   pública desde la que descargarlo. `media_url_base` en `accounts.yml` es
   el prefijo público del host donde el usuario aloja sus archivos (los
   sube él, por su cuenta; este proyecto nunca los transfiere), así que
   `clip.mp4` se resuelve a `<media_url_base>/clip.mp4`. Antes de crear el
   contenedor se hace un `HEAD` a esa URL y se aborta si no responde 200:
   sin esa comprobación, un archivo que el usuario aún no ha subido a su
   host se traduce en un error opaco de Instagram varios minutos después,
   a mitad del sondeo del paso 2.
2. El flujo tiene tres pasos, con sondeo de por medio: crear el
   contenedor → sondear su `status_code` hasta `FINISHED` (los vídeos
   tardan) → publicar. Eso multiplica el número de llamadas de red que
   `publish()` debe blindar frente a fallos.

Ningún fallo de esta red puede abortar la publicación en las demás: por
diseño, `InstagramAdapter.publish()` jamás deja escapar una excepción. Todo
fallo previsto (de red -incluidas las del bucle de sondeo-, de formato de
la respuesta o de configuración) se traduce en un `PostResult(status=
PostStatus.ERROR, ...)` con un mensaje en español que explica, de forma
accionable, qué ha pasado. Como resguardo de última instancia frente a
cualquier fallo *no* previsto, `publish()` envuelve además todo el proceso
en un `except Exception` genérico que también devuelve un `PostResult` de
error, para que la regla se sostenga incluso ante un caso que nadie
anticipó hoy.

Aviso de seguridad propio de este adaptador: el token viaja en el CUERPO de
la petición (`access_token`, dentro de `data=`) al crear el contenedor y al
publicar, y como parámetro de consulta durante el sondeo y al pedir el
permalink -superficies distintas, ninguna de las dos es una cabecera como en
YouTube-, lo que lo hace fácil de filtrar por accidente. Por eso el
resguardo genérico de `publish()` nunca interpola `str(exc)` -solo el
nombre del tipo de excepción-, y los `except` de red de este módulo usan
siempre mensajes fijos. El único texto libre que se interpola en cualquier
mensaje de error de este módulo procede del CUERPO DE LA RESPUESTA de
Instagram (vía `mensaje_de_error`, en `socialctl/adapters/errores.py`) o de
datos que ya vienen de `accounts.yml` (URLs, ids), nunca de la petición ni
de sus cabeceras. Eso por sí solo no bastaría si un intermediario (un
proxy, un balanceador) reflejase la URL o el cuerpo de la petición que
falló en su respuesta de error -el hallazgo crítico de la revisión final,
reproducido con un 502 que reflejaba la URL del sondeo, token incluido-; por
eso `mensaje_de_error` recibe también el token y lo redacta explícitamente
de cualquier texto que fuera a devolver, sea cual sea la vía por la que se
hubiera colado (ver `socialctl/adapters/errores.py`). Con esa redacción, el
mensaje nunca puede arrastrar el token, también en ese caso límite.
"""

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
    """Publica en Instagram mediante el flujo de contenedor de la Graph API.

    A diferencia de YouTube y Facebook, Instagram no admite subir el
    fichero de media directamente: exige una URL pública desde la que
    descargarlo. Esa URL se construye con `media_url_base` (de
    `accounts.yml`) y el nombre del fichero.

    El flujo tiene tres pasos, con sondeo de por medio:

    1. `POST /{ig_user_id}/media` con `video_url` o `image_url` y
       `caption` → devuelve un `creation_id` (el contenedor).
    2. Sondear `GET /{creation_id}?fields=status_code` hasta `FINISHED`
       (los vídeos tardan varios minutos) o `ERROR` (se aborta sin
       publicar).
    3. `POST /{ig_user_id}/media_publish` con el `creation_id` → devuelve
       el id de la publicación ya creada.

    Tras el paso 3, con la publicación ya hecha, se hace un intento
    adicional de mejor esfuerzo: `GET /{id}?fields=permalink` sobre ese
    mismo id para obtener la URL pública real
    (`https://www.instagram.com/reel/<shortcode>/`) y guardarla en
    `PostResult.url`. `media_publish` solo devuelve un id interno de
    media, no el shortcode público, así que sin esta llamada no hay forma
    de construir un enlace que funcione: uno hecho a mano con ese id
    (`instagram.com/<id>`) tiene toda la apariencia de un enlace legítimo
    pero no resuelve a nada. Como la publicación ya ha tenido éxito en ese
    punto, un fallo al pedir el permalink (de red, de formato de la
    respuesta, o ausencia del campo) jamás convierte el resultado en
    `ERROR`: degrada con gracia a `url=None`, conservando el `platform_id`
    correcto (ver `_obtener_permalink`).

    Antes del paso 1 se hace un `HEAD` a la URL pública de la media y se
    aborta si no responde 200 (ver el aviso del módulo).

    `espera_s` (segundos entre sondeos) e `intentos` (máximo de sondeos)
    son parámetros del constructor -no límites de contenido, así que no
    van en `PLATFORM_SPECS`- para poder testear el sondeo sin esperas
    reales; ambos tienen valor por defecto porque, como cualquier
    adaptador que acabe registrado en `ADAPTADORES`, esta clase debe poder
    instanciarse sin argumentos.

    Los valores por defecto (`espera_s=60.0`, `intentos=5`) reproducen la
    cadencia que Meta recomienda ("Troubleshooting > Container publishing
    status", confirmado con Context7): "It is recommended to poll the
    container status once per minute for a maximum of 5 minutes". La
    implementación anterior sondeaba cada 5 segundos hasta 60 veces -12
    veces más peticiones de las recomendadas sobre el mismo recurso- para
    llegar, por otra vía, al mismo techo aproximado de 5 minutos de espera
    total; estos valores mantienen ese mismo techo (razonable para un
    Reel, cuya duración máxima son 15 minutos) pero con la cadencia real
    que Meta documenta, en vez de una elegida sin respaldo.
    """

    platform = Platform.INSTAGRAM

    def __init__(self, espera_s: float = 60.0, intentos: int = 5) -> None:
        self.espera_s = espera_s
        self.intentos = intentos

    def validate(self, post: PlatformPost, brand: Brand) -> list[ValidationError]:
        """Añade, a los del base, los problemas de configuración de cuenta.

        Hallazgo de revisión (I4): `ig_user_id` y `media_url_base` solo se
        comprobaban al publicar (`_publicar`, más abajo), así que un post
        con la cuenta mal configurada pasaba el preview limpio (`Problemas
        detectados: 0`) y el usuario lo aprobaba sin saber que iba a
        fallar. Ninguna de las dos comprobaciones hace red -ambas leen
        `brand.cuentas`, ya cargado en memoria-, así que encajan en
        `validate()` sin romper su contrato ("No hace red"). Se mantienen
        TAMBIÉN en `_publicar` (no se quitan de ahí): `publish()` no puede
        confiar en que alguien haya llamado a `validate()` antes.
        """
        errores = super().validate(post, brand)
        cuenta = brand.cuentas.get("instagram") or {}

        if not cuenta.get("ig_user_id"):
            errores.append(
                ValidationError(
                    platform=self.platform,
                    campo="cuenta",
                    motivo=f"falta ig_user_id en {brand.raiz / 'accounts.yml'}",
                )
            )

        if not cuenta.get("media_url_base"):
            errores.append(
                ValidationError(
                    platform=self.platform,
                    campo="cuenta",
                    motivo=(
                        "falta media_url_base en accounts.yml: Instagram exige "
                        "una URL pública para la media (no admite subir el "
                        "fichero directamente); indica el host donde alojas "
                        "los archivos, por ejemplo 'https://cdn.tumarca.com'"
                    ),
                )
            )

        return errores

    def publish(self, post: PlatformPost, brand: Brand, client: httpx.Client) -> PostResult:
        """Publica el post. Nunca lanza: cualquier fallo vuelve como `PostResult` de error.

        Todos los pasos concretos viven en `_publicar()`, incluido el bucle
        de sondeo de `_esperar_procesado()` -que hace varias llamadas de
        red, ninguna de las cuales puede escapar tampoco-. Este método es
        solo el resguardo de última instancia: si `_publicar()` deja escapar
        una excepción que nadie previó -hoy, o el día que alguien modifique
        este archivo y olvide mantener la regla-, aquí se atrapa igualmente,
        para que un fallo de Instagram nunca pueda abortar la publicación en
        las demás redes.

        El mensaje de este resguardo genérico NUNCA interpola `str(exc)`:
        solo el nombre del tipo de excepción. El token viaja en el cuerpo (y,
        durante el sondeo, como parámetro de consulta) de la petición, así
        que una excepción verdaderamente no prevista podría, en teoría,
        arrastrarlo en su propio mensaje (por ejemplo, una excepción de una
        librería de transporte que volcara la petición que falló); no
        interpolar su texto es lo único que lo garantiza también en ese
        caso límite, no solo en los previstos.
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
                f"ha ocurrido un error inesperado en Instagram ({type(exc).__name__})"
            )

    def _publicar(self, post: PlatformPost, brand: Brand, client: httpx.Client) -> PostResult:
        """Cuerpo real de la publicación, con manejo específico de cada fallo previsto."""
        cuenta = brand.cuentas.get("instagram") or {}

        ig_user_id = cuenta.get("ig_user_id")
        if not ig_user_id:
            return self._error(f"falta ig_user_id en {brand.raiz / 'accounts.yml'}")

        base = cuenta.get("media_url_base")
        if not base:
            return self._error(
                "falta media_url_base en accounts.yml: Instagram exige una URL "
                "pública para la media (no admite subir el fichero "
                "directamente); indica el host donde alojas los archivos, "
                "por ejemplo 'https://cdn.tumarca.com'"
            )

        if not post.media:
            return self._error(
                "no hay ninguna imagen o vídeo que publicar: el post no tiene media adjunta"
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
                f"media_url_base produce una URL de media no válida: {url_media!r}"
            )
        except httpx.TimeoutException:
            # Subclase de httpx.HTTPError: debe ir antes que ese except para
            # no quedar inalcanzable.
            return self._error(
                f"se agotó el tiempo de espera comprobando si {url_media} "
                "está accesible"
            )
        except httpx.HTTPError:
            return self._error(
                f"no se pudo comprobar si {url_media} está accesible: revisa "
                "que el host de media_url_base esté levantado"
            )

        if sonda.status_code != 200:
            return self._error(
                f"{url_media} no responde con HTTP 200 (ha devuelto "
                f"{sonda.status_code}); sube el archivo a ese host antes de "
                "publicar en Instagram"
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
                f"el ig_user_id configurado ({ig_user_id!r}) produce una URL no válida"
            )
        except httpx.TimeoutException:
            return self._error(
                "se agotó el tiempo de espera creando el contenedor de media en Instagram"
            )
        except httpx.HTTPError:
            return self._error(
                "no se pudo conectar con Instagram para crear el contenedor de media"
            )

        if creacion.status_code != 200:
            return self._error(mensaje_de_error(creacion, token))

        try:
            contenedor_id = creacion.json()["id"]
        except json.JSONDecodeError:
            return self._error(
                "Instagram respondió con un cuerpo que no es JSON válido al "
                "crear el contenedor de media"
            )
        except KeyError:
            return self._error("Instagram respondió sin el id del contenedor de media")

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
                "Instagram respondió con un identificador de contenedor de "
                "media inesperado (se esperaba una cadena de texto y llegó "
                f"{type(contenedor_id).__name__}); no se puede continuar sin "
                "un id de contenedor válido"
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
                "se agotó el tiempo de espera publicando en Instagram: no se "
                "puede confirmar si Instagram llegó a aceptar la publicación "
                "antes de que se perdiera la respuesta. NO reintentes sin "
                "comprobar antes la cuenta, para no arriesgarte a duplicar la "
                "publicación.",
                riesgo_duplicado=True,
            )
        except httpx.HTTPError:
            return self._error(
                "no se pudo conectar con Instagram para publicar: no se puede "
                "confirmar si Instagram llegó a aceptar la publicación antes "
                "de que se perdiera la conexión. NO reintentes sin comprobar "
                "antes la cuenta, para no arriesgarte a duplicar la "
                "publicación.",
                riesgo_duplicado=True,
            )

        if publicacion.status_code != 200:
            return self._error(mensaje_de_error(publicacion, token))

        try:
            post_id = publicacion.json()["id"]
        except json.JSONDecodeError:
            return self._error(
                "Instagram respondió con un cuerpo que no es JSON válido tras "
                "publicar: la publicación ya fue aceptada (HTTP 200), pero no "
                "se puede confirmar ni reportar su id. NO reintentes sin "
                "comprobar antes la cuenta, para no arriesgarte a duplicar la "
                "publicación.",
                riesgo_duplicado=True,
            )
        except KeyError:
            return self._error(
                "Instagram respondió sin el id de la publicación: la "
                "publicación ya fue aceptada (HTTP 200), pero no se puede "
                "confirmar ni reportar su id. NO reintentes sin comprobar "
                "antes la cuenta, para no arriesgarte a duplicar la "
                "publicación.",
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
                "Instagram respondió con un identificador de publicación "
                "inesperado (se esperaba una cadena de texto y llegó "
                f"{type(post_id).__name__}); la publicación probablemente "
                "ya está hecha en Instagram -media_publish ya la aceptó y "
                "solo ha fallado el id devuelto para confirmarla-, así que "
                "NO la reintentes: volver a publicar puede duplicarla. "
                "Comprueba la cuenta antes de volver a publicar.",
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
        """Pide el permalink público real del media ya publicado.

        `media_publish` solo devuelve un id interno de media, no el
        shortcode público, así que la única forma de obtener un enlace que
        de verdad resuelva a la publicación es pedir el campo `permalink`
        del nodo IG Media (`GET /{ig-media-id}?fields=permalink`).

        Esta llamada es de mejor esfuerzo: la publicación ya ha tenido
        éxito antes de invocarse, así que esta función NUNCA lanza y
        degrada a `None` ante cualquier fallo -de red, un HTTP distinto de
        200, un cuerpo que no es JSON válido, que no trae el campo
        `permalink`, o que lo trae con un tipo que no es una cadena de texto
        (p. ej. un número)-, sin distinguir el motivo: no hay ningún mensaje
        de error que construir aquí (a diferencia del resto del adaptador),
        así que tampoco hay ningún texto en el que el token pudiera
        filtrarse por esta vía.

        La comprobación de tipo es necesaria porque `PostResult.url` está
        tipado `str | None`: pydantic v2 no coacciona un entero (o
        cualquier otro tipo) a texto, así que devolver el valor tal cual
        cuando no es una cadena haría que `PostResult(url=...)` en
        `_publicar()` lanzara un `ValidationError` -fuera de cualquier
        `try` propio de esa función-, que solo atraparía el resguardo
        genérico de `publish()`. El resultado sería justo lo que la
        degradación con gracia prohíbe: una publicación que Instagram ya
        aceptó reportada como `ERROR`, con el `platform_id` perdido. A
        diferencia de `post_id`/`contenedor_id` (ver `_publicar`), un
        `permalink` con un tipo inesperado sigue siendo cosmético: basta
        con degradar a `None`, no hace falta abortar con `ERROR`.
        """
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
        """Sondea el contenedor hasta que termine. No lanza: cualquier fallo
        de este bucle también vuelve como `PostResult` de error.

        Devuelve `None` si el contenedor terminó bien (toca publicar) o un
        `PostResult` de error si hay que abortar sin publicar: contenedor en
        ERROR o EXPIRED, un fallo de red o de formato durante el sondeo, o
        agotar `self.intentos` sin ver FINISHED.

        El enum completo de `status_code` (confirmado con Context7,
        "Troubleshooting > Container publishing status" e "IG Container >
        Reading > Fields") es `EXPIRED` ("not published within 24 hours"),
        `ERROR` ("failed process"), `FINISHED` ("ready to publish"),
        `IN_PROGRESS` ("currently processing") y `PUBLISHED` ("successfully
        completed"). De esos cinco, `EXPIRED` y `ERROR` son terminales -el
        contenedor nunca va a llegar a FINISHED- así que ambos cortan el
        sondeo de inmediato, cada uno con un mensaje que dice qué ha pasado.
        Antes, `EXPIRED` caía en el mismo camino que "todavía procesando" y
        agotaba los `self.intentos` restantes sin motivo antes de fallar con
        un mensaje genérico de timeout, en vez de abortar ya con la causa
        real. Cualquier otro valor (`IN_PROGRESS`, o uno futuro que Meta
        añada y que hoy no está documentado) sigue tratándose como "todavía
        no ha terminado": se sigue sondeando en vez de tratarlo como éxito o
        como fallo inmediato.
        """
        for intento in range(self.intentos):
            try:
                estado = client.get(
                    f"{GRAFO}/{contenedor_id}",
                    params={"fields": "status_code", "access_token": token},
                )
            except httpx.TimeoutException:
                return self._error(
                    "se agotó el tiempo de espera consultando el estado del "
                    "contenedor de media en Instagram"
                )
            except httpx.HTTPError:
                return self._error(
                    "no se pudo conectar con Instagram para consultar el "
                    "estado del contenedor de media"
                )

            if estado.status_code != 200:
                return self._error(mensaje_de_error(estado, token))

            try:
                codigo = estado.json()["status_code"]
            except json.JSONDecodeError:
                return self._error(
                    "Instagram respondió con un cuerpo que no es JSON válido "
                    "al consultar el contenedor de media"
                )
            except KeyError:
                return self._error(
                    "Instagram respondió sin status_code al consultar el "
                    "contenedor de media"
                )

            if codigo == "FINISHED":
                return None
            if codigo == "ERROR":
                return self._error(
                    "Instagram no ha podido procesar la media (el "
                    "contenedor ha quedado en estado ERROR)"
                )
            if codigo == "EXPIRED":
                # Terminal, igual que ERROR (ver docstring de este método):
                # el contenedor caduca a las 24h sin publicar y nunca va a
                # llegar a FINISHED, así que seguir sondeando solo agotaría
                # self.intentos sin motivo antes de fallar con un mensaje
                # genérico de timeout que ocultaría la causa real.
                return self._error(
                    "el contenedor de media de Instagram ha caducado "
                    "(status_code=EXPIRED) sin llegar a publicarse: no se "
                    "publicó dentro de las 24 horas; crea el contenedor de "
                    "nuevo para volver a intentarlo"
                )

            # No dormir tras el último intento agotado: no serviría de nada
            # y solo ralentizaría cada publicación que acaba fallando.
            if intento < self.intentos - 1:
                time.sleep(self.espera_s)

        return self._error(
            f"el contenedor de Instagram no terminó de procesarse a tiempo "
            f"tras {self.intentos} intentos"
        )

    def _error(self, mensaje: str, *, riesgo_duplicado: bool = False) -> PostResult:
        return PostResult(
            platform=self.platform,
            status=PostStatus.ERROR,
            error=mensaje,
            riesgo_duplicado=riesgo_duplicado,
        )


ADAPTADORES[Platform.INSTAGRAM] = InstagramAdapter
