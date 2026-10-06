"""Modelos de datos compartidos por todo socialctl."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class Platform(str, Enum):
    YOUTUBE = "youtube"
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"


class MediaKind(str, Enum):
    VIDEO = "video"
    IMAGE = "image"


class CampaignType(str, Enum):
    CLIP_VERTICAL = "clip-vertical"
    LANZAMIENTO_VIDEO_LARGO = "lanzamiento-video-largo"
    POST_IMAGEN = "post-imagen"


class PostStatus(str, Enum):
    PUBLICADO = "publicado"
    PENDIENTE_CONFIRMACION = "pendiente_confirmacion"
    ERROR = "error"
    OMITIDO = "omitido"


class MediaAsset(BaseModel):
    path: Path
    kind: MediaKind
    width: int | None = None
    height: int | None = None
    duration_s: float | None = None
    size_bytes: int = 0
    ruta_relativa: str = ""
    """Ruta relativa (separador `/`, subcarpetas incluidas) tal como se
    escribió en `media` en post.yml -p. ej. `short/S2.mp4` si el usuario
    guarda sus vídeos verticales en `<Marca>/media/short/`-, para que quien
    la necesite (el preview, `guardar_post` al persistir de vuelta, la URL
    pública que construye Instagram) muestre o reconstruya esa subcarpeta
    en vez de perderla y quedarse solo con el nombre de archivo.

    Cadena vacía (valor por defecto) cuando el asset no vino de un
    post.yml -por ejemplo, uno construido a mano en un test, como los de
    `tests/test_adapter_instagram.py`-, que es también el caso simple sin
    subcarpeta. `socialctl.media.ruta_relativa_efectiva` es el punto único
    que resuelve ese "vacío": nunca se compara este campo contra `""`
    fuera de esa función, para que los tres lugares que lo consumen no
    puedan divergir en cómo tratan el caso por defecto.
    """


class StoryPost(BaseModel):
    """Una Story independiente del modelo de feed/Reel.

    ``public_url`` es el origen que Instagram descargará; puede quedar en
    ``None`` para el handoff de Facebook, cuya API pública todavía no está
    implementada en socialctl. ``expires_at`` limita la vigencia de la
    aprobación local y no se interpreta como prueba de la retención remota.
    """

    platform: Platform
    media: MediaAsset
    public_url: str | None = None
    text: str | None = None
    expires_at: datetime
    source_video_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{11}$")

    @model_validator(mode="after")
    def validate_story(self):
        if self.platform not in {Platform.FACEBOOK, Platform.INSTAGRAM}:
            raise ValueError("Stories solo se admiten en Facebook o Instagram")
        if self.text is not None and not self.text.strip():
            raise ValueError("el texto opcional de la Story no puede estar vacío")
        if self.expires_at.utcoffset() is None:
            raise ValueError("expires_at debe incluir zona horaria")
        return self


class PlatformPost(BaseModel):
    platform: Platform
    title: str | None = None
    body: str
    hashtags: list[str] = []
    tags: list[str] | None = Field(default=None, exclude_if=lambda value: value is None)
    visible_hashtags: list[str] | None = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator("tags", "visible_hashtags", mode="before")
    @classmethod
    def explicit_youtube_lists(cls, value):
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError("tags/visible_hashtags requieren listas de textos; omitir conserva el modo legacy")
        return value

    @model_validator(mode="after")
    def validate_youtube_tags(self):
        if self.platform != Platform.YOUTUBE and (self.tags is not None or self.visible_hashtags is not None):
            raise ValueError("tags/visible_hashtags solo se admiten en YouTube")
        if self.tags is not None and self.hashtags:
            raise ValueError("YouTube: hashtags legacy y tags explícitos son ambiguos; utiliza uno")
        if self.visible_hashtags is not None:
            for tag in self.visible_hashtags:
                value = tag.removeprefix("#")
                if not value or any(not (char.isalnum() or char == "_") for char in value):
                    raise ValueError("visible_hashtags requiere cada hashtag sin espacios ni puntuación")
        return self

    media: list[MediaAsset] = []
    link: str | None = None
    content_origin: Literal["standalone", "youtube_long"] | None = None
    source_video_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{11}$")
    first_comment: str | None = None
    privacy: str | None = None
    """Nivel de privacidad deseado para la publicación (campo `privacy` del
    bloque de la red en `post.yml`), si esa red admite el concepto.

    Hoy solo lo consume YouTube (`status.privacyStatus`, en
    `socialctl/adapters/youtube.py`; valores confirmados con Context7 en
    developers.google.com/youtube/v3/guides/uploading_a_video: "public",
    "private" o "unlisted"). TikTok resuelve su propia privacidad por una
    vía completamente distinta -``TikTokAdapter._resolver_privacidad_y_duracion``
    consulta ``creator_info`` en la API y valida contra las opciones reales
    que admite la cuenta, nunca un valor fijo de `post.yml`-; Facebook e
    Instagram no tienen ningún equivalente.

    Que el campo viva aquí, genérico para las cuatro redes, sigue el mismo
    criterio que `link` (que hoy solo usa Facebook): quien escribe
    `post.yml` no necesita un esquema distinto por red para un campo
    opcional que solo aplica a una. La diferencia deliberada respecto a
    `link` es qué pasa cuando se indica en una red que no lo admite: `link`
    se ignora en silencio (documentado así en cada adaptador que no lo
    lee), pero un `privacy` puesto en una red sin soporte se RECHAZA en
    `validate()` (`formatter.validar_privacidad`, según
    `PlatformSpec.valores_privacidad`) -un valor de privacidad que el
    usuario cree que se está aplicando y en realidad se ignora es
    exactamente el tipo de valor por defecto silencioso que este proyecto
    ya evita en otros sitios (p. ej. `PlatformSpec.max_hashtags`, que
    tampoco puede quedar sin declarar por accidente).

    `None` (nada indicado en `post.yml`) dispara el valor por defecto de
    `PlatformSpec.privacidad_por_defecto` -en YouTube, `"private"`:
    decisión explícita del proyecto, nunca `"public"`, para que ningún
    vídeo salga visible para el público sin que el usuario lo pida (ver
    `formatter.privacidad_efectiva`, que usan tanto el adaptador de
    YouTube como el preview para no poder divergir entre sí)-.
    """


class Post(BaseModel):
    slug: str
    brand: str
    campaign: CampaignType
    platforms: dict[Platform, PlatformPost]


class PostResult(BaseModel):
    platform: Platform
    status: PostStatus
    url: str | None = None
    platform_id: str | None = None
    error: str | None = None
    warnings: list[str] = []
    publication_id: str | None = None
    first_comment_id: str | None = None
    first_comment_change_id: str | None = None
    first_comment_status: str | None = None
    requested_privacy: str | None = None
    observed_privacy: str | None = None
    observed_publish_at: str | None = None
    observed_processing_status: str | None = None
    visibility_observed_at: str | None = None
    riesgo_duplicado: bool = False
    """Si es ``True``, el contenido puede haberse publicado ya pese al
    error: la red ya aceptó la publicación y solo falló confirmarla o
    reportarla, así que reintentar podría duplicarla. Lo marcan de forma
    explícita los adaptadores que detectan ese escenario (hoy, Instagram y
    TikTok, cuando la respuesta llega en un estado ambiguo después de que la
    red ya aceptó el contenido); el resto de errores -de red, de
    validación, de configuración- no implican este riesgo y dejan el valor
    por defecto, ``False``. `retry` (en `socialctl/cli.py`) decide si puede
    reintentar una red en automático a partir de este campo, no del texto
    de `error`: un mensaje nuevo que exprese el mismo riesgo con otras
    palabras seguiría detectándose, cosa que una búsqueda de texto no
    garantiza.
    """


class ValidationError(BaseModel):
    """Un incumplimiento concreto detectado antes de publicar."""

    platform: Platform
    campo: str
    motivo: str
