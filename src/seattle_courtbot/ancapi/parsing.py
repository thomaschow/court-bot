"""Parsers for ANC's JSON envelope.

ANC wraps every response in `{"headers": {...}, "body": {...}}` with a
`response_code` ("0000" = success, others = various errors). Helpers here pull the
body out, raise typed errors on non-success codes, and adapt the `items` list
into the dataclasses the watcher works with.
"""

from __future__ import annotations

from dataclasses import dataclass

from seattle_courtbot.ancapi.errors import ApiResponseError


@dataclass(frozen=True)
class AvailabilityRange:
    """A single contiguous bookable window for one court on one date.

    ANC doesn't slice availability into 30-min cells — it returns one or more
    `{start_time, end_time, available}` ranges per day. The watcher converts
    these into 30-min slices to share the bay-area pairing-rule machinery.
    """
    resource_id: int
    date: str          # YYYY-MM-DD
    start_time: str    # HH:MM:SS local
    end_time: str      # HH:MM:SS local
    available: bool


def parse_availability_daily(payload: dict, resource_id: int) -> list[AvailabilityRange]:
    body = unwrap(payload)
    details = body.get("details") or {}
    out: list[AvailabilityRange] = []
    for d in details.get("daily_details") or []:
        date_str = d.get("date")
        for t in d.get("times") or []:
            if not isinstance(t, dict):
                continue
            out.append(AvailabilityRange(
                resource_id=resource_id,
                date=str(date_str),
                start_time=str(t.get("start_time") or ""),
                end_time=str(t.get("end_time") or ""),
                available=bool(t.get("available", False)),
            ))
    return out


@dataclass(frozen=True)
class CourtItem:
    """One row from the resource search response."""
    resource_id: int
    name: str
    center_id: int
    center_name: str
    type_id: int
    type_name: str
    site_id: int
    max_capacity: int
    no_internet_permits: bool
    event_type_ids: list[int]    # all event types this resource supports

    @property
    def is_indoor(self) -> bool:
        return "indoor" in self.type_name.lower()


def unwrap(payload: dict) -> dict:
    """Validate the ANC envelope and return the `body`. Raise ApiResponseError on
    a non-success response_code."""
    headers = payload.get("headers") or {}
    code = headers.get("response_code")
    msg = headers.get("response_message", "")
    if code != "0000":
        raise ApiResponseError(code or "?", msg or "no message", raw=payload)
    return payload.get("body") or {}


def parse_resource_search(payload: dict) -> list[CourtItem]:
    body = unwrap(payload)
    items = body.get("items") or []
    out: list[CourtItem] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        out.append(CourtItem(
            resource_id=int(it["id"]),
            name=str(it.get("name") or ""),
            center_id=int(it.get("center_id") or 0),
            center_name=str(it.get("center_name") or ""),
            type_id=int(it.get("type_id") or 0),
            type_name=str(it.get("type_name") or ""),
            site_id=int(it.get("site_id") or 0),
            max_capacity=int(it.get("max_capacity") or 0),
            no_internet_permits=bool(it.get("no_internet_permits", False)),
            event_type_ids=[
                int(e["id"])
                for e in (it.get("event_type_list") or [])
                if isinstance(e, dict) and "id" in e
            ],
        ))
    return out
