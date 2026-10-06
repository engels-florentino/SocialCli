"""Strict documented YouTube metadata operations; no arbitrary HTTP patches."""
from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from socialctl.management.changes import ChangeError, PATCH_FIELDS, _validate_patch_mapping, _validate_snippet
from socialctl.management.youtube import WRITABLE_SNIPPET_FIELDS

STATUS_FIELDS = frozenset({"embeddable", "license", "privacyStatus", "publicStatsViewable",
                           "publishAt", "selfDeclaredMadeForKids", "containsSyntheticMedia"})
STATUS_READ_ONLY = frozenset({"uploadStatus", "failureReason", "rejectionReason", "madeForKids"})
KINDS = {
    "snippet": ("snippet", PATCH_FIELDS),
    "chapters": ("snippet", frozenset({"description"})),
    "localizations": ("localizations", None),
    "status": ("status", frozenset({"embeddable", "license", "publicStatsViewable"})),
    "privacy": ("status", frozenset({"privacyStatus"})),
    "schedule": ("status", frozenset({"publishAt", "privacyStatus"})),
    "audience": ("status", frozenset({"selfDeclaredMadeForKids"})),
    "synthetic": ("status", frozenset({"containsSyntheticMedia"})),
}


def validate_localizations(value: Any) -> None:
    if not isinstance(value, dict):
        raise ChangeError("localizations debe ser un mapping")
    for language, fields in value.items():
        if not isinstance(language, str) or not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", language):
            raise ChangeError("localizations requiere códigos de idioma explícitos")
        if (not isinstance(fields, dict) or set(fields) != {"title", "description"}
                or any(not isinstance(text, str) for text in fields.values())):
            raise ChangeError("localizations admite solo title y description de texto")
        _validate_snippet({**fields, "categoryId": "27"})


def validate_status(value: dict[str, Any], *, outgoing: bool = False) -> None:
    for key, item in value.items():
        if key not in STATUS_FIELDS:
            raise ChangeError(f"campo de status desconocido: {key}")
        if key in {"embeddable", "publicStatsViewable", "selfDeclaredMadeForKids", "containsSyntheticMedia"}:
            if type(item) is not bool:
                raise ChangeError(f"{key} debe ser booleano explícito")
        elif key == "privacyStatus" and (not isinstance(item, str) or item not in {"private", "public", "unlisted"}):
            raise ChangeError("privacyStatus inválido")
        elif key == "license" and (not isinstance(item, str) or item not in {"youtube", "creativeCommon"}):
            raise ChangeError("license inválida")
        elif key == "publishAt":
            if not isinstance(item, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", item):
                raise ChangeError("publishAt requiere RFC3339 con zona horaria")
            try:
                date = datetime.fromisoformat(item.replace("Z", "+00:00"))
            except ValueError:
                raise ChangeError("publishAt requiere RFC3339") from None
            if date.tzinfo is None:
                raise ChangeError("publishAt requiere zona horaria")
            if outgoing and date <= datetime.now(timezone.utc):
                raise ChangeError("publishAt debe ser futuro; el pasado puede publicar inmediatamente")


class MetadataEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    video_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    kind: Literal["snippet", "chapters", "localizations", "status", "privacy", "schedule", "audience", "synthetic"]
    patch: dict[str, Any]
    never_published: bool = False

    @model_validator(mode="after")
    def validate_operation(self):
        if not self.patch:
            raise ChangeError("patch vacío")
        part, fields = KINDS[self.kind]
        if "defaultAudioLanguage" in self.patch:
            raise ChangeError("defaultAudioLanguage: edición no verificada por documentación del método; solo se preserva")
        if fields is not None and set(self.patch) - fields:
            raise ChangeError(f"campo desconocido para operación {self.kind}")
        if part == "snippet":
            _validate_patch_mapping(self.patch)
        elif part == "localizations":
            validate_localizations(self.patch)
        else:
            validate_status(self.patch)
        if self.kind == "schedule":
            if not self.never_published:
                raise ChangeError("schedule requiere never_published: true declarado por el usuario; privado no prueba historial")
            if set(self.patch) != {"publishAt", "privacyStatus"} or self.patch["privacyStatus"] != "private":
                raise ChangeError("schedule requiere publishAt y privacyStatus: private explícitos")
        elif self.never_published:
            raise ChangeError("never_published solo corresponde a schedule")
        return self


def editable_parts(resource: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        "snippet": {key: copy.deepcopy(value) for key, value in resource["snippet"].items()
                    if key in WRITABLE_SNIPPET_FIELDS and value is not None},
        "status": {key: copy.deepcopy(value) for key, value in resource["status"].items() if key in STATUS_FIELDS},
        "localizations": copy.deepcopy(resource.get("localizations", {})),
    }


def validate_chapters(description: str, duration: str) -> None:
    match = re.fullmatch(r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", duration)
    if not match or not any(match.groups()):
        raise ChangeError("capítulos: duración API ausente o no interpretable")
    days, hours, minutes, seconds = [float(value or 0) for value in match.groups()]
    total = days * 86400 + hours * 3600 + minutes * 60 + seconds
    starts = []
    for line in description.splitlines():
        stamp = re.match(r"^\s*(\d+):(\d{2})(?::(\d{2}))?\s+\S", line)
        if stamp:
            first, second, third = stamp.groups()
            if int(second) >= 60 or (third is not None and int(third) >= 60):
                raise ChangeError("capítulo: timestamp inválido")
            starts.append(int(first) * (3600 if third is not None else 60) + int(second) * (60 if third is not None else 1) + int(third or 0))
    if len(starts) < 3 or starts[0] != 0:
        raise ChangeError("capítulos: primer inicio 0:00 y mínimo tres capítulos")
    if any(end - start < 10 for start, end in zip(starts, [*starts[1:], total])):
        raise ChangeError("capítulos: cada capítulo requiere 10 segundos, incluido el último frente a duración API")


def propose_parts(observed, edit: MetadataEdit):
    edit = MetadataEdit.model_validate(edit.model_dump())
    all_parts = editable_parts(observed.resource)
    part = KINDS[edit.kind][0]
    before = {part: all_parts[part]}
    after = {part: {**copy.deepcopy(before[part]), **copy.deepcopy(edit.patch)}}
    if part == "snippet":
        _validate_snippet(after[part])
    elif part == "localizations":
        if not all_parts["snippet"].get("defaultLanguage"):
            raise ChangeError("localizations requiere snippet.defaultLanguage existente")
        validate_localizations(after[part])
    else:
        validate_status(after[part], outgoing=True)
    if edit.kind == "privacy" and "publishAt" in after[part] and after[part]["privacyStatus"] != "private":
        raise ChangeError("existe publishAt: no se cambia privacidad conservando un horario incompatible")
    if edit.kind == "chapters":
        validate_chapters(edit.patch["description"], observed.resource.get("contentDetails", {}).get("duration", ""))
    return before, after
