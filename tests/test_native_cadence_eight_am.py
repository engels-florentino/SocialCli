from datetime import datetime, timezone
from socialctl.native_schedule.cadence import plan_slots, reservation_day


def test_eight_am_approval_remains_eight_am_across_dst_and_collision():
    requested = datetime.fromisoformat('2030-11-02T08:00:00-04:00')
    result = plan_slots([requested], occupied={'2030-11-02'}, now=datetime(2030,11,1,tzinfo=timezone.utc))
    assert result == [datetime.fromisoformat('2030-11-03T08:00:00-05:00')]
    assert reservation_day(result[0], 'America/New_York') == '2030-11-03'


def test_eight_am_day_does_not_gain_a_second_slot_with_legacy_nine_am():
    dates = [datetime.fromisoformat('2030-11-02T08:00:00-04:00'),datetime.fromisoformat('2030-11-02T09:00:00-04:00')]
    result = plan_slots(dates, now=datetime(2030,11,1,tzinfo=timezone.utc))
    assert result == [dates[0],datetime.fromisoformat('2030-11-03T09:00:00-05:00')]
