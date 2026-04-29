from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class SlotView:
    """A slot view that can hold either a per-court availability flag or a multi-court
    consolidated row (CourtReserve's ReadConsolidated returns one row per (time, court_type)
    with an `AvailableCourtIds` array). For consolidated rows, `court_id` is one element
    of that array — `parse_read_consolidated` fans rows out to per-court SlotViews.
    """
    court_id: int
    start: datetime
    end: datetime
    is_available: bool
    raw: dict


def parse_read_consolidated(payload: dict | list | str) -> list[SlotView]:
    """Parse ReadConsolidated → flat list of (court_id, start, end, available) SlotViews.

    Each upstream row is fanned out: a row with AvailableCourtIds=[a,b,c] becomes 3 SlotViews,
    one per court, each marked is_available=True. Closed/in-past rows are skipped.
    """
    if isinstance(payload, str):
        payload = json.loads(payload)
    if isinstance(payload, dict):
        rows = payload.get("Data", payload.get("data", []))
    else:
        rows = payload

    out: list[SlotView] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        if r.get("IsClosed") or r.get("IsInPast") or r.get("IsWaitListSlot"):
            continue
        start = _coerce_dt(r.get("Start"))
        end = _coerce_dt(r.get("End"))
        if start is None or end is None:
            continue
        court_ids = r.get("AvailableCourtIds") or []
        if not isinstance(court_ids, list):
            continue
        for cid in court_ids:
            try:
                cid_i = int(cid)
            except (TypeError, ValueError):
                continue
            out.append(SlotView(court_id=cid_i, start=start, end=end, is_available=True, raw=r))
    return out


# Backwards-compat shim — older code/tests call parse_read_expanded.
def parse_read_expanded(payload: dict | list | str) -> list[SlotView]:
    """Accept either the old ReadExpanded per-court rows or the new ReadConsolidated shape."""
    if isinstance(payload, str):
        payload = json.loads(payload)
    if isinstance(payload, dict):
        rows = payload.get("Data", payload.get("data", []))
    else:
        rows = payload

    if rows and isinstance(rows[0], dict) and "AvailableCourtIds" in rows[0]:
        return parse_read_consolidated(payload)

    out: list[SlotView] = []
    for it in rows or []:
        if not isinstance(it, dict):
            continue
        court_id = _coerce_int(it.get("CourtId") or it.get("ResourceId") or it.get("courtId"))
        start = _coerce_dt(it.get("Start") or it.get("StartDateTime") or it.get("start"))
        end = _coerce_dt(it.get("End") or it.get("EndDateTime") or it.get("end"))
        if court_id is None or start is None or end is None:
            continue
        avail = bool(
            it.get("IsAvailable")
            if "IsAvailable" in it
            else (not it.get("IsBlocked", False) and not it.get("IsBooked", False))
        )
        out.append(SlotView(court_id=court_id, start=start, end=end, is_available=avail, raw=it))
    return out


def parse_confirmation(text: str) -> str | None:
    """Best-effort extraction of a reservation/confirmation id from a POST response."""
    if not text:
        return None
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        data = None
    if isinstance(data, dict):
        for key in ("ReservationId", "Id", "ConfirmationId", "reservationId"):
            v = data.get(key)
            if v:
                return str(v)
    m = re.search(r'"(?:Reservation|Confirmation)?Id"\s*:\s*"?(\d+)"?', text)
    return m.group(1) if m else None


def _coerce_int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _coerce_dt(v) -> datetime | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, str):
        # CourtReserve returns ASP.NET-style "/Date(milliseconds[+offset])/" strings.
        m = re.match(r"/Date\((-?\d+)", v)
        if m:
            return datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc)
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None
