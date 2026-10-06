"""Shared socialctl data models."""

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
    """Original relative media path, including subdirectories. Empty values fall back through ruta_relativa_efectiva."""


class StoryPost(BaseModel):
    """A standalone Story, separate from feed and Reel posts."""

    platform: Platform
    media: MediaAsset
    public_url: str | None = None
    text: str | None = None
    expires_at: datetime
    source_video_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{11}$")

    @model_validator(mode="after")
    def validate_story(self):
        if self.platform not in {Platform.FACEBOOK, Platform.INSTAGRAM}:
            raise ValueError("Stories are only supported on Facebook or Instagram")
        if self.text is not None and not self.text.strip():
            raise ValueError("optional Story text cannot be empty")
        if self.expires_at.utcoffset() is None:
            raise ValueError("expires_at must include a timezone")
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
            raise ValueError("tags/visible_hashtags require lists of strings; omitting them preserves legacy mode")
        return value

    @model_validator(mode="after")
    def validate_youtube_tags(self):
        if self.platform != Platform.YOUTUBE and (self.tags is not None or self.visible_hashtags is not None):
            raise ValueError("tags/visible_hashtags are only supported on YouTube")
        if self.tags is not None and self.hashtags:
            raise ValueError("YouTube: legacy hashtags and explicit tags are ambiguous; use one")
        if self.visible_hashtags is not None:
            for tag in self.visible_hashtags:
                value = tag.removeprefix("#")
                if not value or any(not (char.isalnum() or char == "_") for char in value):
                    raise ValueError("visible_hashtags requires each hashtag without spaces or punctuation")
        return self

    media: list[MediaAsset] = []
    link: str | None = None
    content_origin: Literal["standalone", "youtube_long"] | None = None
    source_video_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{11}$")
    first_comment: str | None = None
    privacy: str | None = None
    """Requested privacy where supported. YouTube uses this field and defaults to private; TikTok validates creator_info options separately."""


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
    """True when content may already be published despite an error. Automatic retry must respect this structured duplicate-risk signal."""


class ValidationError(BaseModel):
    """A specific validation failure detected before publication."""

    platform: Platform
    campo: str
    motivo: str
