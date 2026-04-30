"""Convert ANC's per-day availability *ranges* into 30-min *slices*.

ANC returns availability as one or more contiguous {start_time, end_time}
ranges per day (e.g., on a clean day a court might show 8:30 AM – 11:00 PM as
one range; on a day with a 6-9 PM booking, it shows two ranges, 8:30-18:00
and 21:00-23:00). The watcher's diff/pairing logic — ported from bay-area —
operates on 30-min slices, so we convert.
"""

from __future__ import annotations

from datetime import date as ddate, datetime, time as dtime, timedelta
from typing import Iterable

from seattle_courtbot.ancapi.parsing import AvailabilityRange
from seattle_courtbot.watcher.poller import SliceKey


SLICE_MINUTES = 30


def _parse_hms(s: str) -> dtime:
    """Parse 'HH:MM:SS' or 'HH:MM'."""
    parts = [int(p) for p in s.split(":")]
    while len(parts) < 3:
        parts.append(0)
    return dtime(parts[0] % 24, parts[1], parts[2])


def ranges_to_slices(
    facility_id: str,
    ranges: Iterable[AvailabilityRange],
    *,
    slice_minutes: int = SLICE_MINUTES,
    window_start: dtime | None = None,
    window_end: dtime | None = None,
) -> set[SliceKey]:
    """Expand each `AvailabilityRange` into 30-min slice keys, optionally clipped
    to a [window_start, window_end) time-of-day filter.

    Each slice is `slice_minutes` long and starts on a multiple of `slice_minutes`
    minutes from midnight. A range from 18:00 to 21:00 yields slices at 18:00,
    18:30, 19:00, 19:30, 20:00, 20:30 (six 30-min slices).
    """
    out: set[SliceKey] = set()
    for r in ranges:
        if not r.available:
            continue
        try:
            d = ddate.fromisoformat(r.date)
            start = _parse_hms(r.start_time)
            end = _parse_hms(r.end_time)
        except ValueError:
            continue
        # Clip to time-of-day window
        if window_start is not None and end <= window_start:
            continue
        if window_end is not None and start >= window_end:
            continue
        eff_start = max(start, window_start) if window_start else start
        eff_end = min(end, window_end) if window_end else end
        # Generate slices on slice_minutes boundaries.
        cur = datetime.combine(d, eff_start)
        end_dt = datetime.combine(d, eff_end)
        # Snap to the next slice boundary if not already aligned.
        rem = (cur.minute % slice_minutes) + (cur.second / 60)
        if rem:
            cur += timedelta(minutes=slice_minutes - rem)
        while cur + timedelta(minutes=slice_minutes) <= end_dt:
            sm = cur.hour * 60 + cur.minute
            out.add(SliceKey(
                facility_id=facility_id,
                date_ord=d.toordinal(),
                start_minutes=sm,
                court_id=r.resource_id,
            ))
            cur += timedelta(minutes=slice_minutes)
    return out
