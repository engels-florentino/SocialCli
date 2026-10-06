"""Exact supplied community intents; no generic endpoints or automatic targets."""
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from socialctl.management.youtube_resources import ResourceError, resource_id


def opaque_id(value):
    # Comment/reply/thread/subscription IDs are opaque (replies can contain dots).
    # Always query/JSON data, never URL paths; comma is a provider batch separator.
    if (not isinstance(value, str) or not 0 < len(value) <= 512 or "://" in value
        or any(not c.isprintable() or c.isspace() or c in ",?#" for c in value)):
        raise ResourceError("invalid opaque ID")
    return value


class CommentTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    video_id: str
    thread_id: str
    comment_id: str
    parent_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def no_null(cls, raw):
        if not isinstance(raw, dict) or any(v is None for v in raw.values()):
            raise ResourceError("target requires supplied nonnull fields")
        return raw

    @model_validator(mode="after")
    def validate_ids(self):
        resource_id(self.video_id)
        for value in (self.thread_id, self.comment_id, self.parent_id):
            if value is not None:
                opaque_id(value)
        return self


BASE = {"video_id", "thread_id", "comment_id"}
ACTIONS = {
    "add": ({"video_id", "text"}, {"video_id", "text"}),
    "reply": ({"video_id", "thread_id", "parent_id", "text"}, {"video_id", "thread_id", "parent_id", "text"}),
    "edit": (BASE | {"text"}, BASE | {"text", "parent_id"}),
    "delete": (BASE, BASE | {"parent_id"}),
    "moderate": ({"targets", "moderation_status", "ban_author"}, {"targets", "moderation_status", "ban_author"}),
    "subscribe": ({"channel_id"}, {"channel_id"}),
    "unsubscribe": ({"channel_id", "subscription_id"}, {"channel_id", "subscription_id"}),
    "rate": ({"video_id", "rating"}, {"video_id", "rating"}),
    "report-abuse": ({"video_id", "reason_id"}, {"video_id", "reason_id", "secondary_reason_id", "comments", "language"}),
}


class CommunityEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1] = 1
    action: str
    video_id: str | None = None
    thread_id: str | None = None
    comment_id: str | None = None
    parent_id: str | None = None
    text: str | None = Field(default=None, min_length=1, max_length=10000)
    channel_id: str | None = None
    subscription_id: str | None = None
    targets: list[CommentTarget] | None = Field(default=None, min_length=1, max_length=50)
    moderation_status: Literal["heldForReview", "published", "rejected"] | None = None
    ban_author: bool | None = None
    rating: Literal["like", "dislike", "none"] | None = None
    reason_id: str | None = None
    secondary_reason_id: str | None = None
    comments: str | None = Field(default=None, max_length=10000)
    language: str | None = Field(default=None, min_length=1, max_length=35)

    @model_validator(mode="before")
    @classmethod
    def exact_fields(cls, raw):
        if not isinstance(raw, dict) or not isinstance(raw.get("action"), str) or raw["action"] not in ACTIONS:
            raise ResourceError("unsupported action; spam/pin/heart/polls/cards/end-screens/related Shorts are not emulated")
        required, allowed = ACTIONS[raw["action"]]
        present = set(raw) - {"version", "action"}
        if required - present or present - allowed or any(raw[k] is None for k in present):
            raise ResourceError("missing/extra/null action fields")
        for key in present & {"video_id", "channel_id"}:
            resource_id(raw[key])
        for key in present & {"thread_id", "comment_id", "parent_id", "subscription_id", "reason_id", "secondary_reason_id"}:
            opaque_id(raw[key])
        if "text" in present and (not isinstance(raw["text"], str) or not raw["text"].strip()):
            raise ResourceError("supplied text is empty")
        return raw

    @model_validator(mode="after")
    def moderation(self):
        if self.action == "moderate":
            if len({t.comment_id for t in self.targets}) != len(self.targets):
                raise ResourceError("duplicate moderation IDs")
            if self.ban_author and self.moderation_status != "rejected":
                raise ResourceError("banAuthor accepted only with rejected")
        return self


def edit_raw(edit):
    return edit.model_dump(mode="python", exclude_none=True)


def validate_edit(edit):
    try:
        return CommunityEdit.model_validate(edit_raw(edit) if isinstance(edit, CommunityEdit) else edit)
    except ValidationError as exc:
        raise ResourceError(f"invalid community proposal: {exc}") from None


def load_edit(path):
    try:
        return validate_edit(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError):
        raise ResourceError("failed to read supplied YAML") from None
