"""One content slot per platform/account/day, always 09:00 New York."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .models import aware

ZONE_NAME = 'America/New_York'
ZONE = ZoneInfo(ZONE_NAME)


def slot(day: date) -> datetime:
    return datetime.combine(day, time(9), ZONE).astimezone(timezone.utc)


def reservation_day(publish_at: datetime, timezone_name: str) -> str:
    local = aware(publish_at).astimezone(ZONE)
    if timezone_name != ZONE_NAME or local.time() != time(9):
        raise ValueError('Content must be scheduled at 09:00 America/New_York')
    return local.date().isoformat()


def plan_slots(requested: list[datetime], *, occupied: set[str] | None = None,
               now: datetime | None = None) -> list[datetime]:
    """Stable input-order overflow for ONE explicit platform/account; no writes."""
    now = aware(now or datetime.now(timezone.utc))
    used = set(occupied or ())
    result = []
    for requested_at in requested:
        day = max(aware(requested_at).astimezone(ZONE).date(), now.astimezone(ZONE).date())
        while day.isoformat() in used or slot(day) <= now or slot(day) < aware(requested_at):
            day += timedelta(days=1)
        used.add(day.isoformat())
        result.append(slot(day))
    return result
