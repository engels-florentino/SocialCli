"""Select and aggregate imported YouTube Reporting thumbnail reach observations."""

from datetime import date, datetime, timedelta, timezone
import math

REPORT_TYPES = {'channel_reach_basic_a1', 'channel_reach_combined_a1'}


def _day(value: str) -> date:
    if len(value) != 8 or not value.isdigit():
        raise ValueError('Reporting date must be YYYYMMDD')
    return date.fromisoformat(f'{value[:4]}-{value[4:6]}-{value[6:]}')


def _instant(value: str) -> datetime:
    when = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if when.utcoffset() is None:
        raise ValueError('Report creation time needs timezone')
    return when.astimezone(timezone.utc)


def reach_by_video_day(observations: list[dict]) -> dict[tuple[str, date], dict]:
    """Prefer the newest report for overlapping video/day keys."""
    selected = {}
    for observation in observations:
        binding = observation.get('binding', {})
        report_type = binding.get('report_type_id')
        if binding.get('source') != 'youtubeReporting' or report_type not in REPORT_TYPES:
            continue
        report = binding.get('report', {})
        created = _instant(report['createTime'])
        report_id = report['id']
        priority = (created, int(report_type == 'channel_reach_combined_a1'), report_id)
        for row in observation.get('rows', []):
            dimensions = row.get('dimensions', {})
            video_id = dimensions.get('video_id')
            if not isinstance(video_id, str) or not video_id:
                continue
            day = _day(dimensions['date'])
            metrics = row.get('metrics', {})
            impressions = metrics.get('video_thumbnail_impressions', {}).get('value')
            ctr = metrics.get('video_thumbnail_impressions_ctr', {}).get('value')
            if (type(impressions) is not int or impressions < 0 or
                    ctr is not None and (not isinstance(ctr, (int, float)) or
                                         not math.isfinite(ctr) or not 0 <= ctr <= 100)):
                continue
            key = (video_id, day)
            if key not in selected or priority > selected[key]['priority']:
                selected[key] = {
                    'impressions': impressions,
                    'ctr': float(ctr) if ctr is not None else None,
                    'report_id': report_id,
                    'report_type': report_type,
                    'generated_at': created.isoformat(),
                    'priority': priority,
                }
    for item in selected.values():
        item.pop('priority')
    return selected


def reach_for_video(rows: dict[tuple[str, date], dict], video_id: str,
                    as_of: date, *, days: int = 28) -> dict | None:
    """Return count and impression-weighted CTR; absence is None, never zero."""
    if days < 1:
        raise ValueError('Positive reporting window required')
    start = as_of - timedelta(days=days - 1)
    matched = sorted((day, value) for (vid, day), value in rows.items()
                     if vid == video_id and start <= day <= as_of)
    if not matched:
        return None
    impressions = sum(value['impressions'] for _, value in matched)
    ctr_denominator = sum(value['impressions'] for _, value in matched
                          if value['ctr'] is not None)
    ctr = (round(sum(value['impressions'] * value['ctr'] for _, value in matched
                     if value['ctr'] is not None) / ctr_denominator, 2)
           if ctr_denominator else None)
    return {
        'impressions': impressions,
        'ctr': ctr,
        'from': start.isoformat(),
        'through': matched[-1][0].isoformat(),
        'report_ids': list(dict.fromkeys(value['report_id'] for _, value in matched)),
        'source': 'youtubeReporting',
    }


def attach_reach(pieces, observations: list[dict], as_of: date) -> None:
    """Enrich matching YouTube pieces with a bounded Reporting observation."""
    rows = reach_by_video_day(observations)
    for piece in pieces:
        value = reach_for_video(rows, piece.id, as_of)
        if value is not None:
            piece.especificas['thumbnail_reach_28d'] = value
