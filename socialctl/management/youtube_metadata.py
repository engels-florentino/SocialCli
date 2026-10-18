"""Owned video metadata parts over documented Data API v3 GET/PUT endpoints."""
from __future__ import annotations

from dataclasses import dataclass
import copy
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from typing import Any

from socialctl.management.changes import ChangeError, _validate_snippet
from socialctl.management.metadata_parts import STATUS_FIELDS, STATUS_READ_ONLY, editable_parts, validate_localizations, validate_status
from socialctl.management.youtube import (READ_ONLY_SNIPPET_FIELDS, VIDEOS_URL,
    WRITABLE_SNIPPET_FIELDS, YouTubeManagementClient, YouTubeManagementError, YouTubeUpdateConflict, YouTubeUpdateRejected)


@dataclass(frozen=True)
class MetadataVideo:
    video_id: str
    channel_id: str
    etag: str
    resource: dict[str, Any]


def same_metadata_snapshot(before, after):
    """Ignore only renewed ytimg signature parameters, retaining all other state."""
    if before == after:
        return True
    left, right = copy.deepcopy(before), copy.deepcopy(after)
    changed = False
    for value in (left, right):
        thumbnails = value.get('snippet', {}).get('thumbnails', {})
        if not isinstance(thumbnails, dict):
            return False
        for thumbnail in thumbnails.values():
            if not isinstance(thumbnail, dict) or not isinstance(thumbnail.get('url'), str):
                return False
            parts = urlsplit(thumbnail['url'])
            if parts.scheme != 'https' or not re.fullmatch(r'i\d+\.ytimg\.com', parts.netloc):
                continue
            query = parse_qsl(parts.query, keep_blank_values=True)
            if {'sqp', 'rs'} <= {k for k, _ in query}:
                thumbnail['url'] = urlunsplit(parts._replace(query=urlencode([(k,v) for k,v in query if k not in {'sqp','rs'}])))
    changed = before.get('snippet', {}).get('thumbnails') != after.get('snippet', {}).get('thumbnails')
    if not changed:
        return False  # An unexplained ETag-only change remains a conflict.
    left.pop('etag', None)
    right.pop('etag', None)
    return left == right


class YouTubeMetadataClient(YouTubeManagementClient):
    def inspect(self, video_id: str) -> MetadataVideo:
        configured = self.configured_channel_id
        token = self._token()
        if self._authenticated_channel(token) != configured:
            raise YouTubeManagementError("authenticated channel does not match brand")
        payload = self._get_json(VIDEOS_URL, token,
            params={"part": "snippet,status,localizations,contentDetails", "id": video_id}, operation="read metadata")
        items = payload.get("items")
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise YouTubeManagementError("YouTube did not return a unique verifiable video")
        resource = items[0]
        if resource.get("id") != video_id or not isinstance(resource.get("etag"), str) or not resource["etag"]:
            raise YouTubeManagementError("unexpected video or ETag")
        snippet, status = resource.get("snippet"), resource.get("status")
        if not isinstance(snippet, dict) or not isinstance(status, dict):
            raise YouTubeManagementError("snippet/status missing or invalid")
        if snippet.get("channelId") != configured:
            raise YouTubeManagementError("video does not belong to configured channel")
        if set(snippet) - WRITABLE_SNIPPET_FIELDS - READ_ONLY_SNIPPET_FIELDS:
            raise YouTubeManagementError("snippet contains unknown field; editing blocked")
        if set(status) - STATUS_FIELDS - STATUS_READ_ONLY:
            raise YouTubeManagementError("status contains unknown field; editing blocked")
        validate_localizations(resource.get("localizations", {}))
        validate_status({key: value for key, value in status.items() if key in STATUS_FIELDS})
        return MetadataVideo(video_id, configured, resource["etag"], resource)

    def update_parts(self, video_id: str, parts: dict[str, Any], *, etag: str) -> None:
        if not parts or set(parts) - {"snippet", "status", "localizations"}:
            raise YouTubeUpdateRejected("unknown mutable part")
        current = self.inspect(video_id)
        if current.etag != etag:
            raise YouTubeUpdateConflict("ETag changed before PUT; no write performed")
        current_parts = editable_parts(current.resource)
        try:
            for part, value in parts.items():
                if not isinstance(value, dict):
                    raise ChangeError("invalid part")
                if set(current_parts[part]) - set(value):
                    raise ChangeError("present mutable fields cannot be omitted; complete part required")
                if part == "snippet":
                    if set(value) - WRITABLE_SNIPPET_FIELDS:
                        raise ChangeError("snippet contains unknown or read-only field")
                    _validate_snippet(value)
                    current_audio = current.resource["snippet"].get("defaultAudioLanguage")
                    if value.get("defaultAudioLanguage") != current_audio:
                        raise ChangeError("defaultAudioLanguage is preserved only; editing unverified")
                elif part == "status":
                    validate_status(value, outgoing=True)
                else:
                    validate_localizations(value)
        except ChangeError as exc:
            raise YouTubeUpdateRejected(str(exc)) from None
        def validate_before_put():
            try:
                if "status" in parts:
                    validate_status(parts["status"], outgoing=True)
            except ChangeError as exc:
                raise YouTubeUpdateRejected(str(exc)) from None

        self._update_parts(video_id, parts, etag=etag, before_put=validate_before_put)
