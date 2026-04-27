from __future__ import annotations

from datetime import date as ddate
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo


def local_now(tz: str) -> datetime:
    return datetime.now(ZoneInfo(tz))


def at_local(d: ddate, t: dtime, tz: str) -> datetime:
    """Combine a date + time into a tz-aware datetime in the given zone."""
    return datetime.combine(d, t, tzinfo=ZoneInfo(tz))


def next_window_open(
    *,
    days_ahead: int,
    opens_at_local: dtime,
    tz: str,
    now: datetime | None = None,
) -> datetime:
    """The next moment at which a booking-window opens, returning a tz-aware datetime.

    Booking opens daily at `opens_at_local` for the date `today + days_ahead`.
    The returned moment is the next future window opening (today's, if still in the future,
    or tomorrow's otherwise).
    """
    if now is None:
        now = local_now(tz)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo(tz))

    candidate = datetime.combine(now.date(), opens_at_local, tzinfo=ZoneInfo(tz))
    if candidate <= now:
        candidate = candidate + timedelta(days=1)
    return candidate


def target_date_for(open_at: datetime, days_ahead: int) -> ddate:
    """Given the moment a booking-window opens, return the date being released."""
    return open_at.date() + timedelta(days=days_ahead)


def in_quiet_hours(
    now_local: datetime,
    quiet: tuple[dtime, dtime] | None,
) -> bool:
    if not quiet:
        return False
    start, end = quiet
    t = now_local.time()
    if start <= end:
        return start <= t < end
    # Wraps midnight (e.g., 22:00 - 07:00).
    return t >= start or t < end
