"""Fetch the booking modal HTML to mint a fresh CSRF token + RequestData and pull every
hidden field. The booking POST replays those hidden fields verbatim plus a few user-set
overrides (StartTime, Duration, ReservationTypeId, ...).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date as ddate, datetime, time as dtime, timedelta
from urllib.parse import urlparse

import httpx

from bay_area_courtbot.config import Facility


@dataclass
class ModalState:
    csrf_token: str
    inner_form_url: str
    hidden_fields: dict[str, str] = field(default_factory=dict)


def _format_for_url(d: ddate, t: dtime) -> str:
    """CourtReserve's wrapper expects '5/3/2026 9:00 AM'-style strings (M/d/YYYY h:MM AM/PM)."""
    dt = datetime.combine(d, t)
    return dt.strftime("%-m/%-d/%Y %-I:%M %p")


async def fetch_modal(
    client: httpx.AsyncClient,
    facility: Facility,
    *,
    day: ddate,
    start: dtime,
    duration_minutes: int,
    court_type_id: int = 2,
    court_type: str = "Hard",
) -> ModalState:
    """Two-step fetch: outer wrapper → extract inner URL via regex → inner GET (cross-host).

    Returns a ModalState whose `hidden_fields` are ready to be replayed in the POST body
    after a few user-driven overrides (StartTime, Duration, etc.).
    """
    end = (datetime.combine(day, start) + timedelta(minutes=duration_minutes)).time()
    wrapper_params = {
        "start": _format_for_url(day, start),
        "end": _format_for_url(day, end),
        "customSchedulerId": str(facility.s_id) if facility.s_id else "",
        "courtTypeId": str(court_type_id),
        "courtType": court_type,
    }
    wrapper = await client.get(
        f"/Online/Reservations/CreateReservation/{facility.org_id}",
        params=wrapper_params,
    )
    wrapper.raise_for_status()

    m = re.search(r"fixUrl\(['\"]([^'\"]+)['\"]\)", wrapper.text)
    if not m:
        raise RuntimeError("CreateReservation wrapper did not contain a fixUrl(...) inner URL")
    inner_url = m.group(1).replace("&amp;", "&")

    parsed = urlparse(inner_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    # Cookies are domain=.courtreserve.com so they apply to both app and reservations
    # subdomains; httpx ignores base_url for absolute URLs, so we can reuse the client.
    r = await client.get(inner_url)
    r.raise_for_status()
    html = r.text

    form_action_m = re.search(r'<form[^>]*action="([^"]+)"', html)
    inner_form_url = form_action_m.group(1) if form_action_m else f"{base}/Online/ReservationsApi/CreateReservation/{facility.org_id}?uiCulture=en-US"

    csrf_m = re.search(
        r'<input[^>]*name="__RequestVerificationToken"[^>]*value="([^"]+)"', html
    )
    if not csrf_m:
        raise RuntimeError("modal HTML did not contain a __RequestVerificationToken")
    csrf = csrf_m.group(1)

    hidden: dict[str, str] = {}
    for tag in re.finditer(r"<input[^>]+>", html):
        s = tag.group(0)
        if 'type="hidden"' not in s:
            continue
        n = re.search(r'name="([^"]+)"', s)
        v = re.search(r'value="([^"]*)"', s)
        if n:
            hidden[n.group(1)] = v.group(1) if v else ""

    return ModalState(csrf_token=csrf, inner_form_url=inner_form_url, hidden_fields=hidden)
