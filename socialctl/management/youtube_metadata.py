"""Owned video metadata parts over documented Data API v3 GET/PUT endpoints."""
from __future__ import annotations

from dataclasses import dataclass
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


class YouTubeMetadataClient(YouTubeManagementClient):
    def inspect(self, video_id: str) -> MetadataVideo:
        configured = self.configured_channel_id
        token = self._token()
        if self._authenticated_channel(token) != configured:
            raise YouTubeManagementError("el canal autenticado no coincide con la marca")
        payload = self._get_json(VIDEOS_URL, token,
            params={"part": "snippet,status,localizations,contentDetails", "id": video_id}, operation="leer metadatos")
        items = payload.get("items")
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise YouTubeManagementError("YouTube no devolvió un vídeo único verificable")
        resource = items[0]
        if resource.get("id") != video_id or not isinstance(resource.get("etag"), str) or not resource["etag"]:
            raise YouTubeManagementError("vídeo o ETag inesperado")
        snippet, status = resource.get("snippet"), resource.get("status")
        if not isinstance(snippet, dict) or not isinstance(status, dict):
            raise YouTubeManagementError("snippet/status ausente o inválido")
        if snippet.get("channelId") != configured:
            raise YouTubeManagementError("el vídeo no pertenece al canal configurado")
        if set(snippet) - WRITABLE_SNIPPET_FIELDS - READ_ONLY_SNIPPET_FIELDS:
            raise YouTubeManagementError("snippet contiene campo desconocido; edición bloqueada")
        if set(status) - STATUS_FIELDS - STATUS_READ_ONLY:
            raise YouTubeManagementError("status contiene campo desconocido; edición bloqueada")
        validate_localizations(resource.get("localizations", {}))
        validate_status({key: value for key, value in status.items() if key in STATUS_FIELDS})
        return MetadataVideo(video_id, configured, resource["etag"], resource)

    def update_parts(self, video_id: str, parts: dict[str, Any], *, etag: str) -> None:
        if not parts or set(parts) - {"snippet", "status", "localizations"}:
            raise YouTubeUpdateRejected("parte mutable desconocida")
        current = self.inspect(video_id)
        if current.etag != etag:
            raise YouTubeUpdateConflict("ETag cambió antes del PUT; no se escribió")
        current_parts = editable_parts(current.resource)
        try:
            for part, value in parts.items():
                if not isinstance(value, dict):
                    raise ChangeError("parte inválida")
                if set(current_parts[part]) - set(value):
                    raise ChangeError("no se pueden omitir campos mutables presentes; se requiere parte completa")
                if part == "snippet":
                    if set(value) - WRITABLE_SNIPPET_FIELDS:
                        raise ChangeError("snippet contiene campo desconocido o de solo lectura")
                    _validate_snippet(value)
                    current_audio = current.resource["snippet"].get("defaultAudioLanguage")
                    if value.get("defaultAudioLanguage") != current_audio:
                        raise ChangeError("defaultAudioLanguage solo se preserva; edición no verificada")
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
