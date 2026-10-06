"""Bounded account reads and fixed Analytics transport; no credential refresh."""
from datetime import timedelta
import json
import re
import time
from urllib.parse import quote

import httpx

from socialctl.identity import credential_metadata
from socialctl.management.youtube_owned import YouTubeOwnedClient
from socialctl.management.youtube_resources import API, ResourceError, ResourceRejected, ResourceUncertain, provider_reason, resource_id
from socialctl.metricas.analytics_schema import ReportQuery, parse_query_response, provenance, metric_value
from socialctl.models import Platform

ANALYTICS = "https://youtubeanalytics.googleapis.com/v2"
REPORTING = "https://youtubereporting.googleapis.com/v1"


def opaque(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 512 or any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise ResourceError("invalid opaque provider ID")
    return value


def encoded(value):
    return quote(opaque(value), safe="")


def complete(result):
    if not result["complete"]:
        raise ResourceError("incomplete provider list; absence/ownership not established")
    return result["items"]


def unsigned_long(value):
    """Exact documented unsigned long; preserve caller's raw wire value."""
    if type(value) is int:
        number = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]{1,20}", value):
        number = int(value)
    else:
        raise ResourceError("unsigned long schema invalid")
    if not 0 <= number <= 18446744073709551615:
        raise ResourceError("unsigned long outside uint64 range")
    return number


def failure(exc):
    if exc.http_status == 400:
        return "provider_rejected_report_combination"
    if exc.http_status == 429 or exc.provider_reason == "quotaExceeded":
        return "rate_limited"
    if exc.http_status in {401, 403}:
        return "forbidden_or_missing_grant"
    return "provider_or_schema_unavailable"


class AnalyticsClient(YouTubeOwnedClient):
    def _token(self):
        if self._access_token is None:
            try:
                secret = self.brand.leer_secreto(Platform.YOUTUBE)
                self.credential_observation = credential_metadata(secret)
                token = secret.get("access_token")
            except Exception:
                raise ResourceError("credentials unavailable") from None
            if self.credential_observation["expiry"]["expired"]:
                raise ResourceError("credentials expired; no implicit refresh")
            if not isinstance(token, str) or not token:
                raise ResourceError("credentials unavailable")
            self._access_token = token
        return self._access_token

    def require_scope(self, scopes):
        self._token()
        actual = self.credential_observation["granted_scopes"]
        if actual["state"] != "known" or not set(actual["values"]) & set(scopes):
            raise ResourceError("required granted scope not recorded; no new grant or refresh attempted")

    def _endpoint_allowed(self, method, path):
        return method == "GET" and path in {"/youtube/v3/channels", "/youtube/v3/videos", "/youtube/v3/playlists", "/youtube/v3/videos:batchGetStats"}

    def service_request(self, method, url, *, max_bytes=4*1024*1024, **kwargs):
        parsed = httpx.URL(url)
        paths = {("GET", "/v2/reports"), *( (m, "/v2/" + r) for r in ("groups", "groupItems") for m in ("GET", "POST", "PUT", "DELETE") if not (r == "groupItems" and m == "PUT") )}
        reporting = ((method, parsed.path) in {("GET", "/v1/reportTypes"), ("GET", "/v1/jobs"), ("POST", "/v1/jobs")}
            or method in {"GET", "DELETE"} and bool(re.fullmatch(r"/v1/jobs/[^/]+", parsed.path))
            or method == "GET" and bool(re.fullmatch(r"/v1/jobs/[^/]+/reports(?:/[^/]+)?", parsed.path)))
        allowed = (parsed.host == "youtubeanalytics.googleapis.com" and (method, parsed.path) in paths
            or parsed.host == "youtubereporting.googleapis.com" and reporting
            or parsed.host == "www.googleapis.com" and method == "GET" and parsed.path == "/youtube/v3/videos:batchGetStats")
        if not allowed or parsed.scheme != "https" or parsed.port not in {None, 443} or parsed.userinfo or parsed.query or parsed.fragment:
            raise ResourceError("endpoint not allowed")
        return self._bounded_request(method, url, max_bytes=max_bytes, **kwargs)

    def _bounded_request(self, method, url, *, max_bytes, **kwargs):
        token = self._token()
        writing = method != "GET"
        deadline = time.monotonic() + 120
        try:
            with self.client.stream(method, url, headers={"Authorization": f"Bearer {token}", "Accept-Encoding": "identity"},
                follow_redirects=False, timeout=60, **kwargs) as response:
                if not response.is_success:
                    error = (ResourceUncertain if response.is_redirect or response.status_code >= 500 else ResourceRejected) if writing else ResourceError
                    reason = provider_reason(response, token)
                    raise error(f"provider HTTP {response.status_code}; effect unconfirmed", http_status=response.status_code, provider_reason=reason)
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise (ResourceUncertain if writing else ResourceError)("compressed response not accepted")
                if not writing and response.status_code != 200:
                    raise ResourceError("partial/nonstandard read response not accepted")
                length = response.headers.get("content-length")
                if length is not None and (not re.fullmatch(r"[0-9]{1,12}", length) or int(length) > max_bytes):
                    raise (ResourceUncertain if writing else ResourceError)("invalid response length or byte limit exceeded")
                raw = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    if len(raw)+len(chunk) > max_bytes or time.monotonic() > deadline:
                        raise (ResourceUncertain if writing else ResourceError)("response byte limit")
                    raw.extend(chunk)
                if length is not None and len(raw) != int(length):
                    raise (ResourceUncertain if writing else ResourceError)("partial response length mismatch")
                return httpx.Response(response.status_code, content=bytes(raw), request=response.request)
        except ResourceError:
            raise
        except Exception:
            raise (ResourceUncertain if writing else ResourceError)("provider transport interrupted") from None

    def pages(self, url, key="items", params=None, max_pages=100, paginated=True):
        if type(max_pages) is not int or not 1 <= max_pages <= 100:
            raise ResourceError("page bound 1..100")
        items, seen, ids = [], set(), set()
        params = dict(params or {})
        result = {"items": items, "complete": False, "failure_reason": None, "pages": 0}
        for _ in range(max_pages):
            try:
                payload = self._json(self.service_request("GET", url, params=params))
                if key not in payload and (key == "items" or set(payload)-{"nextPageToken"}):
                    raise ResourceError("list envelope schema missing collection")
                rows = payload.get(key, [])
                if not isinstance(rows, list) or len(rows) > 1000 or len(items)+len(rows) > 100000:
                    raise ResourceError("invalid/oversized provider page")
                page_ids = [opaque(r.get("id")) if isinstance(r, dict) else opaque(None) for r in rows]
                if len(page_ids) != len(set(page_ids)) or set(page_ids) & ids:
                    raise ResourceError("duplicate identity across provider pages")
                token = payload.get("nextPageToken")
                if "nextPageToken" in payload and (not paginated or not isinstance(token, str) or not token or len(token) > 4096 or token in seen):
                    raise ResourceError("invalid/repeated pagination token")
                items.extend(rows)
                ids.update(page_ids)
                result["pages"] += 1
                if token is None:
                    result["complete"] = True
                    return result
                seen.add(token)
                params["pageToken"] = token
            except ResourceError as exc:
                result["failure_reason"] = failure(exc)
                return result
        result["failure_reason"] = "page_limit"
        return result

    def groups(self, max_pages=100):
        self.identity()
        result = self.pages(f"{ANALYTICS}/groups", params={"mine": "true"}, max_pages=max_pages)
        for row in result["items"]:
            if not isinstance(row.get("snippet"), dict) or not isinstance(row["snippet"].get("title"), str) or not isinstance(row.get("contentDetails"), dict) or row["contentDetails"].get("itemType") not in {"youtube#video", "youtube#channel", "youtube#playlist", "youtubePartner#asset"}:
                raise ResourceError("group schema invalid")
        return result

    def group(self, group_id):
        rows = [r for r in complete(self.groups()) if r["id"] == opaque(group_id)]
        if len(rows) != 1:
            raise ResourceError("group not verified in authenticated mine membership")
        return rows[0]

    def group_items(self, group_id):
        self.group(group_id)
        result = self.pages(f"{ANALYTICS}/groupItems", params={"groupId": opaque(group_id)}, paginated=False)
        for row in result["items"]:
            if row.get("groupId") != group_id or not isinstance(row.get("resource"), dict):
                raise ResourceError("groupItems identity mismatch")
            opaque(row["resource"].get("id"))
        return result

    def bind_group_resource(self, group, rid):
        kind = group["contentDetails"]["itemType"]
        if kind == "youtube#video":
            return self.one("videos", resource_id(rid), parts="snippet")
        if kind == "youtube#channel" and rid == self.identity():
            return {"id": rid}
        if kind == "youtube#playlist":
            return self.one("playlists", resource_id(rid))
        raise ResourceError("resource not owned or content-owner asset unsupported")

    def query(self, query):
        query = ReportQuery.model_validate(query.model_dump())
        account = self.identity()
        for vid in query.video_ids:
            self.one("videos", vid, parts="snippet")
        for playlist in query.playlist_ids:
            self.one("playlists", playlist)
        if query.group_id:
            self.group(query.group_id)
        result = {"query": query.model_dump(mode="json"), "rows": [], "complete": False, "failure_reason": None,
            "provenance": provenance("youtubeAnalytics.reports.query", account), "pages": 0}
        result["provenance"]["dimensions"] = query.dimensions
        result["provenance"]["paid_organic_eligibility"] = query.audience_type or "report_metric_dependent_not_inferred"
        result["provenance"]["credential_observation"] = self.credential_observation
        params, seen = query.params(), set()
        for _ in range(query.max_pages):
            try:
                payload = self._json(self.service_request("GET", f"{ANALYTICS}/reports", params=params))
                rows = parse_query_response(query, payload)
                keys = [json.dumps(r["dimensions"], sort_keys=True) for r in rows]
                if len(keys) != len(set(keys)) or set(keys) & seen:
                    raise ResourceError("duplicate dimensions across pages")
                result["rows"].extend(rows)
                seen.update(keys)
                result["pages"] += 1
                if len(rows) < query.max_results:
                    result["complete"] = True
                    break
                params["startIndex"] += len(rows)
            except ResourceError as exc:
                result["failure_reason"] = failure(exc)
                break
        if not result["complete"] and result["failure_reason"] is None:
            result["failure_reason"] = "page_limit"
        if not result["rows"]:
            result["missing_metrics"] = {m: {"value": None, "missing_reason": result["failure_reason"] or "no_rows_or_suppressed"} for m in query.metrics}
        days = [r["dimensions"]["day"] for r in result["rows"] if "day" in r["dimensions"]]
        result["provenance"]["returned_date_coverage"] = {"first": min(days) if days else None, "last": max(days) if days else None,
            "observed_days": len(set(days)), "limitation": "pagination complete does not prove all requested dates available"}
        result["provenance"]["metric_definitions"] = {m: {"url": "https://developers.google.com/youtube/analytics/metrics#" + m,
            "aggregation": "official returned value; no local aggregation", "unit": {"estimatedMinutesWatched": "minutes", "estimatedRedMinutesWatched": "minutes",
                "averageViewDuration": "seconds", "averageViewPercentage": "percentage", "viewerPercentage": "percentage"}.get(m, "see exact provider metric definition")} for m in query.metrics}
        return result

    def compare(self, end_date, metrics=None, video_ids=None):
        result = {"source": "official_period_values_side_by_side", "windows": [], "causal_claim": False,
            "confounders": ["publication cadence", "audience mix", "paid traffic", "seasonality", "provider latency/suppression", "definition changes"],
            "aggregation": "none; no summed rates or unweighted means"}
        metrics = list(dict.fromkeys([*(metrics or ["views", "averageViewDuration"]), "views"]))
        for days in (7, 28):
            current = self.query(ReportQuery(start_date=end_date-timedelta(days=days-1), end_date=end_date, metrics=metrics, video_ids=video_ids or []))
            prior = self.query(ReportQuery(start_date=end_date-timedelta(days=2*days-1), end_date=end_date-timedelta(days=days), metrics=metrics, video_ids=video_ids or []))
            result["windows"].append({"days": days, "current": current, "previous": prior,
                "sample_size": {"unit": "provider views, not unique viewers", "current": self._sample(current), "previous": self._sample(prior)}})
        return result

    @staticmethod
    def _sample(result):
        return result["rows"][0]["metrics"].get("views") if result["complete"] and len(result["rows"]) == 1 else {"value": None, "missing_reason": "incomplete_or_unavailable"}

    def batch_stats(self, video_ids, parts=None):
        parts = parts or ["id", "statistics"]
        if not isinstance(video_ids, list) or not 1 <= len(video_ids) <= 50 or len(set(video_ids)) != len(video_ids) or not isinstance(parts, list) or not parts or set(parts)-{"id", "snippet", "statistics", "contentDetails"}:
            raise ResourceError("batch requires 1..50 distinct owned IDs and documented parts (local bound)")
        account = self.identity()
        for vid in video_ids:
            self.one("videos", resource_id(vid), parts="snippet")
        payload = self._json(self.service_request("GET", f"{API}/videos:batchGetStats", params={"id": ",".join(video_ids), "part": ",".join(parts)}))
        rows, summary = payload.get("items"), payload.get("summary")
        has_summary = "summary" in payload
        if not isinstance(rows, list) or has_summary and not isinstance(summary, dict):
            raise ResourceError("batch stats invalid envelope")
        ids = [r.get("id") if isinstance(r, dict) else None for r in rows]
        if any(not isinstance(v, str) for v in ids) or len(set(ids)) != len(ids) or set(ids)-set(video_ids):
            raise ResourceError("batch stats returned duplicate, missing or unexpected identity")
        # Actual responses can omit the documented summary entirely. Returned
        # exact IDs still establish their own values; absence of another ID does
        # not establish provider failure. Explicit null/malformed summaries fail.
        failed = summary.get("failedVideoIds", []) if has_summary else []
        if has_summary and (not isinstance(failed, list) or any(not isinstance(v, str) for v in failed)
            or len(set(ids+failed)) != len(ids+failed) or set(ids+failed) != set(video_ids)
            or any(unsigned_long(summary.get(k)) != n for k, n in {"requestedVideoCount": len(video_ids), "succeededVideoCount": len(rows), "failedVideoCount": len(failed)}.items())):
            raise ResourceError("batch stats exact success/failure IDs or counts mismatch")
        unaccounted = [v for v in video_ids if v not in set(ids+failed)]
        p = provenance("youtube.videos.batchGetStats", account)
        p.update({"credential_observation": self.credential_observation, "provider_summary_present": has_summary,
            "partition_provenance": "returned ID membership checked locally; missing IDs without summary have unknown provider outcomes",
            "period": "cumulative_snapshot_not_Analytics", "definitions_url": "https://developers.google.com/youtube/v3/docs/videos#statistics.viewCount",
            "viewCount_definition_effective": "2026-08-24", "viewCount_definition": "playback-start counting across formats per resource docs; do not apply to Analytics"})
        outcomes = {v: {"value": None, "missing_reason": "provider_failed_id"} for v in failed}
        outcomes.update({v: {"value": None, "missing_reason": "provider_outcome_unaccounted"} for v in unaccounted})
        for row in rows:
            item = {"value": row, "missing_reason": None}
            if "statistics" in parts:
                stats = row.get("statistics", {})
                if not isinstance(stats, dict):
                    raise ResourceError("batch statistics schema invalid")
                item["statistics"] = {}
                for name in ("viewCount", "likeCount", "commentCount"):
                    value = stats.get(name)
                    item["statistics"][name] = metric_value(unsigned_long(value) if value is not None else None, "provider_field_absent")
            outcomes[row["id"]] = item
        return {"outcomes": outcomes, "summary": summary, "complete": not unaccounted,
            "provider_summary": {"value": summary, "missing_reason": None if has_summary else "provider_summary_omitted"},
            "observed_partition": {"source": "local_exact_id_validation", "returned_ids": ids,
                "provider_failed_ids": failed if has_summary else None, "unaccounted_ids": unaccounted}, "provenance": p}
