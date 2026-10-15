"""Exact customer instants alongside approved 08:00/09:00 legacy reservations."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .models import aware

ZONE_NAME = 'America/New_York'
ZONE = ZoneInfo(ZONE_NAME)


def slot(day: date, *, hour: int = 9) -> datetime:
    return datetime.combine(day, time(hour), ZONE).astimezone(timezone.utc)


def reservation_day(publish_at: datetime, timezone_name: str) -> str:
    local = aware(publish_at).astimezone(ZONE)
    if timezone_name != ZONE_NAME or local.time() not in {time(8), time(9)}:
        raise ValueError('Content must be scheduled at approved 08:00 or legacy 09:00 America/New_York')
    return local.date().isoformat()


def reservation_key(job) -> str:
    if job.schedule_mode == 'legacy_daily':
        return reservation_day(job.publish_at, job.timezone_name)
    instant = aware(job.publish_at)
    if instant.microsecond:
        raise ValueError('Exact scheduling requires whole seconds; no silent rounding')
    return 'at:' + instant.isoformat()


def slot_day(key: str) -> str:
    if key.startswith('at:'):
        return aware(datetime.fromisoformat(key[3:])).astimezone(ZONE).date().isoformat()
    return date.fromisoformat(key).isoformat()


def slots_conflict(first: str, second: str) -> bool:
    if first.startswith('at:') and second.startswith('at:'):
        return first == second
    return slot_day(first) == slot_day(second)


def exact_time(value: datetime, timezone_name: str) -> datetime:
    """Require explicit, valid local wall time and offset; never guess a DST fold."""
    try:
        zone = ZoneInfo(timezone_name)
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError('Unknown IANA timezone') from exc
    instant = aware(value)
    local = instant.astimezone(zone)
    if value.microsecond:
        raise ValueError('Exact scheduling requires whole seconds; no silent rounding')
    if (local.replace(tzinfo=None) != value.replace(tzinfo=None)
            or local.utcoffset() != value.utcoffset()):
        raise ValueError('Publication time offset does not match the timezone, or local time does not exist')
    return instant


def plan_slots(requested: list[datetime], *, occupied: set[str] | None = None,
               now: datetime | None = None) -> list[datetime]:
    """Stable input-order overflow for ONE explicit platform/account; no writes."""
    now = aware(now or datetime.now(timezone.utc))
    used = set(occupied or ())
    result = []
    for requested_at in requested:
        requested_local = aware(requested_at).astimezone(ZONE)
        hour = 8 if requested_local.time() == time(8) else 9
        day = max(aware(requested_at).astimezone(ZONE).date(), now.astimezone(ZONE).date())
        while day.isoformat() in used or slot(day, hour=hour) <= now or slot(day, hour=hour) < aware(requested_at):
            day += timedelta(days=1)
        used.add(day.isoformat())
        result.append(slot(day, hour=hour))
    return result
