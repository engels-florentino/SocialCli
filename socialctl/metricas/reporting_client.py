"""Reporting jobs/reports identity chain and explicit bounded CSV downloads."""
from datetime import date, datetime, time, timedelta, timezone
import re
from urllib.parse import urlsplit, unquote

from socialctl.management.youtube_resources import ResourceError
from socialctl.metricas.analytics_client import AnalyticsClient, REPORTING, complete, encoded, opaque
from socialctl.metricas.analytics_schema import PACIFIC

MAX_CSV = 16 * 1024 * 1024
ANALYTICS_SCOPES = {"https://www.googleapis.com/auth/yt-analytics.readonly", "https://www.googleapis.com/auth/yt-analytics-monetary.readonly"}


def instant(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise ResourceError("invalid provider timestamp") from None


def report_period(report):
    start, end = instant(report.get("startTime")), instant(report.get("endTime"))
    local_start, local_end = start.astimezone(PACIFIC), end.astimezone(PACIFIC)
    if end <= start or local_start.time() != time.min or local_end.time() != time.min or (end-start).days > 366:
        raise ResourceError("report period must be bounded Pacific midnight intervals")
    return local_start.date(), local_end.date() - timedelta(days=1)


def validate_download_url(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 1000 or not value.isascii() or any(ord(c) <= 32 for c in value):
        raise ResourceError("invalid report download URL")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.netloc != "youtubereporting.googleapis.com" or parsed.fragment or parsed.query != "alt=media":
        raise ResourceError("report download host/query not allowed")
    if not parsed.path.startswith("/v1/media/") or not re.fullmatch(r"/[A-Za-z0-9._~%/-]+", parsed.path):
        raise ResourceError("report download path not allowed")
    segments = parsed.path[len("/v1/media/"):].split("/")
    if any(not s or unquote(s) in {".", ".."} or any(c in unquote(s) for c in "/\\%?#") for s in segments):
        raise ResourceError("noncanonical report download path")
    return value


class ReportingClient(AnalyticsClient):
    def report_types(self, max_pages=100):
        self.identity()
        return self.pages(f"{REPORTING}/reportTypes", "reportTypes", {"pageSize": 100, "includeSystemManaged": "true"}, max_pages)

    def jobs(self, max_pages=100):
        self.identity()
        result = self.pages(f"{REPORTING}/jobs", "jobs", {"pageSize": 100, "includeSystemManaged": "true"}, max_pages)
        for row in result["items"]:
            opaque(row.get("reportTypeId"))
            if not isinstance(row.get("name"), str):
                raise ResourceError("job schema invalid")
        return result

    def job(self, job_id, report_type_id=None):
        rows = [r for r in complete(self.jobs()) if r["id"] == opaque(job_id)]
        if len(rows) != 1:
            raise ResourceError("job not verified for authenticated account")
        row = self._json(self.service_request("GET", f"{REPORTING}/jobs/{encoded(job_id)}"))
        if row.get("id") != job_id or row.get("reportTypeId") != rows[0]["reportTypeId"] or report_type_id is not None and row["reportTypeId"] != report_type_id:
            raise ResourceError("job/report type identity mismatch")
        return row

    def reports(self, job_id, report_type_id, max_pages=100, created_after=None, start_time_at_or_after=None, start_time_before=None):
        self.job(job_id, report_type_id)
        params = {"pageSize": 100}
        for key, value in {"createdAfter": created_after, "startTimeAtOrAfter": start_time_at_or_after, "startTimeBefore": start_time_before}.items():
            if value is not None:
                instant(value)
                params[key] = value
        result = self.pages(f"{REPORTING}/jobs/{encoded(job_id)}/reports", "reports", params, max_pages)
        for row in result["items"]:
            self._validate_report(row, job_id)
        return result

    @staticmethod
    def _validate_report(row, job_id, report_id=None):
        if row.get("jobId") != job_id or report_id is not None and row.get("id") != report_id:
            raise ResourceError("report/job identity mismatch")
        opaque(row.get("id"))
        report_period(row)
        instant(row.get("createTime"))
        validate_download_url(row.get("downloadUrl"))
        return row

    def report(self, job_id, report_type_id, report_id):
        self.job(job_id, report_type_id)
        row = self._json(self.service_request("GET", f"{REPORTING}/jobs/{encoded(job_id)}/reports/{encoded(report_id)}"))
        return self._validate_report(row, job_id, report_id)

    def download(self, *, job_id, report_type_id, report_id, start_date, end_date):
        account = self.identity()
        types = complete(self.report_types())
        if not any(r["id"] == report_type_id for r in types):
            raise ResourceError("report type unavailable for authenticated account")
        row = self.report(job_id, report_type_id, report_id)
        start, end = report_period(row)
        if (start.isoformat(), end.isoformat()) != (start_date, end_date):
            raise ResourceError("explicit report dates mismatch")
        url = validate_download_url(row["downloadUrl"])
        # Only this method may call the download transport, after exact discovery
        # and account/job/type/report/date binding. No bearer follows redirects.
        data = self._bounded_request("GET", url, max_bytes=MAX_CSV).content
        if not data:
            raise ResourceError("empty download; header-only CSV is valid, zero bytes is not")
        return {"source": "youtubeReporting", "account_id": account, "report_type_id": report_type_id,
            "job_id": job_id, "report": row, "credential_observation": self.credential_observation}, data
