from __future__ import annotations

from dataclasses import dataclass
from datetime import date as ddate, time as dtime

from bay_area_courtbot.config import Preferences
from bay_area_courtbot.courtreserve.parsing import SlotView


@dataclass(frozen=True)
class SlotKey:
    facility_id: str
    court_id: int
    date: ddate
    start: dtime


def slot_key(facility_id: str, slot: SlotView) -> SlotKey:
    return SlotKey(
        facility_id=facility_id,
        court_id=slot.court_id,
        date=slot.start.date(),
        start=slot.start.time(),
    )


def find_new_openings(
    facility_id: str,
    prev: list[SlotView],
    curr: list[SlotView],
    prefs: Preferences,
) -> list[SlotView]:
    """Slots that are available in `curr` but were not available in `prev` and match preferences.

    First snapshot (prev empty) is treated as the baseline — no openings reported, so we
    don't book everything that's already open the moment the daemon starts.
    """
    if not prev:
        return []
    prev_avail = {slot_key(facility_id, s) for s in prev if s.is_available}
    out: list[SlotView] = []
    for s in curr:
        if not s.is_available:
            continue
        k = slot_key(facility_id, s)
        if k in prev_avail:
            continue
        if not _matches_any_rule(prefs, k):
            continue
        out.append(s)
    return out


def _matches_any_rule(prefs: Preferences, k: SlotKey) -> bool:
    weekday = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][k.date.weekday()]
    for rule in prefs.rules:
        if weekday not in rule.day_of_week:
            continue
        if any(w.start <= k.start < w.end for w in rule.time_windows):
            if not rule.court_whitelist or k.court_id in rule.court_whitelist:
                return True
    return False
