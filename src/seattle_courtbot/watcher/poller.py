"""Seattle cancellation watcher daemon (Phase 3 stub).

The full watcher loop lands once Phase 2 wires up:
  - AncClient.read_availability(facility, day) → list[SliceKey]
  - AncClient.create_reservation(slot) → confirmation_id

Until then this module exposes the same parameterised pairing-rule machinery as
bay_area_courtbot's `scripts/watch_cancellations.py` so its diff/discard logic
is unit-testable in isolation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date as ddate, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from seattle_courtbot.config import PairingRule


LOCAL = ZoneInfo("America/Los_Angeles")


@dataclass(frozen=True)
class SliceKey:
    """A single bookable 30-min slice on a given (date, court). Mirrors the shape
    used by the bay-area watcher; the ANC schedule reader (Phase 2) is responsible
    for producing these from whatever shape ANC returns."""
    facility_id: str
    date_ord: int
    start_minutes: int
    court_id: int


def gap_minutes(delta_min: int, slot_duration_min: int) -> int:
    """End-to-start gap between two equal-duration slices given their start
    delta. Adjacent → 0; overlapping → negative (returned as -1 sentinel)."""
    gap = abs(delta_min) - slot_duration_min
    return gap if gap >= 0 else -1


def has_partner(
    snapshot: set[SliceKey],
    facility_id: str,
    day: ddate,
    start_local: dtime,
    court_id: int,
    rule: PairingRule,
) -> bool:
    """Does the snapshot contain a partner slice for the given (facility, date,
    start, court) under the configured pairing rule? Two arms (either passes):

      1. Any-court arm: end-to-start gap ≤ rule.max_any_court_gap_min on any court.
      2. Same-court arm: end-to-start gap ≤ rule.max_same_court_gap_min on the
         same court.
    """
    slot_min = start_local.hour * 60 + start_local.minute
    date_ord = day.toordinal()
    for k in snapshot:
        if k.facility_id != facility_id or k.date_ord != date_ord:
            continue
        if k.start_minutes == slot_min and k.court_id == court_id:
            continue
        delta = k.start_minutes - slot_min
        gap = gap_minutes(delta, rule.slot_duration_min)
        if gap < 0:
            continue
        if gap <= rule.max_any_court_gap_min:
            return True
        if k.court_id == court_id and gap <= rule.max_same_court_gap_min:
            return True
    return False


@dataclass(frozen=True)
class RelaxationLevel:
    label: str
    max_any_court_gap_min: int
    max_same_court_gap_min: int


DEFAULT_RELAXATION_LEVELS: tuple[RelaxationLevel, ...] = (
    RelaxationLevel("adjacent_any_court", 0, 0),
    RelaxationLevel("same_court_30min_gap", 0, 30),
    RelaxationLevel("same_court_60min_gap", 0, 60),
)


def capture_neighbors(
    snapshot: set[SliceKey],
    facility_id: str,
    day: ddate,
    start_local: dtime,
    court_id: int,
    *,
    within_min: int = 90,
    slot_duration_min: int = 30,
    levels: tuple[RelaxationLevel, ...] = DEFAULT_RELAXATION_LEVELS,
) -> str:
    """Serialise the slices around a discarded slot, including which relaxation
    levels each one would satisfy. Returns JSON for the ledger's `neighbors` column."""
    slot_min = start_local.hour * 60 + start_local.minute
    date_ord = day.toordinal()
    out = []
    for k in snapshot:
        if k.facility_id != facility_id or k.date_ord != date_ord:
            continue
        if k.start_minutes == slot_min and k.court_id == court_id:
            continue
        delta = k.start_minutes - slot_min
        if abs(delta) > within_min:
            continue
        gap = gap_minutes(delta, slot_duration_min)
        qualifies = []
        for lvl in levels:
            if gap < 0:
                continue
            if gap <= lvl.max_any_court_gap_min:
                qualifies.append(lvl.label)
                continue
            if k.court_id == court_id and gap <= lvl.max_same_court_gap_min:
                qualifies.append(lvl.label)
        h, m = divmod(k.start_minutes, 60)
        out.append({
            "court_id": k.court_id,
            "start": f"{h:02d}:{m:02d}",
            "delta_min": delta,
            "gap_min": gap,
            "qualifies_under": qualifies,
        })
    out.sort(key=lambda x: (abs(x["delta_min"]), x["court_id"]))
    return json.dumps(out)
