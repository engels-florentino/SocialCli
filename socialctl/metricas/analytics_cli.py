"""Named Analytics/Reporting commands; reads never create jobs or refresh OAuth."""
from datetime import date
import json
from pathlib import Path

import httpx
import typer
import yaml

from socialctl.metricas.analytics_changes import AnalyticsStore, prepare as prepare_change, apply as apply_change, reconcile as reconcile_change
from socialctl.metricas.analytics_ingest import ObservationStore, read_supplied
from socialctl.metricas.analytics_schema import ReportQuery
from socialctl.metricas.reporting_client import ReportingClient


def make_http_client():
    return httpx.Client(timeout=60, follow_redirects=False)


def output(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def preview(change):
    return "DRY-RUN — PREVIEW COMPLETO Analytics/Reporting\n" + output(change.model_dump(mode="json")) + "\nHuella exacta para aprobar: " + change.fingerprint


def load_mapping(path):
    try:
        value = yaml.safe_load(read_supplied(path, 65536))
    except yaml.YAMLError:
        raise ValueError("invalid supplied JSON/YAML") from None
    if not isinstance(value, dict):
        raise ValueError("supplied JSON/YAML must be a mapping")
    return value


def register(content_app, default_root):
    from socialctl.management.cli import _brand, _fail
    analytics = typer.Typer(help="Analytics/Reporting: periodos oficiales, CSV y propuestas exactas.")
    content_app.add_typer(analytics, name="youtube-analytics")

    def read(root, brand, callback):
        selected = _brand(root, brand)
        try:
            with make_http_client() as http:
                value = callback(ReportingClient(selected, http))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            _fail(str(exc))
        typer.echo(output(value))
        if isinstance(value, dict) and value.get("complete") is False:
            raise typer.Exit(1)

    @analytics.command("query")
    def query(file: Path = typer.Option(..., "--file"), brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root")):
        def run(client):
            result = client.query(ReportQuery.model_validate(load_mapping(file)))
            saved = ObservationStore(client.brand.raiz, client.configured_channel_id).save_query(result)
            return {**result, "stored": saved}
        read(root, brand, run)

    @analytics.command("compare")
    def compare(end: str = typer.Option(..., "--end"), metric: list[str] = typer.Option(None, "--metric"), video_id: list[str] = typer.Option(None, "--video-id"),
                brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root")):
        def run(client):
            result = client.compare(date.fromisoformat(end), metric, video_id)
            store = ObservationStore(client.brand.raiz, client.configured_channel_id)
            for window in result["windows"]:
                for name in ("current", "previous"):
                    window[name]["stored"] = store.save_query(window[name])
            result["complete"] = all(w[k]["complete"] for w in result["windows"] for k in ("current", "previous"))
            return result
        read(root, brand, run)

    @analytics.command("batch-stats")
    def batch_stats(video_id: list[str] = typer.Option(..., "--video-id"), part: list[str] = typer.Option(None, "--part"),
                    brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root")):
        read(root, brand, lambda c: c.batch_stats(video_id, part))

    @analytics.command("groups-list")
    def groups_list(brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root"), max_pages: int = typer.Option(100, "--max-pages", min=1, max=100)):
        read(root, brand, lambda c: c.groups(max_pages))

    @analytics.command("group-show")
    def group_show(group_id: str, brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root")):
        read(root, brand, lambda c: c.group(group_id))

    @analytics.command("group-items-list")
    def group_items_list(group_id: str, brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root")):
        read(root, brand, lambda c: c.group_items(group_id))

    @analytics.command("report-types-list")
    def report_types_list(brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root"), max_pages: int = typer.Option(100, "--max-pages", min=1, max=100)):
        read(root, brand, lambda c: c.report_types(max_pages))

    @analytics.command("jobs-list")
    def jobs_list(brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root"), max_pages: int = typer.Option(100, "--max-pages", min=1, max=100)):
        read(root, brand, lambda c: c.jobs(max_pages))

    @analytics.command("job-show")
    def job_show(job_id: str, report_type_id: str = typer.Option(..., "--report-type-id"), brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root")):
        read(root, brand, lambda c: c.job(job_id, report_type_id))

    @analytics.command("reports-list")
    def reports_list(job_id: str, report_type_id: str = typer.Option(..., "--report-type-id"), brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root"),
                     max_pages: int = typer.Option(100, "--max-pages", min=1, max=100), created_after: str | None = typer.Option(None, "--created-after"),
                     start_time_at_or_after: str | None = typer.Option(None, "--start-time-at-or-after"), start_time_before: str | None = typer.Option(None, "--start-time-before")):
        read(root, brand, lambda c: c.reports(job_id, report_type_id, max_pages, created_after, start_time_at_or_after, start_time_before))

    @analytics.command("report-show")
    def report_show(job_id: str, report_id: str, report_type_id: str = typer.Option(..., "--report-type-id"), brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root")):
        read(root, brand, lambda c: c.report(job_id, report_type_id, report_id))

    @analytics.command("report-download")
    def report_download(job_id: str, report_id: str, report_type_id: str = typer.Option(..., "--report-type-id"), start: str = typer.Option(..., "--start"), end: str = typer.Option(..., "--end"),
                        brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root")):
        """Descarga e importa atómicamente CSV y procedencia en almacén privado."""
        def run(client):
            binding, data = client.download(job_id=job_id, report_id=report_id, report_type_id=report_type_id, start_date=start, end_date=end)
            store = ObservationStore(client.brand.raiz, client.configured_channel_id)
            value = store.import_csv(data, binding)
            return {"id": value["id"], "rows": len(value["rows"]), "binding": binding, "raw_path": str(store.root / (value["id"] + ".csv")), "checkpoint": "complete"}
        read(root, brand, run)

    @analytics.command("studio-import")
    def studio_import(file: Path = typer.Option(..., "--file"), metadata: Path = typer.Option(..., "--metadata"), brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root")):
        selected = _brand(root, brand)
        try:
            binding = load_mapping(metadata)
            if binding.get("source") != "youtubeStudio":
                raise ValueError("studio-import requires source youtubeStudio; supplied files cannot assert API authentication")
            account = selected.cuentas.get("youtube", {}).get("channel_id")
            store = ObservationStore(selected.raiz, account)
            value = store.import_csv(read_supplied(file), binding)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            _fail(str(exc))
        typer.echo(output({"id": value["id"], "rows": len(value["rows"]), "provenance": value["provenance"]}))

    @analytics.command("observations")
    def observations(brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root")):
        selected = _brand(root, brand)
        try:
            value = ObservationStore(selected.raiz, selected.cuentas.get("youtube", {}).get("channel_id")).active()
        except (ValueError, OSError) as exc:
            _fail(str(exc))
        typer.echo(output(value))

    @analytics.command("prepare")
    def prepare(file: Path = typer.Option(..., "--file"), dry_run: bool = typer.Option(False, "--dry-run"), brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root")):
        selected = _brand(root, brand)
        try:
            with make_http_client() as http:
                change = prepare_change(ReportingClient(selected, http), AnalyticsStore(selected.raiz), load_mapping(file))
        except (ValueError, OSError) as exc:
            _fail(str(exc))
        typer.echo(preview(change))

    @analytics.command("apply")
    def apply(change_id: str, brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root")):
        selected = _brand(root, brand)
        store = AnalyticsStore(selected.raiz)
        try:
            change = store.load(change_id)
            typer.echo(preview(change))
            approval = typer.prompt("Escribe la huella exacta para aprobar")
            if approval != change.fingerprint:
                raise ValueError("approval does not match fingerprint")
            with make_http_client() as http:
                result = apply_change(ReportingClient(selected, http), store, change_id, approval)
        except (ValueError, OSError) as exc:
            _fail(str(exc))
        typer.echo(output(result.model_dump(mode="json")))
        if not result.verified:
            raise typer.Exit(1)

    @analytics.command("status")
    def status(change_id: str, brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root")):
        selected = _brand(root, brand)
        try:
            value = AnalyticsStore(selected.raiz).load(change_id)
        except ValueError as exc:
            _fail(str(exc))
        typer.echo(output(value.model_dump(mode="json")))

    @analytics.command("reconcile")
    def reconcile(change_id: str, brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root")):
        read(root, brand, lambda c: reconcile_change(c, AnalyticsStore(c.brand.raiz), change_id).model_dump(mode="json"))
