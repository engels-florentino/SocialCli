"""Bounded fixed-host community reads and one explicit effect, without retry."""
import json
import re

from socialctl.management.community_schema import opaque_id
from socialctl.management.youtube_resources import API, YouTubeResourcesClient, ResourceError, ResourceUncertain, resource_id

CATALOGS = {"languages": "i18nLanguages", "regions": "i18nRegions", "categories": "videoCategories", "abuse-reasons": "videoAbuseReportReasons"}
FILTERS = ("published", "heldForReview", "likelySpam")


def complete(report):
    if not report["complete"]:
        raise ResourceError("incomplete list: " + str(report.get("error")))
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
            raise ResourceError("authenticated channel differs from configured brand")
        return self.configured_channel_id

    def _pages(self, resource, params, validate, *, max_pages=100, paginated=True):
        from socialctl.management.changes import now_utc
        if type(max_pages) is not int or not 1 <= max_pages <= 100:
            raise ResourceError("max-pages must be between 1 and 100")
        self.identity()
        result = {"items": [], "complete": False, "pages": 0, "absence_proven": False, "error": None,
            "observed_at": now_utc().isoformat(), "source": f"{API}/{resource}",
            "scope": {"resource": resource, **params}, "limitation": "Complete only for this filter; does not prove global absence, write permissions or complete replies within threads."}
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
                    raise ResourceError("empty unverifiable envelope")
                rows = payload.get("items", [])
                if not isinstance(rows, list) or len(rows) > (50 if paginated else 5000):
                    raise ResourceError("invalid items or limit exceeded")
                accepted = []
                for row in rows:
                    key = validate(row)
                    if key in ids:
                        raise ResourceError("duplicate ID during pagination")
                    ids.add(key)
                    accepted.append(row)
                result["items"].extend(accepted)
                result["pages"] += 1
                next_token = payload.get("nextPageToken")
                if "nextPageToken" in payload and (not isinstance(next_token, str) or not 0 < len(next_token) <= 2048 or next_token in tokens):
                    raise ResourceError("invalid or repeated pagination")
                if not paginated and any(k in payload for k in ("nextPageToken", "prevPageToken")):
                    raise ResourceError("undocumented catalog pagination")
                if paginated:
                    page = payload.get("pageInfo")
                    if page is None:
                        raise ResourceError("pagination metadata missing; verified items retained")
                    if not isinstance(page, dict) or type(page.get("totalResults")) is not int or page["totalResults"] < 0 or type(page.get("resultsPerPage")) is not int or not len(rows) <= page["resultsPerPage"] <= 50:
                        raise ResourceError("pagination lacks verifiable metadata")
                    if not rows and (next_token or page["totalResults"] != 0):
                        raise ResourceError("contradictory empty page")
                    if resource != "search":  # Search documents approximate totals.
                        if expected_total is not None and page["totalResults"] != expected_total:
                            raise ResourceError("total changed during pagination")
                        expected_total = page["totalResults"]
                if next_token is None:
                    if expected_total is not None and expected_total != len(result["items"]):
                        raise ResourceError("total does not match received list; partial read")
                    result["complete"] = True
                    break
                tokens.add(next_token)
                token = next_token
            except KeyboardInterrupt:
                result["error"] = "Read interrupted; verified items retained"
                result["interrupted"] = True
                break
            except ResourceError as exc:
                result["error"] = str(exc)
                break
        if not result["complete"] and result["error"] is None:
            result["error"] = "page limit reached"
        if not result["complete"] and token and result["error"] == "page limit reached":
            result["cursor"] = token
        return result

    @staticmethod
    def _row(row, kind=None):
        if not isinstance(row, dict) or not isinstance(row.get("snippet"), dict):
            raise ResourceError("resource lacks snippet")
        if kind and row.get("kind", kind) != kind:
            raise ResourceError("unexpected resource type")
        return opaque_id(row.get("id"))

    def _comment(self, row, *, parent=None):
        cid = self._row(row, "youtube#comment")
        snip = row["snippet"]
        if not isinstance(row.get("etag"), str) or not row["etag"]:
            raise ResourceError("comment lacks ETag")
        if snip.get("parentId") != parent or ("channelId" in snip and snip["channelId"] != self.configured_channel_id):
            raise ResourceError("comment belongs to another parent/channel")
        author = snip.get("authorChannelId")
        if author is not None and (not isinstance(author, dict) or not isinstance(author.get("value"), str)):
            raise ResourceError("invalid comment author")
        return cid

    def _thread(self, row, video_id):
        tid = self._row(row, "youtube#commentThread")
        snip = row["snippet"]
        if snip.get("videoId") != video_id or snip.get("channelId") != self.configured_channel_id:
            raise ResourceError("thread belongs to another video/channel")
        self._comment(snip.get("topLevelComment"))
        return tid

    def list_threads(self, video_id, *, moderation_status="published", max_pages=100):
        self.inspect(resource_id(video_id))
        if moderation_status not in FILTERS:
            raise ResourceError("undocumented moderation filter")
        return self._pages("commentThreads", {"part": "snippet", "videoId": video_id,
            "moderationStatus": moderation_status, "textFormat": "plainText"},
            lambda row: self._thread(row, video_id), max_pages=max_pages)

    def thread(self, video_id, thread_id):
        self.inspect(resource_id(video_id))
        payload = self._json(self._request("GET", f"{API}/commentThreads", params={"part": "snippet", "id": opaque_id(thread_id), "textFormat": "plainText"}))
        rows = payload.get("items")
        if not isinstance(rows, list) or len(rows) != 1 or any(k in payload for k in ("nextPageToken", "prevPageToken")):
            raise ResourceError("thread unverifiable by ID; absence/permissions inconclusive")
        self._exact_total(payload)
        if self._thread(rows[0], video_id) != thread_id:
            raise ResourceError("thread ID mismatch")
        return rows[0]

    def list_replies(self, video_id, thread_id, parent_id, *, max_pages=100):
        thread = self.thread(video_id, thread_id)
        if thread["snippet"]["topLevelComment"]["id"] != opaque_id(parent_id):
            raise ResourceError("parent_id is not thread's top-level comment")
        return self._pages("comments", {"part": "snippet", "parentId": parent_id, "textFormat": "plainText"},
            lambda row: self._comment(row, parent=parent_id), max_pages=max_pages)

    def find_comment(self, video_id, thread_id, comment_id, parent_id=None, *, moderation=False, cache=None, allow_partial=False):
        opaque_id(comment_id)
        cache = {} if cache is None else cache
        if parent_id is not None:
            key = (video_id, thread_id, parent_id)
            if key not in cache:
                report = self.list_replies(video_id, thread_id, parent_id)
                if not report['complete'] and not (allow_partial and str(report.get('error', '')).startswith('pagination metadata missing')):
                    complete(report)
                cache[key] = report['items']
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
            raise ResourceError("comment unverifiable in thread/parent; absence/permissions inconclusive")
        return found[0]

    @staticmethod
    def _exact_total(payload):
        page = payload.get("pageInfo")
        if page is not None and (not isinstance(page, dict) or type(page.get("totalResults")) is not int or page["totalResults"] != 1):
            raise ResourceError("ID response has ambiguous total")

    def external(self, resource, rid):
        if resource not in {"videos", "channels"}:
            raise ResourceError("unsupported external target")
        self.identity()
        payload = self._json(self._request("GET", f"{API}/{resource}", params={"part": "snippet", "id": resource_id(rid)}))
        rows = payload.get("items")
        if not isinstance(rows, list) or len(rows) != 1 or any(k in payload for k in ("nextPageToken", "prevPageToken")):
            raise ResourceError("external target unverifiable")
        self._exact_total(payload)
        if self._row(rows[0]) != rid:
            raise ResourceError("external target ID mismatch")
        return rows[0]

    def _subscription(self, row):
        rid = self._row(row, "youtube#subscription")
        snip = row["snippet"]
        target = snip.get("resourceId")
        if snip.get("channelId") != self.configured_channel_id or not isinstance(target, dict) or target.get("kind") != "youtube#channel":
            raise ResourceError("subscription belongs to another actor or invalid target")
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
            raise ResourceError("rating unverifiable for exact ID")
        return rows[0]

    def catalog(self, name, *, language="en", region=None):
        if name not in CATALOGS or not isinstance(language, str) or not re.fullmatch(r"[A-Za-z_0-9-]{1,35}", language):
            raise ResourceError("unsupported catalog/language")
        params = {"part": "snippet", "hl": language}
        if name == "categories":
            if not isinstance(region, str) or not re.fullmatch(r"[A-Z]{2}", region):
                raise ResourceError("categories requires explicit ISO region")
            params["regionCode"] = region
        elif region is not None:
            raise ResourceError("region accepted only for categories")
        return self._pages(CATALOGS[name], params, self._row, paginated=False)

    def search(self, query, *, kind="video", order="relevance", channel_id=None, max_pages=100):
        if not isinstance(query, str) or not 0 < len(query.strip()) <= 1000 or kind not in {"video", "channel", "playlist"} or order not in {"date", "rating", "relevance", "title", "videoCount", "viewCount"}:
            raise ResourceError("invalid search query/type/order")
        params = {"part": "snippet", "q": query, "type": kind, "order": order}
        if channel_id is not None:
            params["channelId"] = resource_id(channel_id)
        def validate(row):
            if not isinstance(row, dict) or not isinstance(row.get("id"), dict) or not isinstance(row.get("snippet"), dict):
                raise ResourceError("invalid search result")
            rid = row["id"]
            if rid.get("kind") != "youtube#" + kind:
                raise ResourceError("unexpected search result type")
            if channel_id is not None and row["snippet"].get("channelId") != channel_id:
                raise ResourceError("search returned another channel")
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
                    raise ResourceError("activities timestamp requires RFC3339 with timezone") from None
                params[key] = value
        def validate(row):
            rid = self._row(row, "youtube#activity")
            if row["snippet"].get("channelId") != self.configured_channel_id:
                raise ResourceError("activity from another channel")
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
            raise ResourceError("unsupported community action")
        if response.status_code != 204:
            raise ResourceUncertain("bodyless method did not return 204; uncertain outcome")
        return {"http_status": 204}
