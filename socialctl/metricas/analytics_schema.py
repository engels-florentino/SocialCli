"""Explicit supported Analytics families; official values and Pacific periods."""
from datetime import date, datetime, time, timedelta, timezone
import math
import re
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from socialctl.management.youtube_resources import ResourceError

PACIFIC = ZoneInfo("America/Los_Angeles")
BASE_METRICS = {"views", "estimatedMinutesWatched", "averageViewDuration", "averageViewPercentage",
    "subscribersGained", "subscribersLost", "likes", "dislikes", "comments", "shares", "engagedViews",
    "redViews", "estimatedRedMinutesWatched", "videosAddedToPlaylists", "videosRemovedFromPlaylists"}
INTERACTION_METRICS = set("annotationClickThroughRate annotationCloseRate annotationClicks annotationImpressions annotationClosableImpressions annotationClickableImpressions annotationCloses cardClicks cardImpressions cardClickRate cardTeaserClicks cardTeaserImpressions cardTeaserClickRate".split())
PLAYLIST_METRICS = set("averageTimeInPlaylist playlistAverageViewDuration playlistEstimatedMinutesWatched playlistSaves playlistStarts playlistViews viewsPerPlaylistStart".split())
RETENTION_METRICS = {"audienceWatchRatio", "relativeRetentionPerformance", "startedWatching", "stoppedWatching", "totalSegmentImpressions"}
DIMENSIONS = set("day month video playlist country province dma city insightPlaybackLocationType insightPlaybackLocationDetail insightTrafficSourceType insightTrafficSourceDetail deviceType operatingSystem ageGroup gender sharingService subscribedStatus liveOrOnDemand youtubeProduct creatorContentType elapsedVideoTimeRatio".split())
FILTER_NAMES = DIMENSIONS - {"day", "month", "video", "playlist", "elapsedVideoTimeRatio"} | {"continent", "subContinent"}
FAMILIES = {(): BASE_METRICS, ("day",): BASE_METRICS, ("video",): BASE_METRICS,
    ("country",): BASE_METRICS, ("insightTrafficSourceType",): {"views", "estimatedMinutesWatched"},
    ("ageGroup",): {"viewerPercentage"}, ("gender",): {"viewerPercentage"},
    ("elapsedVideoTimeRatio",): RETENTION_METRICS}


def period_bounds(start, end):
    """Inclusive provider dates -> exact half-open UTC interval, including DST."""
    return (datetime.combine(start, time.min, PACIFIC).astimezone(timezone.utc),
            datetime.combine(end + timedelta(days=1), time.min, PACIFIC).astimezone(timezone.utc))


class ReportQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_date: date
    end_date: date
    metrics: list[str] = Field(default_factory=lambda: ["views"])
    dimensions: list[str] = Field(default_factory=list)
    video_ids: list[str] = Field(default_factory=list)
    playlist_ids: list[str] = Field(default_factory=list)
    group_id: str | None = None
    filters: dict[str, str] = Field(default_factory=dict)
    audience_type: str | None = None
    sort: list[str] = Field(default_factory=list)
    max_results: int = Field(default=200, ge=1, le=200, strict=True)
    start_index: int = Field(default=1, ge=1, le=100000, strict=True)
    max_pages: int = Field(default=100, ge=1, le=100, strict=True)

    @model_validator(mode="after")
    def compatible(self):
        allowed = set(FAMILIES.get(tuple(self.dimensions), BASE_METRICS | INTERACTION_METRICS | PLAYLIST_METRICS))
        # Named public channel dimensions/metrics, with hard family constraints
        # where documented. The provider validates remaining combinations; an
        # HTTP400 is recorded explicitly, never advertised as available data.
        if set(self.dimensions) & {"ageGroup", "gender"}:
            allowed = {"viewerPercentage"}
        elif "elapsedVideoTimeRatio" in self.dimensions:
            allowed = RETENTION_METRICS
        elif tuple(self.dimensions) in {(), ("day",), ("video",), ("country",)}:
            allowed |= INTERACTION_METRICS | PLAYLIST_METRICS
        if self.start_date > self.end_date or (self.end_date-self.start_date).days > 3660:
            raise ValueError("period outside local bound (3661 dates)")
        if (set(self.dimensions)-DIMENSIONS or len(self.dimensions) > 5 or len(set(self.dimensions)) != len(self.dimensions)
            or not self.metrics or len(self.metrics) > 40 or set(self.metrics)-allowed or len(self.metrics) != len(set(self.metrics))):
            raise ValueError("unsupported dimensions/metrics family; monetary/content-owner families not enabled")
        if len(self.video_ids) > 50 or len(set(self.video_ids)) != len(self.video_ids) or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", v) for v in self.video_ids):
            raise ValueError("explicit video IDs outside local bound")
        if self.video_ids and self.group_id:
            raise ValueError("video and group filters are exclusive")
        if len(self.playlist_ids) > 50 or len(set(self.playlist_ids)) != len(self.playlist_ids) or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", v) for v in self.playlist_ids):
            raise ValueError("explicit playlist IDs outside local bound")
        if self.playlist_ids and (self.video_ids or self.group_id):
            raise ValueError("resource filters are exclusive")
        if set(self.filters)-FILTER_NAMES or any(not v or len(v) > 1000 or any(c in v for c in ";=\r\n") for v in self.filters.values()):
            raise ValueError("unsupported filter name/value")
        if "province" in self.dimensions and self.filters.get("country") != "US":
            raise ValueError("province dimension requires country==US")
        if "month" in self.dimensions and (self.start_date.day != 1 or self.end_date.day != 1):
            raise ValueError("month reports require first-of-month start/end dates")
        if set(self.dimensions) & {"day", "month"} == {"day", "month"}:
            raise ValueError("day and month dimensions are exclusive")
        if self.group_id is not None and (not self.group_id or len(self.group_id) > 512 or any(c in self.group_id for c in ";,\r\n")):
            raise ValueError("invalid group filter")
        if self.audience_type not in {None, "ORGANIC", "AD_INSTREAM", "AD_INDISPLAY"}:
            raise ValueError("unsupported audienceType")
        retention = self.dimensions == ["elapsedVideoTimeRatio"]
        if retention and (len(self.video_ids) != 1 or self.audience_type is None):
            raise ValueError("retention requires one owned video and explicit audienceType")
        if not retention and self.audience_type is not None:
            raise ValueError("audienceType only enabled for retention")
        if any(s.removeprefix("-") not in self.metrics+self.dimensions for s in self.sort):
            raise ValueError("sort must reference returned fields")
        if self.dimensions == ["video"] and not self.sort:
            self.sort = ["-views" if "views" in self.metrics else "-" + self.metrics[0]]
        if self.dimensions == ["day"] and not self.sort:
            self.sort = ["day"]
        return self

    def params(self):
        params = {"ids": "channel==MINE", "startDate": self.start_date.isoformat(), "endDate": self.end_date.isoformat(),
            "metrics": ",".join(self.metrics), "maxResults": self.max_results, "startIndex": self.start_index}
        if self.dimensions:
            params["dimensions"] = ",".join(self.dimensions)
        filters = (["video==" + ",".join(self.video_ids)] if self.video_ids else [])
        if self.playlist_ids:
            filters.append("playlist==" + ",".join(self.playlist_ids))
        if self.group_id:
            filters.append("group==" + self.group_id)
        if self.audience_type:
            filters.append("audienceType==" + self.audience_type)
        filters.extend(k + "==" + v for k, v in sorted(self.filters.items()))
        if filters:
            params["filters"] = ";".join(filters)
        if self.sort:
            params["sort"] = ",".join(self.sort)
        return params


def metric_value(value, missing_reason="provider_null"):
    if value is None:
        return {"value": None, "missing_reason": missing_reason}
    if type(value) not in {int, float} or not math.isfinite(value):
        raise ResourceError("schema: invalid numeric metric")
    return {"value": value, "missing_reason": None}


def parse_query_response(query, payload):
    headers = payload.get("columnHeaders")
    expected = query.dimensions + query.metrics
    if not isinstance(headers, list) or [h.get("name") if isinstance(h, dict) else None for h in headers] != expected:
        raise ResourceError("schema: unexpected Analytics columns")
    for i, h in enumerate(headers):
        if h.get("columnType") != ("DIMENSION" if i < len(query.dimensions) else "METRIC") or h.get("dataType") not in {"STRING", "INTEGER", "FLOAT"}:
            raise ResourceError("schema: invalid Analytics header")
        if i >= len(query.dimensions) and h["dataType"] == "STRING":
            raise ResourceError("schema: metric type changed")
    raw = payload.get("rows", [])
    if not isinstance(raw, list) or len(raw) > query.max_results:
        raise ResourceError("schema: invalid or oversized rows")
    result = []
    for row in raw:
        if not isinstance(row, list) or len(row) != len(headers):
            raise ResourceError("schema: row/header mismatch")
        for value, header in zip(row, headers):
            if value is not None and ((header["dataType"] == "INTEGER" and type(value) is not int)
                or (header["dataType"] == "STRING" and not isinstance(value, str))
                or (header["dataType"] == "FLOAT" and (type(value) not in {int, float} or not math.isfinite(value)))):
                raise ResourceError("schema: header/value type changed")
        dims = dict(zip(query.dimensions, row))
        if any(type(v) not in {str, int, float} for v in dims.values()):
            raise ResourceError("schema: invalid dimension")
        if "day" in dims:
            try:
                day = date.fromisoformat(dims["day"])
                if not query.start_date <= day <= query.end_date:
                    raise ValueError()
            except (TypeError, ValueError):
                raise ResourceError("schema: day outside requested period") from None
        result.append({"dimensions": dims, "metrics": {name: metric_value(value) for name, value in zip(query.metrics, row[len(query.dimensions):])}})
    return result


def provenance(source, account):
    return {"source": source, "account_id": account, "observed_at": datetime.now(timezone.utc).isoformat(),
        "provider_timezone": "America/Los_Angeles", "latency": "provider_dependent_latest_common_metric_day",
        "historical_coverage": "only_returned_rows; absent_dates_not_zero", "paid_organic_eligibility": "metric_and_report_dependent",
        "missing_reason": "absent_or_suppressed_not_disambiguated", "monetization_eligible_hours": None,
        "monetization_missing_reason": "watch_time_is_not_YPP_eligible_hours",
        "definitions_url": "https://developers.google.com/youtube/analytics/metrics",
        "definition_observed_at": "2026-09-13", "schema_version": 1,
        "retention": {"category": "authorized_statistics", "authorization_check_days": 30,
            "in_client_revocation_removal_days": 7, "external_revocation_check_days": 30,
            "cleanup_owner": "Task13; no automatic purge", "scope": "API-derived observations only; supplied originals excluded"}}
