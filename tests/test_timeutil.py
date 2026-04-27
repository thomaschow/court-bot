from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from courtbot.timeutil import in_quiet_hours, next_window_open, target_date_for


TZ = "America/Los_Angeles"


def test_next_window_open_today_if_future() -> None:
    now = datetime(2026, 5, 1, 6, 30, tzinfo=ZoneInfo(TZ))
    out = next_window_open(days_ahead=7, opens_at_local=time(7, 0), tz=TZ, now=now)
    assert out == datetime(2026, 5, 1, 7, 0, tzinfo=ZoneInfo(TZ))


def test_next_window_open_tomorrow_if_passed() -> None:
    now = datetime(2026, 5, 1, 7, 30, tzinfo=ZoneInfo(TZ))
    out = next_window_open(days_ahead=7, opens_at_local=time(7, 0), tz=TZ, now=now)
    assert out.date() == date(2026, 5, 2)
    assert out.time() == time(7, 0)


def test_target_date_for() -> None:
    open_at = datetime(2026, 5, 1, 7, 0, tzinfo=ZoneInfo(TZ))
    assert target_date_for(open_at, days_ahead=7) == date(2026, 5, 8)


def test_in_quiet_hours_overnight() -> None:
    quiet = (time(22, 0), time(7, 0))
    assert in_quiet_hours(datetime(2026, 5, 1, 23, 0, tzinfo=ZoneInfo(TZ)), quiet) is True
    assert in_quiet_hours(datetime(2026, 5, 1, 6, 0, tzinfo=ZoneInfo(TZ)), quiet) is True
    assert in_quiet_hours(datetime(2026, 5, 1, 7, 0, tzinfo=ZoneInfo(TZ)), quiet) is False
    assert in_quiet_hours(datetime(2026, 5, 1, 12, 0, tzinfo=ZoneInfo(TZ)), quiet) is False


def test_in_quiet_hours_same_day() -> None:
    quiet = (time(13, 0), time(14, 0))
    assert in_quiet_hours(datetime(2026, 5, 1, 13, 30, tzinfo=ZoneInfo(TZ)), quiet) is True
    assert in_quiet_hours(datetime(2026, 5, 1, 12, 30, tzinfo=ZoneInfo(TZ)), quiet) is False
