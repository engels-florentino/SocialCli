"""Bounded lossless CSV parsing and crash-safe version/active-period checkpoints."""
import csv
from datetime import date
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import uuid
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict

from socialctl.identity import credential_metadata
from socialctl.management.resource_changes import ResourceStore, digest, save_download
from socialctl.management.youtube_resources import ResourceError
from socialctl.metricas.analytics_client import opaque
from socialctl.metricas.analytics_schema import metric_value, provenance
from socialctl.metricas.reporting_client import MAX_CSV, instant, report_period

DIMENSIONS = set("date channel_id video_id playlist_id country_code province_code playback_location_type playback_location_detail live_or_on_demand subscribed_status traffic_source_type traffic_source_detail device_type operating_system age_group gender sharing_service annotation_type annotation_id card_type card_id end_screen_element_type end_screen_element_id subtitle_language subtitle_language_autotranslated".split())
COUNTS = set("views engaged_views comments likes dislikes shares subscribers_gained subscribers_lost videos_added_to_playlists videos_removed_from_playlists card_clicks card_impressions card_teaser_clicks card_teaser_impressions annotation_clicks annotation_impressions annotation_closable_impressions annotation_clickable_impressions annotation_closes end_screen_element_clicks end_screen_element_impressions".split())
COUNTS |= {"red_views", "video_thumbnail_impressions"}
FLOATS = {"watch_time_minutes": "minutes", "average_view_duration_seconds": "seconds", "average_view_duration_percentage": "percentage",
    "annotation_click_through_rate": "rate", "annotation_close_rate": "rate", "card_click_rate": "rate", "card_teaser_click_rate": "rate",
    "end_screen_element_click_rate": "rate", "views_percentage": "percentage", "red_watch_time_minutes": "minutes", "video_thumbnail_impressions_ctr": "percentage"}
DEFINITIONS = "https://developers.google.com/youtube/reporting/v1/reports/"


class StudioMetadata(BaseModel):
    """Normalize JSON strings and safe_load's native YAML date/time values."""
    model_config = ConfigDict(extra="forbid")
    source: Literal["youtubeStudio"]
    account_id: str
    export_id: str
    start_date: date
    end_date: date
    exported_at: AwareDatetime
    source_filename: str


def read_supplied(path, maximum=MAX_CSV):
    try:
        fd = os.open(Path(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum:
                raise ResourceError("supplied file must be bounded nonempty regular file")
            data = stream.read(maximum+1)
            after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or len(data) != before.st_size:
                raise ResourceError("supplied file changed during read")
            return data
    except OSError:
        raise ResourceError("cannot read supplied file without following symlinks") from None


def parse_csv(data, binding):
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_CSV:
        raise ResourceError("CSV byte limit")
    try:
        text = data.decode("utf-8-sig")
        if "\x00" in text:
            raise ValueError()
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        headers = next(reader)
        if not headers or len(headers) > 256 or len(set(headers)) != len(headers) or any(not h or len(h) > 256 for h in headers):
            raise ValueError()
        reporting = binding["source"] == "youtubeReporting"
        if reporting:
            start, end = report_period(binding["report"])
            if "date" not in headers:
                raise ResourceError("schema: report has no date; raw download may be saved separately")
        else:
            start, end = date.fromisoformat(binding["start_date"]), date.fromisoformat(binding["end_date"])
        rows, seen = [], set()
        known = DIMENSIONS | COUNTS | set(FLOATS) if reporting else set()
        columns = {h: {"kind": "dimension" if h in DIMENSIONS and reporting else "metric" if h in COUNTS|set(FLOATS) and reporting else "unknown",
            "semantics_source": "official_catalog_2026-09-13" if h in known else "unknown",
            "definition_url": DEFINITIONS + ("dimensions#" if h in DIMENSIONS else "metrics#") + h if h in known else None,
            "unit": "count" if h in COUNTS and reporting else FLOATS.get(h) if reporting else None} for h in headers}
        for raw in reader:
            if len(rows) >= 100000 or len(raw) != len(headers) or any(len(v) > 8192 for v in raw):
                raise ResourceError("CSV row/column/cell limit or schema mismatch")
            values = dict(zip(headers, raw))
            # Only vetted Reporting dimensions establish a row's primary key.
            # Unknown semantics (especially Studio titles) cannot justify dedupe;
            # preserve every such row in source order, including identical rows.
            if reporting and set(headers) <= known:
                identity = tuple(values[h] for h in headers if h in DIMENSIONS)
                if identity in seen:
                    raise ResourceError("duplicate CSV dimension identity")
                seen.add(identity)
            if reporting:
                raw_date = values["date"]
                if len(raw_date) != 8 or not raw_date.isdigit():
                    raise ResourceError("schema: Reporting date requires YYYYMMDD")
                day = date.fromisoformat(raw_date[:4]+"-"+raw_date[4:6]+"-"+raw_date[6:])
                if not start <= day <= end:
                    raise ResourceError("CSV date outside bound report")
                if values.get("channel_id") not in {None, "", binding["account_id"]}:
                    raise ResourceError("CSV account mismatch")
            row = {"raw": values, "dimensions": {}, "metrics": {}, "unknown": {}}
            for h, value in values.items():
                if columns[h]["kind"] == "dimension":
                    row["dimensions"][h] = value
                elif columns[h]["kind"] == "metric":
                    number = None if value == "" else int(value) if h in COUNTS else float(value)
                    row["metrics"][h] = metric_value(number, "empty_csv_cell_not_zero")
                else:
                    row["unknown"][h] = {"raw": value, "missing_reason": "unknown_column_semantics"}
            rows.append(row)
        return {"headers": headers, "columns": columns, "rows": rows}
    except (ValueError, UnicodeError, csv.Error, StopIteration):
        raise ResourceError("CSV schema/encoding/numeric value invalid; no checkpoint committed") from None


class ObservationStore(ResourceStore):
    def __init__(self, brand_root, account_id):
        self.root = Path(brand_root) / ".socialctl" / "youtube-observations"
        self.account_id = opaque(account_id)

    def _read_json(self, path):
        return json.loads(read_supplied(path, 128*1024*1024))

    def index(self):
        self._ensure_root()
        path = self.root / "index.json"
        if not path.exists() and not path.is_symlink():
            return {"version": 1, "account_id": self.account_id, "imports": {}, "active": {}}
        value = self._read_json(path)
        if value.get("version") != 1 or value.get("account_id") != self.account_id:
            raise ResourceError("observation index account/schema mismatch")
        return value

    def _commit_index(self, value):
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
        if len(raw) > 16*1024*1024:
            raise ResourceError("checkpoint size limit")
        if (self.root / "index.json").is_symlink():
            raise ResourceError("checkpoint symlink rejected")
        fd, temporary = tempfile.mkstemp(dir=self.root, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.root / "index.json")
            self._sync_directory(self.root)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _immutable_file(self, path, data):
        if path.exists() or path.is_symlink():
            if read_supplied(path, max(len(data), MAX_CSV)) != data:
                raise ResourceError("immutable observation file conflict")
        else:
            save_download(path, data)

    def import_csv(self, data, binding):
        # Canonicalize supplied metadata before checking ownership or persistence.
        if binding.get("source") == "youtubeStudio":
            binding = StudioMetadata.model_validate(binding).model_dump(mode="json")
        binding = json.loads(json.dumps(binding, allow_nan=False))
        # This observation describes authorization at read time, not the report's
        # immutable identity. A changed grant/expiry must not defeat report dedupe.
        credentials = binding.pop("credential_observation", None)
        if binding.get("account_id") != self.account_id:
            raise ResourceError("import account mismatch")
        source = binding.get("source")
        if source == "youtubeReporting":
            report = binding.get("report", {})
            rid, job, rtype = opaque(report.get("id")), opaque(binding.get("job_id")), opaque(binding.get("report_type_id"))
            if report.get("jobId") != job:
                raise ResourceError("report/job binding mismatch")
            start, end = report_period(report)
            created = instant(report.get("createTime")).isoformat()
            period = [source, self.account_id, job, rtype, start.isoformat(), end.isoformat()]
            identifier = [source, self.account_id, job, rid]
        elif source == "youtubeStudio":
            rid = opaque(binding.get("export_id"))
            start, end = date.fromisoformat(binding["start_date"]), date.fromisoformat(binding["end_date"])
            if start > end:
                raise ResourceError("invalid supplied export dates")
            created = instant(binding.get("exported_at")).isoformat()
            if not isinstance(binding.get("source_filename"), str) or not binding["source_filename"]:
                raise ResourceError("supplied source filename required")
            period = [source, self.account_id, rid, start.isoformat(), end.isoformat()]
            identifier = [source, self.account_id, rid]
        else:
            raise ResourceError("unsupported CSV provenance source")
        parsed = parse_csv(data, binding)
        key = digest(json.dumps(identifier).encode())
        period_key = digest(json.dumps(period).encode())
        checksum = digest(data)
        binding_digest = digest(json.dumps(binding, sort_keys=True).encode())
        with self.apply_lock(str(uuid.uuid5(uuid.NAMESPACE_URL, "youtube-observations-index"))):
            index = self.index()
            existing = index["imports"].get(key)
            if existing:
                if (existing["sha256"], existing["binding_sha256"]) != (checksum, binding_digest):
                    raise ResourceError("same report ID has different bytes/binding; refuses overwrite")
                return self._read_json(self.root / (key + ".json"))
            p = provenance(source, self.account_id)
            p.update({"account_binding": "authenticated_report_job_chain" if source == "youtubeReporting" else "user_supplied_not_API_verified",
                "definitions_url": DEFINITIONS + "metrics" if source == "youtubeReporting" else None,
                "dimensions": [h for h in parsed["headers"] if parsed["columns"][h]["kind"] == "dimension"],
                "latency": "first report up to 48h; generated files only" if source == "youtubeReporting" else "user_supplied_export",
                "historical_coverage": "provider generated; normal downloads 60 days, historical 30 days; not promised lifetime" if source == "youtubeReporting" else "declared supplied period"})
            if source == "youtubeReporting":
                p["credential_observation"] = credentials if credentials is not None else credential_metadata({})
            if source == "youtubeStudio":
                p["retention"] = {"category": "user_supplied_export", "api_cleanup_eligible": False,
                    "originals": "supplied original files are never changed or removed"}
            observation = {"id": key, "binding": binding, "sha256": checksum, "provenance": p, **parsed}
            serialized = json.dumps(observation, ensure_ascii=False, allow_nan=False).encode()
            if len(serialized) > 128*1024*1024:
                raise ResourceError("parsed observation byte limit; no checkpoint committed")
            # A crash can leave orphan immutable files, but only the final atomic
            # index replacement activates a complete import. Recovery reuses bytes.
            raw_path = self.root / (key + ".csv")
            parsed_path = self.root / (key + ".json")
            self._immutable_file(raw_path, data)
            if parsed_path.exists():
                orphan = self._read_json(parsed_path)
                if orphan.get("sha256") != checksum or orphan.get("binding") != binding:
                    raise ResourceError("orphan import binding conflict")
                observation = orphan
            else:
                self._immutable_file(parsed_path, serialized)
            index["imports"][key] = {"sha256": checksum, "binding_sha256": binding_digest, "period_key": period_key, "created_at": created, "checkpoint": "complete"}
            previous = index["active"].get(period_key)
            if previous is None or created > index["imports"][previous]["created_at"]:
                index["active"][period_key] = key
            elif created == index["imports"][previous]["created_at"]:
                raise ResourceError("ambiguous equal-time backfill; no active version guessed")
            self._commit_index(index)
            return observation

    def save_query(self, result):
        if result.get("provenance", {}).get("account_id") != self.account_id:
            raise ResourceError("query account mismatch")
        self._ensure_root()
        data = json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
        if len(data) > 128*1024*1024:
            raise ResourceError("query observation byte limit")
        key = digest(data)
        self._immutable_file(self.root / (key + ".query.json"), data)
        return {"id": key, "path": str(self.root / (key + ".query.json")), "complete": result["complete"]}

    def active(self):
        index = self.index()
        return [self._read_json(self.root / (key + ".json")) for key in index["active"].values()]
