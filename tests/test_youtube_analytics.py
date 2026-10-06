from datetime import date

import pytest

from socialctl.metricas.analytics_schema import ReportQuery, period_bounds, parse_query_response
from socialctl.management.youtube_resources import ResourceError
import httpx
from tests.test_youtube_management import _brand, _snippet


def service(tmp_path, handler):
    from socialctl.metricas.analytics_client import AnalyticsClient
    brand = _brand(tmp_path, name="SyntheticAnalytics")
    requests = []
    def route(request):
        requests.append(request)
        if request.url.path == "/youtube/v3/channels":
            return httpx.Response(200, json={"items": [{"id": "channel-a"}], "pageInfo": {"totalResults": 1, "resultsPerPage": 50}})
        if request.url.path == "/youtube/v3/videos":
            return httpx.Response(200, json={"items": [{"id": request.url.params["id"], "etag": "e", "snippet": _snippet()}], "pageInfo": {"totalResults": 1, "resultsPerPage": 50}})
        return handler(request)
    return brand, AnalyticsClient(brand, httpx.Client(transport=httpx.MockTransport(route))), requests


def test_daily_query_provenance_and_partial_rate_limit(tmp_path):
    def handler(request):
        assert request.url.host == "youtubeanalytics.googleapis.com"
        if request.url.params["startIndex"] == "2":
            return httpx.Response(429)
        return httpx.Response(200, json={"columnHeaders": [{"name": "day", "columnType": "DIMENSION", "dataType": "STRING"}, {"name": "views", "columnType": "METRIC", "dataType": "INTEGER"}], "rows": [["2026-01-01", 3]]})
    _, client, requests = service(tmp_path, handler)
    result = client.query(ReportQuery(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2), dimensions=["day"], max_results=1))
    assert not result["complete"] and result["failure_reason"] == "rate_limited"
    assert result["rows"][0]["metrics"]["views"]["value"] == 3
    assert result["provenance"]["provider_timezone"] == "America/Los_Angeles"
    assert all(r.method == "GET" for r in requests)


def test_batch_stats_exact_partial_ids_and_ownership(tmp_path):
    payload = {"items": [{"id": "v1", "statistics": {"viewCount": "10"}}], "summary": {"requestedVideoCount": 2, "succeededVideoCount": 1, "failedVideoCount": 1, "failedVideoIds": ["v2"]}}
    _, client, requests = service(tmp_path, lambda r: httpx.Response(200, json=payload))
    result = client.batch_stats(["v1", "v2"])
    assert result["outcomes"]["v2"] == {"value": None, "missing_reason": "provider_failed_id"}
    assert any(r.url.path.endswith("videos:batchGetStats") for r in requests)
    payload["summary"]["failedVideoIds"] = ["unexpected"]
    with pytest.raises(ResourceError):
        client.batch_stats(["v1", "v2"])


def test_expired_credentials_never_refresh_and_fixed_hosts(tmp_path):
    from socialctl.models import Platform
    brand, client, requests = service(tmp_path, lambda r: httpx.Response(200, json={}))
    brand.guardar_secreto(Platform.YOUTUBE, {"access_token": "synthetic", "refresh_token": "synthetic", "expira_en": 1})
    with pytest.raises(ResourceError, match="expired"):
        client.groups()
    assert not requests
    with pytest.raises(ResourceError, match="endpoint"):
        client.service_request("GET", "https://evil.invalid/v2/groups")


def test_group_ownership_requires_complete_mine_listing(tmp_path):
    _, client, _ = service(tmp_path, lambda r: httpx.Response(200, json={"items": [], "nextPageToken": "same"}))
    with pytest.raises(ResourceError, match="incomplete"):
        client.group("opaque.group:ID")


def test_malformed_analytics_envelope_cannot_prove_absence(tmp_path):
    _, client, _ = service(tmp_path, lambda r: httpx.Response(200, json={}))
    assert not client.groups()["complete"]


def test_period_comparison_uses_four_official_totals_without_aggregation(tmp_path):
    values = iter([(100, 2.5), (90, 9.5), (300, 4.1), (200, 1.8)])
    def handler(request):
        assert "dimensions" not in request.url.params
        views, duration = next(values)
        return httpx.Response(200, json={"columnHeaders": [{"name": "views", "columnType": "METRIC", "dataType": "INTEGER"}, {"name": "averageViewDuration", "columnType": "METRIC", "dataType": "FLOAT"}], "rows": [[views, duration]]})
    _, client, requests = service(tmp_path, handler)
    result = client.compare(date(2026, 1, 31))
    assert [w["days"] for w in result["windows"]] == [7, 28]
    assert result["windows"][0]["sample_size"]["current"]["value"] == 100
    assert result["windows"][0]["previous"]["rows"][0]["metrics"]["averageViewDuration"]["value"] == 9.5
    assert "score" not in result and result["causal_claim"] is False
    assert len([r for r in requests if r.url.path == "/v2/reports"]) == 4


def test_known_families_filters_and_provider_combination_failure(tmp_path):
    query = ReportQuery(start_date=date(2026, 1, 1), end_date=date(2026, 1, 31), dimensions=["deviceType", "operatingSystem"], metrics=["engagedViews"], filters={"country": "US"})
    assert query.params()["filters"] == "country==US"
    _, client, _ = service(tmp_path, lambda r: httpx.Response(400))
    result = client.query(query)
    assert result["failure_reason"] == "provider_rejected_report_combination"
    assert result["missing_metrics"]["engagedViews"]["value"] is None


def test_redirects_and_response_limit_never_follow_or_retry(tmp_path):
    _, client, requests = service(tmp_path, lambda r: httpx.Response(302, headers={"Location": "https://evil.invalid/"}))
    with pytest.raises(ResourceError):
        client.service_request("GET", "https://youtubeanalytics.googleapis.com/v2/groups")
    assert len(requests) == 1 and requests[0].url.host != "evil.invalid"
    _, oversized, _ = service(tmp_path / "other", lambda r: httpx.Response(200, content=b"x"*10))
    with pytest.raises(ResourceError, match="limit"):
        oversized.service_request("GET", "https://youtubeanalytics.googleapis.com/v2/groups", max_bytes=9)


def test_query_daily_period_and_pacific_dst():
    q = ReportQuery(start_date=date(2026, 3, 8), end_date=date(2026, 3, 8), dimensions=["day"], metrics=["views", "averageViewDuration"])
    assert q.params()["ids"] == "channel==MINE"
    start, end = period_bounds(q.start_date, q.end_date)
    assert (end - start).total_seconds() == 23 * 3600
    start, end = period_bounds(date(2026, 11, 1), date(2026, 11, 1))
    assert (end - start).total_seconds() == 25 * 3600


def test_query_rejects_unknown_parameters_and_incompatible_family():
    with pytest.raises(ValueError):
        ReportQuery(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2), endpoint="https://evil.invalid")
    with pytest.raises(ValueError):
        ReportQuery(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2), dimensions=["ageGroup"], metrics=["views"])


def test_missing_values_remain_null_and_schema_change_fails():
    q = ReportQuery(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2), metrics=["views"])
    payload = {"columnHeaders": [{"name": "views", "columnType": "METRIC", "dataType": "INTEGER"}], "rows": [[None]]}
    rows = parse_query_response(q, payload)
    assert rows[0]["metrics"]["views"] == {"value": None, "missing_reason": "provider_null"}
    payload["columnHeaders"][0]["name"] = "newViews"
    with pytest.raises(ResourceError, match="schema"):
        parse_query_response(q, payload)


def test_integer_schema_change_and_missing_batch_fields(tmp_path):
    q = ReportQuery(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2))
    with pytest.raises(ResourceError, match="schema"):
        parse_query_response(q, {"columnHeaders": [{"name": "views", "columnType": "METRIC", "dataType": "INTEGER"}], "rows": [[1.5]]})
    payload = {"items": [{"id": "v1", "statistics": {"viewCount": "4"}}], "summary": {"requestedVideoCount": 1, "succeededVideoCount": 1, "failedVideoCount": 0}}
    _, client, _ = service(tmp_path, lambda r: httpx.Response(200, json=payload))
    result = client.batch_stats(["v1"])
    assert result["outcomes"]["v1"]["statistics"]["likeCount"] == {"value": None, "missing_reason": "provider_field_absent"}


def test_batch_unsigned_long_wire_encodings_and_no_float_coercion(tmp_path):
    payload = {"items": [{"id": "v1", "statistics": {"viewCount": 4}}], "summary": {"requestedVideoCount": "1", "succeededVideoCount": "1", "failedVideoCount": "0", "failedVideoIds": []}}
    _, client, _ = service(tmp_path, lambda r: httpx.Response(200, json=payload))
    result = client.batch_stats(["v1"])
    assert result["outcomes"]["v1"]["statistics"]["viewCount"]["value"] == 4
    assert result["summary"]["requestedVideoCount"] == "1"
    for invalid in (1.0, True, -1, "18446744073709551616"):
        payload["items"][0]["statistics"]["viewCount"] = invalid
        with pytest.raises(ResourceError):
            client.batch_stats(["v1"])


@pytest.mark.parametrize("status,headers", [(206, {}), (200, {"Content-Length": "9999"}), (200, {"Content-Encoding": "gzip"})])
def test_batch_stats_uses_strict_complete_response_transport(tmp_path, status, headers):
    import gzip
    import json
    payload = {"items": [{"id": "v1", "statistics": {"viewCount": "5"}}], "summary": {"requestedVideoCount": 1, "succeededVideoCount": 1, "failedVideoCount": 0}}
    raw = json.dumps(payload).encode()
    if headers.get("Content-Encoding"):
        raw = gzip.compress(raw)
    _, client, requests = service(tmp_path, lambda r: httpx.Response(status, headers=headers, content=raw))
    with pytest.raises(ResourceError):
        client.batch_stats(["v1"])
    assert len([r for r in requests if r.url.path.endswith("videos:batchGetStats")]) == 1


def test_batch_stats_keeps_sanitized_actual_credential_observation(tmp_path):
    from socialctl.models import Platform
    payload = {"items": [{"id": "v1"}], "summary": {"requestedVideoCount": 1, "succeededVideoCount": 1, "failedVideoCount": 0}}
    brand, client, _ = service(tmp_path, lambda r: httpx.Response(200, json=payload))
    brand.guardar_secreto(Platform.YOUTUBE, {"access_token": "synthetic-private-token", "refresh_token": "synthetic-refresh", "granted_scopes": ["https://www.googleapis.com/auth/youtube.readonly"], "expira_en": 4102444800})
    result = client.batch_stats(["v1"])
    credential = result["provenance"]["credential_observation"]
    assert credential["granted_scopes"]["values"] == ["https://www.googleapis.com/auth/youtube.readonly"]
    assert credential["expiry"]["unix_seconds"] == 4102444800
    assert "synthetic-private-token" not in str(result) and "synthetic-refresh" not in str(result)


def test_real_shaped_batch_without_summary_preserves_exact_owned_results(tmp_path):
    payload = {"kind": "youtube#batchGetStatsResponse", "etag": "synthetic-etag", "items": [
        {"kind": "youtube#videoStats", "etag": "v1-etag", "id": "v1", "statistics": {"viewCount": "5", "likeCount": "2", "commentCount": "1"}},
        {"kind": "youtube#videoStats", "etag": "v2-etag", "id": "v2", "statistics": {"viewCount": "8", "likeCount": "3", "commentCount": "0"}},
    ]}
    _, client, requests = service(tmp_path, lambda r: httpx.Response(200, json=payload))
    result = client.batch_stats(["v1", "v2"])
    assert result["complete"] is True
    assert result["summary"] is None
    assert result["provider_summary"] == {"value": None, "missing_reason": "provider_summary_omitted"}
    assert result["observed_partition"] == {"source": "local_exact_id_validation", "returned_ids": ["v1", "v2"], "provider_failed_ids": None, "unaccounted_ids": []}
    assert result["outcomes"]["v1"]["value"] == payload["items"][0]
    assert result["outcomes"]["v2"]["statistics"]["viewCount"]["value"] == 8
    assert result["provenance"]["provider_summary_present"] is False
    assert len([r for r in requests if r.url.path == "/youtube/v3/videos"]) == 2


@pytest.mark.parametrize("returned", [["v1"], []])
def test_no_summary_missing_ids_are_unknown_partial_not_provider_failures(tmp_path, returned):
    payload = {"kind": "youtube#batchGetStatsResponse", "etag": "synthetic", "items": [{"id": v, "statistics": {"viewCount": "5"}} for v in returned]}
    _, client, _ = service(tmp_path, lambda r: httpx.Response(200, json=payload))
    result = client.batch_stats(["v1", "v2"])
    assert result["complete"] is False
    assert result["observed_partition"]["provider_failed_ids"] is None
    assert result["summary"] is None
    for vid in {"v1", "v2"} - set(returned):
        assert result["outcomes"][vid] == {"value": None, "missing_reason": "provider_outcome_unaccounted"}
    if returned:
        assert result["outcomes"]["v1"]["statistics"]["viewCount"]["value"] == 5


@pytest.mark.parametrize("items", [[{"id": "v1"}, {"id": "v1"}], [{"id": "wrong-id"}], [{"statistics": {"viewCount": "1"}}]])
def test_no_summary_duplicate_wrong_or_missing_returned_identity_fails(tmp_path, items):
    _, client, _ = service(tmp_path, lambda r: httpx.Response(200, json={"kind": "youtube#batchGetStatsResponse", "etag": "synthetic", "items": items}))
    with pytest.raises(ResourceError):
        client.batch_stats(["v1", "v2"])


@pytest.mark.parametrize("summary", [None, {}, {"requestedVideoCount": 2, "succeededVideoCount": 0, "failedVideoCount": 1, "failedVideoIds": ["v2"]}])
def test_present_summary_must_still_match_strict_provider_contract(tmp_path, summary):
    _, client, _ = service(tmp_path, lambda r: httpx.Response(200, json={"items": [{"id": "v1"}], "summary": summary}))
    with pytest.raises(ResourceError):
        client.batch_stats(["v1", "v2"])
