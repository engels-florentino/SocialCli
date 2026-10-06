"""Resource-specific proposal contract and preserving field scopes for Task 9.

No URL, HTTP verb, endpoint or arbitrary API payload is a proposal input.
"""
from __future__ import annotations

import copy
import re
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from socialctl.management.youtube_resources import ResourceError, resource_id

ACTIONS = {
    "playlist-create": ({"patch"}, {"patch"}),
    "playlist-update": ({"playlist_id", "patch"}, {"playlist_id", "patch"}),
    "playlist-delete": ({"playlist_id"}, {"playlist_id"}),
    "item-insert": ({"playlist_id", "video_id", "position"}, {"playlist_id", "video_id", "position"}),
    "item-update": ({"playlist_id", "item_id", "patch"}, {"playlist_id", "item_id", "patch"}),
    "item-delete": ({"playlist_id", "item_id"}, {"playlist_id", "item_id"}),
    "items-reorder": ({"playlist_id", "positions"}, {"playlist_id", "positions"}),
    "section-create": ({"patch"}, {"patch"}),
    "section-update": ({"section_id", "patch"}, {"section_id", "patch"}),
    "section-delete": ({"section_id"}, {"section_id"}),
    "channel-update": ({"channel_id", "patch"}, {"channel_id", "patch"}),
    "channel-audience": ({"channel_id", "audience"}, {"channel_id", "audience"}),
    "banner-upload": ({"channel_id", "file"}, {"channel_id", "file"}),
    "banner-apply": ({"channel_id", "upload_change_id"}, {"channel_id", "upload_change_id"}),
    "watermark-set": ({"channel_id", "target_channel_id", "file", "timing"}, {"channel_id", "target_channel_id", "file", "timing"}),
    "watermark-unset": ({"channel_id"}, {"channel_id"}),
    "image-insert": ({"playlist_id", "file", "image_type"}, {"playlist_id", "file", "image_type"}),
    "image-update": ({"playlist_id", "image_id"}, {"playlist_id", "image_id", "file", "patch"}),
    "image-delete": ({"playlist_id", "image_id"}, {"playlist_id", "image_id"}),
    "video-recording-date": ({"video_id", "recording_date"}, {"video_id", "recording_date"}),
    "video-delete": ({"video_id"}, {"video_id"}),
}


def image_id(value):
    # Discovery identifies a composite playlist/type ID. It is opaque and is
    # always passed in query/JSON, never interpolated into a URL path.
    if (not isinstance(value, str) or not 0 < len(value) <= 512 or "://" in value or "@" in value
        or any(not char.isprintable() or char.isspace() for char in value)):
        raise ResourceError("invalid image ID")
    return value


def section_id(value):
    # ChannelSection.id is opaque (observed channel-id.section-id composites).
    # A comma would turn exact-ID list lookup into a multi-ID filter. Separators
    # otherwise remain encoded query/JSON data, never URL path interpolation.
    if (not isinstance(value, str) or not 0 < len(value) <= 512 or "://" in value
        or "@" in value or "," in value
        or any(not char.isprintable() or char.isspace() for char in value)):
        raise ResourceError("invalid section ID")
    return value


def owned_resource_id(resource, value):
    validator = {"channelSections": section_id, "playlistImages": image_id}.get(resource, resource_id)
    return validator(value)


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    item_id: str
    position: int = Field(ge=0, le=4999)


class OwnedEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1] = 1
    action: str
    playlist_id: str | None = None
    item_id: str | None = None
    video_id: str | None = None
    section_id: str | None = None
    channel_id: str | None = None
    target_channel_id: str | None = None
    image_id: str | None = None
    image_type: Literal["hero"] | None = None
    file: str | None = None
    upload_change_id: str | None = None
    patch: dict | None = None
    position: int | None = Field(default=None, ge=0, le=4999)
    positions: list[Position] | None = Field(default=None, min_length=1, max_length=5000)
    audience: bool | None = None
    recording_date: str | None = None
    timing: dict | None = None

    @model_validator(mode="before")
    @classmethod
    def exact_action_fields(cls, value):
        if not isinstance(value, dict) or not isinstance(value.get("action"), str) or value["action"] not in ACTIONS:
            raise ResourceError("unsupported owned-resource action")
        required, allowed = ACTIONS[value["action"]]
        present = set(value) - {"action", "version"}
        if required - present or present - allowed or any(value[k] is None for k in present):
            raise ResourceError("missing/extra/null action fields; omission preserves values, null is not accepted")
        for key in present & {"playlist_id", "item_id", "video_id", "channel_id", "target_channel_id"}:
            resource_id(value[key])
        if "section_id" in present:
            section_id(value["section_id"])
        if "image_id" in present:
            image_id(value["image_id"])
        if value["action"] == "image-update" and not (present & {"file", "patch"}):
            raise ResourceError("image-update requires metadata patch and/or supplied file")
        if "file" in present and (not isinstance(value["file"], str) or not value["file"]):
            raise ResourceError("invalid supplied file")
        if "patch" in present and (not isinstance(value["patch"], dict) or not value["patch"]):
            raise ResourceError("empty or invalid patch")
        return value


def edit_raw(edit):
    return edit.model_dump(mode="python", exclude_none=True)


def validate_edit(edit):
    try:
        return OwnedEdit.model_validate(edit_raw(edit) if isinstance(edit, OwnedEdit) else edit)
    except ValidationError as exc:
        raise ResourceError(f"invalid proposal: {exc}") from None


def target(edit):
    if edit.action.startswith("playlist-"):
        return "playlists", edit.playlist_id
    if edit.action.startswith(("item-", "items-")):
        return "playlistItems", edit.item_id
    if edit.action.startswith("section-"):
        return "channelSections", edit.section_id
    if edit.action.startswith("image-"):
        return "playlistImages", edit.image_id
    if edit.action.startswith("video-"):
        return "videos", edit.video_id
    return "channels", edit.channel_id


def load_owned_edit(path):
    try:
        return validate_edit(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError):
        raise ResourceError("failed to read proposal YAML") from None


def mapping(value, allowed, label):
    if not isinstance(value, dict) or set(value) - set(allowed) or any(v is None for v in value.values()):
        raise ResourceError(f"{label}: unknown/noneditable fields or null")
    return value


def text_fields(value, fields):
    for key in set(value) & set(fields):
        if not isinstance(value[key], str):
            raise ResourceError(f"{key} must be text")


def localizations(value):
    if not isinstance(value, dict):
        raise ResourceError("localizations must be a mapping")
    for lang, localized in value.items():
        if not isinstance(lang, str) or not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", lang):
            raise ResourceError("invalid localizations language")
        mapping(localized, {"title", "description"}, "localizations")
        text_fields(localized, {"title", "description"})
    return value


def merge(before, patch):
    after = copy.deepcopy(before)
    for key, value in patch.items():
        after[key] = merge(after.get(key, {}), value) if isinstance(value, dict) else copy.deepcopy(value)
    return after


PLAYLIST_SNIPPET = {"title", "description", "defaultLanguage"}
PLAYLIST_READ = {"publishedAt", "channelId", "channelTitle", "thumbnails", "localized"}
ITEM_SNIPPET = {"playlistId", "resourceId", "position"}
ITEM_READ = {"publishedAt", "channelId", "title", "description", "thumbnails", "channelTitle", "videoOwnerChannelTitle", "videoOwnerChannelId"}
CHANNEL_FIELDS = {"country", "description", "defaultLanguage", "keywords", "trackingAnalyticsAccountId", "unsubscribedTrailer"}
CHANNEL_DEPRECATED = {"profileColor", "showBrowseView", "moderateComments", "featuredChannelsTitle", "featuredChannelsUrls", "defaultTab", "showRelatedChannels"}
IMAGE_DEPRECATED = {"backgroundImageUrl", "bannerImageUrl", "bannerMobileImageUrl", "bannerTabletLowImageUrl", "bannerTabletImageUrl", "bannerTabletHdImageUrl", "bannerTabletExtraHdImageUrl", "bannerMobileLowImageUrl", "bannerMobileMediumHdImageUrl", "bannerMobileHdImageUrl", "bannerMobileExtraHdImageUrl", "bannerTvImageUrl", "bannerTvLowImageUrl", "bannerTvMediumImageUrl", "bannerTvHighImageUrl", "watchIconImageUrl", "trackingImageUrl", "largeBrandedBannerImageUrl", "largeBrandedBannerImageImapScript", "smallBrandedBannerImageUrl", "smallBrandedBannerImageImapScript"}


def editable(resource, part, row):
    raw = row.get(part, {})
    if part == "localizations":
        return copy.deepcopy(localizations(raw))
    if resource == "channels" and part == "brandingSettings":
        mapping(raw, {"channel", "image", "watch", "hints"}, "brandingSettings")
        channel = mapping(raw.get("channel", {}), CHANNEL_FIELDS | CHANNEL_DEPRECATED | {"title"}, "brandingSettings.channel")
        image = mapping(raw.get("image", {}), {"bannerExternalUrl"} | IMAGE_DEPRECATED, "brandingSettings.image")
        result = {}
        if "channel" in raw:
            result["channel"] = {k: v for k, v in channel.items() if k in CHANNEL_FIELDS or k == "title"}
            text_fields(result["channel"], result["channel"])
        if "bannerExternalUrl" in image:
            result["image"] = {"bannerExternalUrl": image["bannerExternalUrl"]}
        return result
    scopes = {
        ("playlists", "snippet"): (PLAYLIST_SNIPPET, PLAYLIST_READ),
        ("playlists", "status"): ({"privacyStatus", "podcastStatus"}, set()),
        ("playlistItems", "snippet"): (ITEM_SNIPPET, ITEM_READ),
        ("playlistItems", "contentDetails"): ({"note", "startAt", "endAt"}, {"videoId", "videoPublishedAt"}),
        ("channelSections", "snippet"): ({"type", "title", "position"}, {"channelId"}),
        ("channelSections", "contentDetails"): ({"playlists", "channels"}, set()),
        ("channels", "status"): ({"selfDeclaredMadeForKids"}, {"privacyStatus", "isLinked", "longUploadsStatus", "madeForKids"}),
        ("playlistImages", "snippet"): ({"playlistId", "type", "width", "height"}, set()),
        # Deprecated location fields are not silently dropped from a PUT. A
        # legacy nonempty recordingDetails part needs separate evidence.
        ("videos", "recordingDetails"): ({"recordingDate"}, set()),
    }
    if (resource, part) not in scopes:
        raise ResourceError("unsupported part")
    writable, readonly = scopes[resource, part]
    mapping(raw, writable | readonly, f"{resource}.{part}")
    return copy.deepcopy({k: v for k, v in raw.items() if k in writable})


def section_type(value):
    types = {"allPlaylists", "completedEvents", "liveEvents", "multipleChannels", "multiplePlaylists", "popularUploads", "recentUploads", "singlePlaylist", "subscriptions", "upcomingEvents"}
    if not isinstance(value, str) or value not in types:
        raise ResourceError("unsupported section type")
    return value


def section_valid(body, account):
    snip = body["snippet"]
    section_type(snip.get("type"))
    details = body.get("contentDetails", {})
    for key, ids in details.items():
        if not isinstance(ids, list) or not ids or any(not isinstance(rid, str) for rid in ids) or len(ids) != len(set(ids)):
            raise ResourceError("empty/duplicate/invalid section IDs")
        for rid in ids:
            resource_id(rid)
        if key == "channels" and account in ids:
            raise ResourceError("section cannot include own channel")
    typ = snip["type"]
    if typ in {"singlePlaylist", "multiplePlaylists"}:
        if not details.get("playlists") or "channels" in details or (typ == "singlePlaylist" and len(details["playlists"]) != 1):
            raise ResourceError("invalid content for playlist section")
    elif typ == "multipleChannels":
        if not details.get("channels") or "playlists" in details:
            raise ResourceError("section requires channels only")
    elif details:
        raise ResourceError("this section type does not accept contentDetails")
    if typ in {"multiplePlaylists", "multipleChannels"}:
        title = snip.get("title")
        if not isinstance(title, str) or not title.strip() or len(title) > 100 or "<" in title or ">" in title:
            raise ResourceError("section title required/invalid")
    elif "title" in snip:
        raise ResourceError("this section type ignores title; no no-op proposed")


def build_after(edit, resource, before, account):
    patch = copy.deepcopy(edit.patch or {})
    action = edit.action
    if action == "channel-audience":
        return {"status": {**editable("channels", "status", before), "selfDeclaredMadeForKids": edit.audience}}
    if action == "video-recording-date":
        value = edit.recording_date
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value):
                raise ValueError()
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            raise ResourceError("recordingDate requires ISO 8601 timestamp with explicit timezone") from None
        return {"recordingDetails": {**editable("videos", "recordingDetails", before), "recordingDate": value}}
    if action == "item-insert":
        return {"snippet": {"playlistId": edit.playlist_id, "resourceId": {"kind": "youtube#video", "videoId": edit.video_id}, "position": edit.position}}
    if action == "image-insert":
        return {"snippet": {"playlistId": edit.playlist_id, "type": edit.image_type}}
    allowed = {
        "playlists": {"snippet": PLAYLIST_SNIPPET, "status": {"privacyStatus", "podcastStatus"}, "localizations": None},
        "playlistItems": {"snippet": {"position"}, "contentDetails": {"note"}},
        "channelSections": {"snippet": {"type", "title", "position"}, "contentDetails": {"playlists", "channels"}},
        "channels": {"brandingSettings": {"channel"}, "localizations": None},
        "playlistImages": {"snippet": {"width", "height"}},
    }[resource]
    if action == "section-update":
        # Read-only inspection keeps new/unknown remote enums, but a PUT must
        # not reinterpret their semantics even if patch replaces the type.
        section_type(before.get("snippet", {}).get("type"))
    mapping(patch, allowed, "patch")
    for part, value in patch.items():
        if part == "localizations":
            localizations(value)
        else:
            mapping(value, allowed[part], part)
        if part == "brandingSettings":
            for key, val in value.items():
                mapping(val, CHANNEL_FIELDS, "brandingSettings.channel")
                text_fields(val, CHANNEL_FIELDS)
    if resource == "channels" and len(patch) != 1:
        raise ResourceError("channels.update accepts exactly one part")
    parts = set(patch)
    if resource in {"playlists", "playlistItems", "channelSections", "playlistImages"}:
        parts.add("snippet")
    if resource == "channelSections":
        parts.add("contentDetails")
    after = {part: merge(editable(resource, part, before), patch.get(part, {})) for part in sorted(parts)}
    if resource == "channels":
        channel = after.get("brandingSettings", {}).get("channel", {})
        for field, limit in {"keywords": 500, "description": 1000}.items():
            if len(channel.get(field, "")) > limit:
                raise ResourceError(f"brandingSettings.channel.{field} exceeds documented maximum of {limit} characters")
    for part, value in after.items():
        text_fields(value, {"title", "description", "defaultLanguage", "note", "startAt", "endAt"})
        if "position" in value and (type(value["position"]) is not int or value["position"] < 0 or value["position"] > 4999):
            raise ResourceError("position requires a valid zero-based integer")
    if resource == "playlists":
        snip = after["snippet"]
        if not snip.get("title", "").strip():
            raise ResourceError("invalid playlist title/description")
        status = after.get("status", {})
        text_fields(status, {"privacyStatus", "podcastStatus"})
        if "privacyStatus" in status and status["privacyStatus"] not in {"public", "unlisted", "private"}:
            raise ResourceError("invalid privacyStatus")
        if "podcastStatus" in status and status["podcastStatus"] not in {"enabled", "disabled", "unspecified"}:
            raise ResourceError("invalid podcastStatus")
        if action == "playlist-create" and ("description" not in snip or "privacyStatus" not in status or "podcastStatus" in status):
            raise ResourceError("playlist creation requires explicit title/description/privacy; podcastStatus is update-only")
        if after.get("localizations") and not snip.get("defaultLanguage"):
            raise ResourceError("localizations requires defaultLanguage")
    elif resource == "channelSections":
        section_valid(after, account)
    elif resource == "playlistItems" and len(after.get("contentDetails", {}).get("note", "")) > 280:
        raise ResourceError("note exceeds documented maximum of 280 characters")
    elif resource == "playlistImages":
        for key in {"width", "height"} & set(after["snippet"]):
            if type(after["snippet"][key]) is not int or not 0 < after["snippet"][key] <= 2147483647:
                raise ResourceError("image dimensions must be positive integers")
    elif resource == "channels" and after.get("localizations"):
        settings = before.get("brandingSettings", {}).get("channel", {})
        if not settings.get("defaultLanguage") and not before.get("snippet", {}).get("defaultLanguage"):
            raise ResourceError("channel localizations requires existing defaultLanguage")
    return after
