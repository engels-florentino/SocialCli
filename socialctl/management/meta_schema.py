"""Typed, method-specific Meta edits. Read fields never imply write permission."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from socialctl.management.changes import ChangeError


class MetaError(ChangeError):
    pass


class MetaRejected(MetaError):
    pass


class MetaUncertain(MetaError):
    pass


def meta_id(value, *, composite=False):
    pattern = r"[0-9]{1,64}(?:_[0-9]{1,64})?" if composite else r"[0-9]{1,64}"
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise MetaError("ID Meta inválido para este recurso; no se admiten URLs/rutas")
    return value


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PagePatch(Strict):
    about: str | None = None
    bio: str | None = None
    description: str | None = None
    company_overview: str | None = None
    general_info: str | None = None
    mission: str | None = None
    phone: str | None = None
    website: str | None = None


class PostPatch(Strict):
    message: str | None = None


class VideoPatch(Strict):
    description: str | None = None


class MediaPatch(Strict):
    comment_enabled: bool | None = None


PATCHES = {"profile-update": PagePatch, "post-update": PostPatch,
           "video-update": VideoPatch, "media-update": MediaPatch}
ACTIONS = set(PATCHES) | {"post-delete", "video-delete", "media-delete", "comment-moderate",
                         "like", "unlike", "subscription-set", "subscription-remove",
                         "schedule-reprogram", "schedule-cancel", "mention-reply"}


class MetaEdit(Strict):
    version: Literal[1] = 1
    action: str
    target_id: str
    patch: dict | None = None
    comment_id: str | None = None
    hide: bool | None = None
    subscribed_fields: list[str] | None = None
    scheduled_publish_time: int | None = Field(default=None, gt=0)
    message: str | None = None
    media_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def exact_fields(cls, raw):
        if not isinstance(raw, dict) or raw.get("action") not in ACTIONS:
            raise MetaError("acción Meta no admitida")
        action = raw["action"]
        optional = {"comment-moderate": {"comment_id", "hide"},
                    "subscription-set": {"subscribed_fields"},
                    "schedule-reprogram": {"scheduled_publish_time"},
                    "mention-reply": {"comment_id", "media_id", "message"}}
        required = {"action", "target_id"} | ({"patch"} if action in PATCHES else optional.get(action, set()))
        if required - set(raw) or set(raw) - required - {"version"} or any(raw[k] is None for k in raw):
            raise MetaError("campos omitidos/extra/null para la acción; omitir conserva")
        meta_id(raw["target_id"], composite=True)
        if "comment_id" in raw:
            meta_id(raw["comment_id"], composite=True)
        if "media_id" in raw:
            meta_id(raw["media_id"])
        if action in PATCHES:
            patch = raw["patch"]
            if not isinstance(patch, dict) or not patch or any(v is None for v in patch.values()):
                raise MetaError("patch vacío/null; omitir conserva")
            PATCHES[action].model_validate(patch)
            if any(isinstance(v, str) and len(v) > 63206 for v in patch.values()):
                raise MetaError("texto excede el límite local")
        if action == "subscription-set":
            values = raw["subscribed_fields"]
            if not isinstance(values, list) or not 1 <= len(values) <= 3 or len(set(values)) != len(values):
                raise MetaError("selecciona campos de suscripción explícitos y únicos")
        if action == "mention-reply" and (not isinstance(raw["message"], str) or not 0 < len(raw["message"]) <= 2200):
            raise MetaError("texto de respuesta explícito inválido")
        return raw


def validate_edit(value):
    try:
        return MetaEdit.model_validate(value.model_dump(exclude_none=True) if isinstance(value, MetaEdit) else value)
    except ValidationError:
        raise MetaError("tipos/campos de la propuesta Meta inválidos") from None


def load_edit(path):
    try:
        path = Path(path)
        if path.stat().st_size > 1024 * 1024:
            raise MetaError("propuesta excede 1 MiB")
        return validate_edit(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError):
        raise MetaError("no se pudo leer la propuesta YAML") from None
