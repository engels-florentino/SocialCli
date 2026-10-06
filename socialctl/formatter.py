"""Platform-specific formatting rules and limits."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from socialctl.models import Platform, PlatformPost, ValidationError


class PlatformSpec(BaseModel):
    max_title: int | None = None
    exige_title: bool = False
    max_body: int
    unidad_max_body: Literal["caracteres", "bytes_utf8", "unidades_utf16"] = "caracteres"
    """Unit used to measure the composed body: code points, UTF-8 bytes or UTF-16 units, according to the platform."""
    hook_chars: int | None = None  # chars visibles antes del "ver más"
    max_hashtags: int | None
    """Maximum number of hashtag or tag elements; every platform must declare this explicitly."""
    max_tags_chars: int | None = None
    """Aggregate tag character limit, including separators, for YouTube snippet.tags."""
    hashtags_en_texto: bool = True
    """Whether hashtags are included in the published text; YouTube sends legacy hashtags as separate tag metadata."""
    incluye_link_en_texto: bool = False
    """Whether to append the optional link to published text; currently only Facebook does so."""
    valores_privacidad: list[str] | None = None
    """Supported privacy values, or None when this generic field is unsupported. Unsupported explicit values are rejected."""
    privacidad_por_defecto: str | None = None
    """Default effective privacy for supported platforms; YouTube defaults to private."""
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
    """Join body text and hashtags with the standard separator."""
    if not hashtags:
        return body
    etiquetas = " ".join(f"#{h.lstrip('#')}" for h in hashtags)
    return f"{body}\n\n{etiquetas}"


def texto_publicado(post: PlatformPost) -> str:
    """Return the exact text sent by the platform adapter."""
    spec = PLATFORM_SPECS[post.platform]
    texto = componer_caption(post.body, post.hashtags) if spec.hashtags_en_texto else componer_caption(post.body, post.visible_hashtags or [])
    if spec.incluye_link_en_texto and post.link:
        texto = f"{texto}\n\n{post.link}"
    return texto


def youtube_tags(post: PlatformPost) -> list[str]:
    """Explicit internal tags, or the unchanged legacy `hashtags` mapping."""
    return post.tags if post.tags is not None else post.hashtags


def privacidad_efectiva(post: PlatformPost) -> str | None:
    """Return the privacy value actually sent to the API, or None if unsupported."""
    spec = PLATFORM_SPECS[post.platform]
    if spec.valores_privacidad is None:
        return None
    return post.privacy if post.privacy is not None else spec.privacidad_por_defecto


def validar_privacidad(post: PlatformPost) -> list[ValidationError]:
    """Validate privacy against the platform's supported values."""
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
                    f"{post.platform.value} does not support the 'privacy' field "
                    f"(received '{post.privacy}'); remove it from the block "
                    f"for {post.platform.value} in post.yml"
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
                    f"'{post.privacy}' is not a valid privacy value "
                    f"for {post.platform.value}. Valid values: {validos}"
                ),
            )
        )

    return errores


def _longitud_utf16(texto: str) -> int:
    """Count UTF-16 code units, as required by TikTok."""
    return len(texto.encode("utf-16-le")) // 2


def _longitud_agregada_tags(tags: list[str]) -> int:
    """Count aggregate YouTube tag length, including separators and quoted spaces."""
    if not tags:
        return 0
    piezas = [f'"{t}"' if " " in t else t for t in tags]
    return len(",".join(piezas))


def validar_texto(post: PlatformPost) -> list[ValidationError]:
    """Validate post text against the platform's limits."""
    spec = PLATFORM_SPECS[post.platform]
    errores: list[ValidationError] = []

    if spec.exige_title and not post.title:
        errores.append(
            ValidationError(
                platform=post.platform,
                campo="title",
                motivo=f"{post.platform.value} requires a title, but none was provided",
            )
        )

    if post.title and spec.max_title and len(post.title) > spec.max_title:
        errores.append(
            ValidationError(
                platform=post.platform,
                campo="title",
                motivo=(
                    f"the title has {len(post.title)} characters "
                    f"; the maximum is {spec.max_title}"
                ),
            )
        )

    caption = texto_publicado(post) if post.platform == Platform.YOUTUBE else componer_caption(post.body, post.hashtags)
    if spec.unidad_max_body == "bytes_utf8":
        longitud_body = len(caption.encode("utf-8"))
        unidad_body = "bytes"
    elif spec.unidad_max_body == "unidades_utf16":
        longitud_body = _longitud_utf16(caption)
        unidad_body = "UTF-16 units"
    else:
        longitud_body = len(caption)
        unidad_body = "characters"

    if longitud_body > spec.max_body:
        errores.append(
            ValidationError(
                platform=post.platform,
                campo="body",
                motivo=(
                    f"the text uses {longitud_body} {unidad_body} "
                    f"; the maximum is {spec.max_body} {unidad_body}"
                ),
            )
        )

    if spec.max_hashtags is not None and len(post.hashtags) > spec.max_hashtags:
        errores.append(
            ValidationError(
                platform=post.platform,
                campo="hashtags",
                motivo=(
                    f"there are {len(post.hashtags)} hashtags "
                    f"; the maximum is {spec.max_hashtags}"
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
                        f"tags use {longitud_tags} characters "
                        f"combined; the maximum is {spec.max_tags_chars}"
                    ),
                )
            )

    if spec.hook_chars and not post.body[: spec.hook_chars].strip():
        errores.append(
            ValidationError(
                platform=post.platform,
                campo="gancho",
                motivo=(
                    f"the first {spec.hook_chars} characters are empty; "
                    "the feed truncates there, leaving no visible text"
                ),
            )
        )

    return errores
