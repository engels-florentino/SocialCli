"""Provider inventory reads with explicit field and ownership boundaries."""
from __future__ import annotations

from socialctl.identity import ReadError, remote_id
from socialctl.models import Platform

YOUTUBE_PARTS = "snippet,status,contentDetails,processingDetails,localizations"
FACEBOOK_FIELDS = "id,from,title,description,created_time,updated_time,status,published,permalink_url"
INSTAGRAM_FIELDS = "id,owner,caption,media_type,media_product_type,timestamp,permalink"


def cursor(value):
    if not isinstance(value, str) or not value or len(value) > 2048 or any(ord(c) < 32 for c in value):
        raise ReadError("invalid_cursor")
    return value


def read_page(probe, next_cursor):
    if probe.identity is None:
        raise ReadError("identity_unverified")
    if probe.platform is Platform.YOUTUBE:
        playlist = remote_id(probe.uploads_playlist)
        params = {"part": "contentDetails", "playlistId": playlist, "maxResults": "50"}
        if next_cursor:
            params["pageToken"] = cursor(next_cursor)
        data = probe.get("playlistItems", params)
        entries = data.get("items")
        if not isinstance(entries, list):
            raise ReadError("invalid_response")
        try:
            ids = [remote_id(row["contentDetails"]["videoId"]) for row in entries]
        except (KeyError, TypeError):
            raise ReadError("invalid_response") from None
        if len(ids) > 50:
            raise ReadError("invalid_response")
        rows = []
        if ids:
            videos = probe.get("videos", {"part": YOUTUBE_PARTS, "id": ",".join(ids), "maxResults": "50"})
            rows = videos.get("items")
            if not isinstance(rows, list) or any(not isinstance(row, dict) or row.get("id") not in ids for row in rows):
                raise ReadError("invalid_response")
        following = data.get("nextPageToken")
        missing = sorted(set(ids) - {row["id"] for row in rows})
        source = "youtube.uploads_playlist"
    else:
        facebook = probe.platform is Platform.FACEBOOK
        edge = "videos" if facebook else "media"
        params = {"fields": FACEBOOK_FIELDS if facebook else INSTAGRAM_FIELDS, "limit": "100"}
        if next_cursor:
            params["after"] = cursor(next_cursor)
        data = probe.get(f"{probe.account_id}/{edge}", params)
        rows = data.get("data")
        if not isinstance(rows, list) or len(rows) > 100 or any(not isinstance(row, dict) for row in rows):
            raise ReadError("invalid_response")
        paging = data.get("paging", {})
        if not isinstance(paging, dict):
            raise ReadError("invalid_response")
        cursors = paging.get("cursors", {})
        if not isinstance(cursors, dict):
            raise ReadError("invalid_response")
        for direction in ("before", "after"):
            if direction in cursors:
                cursor(cursors[direction])
        if "next" in paging:
            if not isinstance(paging["next"], str) or not paging["next"]:
                raise ReadError("invalid_response")
            following = cursor(cursors.get("after"))
        else:
            following = None
        missing, source = [], f"{probe.platform.value}.{edge}"
    # Never request provider-supplied paging URLs; retain only opaque cursors.
    if following is not None:
        following = cursor(following)
    items = [normalize(probe, row, source) for row in rows]
    return items, following, missing, source


def normalize(probe, row, source):
    identifier = remote_id(row.get("id"))
    for field in ("snippet", "contentDetails", "processingDetails", "from", "owner"):
        if row.get(field) is not None and not isinstance(row[field], dict):
            raise ReadError("invalid_response")
    snippet = row.get("snippet") or {}
    if probe.platform is Platform.YOUTUBE:
        owner = snippet.get("channelId")
    else:
        owner = (row.get("from" if probe.platform is Platform.FACEBOOK else "owner") or {}).get("id")
    if owner != probe.account_id:
        raise ReadError("content_owner_mismatch")
    details = row.get("contentDetails") or {}
    return {"version": 1, "brand": probe.brand.nombre, "platform": probe.platform.value,
            "account_id": probe.account_id, "remote_id": identifier, "source": source,
            "format": row.get("media_product_type") or row.get("media_type") or ("video" if probe.platform is not Platform.INSTAGRAM else None),
            "language": snippet.get("defaultLanguage"), "audio_language": snippet.get("defaultAudioLanguage"),
            "title": snippet.get("title", row.get("title")),
            "description": snippet.get("description", row.get("description", row.get("caption"))),
            "published_at": snippet.get("publishedAt", row.get("timestamp", row.get("created_time"))),
            "updated_at": row.get("updated_time"), "observed_status": row.get("status"),
            "published": row.get("published"), "processing": row.get("processingDetails"),
            "restrictions": details.get("regionRestriction"), "duration": details.get("duration"),
            "presence": "observed", "deletion_proven": False, "raw": row}
