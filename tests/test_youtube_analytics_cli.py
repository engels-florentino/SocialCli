import json

import httpx
from typer.testing import CliRunner

from socialctl.cli import app
from tests.test_youtube_analytics_changes import setup_service

runner = CliRunner()


def test_cli_full_preview_brand_exact_approval_no_yes(tmp_path, monkeypatch):
    brand, client, store, api, requests = setup_service(tmp_path)
    monkeypatch.setattr("socialctl.metricas.analytics_cli.make_http_client", lambda: httpx.Client(transport=client.client._transport))
    file = tmp_path / "edit.json"
    file.write_text(json.dumps({"version": 1, "action": "group-update", "group_id": "group.opaque:1", "title": "Exact approved title"}))
    base = ["content", "youtube-analytics"]
    target = ["--brand", brand.nombre, "--root", str(tmp_path)]
    missing = runner.invoke(app, [*base, "prepare", "--file", str(file)])
    assert missing.exit_code != 0 and not requests
    preview = runner.invoke(app, [*base, "prepare", "--file", str(file), "--dry-run", *target])
    assert preview.exit_code == 0, preview.output
    change = store.load(next(store.root.glob("*.json")).stem)
    assert json.dumps(change.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) in preview.output
    assert runner.invoke(app, [*base, "apply", change.id, *target], input="yes\n").exit_code != 0
    assert runner.invoke(app, [*base, "apply", change.id, "--yes", *target]).exit_code != 0
    assert not api.writes
    accepted = runner.invoke(app, [*base, "apply", change.id, *target], input=change.fingerprint+"\n")
    assert accepted.exit_code == 0, accepted.output
    assert accepted.output.index("Exact approved title") < accepted.output.index('Enter the exact fingerprint')
    assert len(api.writes) == 1


def test_cli_named_reads_no_jobs_created(tmp_path, monkeypatch):
    brand, client, _, api, _ = setup_service(tmp_path)
    monkeypatch.setattr("socialctl.metricas.analytics_cli.make_http_client", lambda: httpx.Client(transport=client.client._transport))
    for cmd in (["groups-list"], ["group-show", "group.opaque:1"], ["group-items-list", "group.opaque:1"], ["report-types-list"], ["jobs-list"]):
        result = runner.invoke(app, ["content", "youtube-analytics", *cmd, "--brand", brand.nombre, "--root", str(tmp_path)])
        assert result.exit_code == 0, result.output
    assert not api.writes


def test_query_comparison_reporting_and_studio_commands(tmp_path, monkeypatch):
    from tests.test_youtube_analytics import service
    from tests.test_youtube_reporting import report
    from socialctl.metricas.analytics_ingest import ObservationStore
    def handler(request):
        path, params = request.url.path, request.url.params
        if path == "/v2/reports":
            dimensions = params.get("dimensions", "").split(",") if params.get("dimensions") else []
            metrics = params["metrics"].split(",")
            return httpx.Response(200, json={"columnHeaders": [{"name": d, "columnType": "DIMENSION", "dataType": "STRING"} for d in dimensions]+[{"name": m, "columnType": "METRIC", "dataType": "INTEGER"} for m in metrics], "rows": [[params["startDate"] for d in dimensions]+[8 for m in metrics]]})
        if path == "/v1/jobs":
            return httpx.Response(200, json={"jobs": [{"id": "job1", "name": "Explicit", "reportTypeId": "channel_basic_a3"}]})
        if path == "/v1/jobs/job1":
            return httpx.Response(200, json={"id": "job1", "name": "Explicit", "reportTypeId": "channel_basic_a3"})
        if path == "/v1/reportTypes":
            return httpx.Response(200, json={"reportTypes": [{"id": "channel_basic_a3", "name": "Basic"}]})
        if path.endswith("/reports"):
            return httpx.Response(200, json={"reports": [report()]})
        if path.endswith("/reports/report1"):
            return httpx.Response(200, json=report())
        return httpx.Response(200, content=b"date,views\n20260101,9\n")
    brand, client, requests = service(tmp_path, handler)
    monkeypatch.setattr("socialctl.metricas.analytics_cli.make_http_client", lambda: httpx.Client(transport=client.client._transport))
    query = tmp_path / "query.yml"
    query.write_text("start_date: 2026-01-01\nend_date: 2026-01-02\ndimensions: [day]\nmetrics: [views]\n")
    target = ["--brand", brand.nombre, "--root", str(tmp_path)]
    for command in (["query", "--file", str(query)], ["compare", "--end", "2026-01-31"],
        ["job-show", "job1", "--report-type-id", "channel_basic_a3"],
        ["reports-list", "job1", "--report-type-id", "channel_basic_a3"],
        ["report-show", "job1", "report1", "--report-type-id", "channel_basic_a3"],
        ["report-download", "job1", "report1", "--report-type-id", "channel_basic_a3", "--start", "2026-01-01", "--end", "2026-01-01"],
        ["observations"]):
        result = runner.invoke(app, ["content", "youtube-analytics", *command, *target])
        assert result.exit_code == 0, result.output
    store = ObservationStore(brand.raiz, "channel-a")
    assert len(store.active()) == 1 and len(list(store.root.glob("*.query.json"))) == 5
    original = tmp_path / "studio.csv"
    original.write_bytes(b"Views\n8\n")
    metadata = tmp_path / "studio.json"
    metadata.write_text(json.dumps({"source": "youtubeStudio", "account_id": "channel-a", "export_id": "export", "start_date": "2026-01-01", "end_date": "2026-01-01", "exported_at": "2026-01-04T00:00:00Z", "source_filename": "studio.csv"}))
    result = runner.invoke(app, ["content", "youtube-analytics", "studio-import", "--file", str(original), "--metadata", str(metadata), *target])
    assert result.exit_code == 0, result.output
    assert len(store.active()) == 2 and original.read_bytes() == b"Views\n8\n"
    assert all(r.method == "GET" for r in requests)


def test_studio_import_normal_yaml_dates_timestamp_and_identical_rows(tmp_path):
    from tests.test_youtube_management import _brand
    from socialctl.metricas.analytics_ingest import ObservationStore
    brand = _brand(tmp_path, name="SyntheticStudioYAML")
    csv = tmp_path / "studio.csv"
    csv.write_bytes(b"Title,Views\nRepeated,7\nRepeated,7\n")
    metadata = tmp_path / "studio.yml"
    metadata.write_text("source: youtubeStudio\naccount_id: channel-a\nexport_id: export-yaml\nstart_date: 2026-01-01\nend_date: 2026-01-02\nexported_at: 2026-01-03T12:00:00Z\nsource_filename: studio.csv\n")
    args = ["content", "youtube-analytics", "studio-import", "--brand", brand.nombre, "--root", str(tmp_path), "--file", str(csv), "--metadata", str(metadata)]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    store = ObservationStore(brand.raiz, "channel-a")
    observation = store.active()[0]
    assert observation["binding"]["start_date"] == "2026-01-01" and isinstance(observation["binding"]["exported_at"], str)
    assert len(observation["rows"]) == 2
    assert runner.invoke(app, args).exit_code == 0
    assert len(store.index()["imports"]) == 1
    assert csv.read_bytes() == b"Title,Views\nRepeated,7\nRepeated,7\n"


def test_cli_no_summary_batch_partial_is_explicit_and_nonzero_exit(tmp_path, monkeypatch):
    from tests.test_youtube_analytics import service
    payload = {"kind": "youtube#batchGetStatsResponse", "etag": "synthetic", "items": [{"id": "v1", "statistics": {"viewCount": "5"}}]}
    brand, client, _ = service(tmp_path, lambda r: httpx.Response(200, json=payload))
    monkeypatch.setattr("socialctl.metricas.analytics_cli.make_http_client", lambda: httpx.Client(transport=client.client._transport))
    args = ["content", "youtube-analytics", "batch-stats", "--brand", brand.nombre, "--root", str(tmp_path), "--video-id", "v1"]
    complete = runner.invoke(app, args)
    assert complete.exit_code == 0, complete.output
    assert json.loads(complete.output)["summary"] is None
    partial = runner.invoke(app, [*args, "--video-id", "v2"])
    assert partial.exit_code == 1
    observed = json.loads(partial.output)
    assert observed["outcomes"]["v1"]["statistics"]["viewCount"]["value"] == 5
    assert observed["outcomes"]["v2"]["missing_reason"] == "provider_outcome_unaccounted"
