"""Fixed YouTube owned-resource reads and single effects; never retries a write."""
from __future__ import annotations

import json
import uuid

from socialctl.management.owned_schema import image_id, owned_resource_id, resource_id, target, validate_edit
from socialctl.management.youtube_resources import API, UPLOAD, YouTubeResourcesClient, ResourceError, ResourceUncertain

VIDEO_PARTS = {"snippet", "status", "localizations", "contentDetails", "processingDetails", "statistics", "recordingDetails", "fileDetails", "suggestions", "player", "topicDetails", "liveStreamingDetails", "paidProductPlacementDetails"}
CHANNEL_PARTS = {"snippet", "brandingSettings", "status", "localizations", "contentDetails", "statistics", "topicDetails", "contentOwnerDetails"}
PARTS = {"playlists": "snippet,status,localizations,contentDetails", "playlistItems": "snippet,contentDetails,status",
    "channelSections": "snippet,contentDetails", "playlistImages": "snippet",
    "channels": "snippet,brandingSettings,status,localizations", "videos": "snippet,recordingDetails"}
KINDS = {"playlists": "youtube#playlist", "playlistItems": "youtube#playlistItem", "channelSections": "youtube#channelSection", "playlistImages": "youtube#playlistImage", "channels": "youtube#channel", "videos": "youtube#video"}


class YouTubeOwnedClient(YouTubeResourcesClient):
    def _endpoint_allowed(self, method, path):
        regular = {("GET", name) for name in PARTS}
        regular |= {(verb, name) for name in {"playlists", "playlistItems", "channelSections", "playlistImages"} for verb in {"POST", "PUT", "DELETE"}}
        regular |= {("PUT", "channels"), ("PUT", "videos"), ("DELETE", "videos"), ("POST", "watermarks/unset")}
        uploads = {("POST", "channelBanners/insert"), ("POST", "watermarks/set"), ("POST", "playlistImages"), ("PUT", "playlistImages")}
        return any(method == verb and path == "/youtube/v3/" + name for verb, name in regular) or any(
            method == verb and path == "/upload/youtube/v3/" + name for verb, name in uploads)

    def identity(self):
        configured = self.configured_channel_id
        if self._authenticated_channel(self._token()) != configured:
            raise ResourceError("el canal autenticado no coincide con la marca/cuenta configurada")
        return configured

    def _validate_row(self, resource, row, *, parent=None):
        if not isinstance(row, dict):
            raise ResourceError("recurso inválido")
        owned_resource_id(resource, row.get("id"))
        if row.get("kind", KINDS[resource]) != KINDS[resource]:
            raise ResourceError("tipo de recurso inesperado")
        if resource != "playlistImages" and (not isinstance(row.get("etag"), str) or not row["etag"]):
            raise ResourceError("recurso sin ETag verificable")
        snip = row.get("snippet")
        if not isinstance(snip, dict):
            raise ResourceError("recurso sin snippet verificable")
        if resource == "channels":
            owned = row["id"] == self.configured_channel_id
        elif resource in {"playlistImages", "playlistItems"}:
            owned = bool(parent) and snip.get("playlistId") == parent
            if resource == "playlistItems":
                rid = snip.get("resourceId")
                if not isinstance(rid, dict) or set(rid) != {"kind", "videoId"} or rid["kind"] != "youtube#video":
                    raise ResourceError("playlistItem sin videoId/resourceId explícito")
                resource_id(rid["videoId"])
                if type(snip.get("position")) is not int or snip["position"] < 0:
                    raise ResourceError("posición de playlistItem inválida")
                content = row.get("contentDetails", {})
                if not isinstance(content, dict) or ("videoId" in content and content["videoId"] != rid["videoId"]):
                    raise ResourceError("playlistItem tiene identidades de vídeo contradictorias")
                owned = owned and snip.get("channelId") == self.configured_channel_id
            else:
                if snip.get("type") != "hero" or set(snip) - {"playlistId", "type", "width", "height"}:
                    raise ResourceError("tipo/campos de imagen desconocidos")
                for field in {"width", "height"} & set(snip):
                    if type(snip[field]) is not int or snip[field] <= 0:
                        raise ResourceError("dimensiones remotas de imagen inválidas")
        else:
            owned = snip.get("channelId") == self.configured_channel_id
        if not owned:
            raise ResourceError("el recurso no pertenece al canal/playlist configurado")
        return row

    def one(self, resource, rid, *, parent=None, parts=None, optional=False):
        if resource not in PARTS:
            raise ResourceError("recurso de lectura no admitido")
        self.identity()
        if resource in {"playlistItems", "playlistImages"}:
            self.one("playlists", resource_id(parent))
        if resource == "playlistImages":
            result = self.list_images(parent)
            if not result["complete"]:
                raise ResourceError("listado de imágenes incompleto; identidad no verificable")
            rows = [r for r in result["items"] if r["id"] == image_id(rid)]
        else:
            owned_resource_id(resource, rid)
            try:
                payload = self._json(self._request("GET", f"{API}/{resource}", params={"part": parts or PARTS[resource], "id": rid}))
            except ResourceError as exc:
                # Only this method's documented exact-ID reason is absence.
                # The caller still needs a durable delete receipt to prove success.
                if optional and resource == "channelSections" and exc.http_status == 404 and exc.provider_reason == "channelSectionNotFound":
                    return None
                raise
            rows = self._section_rows(payload) if resource == "channelSections" else payload.get("items")
            if rows is None and self._empty_complete(payload):
                rows = []
            if not isinstance(rows, list) or len(rows) > 1 or any(k in payload for k in ("nextPageToken", "prevPageToken")):
                raise ResourceError("lectura por ID incompleta o ambigua")
            page = payload.get("pageInfo")
            if page is not None and (not isinstance(page, dict) or type(page.get("totalResults")) is not int or page["totalResults"] != len(rows)):
                raise ResourceError("lectura por ID contradictoria: totalResults no coincide")
        if not rows:
            if optional:
                return None
            raise ResourceError("no se encontró el ID propio; ausencia/permisos no concluyentes")
        row = self._validate_row(resource, rows[0], parent=parent)
        if row["id"] != rid:
            raise ResourceError("ID devuelto no coincide con el ID solicitado")
        return row

    @staticmethod
    def _section_rows(payload):
        # Discovery's unpaginated response has no pageInfo or continuation. An
        # omitted optional items array is empty only in the valid method envelope.
        if (set(payload) - {"kind", "etag", "items", "eventId", "visitorId"}
            or payload.get("kind") != "youtube#channelSectionListResponse"
            or not isinstance(payload.get("etag"), str) or not payload["etag"]):
            raise ResourceError("envoltura no paginada de secciones inválida")
        rows = payload.get("items", [])
        if not isinstance(rows, list) or len(rows) > 10:
            raise ResourceError("items de secciones inválidos o límite excedido")
        return rows

    @staticmethod
    def _empty_complete(payload):
        page = payload.get("pageInfo")
        return (isinstance(page, dict) and type(page.get("totalResults")) is int and page["totalResults"] == 0
            and type(page.get("resultsPerPage")) is int and 0 < page["resultsPerPage"] <= 50
            and not any(k in payload for k in ("nextPageToken", "prevPageToken")))

    def _list(self, resource, params, *, parent=None, max_pages=100):
        if type(max_pages) is not int or not 1 <= max_pages <= 100:
            raise ResourceError("max-pages exige un entero entre 1 y 100")
        self.identity()
        report = {"items": [], "complete": False, "absence_proven": False, "pages": 0,
            "limitation": "Lectura acotada; la lista no prueba ausencia global ni permisos de escritura.", "error": None}
        tokens, ids = set(), set()
        token = None
        expected_total = None
        for _ in range(max_pages):
            try:
                query = {"part": PARTS[resource], **params}
                if resource != "channelSections":
                    query["maxResults"] = 50
                if token is not None:
                    query["pageToken"] = token
                payload = self._json(self._request("GET", f"{API}/{resource}", params=query))
                rows = self._section_rows(payload) if resource == "channelSections" else payload.get("items")
                if rows is None and self._empty_complete(payload):
                    rows = []
                if not isinstance(rows, list) or len(rows) > (10 if resource == "channelSections" else 50):
                    raise ResourceError("items inválidos o límite excedido")
                page_rows = []
                for row in rows:
                    self._validate_row(resource, row, parent=parent)
                    if row["id"] in ids:
                        raise ResourceError("lista incompleta: ID duplicado entre páginas")
                    ids.add(row["id"])
                    page_rows.append(row)
                report["items"].extend(page_rows)
                report["pages"] += 1
                next_token = payload.get("nextPageToken")
                if resource == "channelSections":
                    if any(k in payload for k in ("nextPageToken", "prevPageToken")):
                        raise ResourceError("paginación no documentada de secciones")
                    report["complete"] = True
                    break
                page = payload.get("pageInfo")
                if not isinstance(page, dict) or type(page.get("totalResults")) is not int or page["totalResults"] < 0:
                    raise ResourceError("falta totalResults válido; lista incompleta")
                if expected_total is not None and page["totalResults"] != expected_total:
                    raise ResourceError("total de lista cambió durante la lectura")
                expected_total = page["totalResults"]
                if "nextPageToken" not in payload:
                    report["complete"] = len(report["items"]) == expected_total
                    if not report["complete"]:
                        report["error"] = "totalResults no coincide con IDs leídos; lista incompleta"
                    break
                if not isinstance(next_token, str) or not next_token or len(next_token) > 2048 or next_token in tokens:
                    raise ResourceError("lista incompleta: continuación inválida/repetida")
                tokens.add(next_token)
                token = next_token
            except ResourceError as exc:
                report["error"] = str(exc)
                break
        if not report["complete"] and not report["error"]:
            report["error"] = "límite de páginas; lista incompleta"
        return report

    def list_playlists(self, *, max_pages=100):
        return self._list("playlists", {"mine": "true"}, max_pages=max_pages)

    def list_items(self, playlist_id, *, max_pages=100):
        self.one("playlists", resource_id(playlist_id))
        return self._list("playlistItems", {"playlistId": playlist_id}, parent=playlist_id, max_pages=max_pages)

    def list_sections(self):
        return self._list("channelSections", {"channelId": self.configured_channel_id})

    def list_images(self, playlist_id, *, max_pages=100):
        self.one("playlists", resource_id(playlist_id))
        # Current discovery20260910 plus actual controller GET evidence. The
        # method page's id/playlistId filters contradict the service schema.
        return self._list("playlistImages", {"parent": playlist_id}, parent=playlist_id, max_pages=max_pages)

    def inspect_channel(self, channel_id, *, parts=None):
        selected = set(parts or PARTS["channels"].split(","))
        if not selected or selected - CHANNEL_PARTS:
            raise ResourceError("partes de canal de lectura no admitidas")
        selected.add("snippet")
        if resource_id(channel_id) != self.configured_channel_id:
            raise ResourceError("canal no coincide con la marca")
        return self.one("channels", channel_id, parts=",".join(sorted(selected)))

    def inspect_video(self, video_id, *, parts=None):
        selected = set(parts or PARTS["videos"].split(","))
        if not selected or selected - VIDEO_PARTS:
            raise ResourceError("partes de vídeo de lectura no admitidas")
        selected.add("snippet")
        return self.one("videos", video_id, parts=",".join(sorted(selected)))

    def effect(self, edit, resource, rid, body, *, etag=None, asset=None, data=None):
        """One fixed effect. Only the durable coordinator calls this method."""
        edit = validate_edit(edit)
        if target(edit) != (resource, rid):
            raise ResourceError("acción e ID no coinciden con el recurso de destino")
        action = edit.action
        deleting = action.endswith("-delete")
        inserting = action in {"playlist-create", "item-insert", "section-create", "image-insert"}
        headers = {"If-Match": etag} if etag and resource != "playlistImages" else {}
        if action == "banner-upload":
            response = self._request("POST", f"{UPLOAD}/channelBanners/insert",
                headers={"Content-Type": asset["mime"]}, params={"uploadType": "media"}, content=data)
            return self._json(response, writing=True)
        if action == "watermark-unset":
            response = self._request("POST", f"{API}/watermarks/unset", params={"channelId": edit.channel_id})
        elif deleting:
            response = self._request("DELETE", f"{API}/{resource}", headers=headers, params={"id": rid})
        else:
            outgoing = dict(body) if inserting or action == "watermark-set" else {"id": rid, **body}
            params = {"part": ",".join(sorted(body))}
            endpoint, method = resource, "POST" if inserting else "PUT"
            if action == "watermark-set":
                endpoint, method, params, headers = "watermarks/set", "POST", {"channelId": edit.channel_id}, {}
            if data is None:
                response = self._request(method, f"{API}/{endpoint}", headers=headers, params=params, json=outgoing)
            else:
                boundary = "socialctl_" + uuid.uuid4().hex
                while boundary.encode() in data:
                    boundary = "socialctl_" + uuid.uuid4().hex
                content = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
                    + json.dumps(outgoing, ensure_ascii=False).encode()
                    + f"\r\n--{boundary}\r\nContent-Type: {asset['mime']}\r\n\r\n".encode()
                    + data + f"\r\n--{boundary}--\r\n".encode())
                response = self._request(method, f"{UPLOAD}/{endpoint}", params={**params, "uploadType": "multipart"},
                    headers={**headers, "Content-Type": f"multipart/related; boundary={boundary}"}, content=content)
        if deleting or action in {"watermark-set", "watermark-unset"}:
            if response.status_code != 204:
                raise ResourceUncertain("se esperaba 204 No Content; resultado no confirmado")
            return {"http_status": 204}
        return self._json(response, writing=True)
