"""Cliente seguro para inspeccionar y editar metadatos de YouTube.

Este módulo usa exclusivamente el endpoint normal de Data API. V1 expone solo
snippet; el cliente V2 valida otras partes antes de usar el transporte común.
"""

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
    """Fallo seguro de lectura o validación de identidad."""


class YouTubeUpdateRejected(YouTubeManagementError):
    """YouTube rechazó de forma inequívoca la escritura."""


class YouTubeUpdateConflict(YouTubeUpdateRejected):
    """El ETag cambió entre la relectura y el PUT (HTTP 412)."""


class YouTubeUpdateUncertain(YouTubeManagementError):
    """No se puede saber con seguridad si el PUT llegó a aplicarse."""


@dataclass(frozen=True)
class InspectedVideo:
    video_id: str
    channel_id: str
    etag: str
    snippet: dict[str, Any]


class YouTubeManagementClient:
    """Inspecciona un vídeo y actualiza solo su ``snippet`` completo."""

    def __init__(self, brand: Brand, client: httpx.Client) -> None:
        self.brand = brand
        self.client = client

    @property
    def configured_channel_id(self) -> str:
        account = self.brand.cuentas.get("youtube") or {}
        channel_id = account.get("channel_id") if isinstance(account, dict) else None
        if not isinstance(channel_id, str) or not channel_id.strip():
            raise YouTubeManagementError(
                f"falta youtube.channel_id en {self.brand.raiz / 'accounts.yml'}"
            )
        return channel_id

    def inspect(self, video_id: str) -> InspectedVideo:
        """Lee un vídeo tras verificar cuenta configurada, token y propiedad."""
        if not isinstance(video_id, str) or not video_id.strip():
            raise YouTubeManagementError("el id de vídeo está vacío")

        configured = self.configured_channel_id
        token = self._token()
        authenticated = self._authenticated_channel(token)
        if authenticated != configured:
            raise YouTubeManagementError(
                "el canal autenticado no coincide con youtube.channel_id de la marca "
                f"{self.brand.nombre}: autenticado={authenticated}, configurado={configured}"
            )

        payload = self._get_json(
            VIDEOS_URL,
            token,
            params={"part": "snippet", "id": video_id},
            operation="leer el vídeo",
        )
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items:
            raise YouTubeManagementError(
                f"YouTube no devolvió el vídeo {video_id}; comprueba el id y sus permisos"
            )
        item = items[0]
        if not isinstance(item, dict) or item.get("id") != video_id:
            raise YouTubeManagementError("YouTube devolvió un recurso de vídeo inesperado")
        snippet = item.get("snippet")
        if not isinstance(snippet, dict):
            raise YouTubeManagementError("YouTube devolvió un snippet de vídeo inválido")

        channel_id = snippet.get("channelId")
        if channel_id != configured:
            raise YouTubeManagementError(
                f"el vídeo {video_id} no pertenece al canal configurado de "
                f"{self.brand.nombre}"
            )

        unknown = set(snippet) - WRITABLE_SNIPPET_FIELDS - READ_ONLY_SNIPPET_FIELDS
        if unknown:
            names = ", ".join(sorted(unknown))
            raise YouTubeManagementError(
                "el snippet contiene un campo desconocido cuya mutabilidad no es segura: "
                f"{names}; se rechaza la edición para no descartarlo"
            )

        etag = item.get("etag")
        if not isinstance(etag, str) or not etag:
            raise YouTubeManagementError(
                "YouTube no devolvió un ETag; la edición se rechaza para no sobrescribir "
                "un cambio concurrente"
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
        """Hace un único PUT condicional de las partes validadas por el llamador.

        Todo fallo de transporte o 5xx queda como resultado incierto. Un 412
        es conflicto definitivo; otros 4xx son rechazos definitivos.
        """
        if not etag:
            raise YouTubeUpdateRejected("no se puede actualizar sin ETag")
        configured = self.configured_channel_id
        token = self._token()
        authenticated = self._authenticated_channel(token)
        if authenticated != configured:
            raise YouTubeUpdateRejected(
                "el canal autenticado cambió antes de videos.update; no se envió el PUT"
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
                "resultado incierto: se agotó el tiempo durante videos.update"
            ) from None
        except (httpx.HTTPError, httpx.InvalidURL):
            raise YouTubeUpdateUncertain(
                "resultado incierto: se perdió la conexión durante videos.update"
            ) from None
        except Exception:
            raise YouTubeUpdateUncertain(
                "resultado incierto: fallo inesperado durante videos.update"
            ) from None

        error = mensaje_de_error(response, token)
        if response.status_code == 412:
            raise YouTubeUpdateConflict(
                "conflicto de ETag (HTTP 412 conditionNotMet); no se reintentó el PUT"
            )
        if 500 <= response.status_code:
            raise YouTubeUpdateUncertain(
                f"resultado incierto tras videos.update: {error}"
            )
        if not response.is_success:
            if response.status_code == 403:
                raise YouTubeUpdateRejected(
                    "YouTube rechazó videos.update; reautoriza con el permiso de gestión "
                    f"youtube.force-ssl o youtube. Detalle: {error}"
                )
            raise YouTubeUpdateRejected(
                f"YouTube rechazó videos.update sin aplicar el cambio: {error}"
            )

        try:
            payload = response.json()
        except Exception:
            raise YouTubeUpdateUncertain(
                "resultado incierto: videos.update respondió sin JSON verificable"
            ) from None
        if not isinstance(payload, dict) or payload.get("id") != video_id:
            raise YouTubeUpdateUncertain(
                "resultado incierto: videos.update devolvió un recurso inesperado"
            )

    def editable_snippet(self, inspected: InspectedVideo) -> dict[str, Any]:
        """Extrae todos los campos escribibles presentes, sin inventar ausentes."""
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
                "no se pudieron leer o refrescar las credenciales de YouTube"
            ) from None

    def _authenticated_channel(self, token: str) -> str:
        payload = self._get_json(
            CHANNELS_URL,
            token,
            params={"part": "id", "mine": "true"},
            operation="comprobar el canal autenticado",
        )
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            raise YouTubeManagementError(
                "la Data API no devolvió ningún canal para el token de la marca"
            )
        channel_id = items[0].get("id")
        if not isinstance(channel_id, str) or not channel_id:
            raise YouTubeManagementError("la Data API devolvió un canal autenticado inválido")
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
                f"se agotó el tiempo al {operation} en YouTube"
            ) from None
        except (httpx.HTTPError, httpx.InvalidURL):
            raise YouTubeManagementError(
                f"no se pudo conectar con YouTube para {operation}"
            ) from None
        except Exception:
            raise YouTubeManagementError(
                f"fallo inesperado al {operation} en YouTube"
            ) from None
        if not response.is_success:
            detail = mensaje_de_error(response, token)
            if response.status_code == 403:
                raise YouTubeManagementError(
                    f"YouTube no permitió {operation}; comprueba youtube.readonly. "
                    f"Detalle: {detail}"
                )
            raise YouTubeManagementError(
                f"YouTube respondió {response.status_code} al {operation}: {detail}"
            )
        try:
            payload = response.json()
        except Exception:
            raise YouTubeManagementError(
                f"YouTube devolvió una respuesta no JSON al {operation}"
            ) from None
        if not isinstance(payload, dict):
            raise YouTubeManagementError(
                f"YouTube devolvió una respuesta inválida al {operation}"
            )
        return payload
