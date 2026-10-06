"""Límites y reglas de formato de cada red.

Todos los límites de plataforma viven aquí y solo aquí. Cambian con el tiempo;
actualizarlos es editar PLATFORM_SPECS, nunca los adaptadores.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from socialctl.models import Platform, PlatformPost, ValidationError


class PlatformSpec(BaseModel):
    max_title: int | None = None
    exige_title: bool = False
    max_body: int
    unidad_max_body: Literal["caracteres", "bytes_utf8", "unidades_utf16"] = "caracteres"
    """Unidad en la que `max_body` mide el cuerpo compuesto (`componer_caption`).

    Confirmado por red (vía Context7, developers.google.com,
    developers.facebook.com y developers.tiktok.com):
    - YouTube (`snippet.description`): **bytes UTF-8** ("The video
      description is limited to 5000 bytes"). Un texto en español con
      tildes/eñes ocupa más bytes que caracteres, así que medir en
      caracteres (como antes) podía dar luz verde a un texto que la API
      rechazaría.
    - Instagram (`caption`): caracteres ("Captions are limited to 2200
      characters").
    - Facebook (`message` de página): no confirmado con Context7 (la cifra
      de `max_body` en sí tampoco lo está, ver comentario en
      `PLATFORM_SPECS`); se deja en "caracteres" (comportamiento previo, sin
      cambios) por no tener evidencia para tocarlo.
    - TikTok (`post_info.title`): confirmado con Context7
      (content-posting-api-reference-direct-post: "Video captions support
      hashtags and mentions, with a maximum length of 2200 UTF-16
      characters") -una TERCERA unidad, distinta tanto de `len()` en Python
      (cuenta puntos de código: un emoji fuera del BMP cuenta 1) como de
      bytes UTF-8 (una tilde/eñe cuenta 2, un emoji fuera del BMP cuenta 4).
      Un emoji fuera del BMP (la mayoría de los emoji "recientes", p. ej.
      🎬 o 😀) se codifica en UTF-16 como un par subrogado: 2 "unidades"
      UTF-16, aunque `len()` lo siga contando como 1 solo carácter. Medir en
      caracteres Python podía dar luz verde a un cuerpo con muchos emoji que
      la API rechazaría por exceder las 2200 unidades UTF-16 reales.

    Por eso este campo vive en `PlatformSpec` (no es una decisión global de
    `validar_texto`): las redes miden de forma distinta y no hay un único
    criterio válido para las cuatro.
    """
    hook_chars: int | None = None  # chars visibles antes del "ver más"
    max_hashtags: int | None
    """Tope al **número** de hashtags/elementos. Sin default: cada red debe
    declararlo explícitamente (incluida YouTube, con `None` explícito, ver
    más abajo), para que omitirlo por accidente siga rompiendo la
    construcción de `PLATFORM_SPECS` en vez de colar un valor por defecto.
    """
    max_tags_chars: int | None = None
    """Tope **agregado** en caracteres para un campo tipo `tags` (array),
    contando separadores. Solo aplica a YouTube (`snippet.tags`): las demás
    redes no tienen un campo equivalente (sus hashtags van como texto
    `#etiqueta` dentro del propio cuerpo, ya cubierto por `max_body`), así
    que se quedan en `None`.
    """
    hashtags_en_texto: bool = True
    """Si los hashtags se incrustan en el propio texto publicado.

    ``True`` (el valor por defecto): la red los recibe como parte del
    mismo campo de texto que el cuerpo, compuestos con `componer_caption`
    -así publican Facebook, Instagram y TikTok-.

    ``False``: la red los recibe en un campo de metadatos aparte y por
    tanto NUNCA aparecen dentro del texto publicado. Hoy la única red así
    es YouTube: `hashtags` viaja como `snippet.tags`
    (`adapters/youtube.py`), no dentro de `snippet.description`.

    Vive aquí, y no como un `if` sobre el nombre de la red dentro de
    `render_preview` (`socialctl/publisher.py`), por el mismo motivo que
    `unidad_max_body` o `acepta_solo_texto`: es esta clase la que sabe cómo
    compone su texto cada red, no quien pinta el preview.
    """
    incluye_link_en_texto: bool = False
    """Si un `PlatformPost.link` presente se añade al final del texto
    publicado (tras el cuerpo y, si los hubiera, los hashtags compuestos).

    Hoy la única red así es Facebook -"la única red donde un enlace no
    penaliza el alcance" (ver `adapters/facebook.py`)-. Los adaptadores de
    las otras tres redes ni siquiera leen `post.link`: un enlace puesto ahí
    para ellas se ignora en silencio.
    """
    valores_privacidad: list[str] | None = None
    """Valores válidos de `PlatformPost.privacy` para esta red.

    `None` (el valor por defecto) significa que la red NO admite el campo:
    `validar_privacidad` rechaza cualquier `post.privacy` no vacío en una
    red así, en vez de ignorarlo en silencio como se hace con `link`. Hoy
    la única red con una lista aquí es YouTube -`["public", "unlisted",
    "private"]`, los tres valores que admite `status.privacyStatus`
    (confirmado con Context7 en
    developers.google.com/youtube/v3/guides/uploading_a_video)-; Facebook,
    Instagram y TikTok se quedan en `None` (TikTok resuelve su privacidad
    por otra vía, consultando `creator_info`, ver
    `TikTokAdapter._resolver_privacidad_y_duracion`; Facebook e Instagram
    no tienen ningún equivalente).
    """
    privacidad_por_defecto: str | None = None
    """Valor efectivo de privacidad cuando `post.privacy` es `None`, para
    las redes que sí admiten el campo (`valores_privacidad is not None`).

    En YouTube, `"private"`: decisión explícita del proyecto -nunca
    `"public"`-, para que ningún vídeo salga visible al público sin que el
    usuario lo pida. `None` en las redes sin `valores_privacidad`, donde no
    tiene sentido ningún valor por defecto.
    """
    acepta_solo_texto: bool = False
    exige_media: bool
    max_media: int = 1
    min_video_s: float | None = None
    max_video_s: float | None = None
    max_bytes: int | None = None
    aspect_ratios: list[str] = []


PLATFORM_SPECS: dict[Platform, PlatformSpec] = {
    Platform.YOUTUBE: PlatformSpec(
        max_title=100,  # "maximum length of 100 characters" (confirmado, en caracteres).
        exige_title=True,
        max_body=5000,
        unidad_max_body="bytes_utf8",  # snippet.description: "limited to 5000 bytes".
        # snippet.tags no tiene un tope documentado de número de elementos:
        # la cifra "15 hashtags" no aparece en la Data API (parece una
        # política de la Central de Ayuda sobre #hashtags escritos como
        # texto en título/descripción, no sobre este campo). Se deja en
        # None -explícito, no un olvido- y se sustituye por max_tags_chars,
        # que sí es el límite real documentado para tags.
        max_hashtags=None,
        max_tags_chars=500,  # snippet.tags: "500 characters, where commas between tags contribute to the total length" (Revision History, 13 mar 2014).
        # adapters/youtube.py publica post.body TAL CUAL como
        # snippet.description y post.hashtags aparte como snippet.tags: los
        # hashtags nunca llegan a aparecer dentro del texto de la
        # descripción, así que aquí es explícito -no el valor por defecto
        # heredado sin más- que esta red es la excepción.
        hashtags_en_texto=False,
        # status.privacyStatus: "public", "private" o "unlisted" (confirmado
        # con Context7 en developers.google.com/youtube/v3/guides/uploading_a_video:
        # "The privacy status of an uploaded video can be set to public,
        # private, or unlisted"). El propio ejemplo de esa guía recomienda
        # "private" o "unlisted" para vídeos de prueba, precisamente para no
        # dejarlos visibles al público por accidente -el mismo motivo por el
        # que este proyecto elige "private" como valor por defecto, nunca
        # "public" (ver privacidad_por_defecto)-.
        valores_privacidad=["public", "unlisted", "private"],
        privacidad_por_defecto="private",
        exige_media=True,
        min_video_s=1,
        max_video_s=None,
        max_bytes=256 * 1024**3,  # videos.insert: "Uploaded files must not exceed 256GB" (subido desde 128GB en abr. 2022).
        aspect_ratios=["9:16", "16:9"],
    ),
    Platform.FACEBOOK: PlatformSpec(
        max_body=63206,
        max_hashtags=30,
        acepta_solo_texto=True,
        # adapters/facebook.py añade post.link (si lo hay) al final del
        # texto compuesto, tanto en el post de solo texto (message) como en
        # el de imagen (caption) o vídeo (description): explícito -no el
        # valor por defecto- porque es la única de las cuatro redes que lo
        # hace.
        incluye_link_en_texto=True,
        exige_media=False,
        max_video_s=90 * 60,
        max_bytes=10 * 1024**3,
        aspect_ratios=["9:16", "16:9", "1:1", "4:5"],
    ),
    Platform.INSTAGRAM: PlatformSpec(
        max_body=2200,
        hook_chars=125,
        max_hashtags=30,
        exige_media=True,
        min_video_s=3,
        max_video_s=15 * 60,
        # Este adaptador publica el vídeo SIEMPRE como Reels
        # (instagram.py fija media_type="REELS"), y "Reel Specifications"
        # (ig-user/media, confirmado con Context7) dice: "Reels must be...
        # a maximum file size of 300MB". El valor viejo (1 GB) triplicaba
        # el límite real: dejaba pasar localmente un archivo que Instagram
        # rechazaría minutos después, de forma opaca, en el sondeo del
        # contenedor (status_code=ERROR).
        max_bytes=300 * 1024**2,
        aspect_ratios=["9:16", "1:1", "4:5"],
    ),
    Platform.TIKTOK: PlatformSpec(
        max_body=2200,
        # post_info.title: "maximum length of 2200 UTF-16 characters"
        # (content-posting-api-reference-direct-post, confirmado con
        # Context7) -no caracteres Python ni bytes UTF-8, ver docstring de
        # unidad_max_body.
        unidad_max_body="unidades_utf16",
        max_hashtags=30,
        exige_media=True,
        min_video_s=3,
        max_video_s=10 * 60,
        max_bytes=4 * 1024**3,
        aspect_ratios=["9:16"],
    ),
}


def componer_caption(body: str, hashtags: list[str]) -> str:
    """Une cuerpo y hashtags con el separador estándar."""
    if not hashtags:
        return body
    etiquetas = " ".join(f"#{h.lstrip('#')}" for h in hashtags)
    return f"{body}\n\n{etiquetas}"


def texto_publicado(post: PlatformPost) -> str:
    """Texto exactamente como lo publica el adaptador de ``post.platform``.

    Es decir: el contenido real del campo de texto principal de esa red
    (`snippet.description` en YouTube; `message`/`caption`/`description`
    según el tipo de contenido en Facebook; `caption` en Instagram;
    `post_info.title` en TikTok), no una aproximación.

    Dos decisiones de composición cambian de una red a otra, y ambas viven
    en `PLATFORM_SPECS` -no aquí como un `if` sobre la plataforma-, porque
    son propiedades de cada red, igual que su unidad de medida o sus
    límites:

    - `hashtags_en_texto`: si los hashtags se incrustan en el texto
      (`componer_caption`) o si, como en YouTube, la red los recibe aparte
      como metadatos y por tanto no aparecen aquí.
    - `incluye_link_en_texto`: si un `post.link` presente se añade al
      final del texto (hoy, solo Facebook).

    `render_preview` (`socialctl/publisher.py`) usa esta misma función
    para pintar el preview, así que no puede divergir de lo que de verdad
    envía cada adaptador: cualquier cambio futuro en cómo una red compone
    su texto se hace aquí una vez, y preview y publicación quedan
    sincronizados sin tocarse el uno al otro.
    """
    spec = PLATFORM_SPECS[post.platform]
    texto = componer_caption(post.body, post.hashtags) if spec.hashtags_en_texto else componer_caption(post.body, post.visible_hashtags or [])
    if spec.incluye_link_en_texto and post.link:
        texto = f"{texto}\n\n{post.link}"
    return texto


def youtube_tags(post: PlatformPost) -> list[str]:
    """Explicit internal tags, or the unchanged legacy `hashtags` mapping."""
    return post.tags if post.tags is not None else post.hashtags


def privacidad_efectiva(post: PlatformPost) -> str | None:
    """Valor de privacidad que de verdad se va a enviar a la API para ``post``.

    Aplica el valor por defecto de la red (`PlatformSpec.privacidad_por_defecto`)
    cuando `post.privacy` es `None`, y devuelve `post.privacy` tal cual
    cuando el usuario sí lo indicó. Devuelve `None` en las redes que no
    admiten el campo (`PlatformSpec.valores_privacidad` es `None`): ahí no
    hay ningún valor "efectivo" que enviar, ni por defecto ni indicado.

    Tanto `adapters/youtube.py` (para construir `status.privacyStatus`)
    como `render_preview` (`socialctl/publisher.py`, para mostrarlo) usan
    esta misma función, para que el preview jamás pueda divergir de lo que
    de verdad se publica -la misma razón por la que `texto_publicado` es
    compartida entre ambos-.
    """
    spec = PLATFORM_SPECS[post.platform]
    if spec.valores_privacidad is None:
        return None
    return post.privacy if post.privacy is not None else spec.privacidad_por_defecto


def validar_privacidad(post: PlatformPost) -> list[ValidationError]:
    """Comprueba `post.privacy` contra lo que admite la red de ``post``.

    Dos formas de fallar, ambas bloqueantes (aparecen en el preview, no al
    publicar):

    - La red no admite el campo (`PlatformSpec.valores_privacidad` es
      `None`) pero `post.privacy` trae un valor de todas formas: se
      rechaza en vez de ignorarlo en silencio -a diferencia de `link`,
      que sí se ignora sin avisar en las redes que no lo leen-, porque un
      valor de privacidad que el usuario cree que se aplica y en realidad
      se descarta es un riesgo real, no un detalle menor.
    - La red sí admite el campo pero el valor no es ninguno de los que
      acepta (`PlatformSpec.valores_privacidad`).

    `post.privacy is None` nunca es un error, ni siquiera en una red sin
    soporte: es el caso normal de "no se ha indicado nada".
    """
    spec = PLATFORM_SPECS[post.platform]
    errores: list[ValidationError] = []

    if post.privacy is None:
        return errores

    if spec.valores_privacidad is None:
        errores.append(
            ValidationError(
                platform=post.platform,
                campo="privacy",
                motivo=(
                    f"{post.platform.value} no admite el campo 'privacy' "
                    f"(se ha indicado '{post.privacy}'); quítalo del bloque "
                    f"de {post.platform.value} en post.yml"
                ),
            )
        )
    elif post.privacy not in spec.valores_privacidad:
        validos = ", ".join(spec.valores_privacidad)
        errores.append(
            ValidationError(
                platform=post.platform,
                campo="privacy",
                motivo=(
                    f"'{post.privacy}' no es un valor de privacidad válido "
                    f"para {post.platform.value}. Valores válidos: {validos}"
                ),
            )
        )

    return errores


def _longitud_utf16(texto: str) -> int:
    """Longitud de ``texto`` en unidades UTF-16, tal y como la mide TikTok.

    Un punto de código Python (lo que cuenta `len()`) fuera del BMP -la
    mayoría de los emoji "recientes", p. ej. 🎬 o 😀- se codifica en UTF-16
    como un PAR SUBROGADO: dos unidades de 16 bits, no una. Codificar a
    ``utf-16-le`` y dividir entre 2 (cada unidad ocupa exactamente 2 bytes
    en esa codificación) reproduce ese recuento exacto sin depender de
    ninguna librería externa.
    """
    return len(texto.encode("utf-16-le")) // 2


def _longitud_agregada_tags(tags: list[str]) -> int:
    """Longitud agregada de una lista de tags tal y como la cuenta YouTube.

    Solo la usa `snippet.tags` (YouTube): la documentación dice que las
    comas entre elementos suman al total, y que un tag con espacios se
    cuenta como si fuera entre comillas (dos caracteres más), igual que
    "cool, video, more keywords" pasaría a contar sus comas y un tag como
    "more keywords" contaría como `"more keywords"` (con comillas). Unir con
    "," tras envolver entre comillas los tags con espacio reproduce
    exactamente ese recuento.
    """
    if not tags:
        return 0
    piezas = [f'"{t}"' if " " in t else t for t in tags]
    return len(",".join(piezas))


def validar_texto(post: PlatformPost) -> list[ValidationError]:
    """Comprueba el texto de un post contra los límites de su red."""
    spec = PLATFORM_SPECS[post.platform]
    errores: list[ValidationError] = []

    if spec.exige_title and not post.title:
        errores.append(
            ValidationError(
                platform=post.platform,
                campo="title",
                motivo=f"{post.platform.value} exige un título y no se ha indicado",
            )
        )

    if post.title and spec.max_title and len(post.title) > spec.max_title:
        errores.append(
            ValidationError(
                platform=post.platform,
                campo="title",
                motivo=(
                    f"el título tiene {len(post.title)} caracteres "
                    f"y el máximo son {spec.max_title}"
                ),
            )
        )

    caption = texto_publicado(post) if post.platform == Platform.YOUTUBE else componer_caption(post.body, post.hashtags)
    if spec.unidad_max_body == "bytes_utf8":
        longitud_body = len(caption.encode("utf-8"))
        unidad_body = "bytes"
    elif spec.unidad_max_body == "unidades_utf16":
        longitud_body = _longitud_utf16(caption)
        unidad_body = "unidades UTF-16"
    else:
        longitud_body = len(caption)
        unidad_body = "caracteres"

    if longitud_body > spec.max_body:
        errores.append(
            ValidationError(
                platform=post.platform,
                campo="body",
                motivo=(
                    f"el texto ocupa {longitud_body} {unidad_body} "
                    f"y el máximo son {spec.max_body} {unidad_body}"
                ),
            )
        )

    if spec.max_hashtags is not None and len(post.hashtags) > spec.max_hashtags:
        errores.append(
            ValidationError(
                platform=post.platform,
                campo="hashtags",
                motivo=(
                    f"hay {len(post.hashtags)} hashtags "
                    f"y el máximo son {spec.max_hashtags}"
                ),
            )
        )

    if spec.max_tags_chars is not None:
        longitud_tags = _longitud_agregada_tags(youtube_tags(post))
        if longitud_tags > spec.max_tags_chars:
            errores.append(
                ValidationError(
                    platform=post.platform,
                    campo="tags" if post.tags is not None else "hashtags",
                    motivo=(
                        f"las etiquetas ocupan {longitud_tags} caracteres "
                        f"agregados y el máximo son {spec.max_tags_chars}"
                    ),
                )
            )

    if spec.hook_chars and not post.body[: spec.hook_chars].strip():
        errores.append(
            ValidationError(
                platform=post.platform,
                campo="gancho",
                motivo=(
                    f"los primeros {spec.hook_chars} caracteres están vacíos; "
                    "en el feed se corta ahí y el post no dice nada"
                ),
            )
        )

    return errores
