"""Live availability scan for the dashboard.

Fetches `/rest/reservation/resource/availability/daily` for every (facility, court)
in the configured date horizon, in parallel. Returns typed rows the dashboard
template can render.

Each row carries a `book_url` — a deep link to the ANC resource-detail page —
so the user can click through and complete the booking manually in their
browser (sidestepping the form/reserve POST shape we haven't fully cracked).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date as ddate, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from seattle_courtbot.ancapi.client import AncClient
from seattle_courtbot.ancapi.parsing import AvailabilityRange
from seattle_courtbot.auth.session import build_client
from seattle_courtbot.config import Config

LOCAL = ZoneInfo("America/Los_Angeles")
ANC_DETAIL_URL = "https://anc.apm.activecommunities.com/seattle/reservation/search/detail/{rid}"


@dataclass(frozen=True)
class AvailabilitySlot:
    """A bookable time range on a single court+date.

    Pre-computed `book_url` deep-links to the court's detail page; the user
    completes the booking on ANC directly.
    """
    date: ddate
    weekday: str             # 'Mon', 'Tue', ...
    start_time: dtime
    end_time: dtime
    duration_min: int        # max contiguous bookable duration
    facility_id: str
    facility_name: str
    court_id: int
    court_name: str
    book_url: str
    is_lights: bool          # heuristic: facility has "Woodland" or known lit sites


_LIT_FACILITY_HINTS = (
    "Lower Woodland",
    "Magnuson",
    "Amy Yee",
    "Volunteer",  # Volunteer Park has lights too
)


def _is_lights(facility_name: str) -> bool:
    return any(h.lower() in facility_name.lower() for h in _LIT_FACILITY_HINTS)


def _hms_to_minutes(s: str) -> int:
    parts = [int(p) for p in s.split(":")]
    return parts[0] * 60 + parts[1]


def _minutes_to_time(m: int) -> dtime:
    return dtime((m // 60) % 24, m % 60)


def _ranges_to_slots(
    cfg: Config,
    facility,
    court,
    ranges: list[AvailabilityRange],
    *,
    window_start: dtime | None,
    window_end: dtime | None,
    min_duration_min: int,
) -> list[AvailabilitySlot]:
    out: list[AvailabilitySlot] = []
    for r in ranges:
        if not r.available:
            continue
        try:
            d = ddate.fromisoformat(r.date)
        except ValueError:
            continue
        rs = _hms_to_minutes(r.start_time)
        re = _hms_to_minutes(r.end_time)
        # Clip to window
        if window_start is not None:
            ws = _hms_to_minutes(window_start.strftime("%H:%M"))
            rs = max(rs, ws)
        if window_end is not None:
            we = _hms_to_minutes(window_end.strftime("%H:%M"))
            re = min(re, we)
        dur = re - rs
        if dur < min_duration_min:
            continue
        out.append(AvailabilitySlot(
            date=d,
            weekday=d.strftime("%a"),
            start_time=_minutes_to_time(rs),
            end_time=_minutes_to_time(re),
            duration_min=dur,
            facility_id=facility.id,
            facility_name=facility.name,
            court_id=court.id,
            court_name=court.name,
            book_url=ANC_DETAIL_URL.format(rid=court.id),
            is_lights=_is_lights(facility.name),
        ))
    return out


async def scan(
    cfg: Config,
    *,
    days_ahead: int = 14,
    window_start: dtime | None = dtime(17, 0),
    window_end: dtime | None = dtime(21, 0),
    min_duration_min: int = 60,
    facility_filter: set[str] | None = None,
) -> list[AvailabilitySlot]:
    """Scan all configured Seattle facilities for available time ranges within
    the user-set window. Filters: date horizon, time-of-day window, min
    contiguous duration. Returns slot rows ordered by (date, start)."""
    today = datetime.now(LOCAL).date()
    start_d = today + timedelta(days=1)
    end_d = today + timedelta(days=days_ahead)

    facilities = [f for f in cfg.facilities
                  if facility_filter is None or f.id in facility_filter]
    out: list[AvailabilitySlot] = []
    sem = asyncio.Semaphore(8)

    async with build_client(http2=False) as client:
        anc = AncClient(client)

        async def scan_court(facility, court):
            async with sem:
                try:
                    ranges = await anc.read_availability(
                        resource_id=court.id, customer_id=cfg.member_id or 0,
                        start_date=start_d, end_date=end_d,
                    )
                except Exception:
                    return []
                return _ranges_to_slots(
                    cfg, facility, court, ranges,
                    window_start=window_start, window_end=window_end,
                    min_duration_min=min_duration_min,
                )

        results = await asyncio.gather(
            *(scan_court(f, c) for f in facilities for c in f.courts),
            return_exceptions=False,
        )
    for slot_list in results:
        out.extend(slot_list)
    out.sort(key=lambda s: (s.date, s.start_time, s.facility_id, s.court_id))
    return out
