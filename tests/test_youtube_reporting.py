import json
import os

import httpx
import pytest

from socialctl.management.youtube_resources import ResourceError
from tests.test_youtube_analytics import service


def report(rid="report1", created="2026-01-03T12:00:00Z"):
    return {"id": rid, "jobId": "job1", "startTime": "2026-01-01T08:00:00Z", "endTime": "2026-01-02T08:00:00Z", "createTime": created,
        "downloadUrl": "https://youtubereporting.googleapis.com/v1/media/resource_1?alt=media"}


def binding(rid="report1", created="2026-01-03T12:00:00Z"):
    return {"source": "youtubeReporting", "account_id": "channel-a", "report_type_id": "channel_basic_a3", "job_id": "job1", "report": report(rid, created)}


def test_import_duplicate_backfill_replaces_and_keeps_raw_unknown(tmp_path):
    from socialctl.metricas.analytics_ingest import ObservationStore
    store = ObservationStore(tmp_path, "channel-a")
    data = b"date,channel_id,video_id,views,average_view_duration_seconds,new_column\n20260101,channel-a,,10,3.5,opaque\n"
    first = store.import_csv(data, binding())
    duplicate = store.import_csv(data, binding())
    assert first["id"] == duplicate["id"]
    newer = store.import_csv(data.replace(b",10,", b",11,"), binding("report2", "2026-01-04T12:00:00Z"))
    assert store.active()[0]["id"] == newer["id"] and len(store.active()) == 1
    assert len(store.index()["imports"]) == 2
    assert newer["rows"][0]["dimensions"]["video_id"] == ""
    assert newer["rows"][0]["unknown"]["new_column"] == {"raw": "opaque", "missing_reason": "unknown_column_semantics"}
    assert all(os.stat(p).st_mode & 0o077 == 0 for p in store.root.iterdir() if p.is_file())


def test_partial_import_conflicting_id_account_and_schema_fail(tmp_path, monkeypatch):
    from socialctl.metricas.analytics_ingest import ObservationStore
    store = ObservationStore(tmp_path, "channel-a")
    data = b"date,channel_id,views\n20260101,channel-a,10\n"
    original = store._commit_index
    monkeypatch.setattr(store, "_commit_index", lambda _: (_ for _ in ()).throw(OSError("synthetic crash")))
    with pytest.raises(OSError):
        store.import_csv(data, binding())
    assert not store.active()
    monkeypatch.setattr(store, "_commit_index", original)
    store.import_csv(data, binding())
    with pytest.raises(ResourceError, match="same report"):
        store.import_csv(data.replace(b",10", b",12"), binding())
    for bad in (data.replace(b"channel-a,10", b"other,10"), b"date,views,views\n20260101,1,2\n", b"date,views\n20260101,NaN\n", b"date,views\n20260101\n"):
        with pytest.raises(ResourceError):
            store.import_csv(bad, binding("bad"))
    mismatch = binding("mismatch")
    mismatch["account_id"] = "other"
    with pytest.raises(ResourceError, match="account"):
        store.import_csv(data, mismatch)


def test_header_only_and_studio_separate_observations(tmp_path):
    from socialctl.metricas.analytics_ingest import ObservationStore
    store = ObservationStore(tmp_path, "channel-a")
    assert store.import_csv(b"date,views\n", binding())["rows"] == []
    supplied = {"source": "youtubeStudio", "account_id": "channel-a", "export_id": "export1", "start_date": "2026-01-01", "end_date": "2026-01-01", "exported_at": "2026-01-03T12:00:00Z", "source_filename": "supplied.csv"}
    studio = store.import_csv(b"Date,Views\n2026-01-01,12\n", supplied)
    assert len(store.active()) == 2
    assert studio["provenance"]["account_binding"] == "user_supplied_not_API_verified"
    assert studio["rows"][0]["unknown"]["Views"]["raw"] == "12"


def test_reporting_download_bound_host_dates_no_redirects(tmp_path):
    from socialctl.metricas.reporting_client import ReportingClient
    current = report()
    def handler(request):
        if request.url.path == "/v1/jobs":
            return httpx.Response(200, json={"jobs": [{"id": "job1", "reportTypeId": "channel_basic_a3", "name": "Explicit"}]})
        if request.url.path == "/v1/reportTypes":
            return httpx.Response(200, json={"reportTypes": [{"id": "channel_basic_a3", "name": "Basic"}]})
        if "/reports/" in request.url.path:
            return httpx.Response(200, json=current)
        if "/media/" in request.url.path:
            return httpx.Response(200, content=b"date,views\n20260101,1\n")
        return httpx.Response(200, json={"id": "job1", "reportTypeId": "channel_basic_a3", "name": "Explicit"})
    brand, base, requests = service(tmp_path, handler)
    client = ReportingClient(brand, base.client)
    expected = {"job_id": "job1", "report_type_id": "channel_basic_a3", "report_id": "report1", "start_date": "2026-01-01", "end_date": "2026-01-01"}
    metadata, data = client.download(**expected)
    assert data.endswith(b",1\n") and metadata["account_id"] == "channel-a"
    assert metadata["credential_observation"] == client.credential_observation
    assert metadata["credential_observation"]["granted_scopes"]["state"] == "unknown"
    from socialctl.metricas.analytics_ingest import ObservationStore
    store = ObservationStore(brand.raiz, "channel-a")
    saved = store.import_csv(data, metadata)
    assert store.active()[0]["provenance"]["credential_observation"] == client.credential_observation
    assert saved["provenance"]["credential_observation"]["expiry"]["state"] == "unknown"
    for url in ("https://evil.invalid/v1/media/x?alt=media", "https://youtubereporting.googleapis.com/v1/media/../jobs?alt=media", "https://youtubereporting.googleapis.com/v1/media/x?alt=media&token=x"):
        current["downloadUrl"] = url
        count = len([r for r in requests if "/media/" in r.url.path])
        with pytest.raises(ResourceError):
            client.download(**expected)
        assert len([r for r in requests if "/media/" in r.url.path]) == count
    assert all(r.method == "GET" for r in requests)


def test_reporting_stream_cut_redirect_and_limits_leave_no_checkpoint(tmp_path):
    from socialctl.metricas.reporting_client import ReportingClient
    from socialctl.metricas.analytics_ingest import ObservationStore
    class Cut(httpx.SyncByteStream):
        def __iter__(self):
            yield b"date,views\n20260101,"
            raise httpx.ReadError("synthetic cut")
    brand, base, requests = service(tmp_path, lambda r: httpx.Response(200, stream=Cut()))
    client = ReportingClient(brand, base.client)
    with pytest.raises(ResourceError, match="interrupted"):
        client._bounded_request("GET", report()["downloadUrl"], max_bytes=1024)
    assert not ObservationStore(brand.raiz, "channel-a").index()["imports"]
    assert len(requests) == 1


def test_schema_versions_preserved_and_old_backfill_not_reactivated(tmp_path):
    from socialctl.metricas.analytics_ingest import ObservationStore
    store = ObservationStore(tmp_path, "channel-a")
    old = store.import_csv(b"date,views\n20260101,1\n", binding())
    new = store.import_csv(b"date,views,new_dimension\n20260101,2,X\n20260101,3,Y\n", binding("new", "2026-01-05T00:00:00Z"))
    store.import_csv(b"date,views\n20260101,4\n", binding("older", "2026-01-04T00:00:00Z"))
    assert store.active()[0]["id"] == new["id"]
    assert old["headers"] != new["headers"] and len(store.index()["imports"]) == 3
    assert new["rows"][0]["unknown"]["new_dimension"]["missing_reason"] == "unknown_column_semantics"


def test_store_rejects_symlink_csv_and_checkpoint(tmp_path):
    from socialctl.metricas.analytics_ingest import ObservationStore, read_supplied
    original = tmp_path / "original.csv"
    original.write_bytes(b"date,views\n")
    linked = tmp_path / "linked.csv"
    linked.symlink_to(original)
    with pytest.raises(ResourceError):
        read_supplied(linked)
    store = ObservationStore(tmp_path, "channel-a")
    store._ensure_root()
    (store.root / "index.json").symlink_to(original)
    with pytest.raises(ResourceError):
        store.index()


def test_reporting_empty_ids_and_missing_metrics_are_not_zero(tmp_path):
    from socialctl.metricas.analytics_ingest import ObservationStore
    store = ObservationStore(tmp_path, "channel-a")
    result = store.import_csv(b"date,channel_id,video_id,views,subscribers_gained,video_thumbnail_impressions_ctr\n20260101,,,,3,\n", binding())
    row = result["rows"][0]
    assert row["dimensions"]["channel_id"] == row["dimensions"]["video_id"] == ""
    assert row["metrics"]["views"]["value"] is None and row["metrics"]["subscribers_gained"]["value"] == 3
    assert row["metrics"]["video_thumbnail_impressions_ctr"]["missing_reason"] == "empty_csv_cell_not_zero"


@pytest.mark.parametrize("status,headers", [(206, {}), (200, {"Content-Length": "999"}), (200, {"Content-Encoding": "gzip"})])
def test_partial_http_response_not_a_complete_report(tmp_path, status, headers):
    _, client, _ = service(tmp_path, lambda r: httpx.Response(status, headers=headers, content=b"date,views\n"))
    with pytest.raises(ResourceError):
        client._bounded_request("GET", report()["downloadUrl"], max_bytes=1024)


def test_studio_identical_rows_preserved_in_source_order_and_idempotent(tmp_path):
    from socialctl.metricas.analytics_ingest import ObservationStore
    store = ObservationStore(tmp_path, "channel-a")
    supplied = {"source": "youtubeStudio", "account_id": "channel-a", "export_id": "two-rows", "start_date": "2026-01-01", "end_date": "2026-01-01", "exported_at": "2026-01-03T12:00:00Z", "source_filename": "supplied.csv"}
    data = b"Title,Views\nSame,4\nSame,4\nLast,1\n"
    first = store.import_csv(data, supplied)
    assert [r["raw"] for r in first["rows"]] == [{"Title": "Same", "Views": "4"}, {"Title": "Same", "Views": "4"}, {"Title": "Last", "Views": "1"}]
    assert len(store.import_csv(data, supplied)["rows"]) == 3
    assert len(store.index()["imports"]) == 1


def test_import_credential_unknown_fallback_and_actual_record_persisted(tmp_path):
    from socialctl.identity import credential_metadata
    from socialctl.metricas.analytics_ingest import ObservationStore
    store = ObservationStore(tmp_path, "channel-a")
    absent = store.import_csv(b"date,views\n", binding())
    assert absent["provenance"]["credential_observation"] == credential_metadata({})
    actual = binding("credential-report", "2026-01-05T00:00:00Z")
    actual["credential_observation"] = credential_metadata({"granted_scopes": ["https://www.googleapis.com/auth/yt-analytics.readonly"], "expira_en": 4102444800})
    saved = store.import_csv(b"date,views\n20260101,1\n", actual)
    assert store.active()[0]["provenance"]["credential_observation"] == actual["credential_observation"]
    assert saved["provenance"]["credential_observation"]["granted_scopes"]["state"] == "known"
    original = saved["provenance"]["credential_observation"]
    actual["credential_observation"] = credential_metadata({"granted_scopes": ["https://www.googleapis.com/auth/youtube"], "expira_en": 4102444900})
    repeated = store.import_csv(b"date,views\n20260101,1\n", actual)
    assert repeated["id"] == saved["id"] and repeated["provenance"]["credential_observation"] == original
    assert len(store.index()["imports"]) == 2
    assert "credential_observation" not in repeated["binding"]


def test_unknown_reporting_columns_do_not_establish_complete_primary_key(tmp_path):
    from socialctl.metricas.analytics_ingest import ObservationStore
    store = ObservationStore(tmp_path, "channel-a")
    data = b"date,views,new_segment\n20260101,2,A\n20260101,2,B\n20260101,2,B\n"
    result = store.import_csv(data, binding())
    assert [r["raw"]["new_segment"] for r in result["rows"]] == ["A", "B", "B"]
    with pytest.raises(ResourceError):
        store.import_csv(b"date,views\n20260101,2\n20260101,3\n", binding("known-duplicate"))
