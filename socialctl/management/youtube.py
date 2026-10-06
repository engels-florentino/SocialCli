"""Safe YouTube metadata inspection and editing via standard Data API endpoints; V1 edits snippet, V2 validates other parts."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from typing import Any

import httpx

from socialctl.adapters.errores import mensaje_de_error
from socialctl.auth import AuthError, obtener_token
from socialctl.brands import Brand
from socialctl.models import Platform

CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

WRITABLE_SNIPPET_FIELDS = frozenset(
    {
        "title",
        "description",
        "tags",
        "categoryId",
        "defaultLanguage",
        "defaultAudioLanguage",
    }
)
READ_ONLY_SNIPPET_FIELDS = frozenset(
    {
        "publishedAt",
        "channelId",
        "thumbnails",
        "channelTitle",
        "liveBroadcastContent",
        "localized",
    }
)


class YouTubeManagementError(RuntimeError):
    """Safe read or identity validation failure."""


class YouTubeUpdateRejected(YouTubeManagementError):
    """YouTube unequivocally rejected the write."""


class YouTubeUpdateConflict(YouTubeUpdateRejected):
    """ETag changed between reread and PUT (HTTP 412)."""


class YouTubeUpdateUncertain(YouTubeManagementError):
    """Whether PUT was applied cannot be determined reliably."""


@dataclass(frozen=True)
class InspectedVideo:
    video_id: str
    channel_id: str
    etag: str
    snippet: dict[str, Any]


class YouTubeManagementClient:
    """Inspect a video and update only its complete snippet."""

    def __init__(self, brand: Brand, client: httpx.Client) -> None:
        self.brand = brand
        self.client = client

    @property
    def configured_channel_id(self) -> str:
        account = self.brand.cuentas.get("youtube") or {}
        channel_id = account.get("channel_id") if isinstance(account, dict) else None
        if not isinstance(channel_id, str) or not channel_id.strip():
            raise YouTubeManagementError(
                f"youtube.channel_id missing in {self.brand.raiz / 'accounts.yml'}"
            )
        return channel_id

    def inspect(self, video_id: str) -> InspectedVideo:
        """Read video after verifying configured account, token and ownership."""
        if not isinstance(video_id, str) or not video_id.strip():
            raise YouTubeManagementError("video ID is empty")

        configured = self.configured_channel_id
        token = self._token()
        authenticated = self._authenticated_channel(token)
        if authenticated != configured:
            raise YouTubeManagementError(
                "authenticated channel does not match brand youtube.channel_id "
                f"{self.brand.nombre}: autenticado={authenticated}, configurado={configured}"
            )

        payload = self._get_json(
            VIDEOS_URL,
            token,
            params={"part": "snippet", "id": video_id},
            operation="read video",
        )
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items:
            raise YouTubeManagementError(
                f"YouTube did not return video {video_id}; check its ID and permissions"
            )
        item = items[0]
        if not isinstance(item, dict) or item.get("id") != video_id:
            raise YouTubeManagementError("YouTube returned an unexpected video resource")
        snippet = item.get("snippet")
        if not isinstance(snippet, dict):
            raise YouTubeManagementError("YouTube returned an invalid video snippet")

        channel_id = snippet.get("channelId")
        if channel_id != configured:
            raise YouTubeManagementError(
                f"video {video_id} does not belong to the configured channel for "
                f"{self.brand.nombre}"
            )

        unknown = set(snippet) - WRITABLE_SNIPPET_FIELDS - READ_ONLY_SNIPPET_FIELDS
        if unknown:
            names = ", ".join(sorted(unknown))
            raise YouTubeManagementError(
                "snippet contains an unknown field with uncertain mutability: "
                f"{names}; editing rejected to avoid dropping it"
            )

        etag = item.get("etag")
        if not isinstance(etag, str) or not etag:
            raise YouTubeManagementError(
                "YouTube did not return an ETag; editing rejected to avoid overwriting "
                "a concurrent change"
            )
        return InspectedVideo(
            video_id=video_id,
            channel_id=channel_id,
            etag=etag,
            snippet=dict(snippet),
        )

    def update_snippet(
        self, video_id: str, snippet: dict[str, Any], *, etag: str
    ) -> None:
        self._update_parts(video_id, {"snippet": snippet}, etag=etag)

    def _update_parts(
        self, video_id: str, parts: dict[str, Any], *, etag: str,
        before_put: Callable[[], None] | None = None,
    ) -> None:
        """Send one conditional PUT for caller-validated parts. Transport/5xx errors are uncertain; 412 conflicts and other 4xx rejections are definitive."""
        if not etag:
            raise YouTubeUpdateRejected("cannot update without ETag")
        configured = self.configured_channel_id
        token = self._token()
        authenticated = self._authenticated_channel(token)
        if authenticated != configured:
            raise YouTubeUpdateRejected(
                "authenticated channel changed before videos.update; PUT was not sent"
            )
        headers = {
            "Authorization": f"Bearer {token}",
            "If-Match": etag,
            "Content-Type": "application/json",
        }
        body = {"id": video_id, **parts}
        # Time-sensitive outgoing metadata must be checked after token refresh
        # and final authenticated-channel lookup, immediately before the PUT.
        if before_put is not None:
            before_put()
        try:
            response = self.client.put(
                VIDEOS_URL,
                headers=headers,
                params={"part": ",".join(sorted(parts))},
                json=body,
            )
        except httpx.TimeoutException:
            raise YouTubeUpdateUncertain(
                "uncertain outcome: videos.update timed out"
            ) from None
        except (httpx.HTTPError, httpx.InvalidURL):
            raise YouTubeUpdateUncertain(
                "uncertain outcome: connection lost during videos.update"
            ) from None
        except Exception:
            raise YouTubeUpdateUncertain(
                "uncertain outcome: unexpected failure during videos.update"
            ) from None

        error = mensaje_de_error(response, token)
        if response.status_code == 412:
            raise YouTubeUpdateConflict(
                "ETag conflict (HTTP 412 conditionNotMet); PUT was not retried"
            )
        if 500 <= response.status_code:
            raise YouTubeUpdateUncertain(
                f"uncertain outcome after videos.update: {error}"
            )
        if not response.is_success:
            if response.status_code == 403:
                raise YouTubeUpdateRejected(
                    "YouTube rejected videos.update; reauthorize with management permission "
                    f"youtube.force-ssl or youtube. Details: {error}"
                )
            raise YouTubeUpdateRejected(
                f"YouTube rejected videos.update without applying the change: {error}"
            )

        try:
            payload = response.json()
        except Exception:
            raise YouTubeUpdateUncertain(
                "uncertain outcome: videos.update returned unverifiable JSON"
            ) from None
        if not isinstance(payload, dict) or payload.get("id") != video_id:
            raise YouTubeUpdateUncertain(
                "uncertain outcome: videos.update returned an unexpected resource"
            )

    def editable_snippet(self, inspected: InspectedVideo) -> dict[str, Any]:
        """Extract all present writable fields without inventing absent values."""
        return {
            key: inspected.snippet[key]
            for key in WRITABLE_SNIPPET_FIELDS
            if key in inspected.snippet
        }

    def _token(self) -> str:
        try:
            return obtener_token(self.brand, Platform.YOUTUBE, self.client)
        except AuthError as exc:
            raise YouTubeManagementError(str(exc)) from None
        except Exception:
            # Un fallo imprevisto podría arrastrar el secreto en su texto.
            raise YouTubeManagementError(
                "failed to read or refresh YouTube credentials"
            ) from None

    def _authenticated_channel(self, token: str) -> str:
        payload = self._get_json(
            CHANNELS_URL,
            token,
            params={"part": "id", "mine": "true"},
            operation="check authenticated channel",
        )
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            raise YouTubeManagementError(
                "Data API returned no channel for the brand token"
            )
        channel_id = items[0].get("id")
        if not isinstance(channel_id, str) or not channel_id:
            raise YouTubeManagementError("Data API returned an invalid authenticated channel")
        return channel_id

    def _get_json(
        self, url: str, token: str, *, params: dict[str, str], operation: str
    ) -> dict[str, Any]:
        try:
            response = self.client.get(
                url, headers={"Authorization": f"Bearer {token}"}, params=params
            )
        except httpx.TimeoutException:
            raise YouTubeManagementError(
                f"timed out while attempting to {operation} on YouTube"
            ) from None
        except (httpx.HTTPError, httpx.InvalidURL):
            raise YouTubeManagementError(
                f"failed to connect to YouTube to {operation}"
            ) from None
        except Exception:
            raise YouTubeManagementError(
                f"unexpected failure during {operation} on YouTube"
            ) from None
        if not response.is_success:
            detail = mensaje_de_error(response, token)
            if response.status_code == 403:
                raise YouTubeManagementError(
                    f"YouTube did not allow {operation}; check youtube.readonly. "
                    f"Detalle: {detail}"
                )
            raise YouTubeManagementError(
                f"YouTube returned {response.status_code} during {operation}: {detail}"
            )
        try:
            payload = response.json()
        except Exception:
            raise YouTubeManagementError(
                f"YouTube returned a non-JSON response during {operation}"
            ) from None
        if not isinstance(payload, dict):
            raise YouTubeManagementError(
                f"YouTube returned an invalid response during {operation}"
            )
        return payload
