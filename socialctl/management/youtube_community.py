"""Bounded fixed-host community reads and one explicit effect, without retry."""
import json
import re

from socialctl.management.community_schema import opaque_id
from socialctl.management.youtube_resources import API, YouTubeResourcesClient, ResourceError, ResourceUncertain, resource_id

CATALOGS = {"languages": "i18nLanguages", "regions": "i18nRegions", "categories": "videoCategories", "abuse-reasons": "videoAbuseReportReasons"}
FILTERS = ("published", "heldForReview", "likelySpam")


def complete(report):
    if not report["complete"]:
        raise ResourceError("lista incompleta: " + str(report.get("error")))
    return report["items"]


class YouTubeCommunityClient(YouTubeResourcesClient):
    def _endpoint_allowed(self, method, path):
        reads = {"channels", "videos", "commentThreads", "comments", "subscriptions", "activities", "search", "videos/getRating", *CATALOGS.values()}
        writes = {("POST", "commentThreads"), ("POST", "comments"), ("PUT", "comments"), ("DELETE", "comments"),
            ("POST", "comments/setModerationStatus"), ("POST", "subscriptions"), ("DELETE", "subscriptions"),
            ("POST", "videos/rate"), ("POST", "videos/reportAbuse")}
        return (method == "GET" and path in {"/youtube/v3/" + r for r in reads}) or any(
            method == verb and path == "/youtube/v3/" + name for verb, name in writes)

    def identity(self):
        if self._authenticated_channel(self._token()) != self.configured_channel_id:
            raise ResourceError("canal autenticado distinto de la marca configurada")
        return self.configured_channel_id

    def _pages(self, resource, params, validate, *, max_pages=100, paginated=True):
        from socialctl.management.changes import now_utc
        if type(max_pages) is not int or not 1 <= max_pages <= 100:
            raise ResourceError("max-pages debe estar entre 1 y 100")
        self.identity()
        result = {"items": [], "complete": False, "pages": 0, "absence_proven": False, "error": None,
            "observed_at": now_utc().isoformat(), "source": f"{API}/{resource}",
            "scope": {"resource": resource, **params}, "limitation": "Completo solo para este filtro; no demuestra ausencia global, permisos de escritura ni replies completos dentro de threads."}
        token, tokens, ids = None, set(), set()
        expected_total = None
        for _ in range(max_pages):
            try:
                query = dict(params)
                if paginated:
                    query["maxResults"] = 50
                if token is not None:
                    query["pageToken"] = token
                payload = self._json(self._request("GET", f"{API}/{resource}", params=query))
                if "items" not in payload and not (isinstance(payload.get("kind"), str) and isinstance(payload.get("etag"), str) and payload["etag"]):
                    raise ResourceError("envoltura vacía no verificable")
                rows = payload.get("items", [])
                if not isinstance(rows, list) or len(rows) > (50 if paginated else 5000):
                    raise ResourceError("items inválidos o límite excedido")
                next_token = payload.get("nextPageToken")
                if "nextPageToken" in payload and (not isinstance(next_token, str) or not 0 < len(next_token) <= 2048 or next_token in tokens):
                    raise ResourceError("paginación inválida o repetida")
                if not paginated and any(k in payload for k in ("nextPageToken", "prevPageToken")):
                    raise ResourceError("paginación no documentada en catálogo")
                if paginated:
                    page = payload.get("pageInfo")
                    if not isinstance(page, dict) or type(page.get("totalResults")) is not int or page["totalResults"] < 0 or type(page.get("resultsPerPage")) is not int or not len(rows) <= page["resultsPerPage"] <= 50:
                        raise ResourceError("paginación sin metadatos verificables")
                    if not rows and (next_token or page["totalResults"] != 0):
                        raise ResourceError("página vacía contradictoria")
                    if resource != "search":  # Search documents approximate totals.
                        if expected_total is not None and page["totalResults"] != expected_total:
                            raise ResourceError("total cambió durante la paginación")
                        expected_total = page["totalResults"]
                accepted = []
                for row in rows:
                    key = validate(row)
                    if key in ids:
                        raise ResourceError("ID duplicado durante paginación")
                    ids.add(key)
                    accepted.append(row)
                result["items"].extend(accepted)
                result["pages"] += 1
                if next_token is None:
                    if expected_total is not None and expected_total != len(result["items"]):
                        raise ResourceError("total no coincide con la lista recibida; lectura parcial")
                    result["complete"] = True
                    break
                tokens.add(next_token)
                token = next_token
            except ResourceError as exc:
                result["error"] = str(exc)
                break
        if not result["complete"] and result["error"] is None:
            result["error"] = "límite de páginas alcanzado"
        return result

    @staticmethod
    def _row(row, kind=None):
        if not isinstance(row, dict) or not isinstance(row.get("snippet"), dict):
            raise ResourceError("recurso sin snippet")
        if kind and row.get("kind", kind) != kind:
            raise ResourceError("tipo de recurso inesperado")
        return opaque_id(row.get("id"))

    def _comment(self, row, *, parent=None):
        cid = self._row(row, "youtube#comment")
        snip = row["snippet"]
        if not isinstance(row.get("etag"), str) or not row["etag"]:
            raise ResourceError("comentario sin ETag")
        if snip.get("parentId") != parent or ("channelId" in snip and snip["channelId"] != self.configured_channel_id):
            raise ResourceError("comentario pertenece a otro padre/canal")
        author = snip.get("authorChannelId")
        if author is not None and (not isinstance(author, dict) or not isinstance(author.get("value"), str)):
            raise ResourceError("autor de comentario inválido")
        return cid

    def _thread(self, row, video_id):
        tid = self._row(row, "youtube#commentThread")
        snip = row["snippet"]
        if snip.get("videoId") != video_id or snip.get("channelId") != self.configured_channel_id:
            raise ResourceError("hilo pertenece a otro vídeo/canal")
        self._comment(snip.get("topLevelComment"))
        return tid

    def list_threads(self, video_id, *, moderation_status="published", max_pages=100):
        self.inspect(resource_id(video_id))
        if moderation_status not in FILTERS:
            raise ResourceError("filtro de moderación no documentado")
        return self._pages("commentThreads", {"part": "snippet", "videoId": video_id,
            "moderationStatus": moderation_status, "textFormat": "plainText"},
            lambda row: self._thread(row, video_id), max_pages=max_pages)

    def thread(self, video_id, thread_id):
        self.inspect(resource_id(video_id))
        payload = self._json(self._request("GET", f"{API}/commentThreads", params={"part": "snippet", "id": opaque_id(thread_id), "textFormat": "plainText"}))
        rows = payload.get("items")
        if not isinstance(rows, list) or len(rows) != 1 or any(k in payload for k in ("nextPageToken", "prevPageToken")):
            raise ResourceError("hilo no verificable por ID; ausencia/permisos no concluyentes")
        self._exact_total(payload)
        if self._thread(rows[0], video_id) != thread_id:
            raise ResourceError("ID del hilo no coincide")
        return rows[0]

    def list_replies(self, video_id, thread_id, parent_id, *, max_pages=100):
        thread = self.thread(video_id, thread_id)
        if thread["snippet"]["topLevelComment"]["id"] != opaque_id(parent_id):
            raise ResourceError("parent_id no es el comentario principal del hilo")
        return self._pages("comments", {"part": "snippet", "parentId": parent_id, "textFormat": "plainText"},
            lambda row: self._comment(row, parent=parent_id), max_pages=max_pages)

    def find_comment(self, video_id, thread_id, comment_id, parent_id=None, *, moderation=False, cache=None):
        opaque_id(comment_id)
        cache = {} if cache is None else cache
        if parent_id is not None:
            key = (video_id, thread_id, parent_id)
            if key not in cache:
                cache[key] = complete(self.list_replies(video_id, thread_id, parent_id))
            rows = cache[key]
        elif moderation:
            rows = []
            for state in FILTERS:
                key = (video_id, state)
                if key not in cache:
                    cache[key] = complete(self.list_threads(video_id, moderation_status=state))
                for thread in cache[key]:
                    if thread["id"] == thread_id:
                        rows.append(thread["snippet"]["topLevelComment"])
            # Threads with held replies can also appear in published; same top is
            # allowed only if the returned resource itself agrees exactly.
            rows = list({json.dumps(r, sort_keys=True): r for r in rows}.values())
        else:
            rows = [self.thread(video_id, thread_id)["snippet"]["topLevelComment"]]
        found = [r for r in rows if r["id"] == comment_id]
        if len(found) != 1:
            raise ResourceError("comentario no verificable en hilo/padre; ausencia/permisos no concluyentes")
        return found[0]

    @staticmethod
    def _exact_total(payload):
        page = payload.get("pageInfo")
        if page is not None and (not isinstance(page, dict) or type(page.get("totalResults")) is not int or page["totalResults"] != 1):
            raise ResourceError("respuesta por ID tiene total ambiguo")

    def external(self, resource, rid):
        if resource not in {"videos", "channels"}:
            raise ResourceError("destino externo no admitido")
        self.identity()
        payload = self._json(self._request("GET", f"{API}/{resource}", params={"part": "snippet", "id": resource_id(rid)}))
        rows = payload.get("items")
        if not isinstance(rows, list) or len(rows) != 1 or any(k in payload for k in ("nextPageToken", "prevPageToken")):
            raise ResourceError("destino externo no verificable")
        self._exact_total(payload)
        if self._row(rows[0]) != rid:
            raise ResourceError("ID del destino externo no coincide")
        return rows[0]

    def _subscription(self, row):
        rid = self._row(row, "youtube#subscription")
        snip = row["snippet"]
        target = snip.get("resourceId")
        if snip.get("channelId") != self.configured_channel_id or not isinstance(target, dict) or target.get("kind") != "youtube#channel":
            raise ResourceError("suscripción de otro actor o destino inválido")
        resource_id(target.get("channelId"))
        return rid

    def list_subscriptions(self, *, max_pages=100):
        return self._pages("subscriptions", {"part": "snippet", "mine": "true"}, self._subscription, max_pages=max_pages)

    def get_rating(self, video_id):
        self.external("videos", video_id)
        payload = self._json(self._request("GET", f"{API}/videos/getRating", params={"id": video_id}))
        rows = payload.get("items")
        if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
            or rows[0].get("videoId") != video_id or rows[0].get("rating") not in {"like", "dislike", "none", "unspecified"}
            or any(k in payload for k in ("nextPageToken", "prevPageToken"))):
            raise ResourceError("rating no verificable para el ID exacto")
        return rows[0]

    def catalog(self, name, *, language="en", region=None):
        if name not in CATALOGS or not isinstance(language, str) or not re.fullmatch(r"[A-Za-z_0-9-]{1,35}", language):
            raise ResourceError("catálogo/idioma no admitido")
        params = {"part": "snippet", "hl": language}
        if name == "categories":
            if not isinstance(region, str) or not re.fullmatch(r"[A-Z]{2}", region):
                raise ResourceError("categories exige region ISO explícita")
            params["regionCode"] = region
        elif region is not None:
            raise ResourceError("region solo se admite para categories")
        return self._pages(CATALOGS[name], params, self._row, paginated=False)

    def search(self, query, *, kind="video", order="relevance", channel_id=None, max_pages=100):
        if not isinstance(query, str) or not 0 < len(query.strip()) <= 1000 or kind not in {"video", "channel", "playlist"} or order not in {"date", "rating", "relevance", "title", "videoCount", "viewCount"}:
            raise ResourceError("consulta/tipo/orden de search inválido")
        params = {"part": "snippet", "q": query, "type": kind, "order": order}
        if channel_id is not None:
            params["channelId"] = resource_id(channel_id)
        def validate(row):
            if not isinstance(row, dict) or not isinstance(row.get("id"), dict) or not isinstance(row.get("snippet"), dict):
                raise ResourceError("resultado search inválido")
            rid = row["id"]
            if rid.get("kind") != "youtube#" + kind:
                raise ResourceError("tipo de resultado search distinto")
            if channel_id is not None and row["snippet"].get("channelId") != channel_id:
                raise ResourceError("search devolvió otro canal")
            return resource_id(rid.get(kind + "Id"))
        return self._pages("search", params, validate, max_pages=max_pages)

    def activities(self, *, published_after=None, published_before=None, max_pages=100):
        from datetime import datetime
        params = {"part": "snippet,contentDetails", "channelId": self.configured_channel_id}
        for key, value in (("publishedAfter", published_after), ("publishedBefore", published_before)):
            if value is not None:
                try:
                    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    if stamp.tzinfo is None:
                        raise ValueError()
                except (ValueError, AttributeError):
                    raise ResourceError("fecha de activities exige RFC3339 con zona") from None
                params[key] = value
        def validate(row):
            rid = self._row(row, "youtube#activity")
            if row["snippet"].get("channelId") != self.configured_channel_id:
                raise ResourceError("actividad de otro canal")
            return rid
        return self._pages("activities", params, validate, max_pages=max_pages)

    def effect(self, edit, body, before):
        action = edit.action
        headers = {}
        if action in {"edit", "delete"}:
            headers["If-Match"] = before["comment"]["etag"]
        if action in {"add", "reply", "edit", "subscribe"}:
            name = {"add": "commentThreads", "subscribe": "subscriptions"}.get(action, "comments")
            response = self._request("PUT" if action == "edit" else "POST", f"{API}/{name}",
                params={"part": "snippet"}, headers=headers, json=body)
            return self._json(response, writing=True)
        if action in {"delete", "unsubscribe"}:
            name = "comments" if action == "delete" else "subscriptions"
            response = self._request("DELETE", f"{API}/{name}", params={"id": edit.comment_id if action == "delete" else edit.subscription_id}, headers=headers)
        elif action == "moderate":
            response = self._request("POST", f"{API}/comments/setModerationStatus", params={
                "id": ",".join(t.comment_id for t in edit.targets), "moderationStatus": edit.moderation_status,
                **({"banAuthor": "true"} if edit.ban_author else {})})
        elif action == "rate":
            response = self._request("POST", f"{API}/videos/rate", params={"id": edit.video_id, "rating": edit.rating})
        elif action == "report-abuse":
            response = self._request("POST", f"{API}/videos/reportAbuse", json=body)
        else:
            raise ResourceError("acción de comunidad no admitida")
        if response.status_code != 204:
            raise ResourceUncertain("método sin cuerpo no devolvió204; resultado incierto")
        return {"http_status": 204}
