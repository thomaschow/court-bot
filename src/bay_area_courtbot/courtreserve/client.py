from __future__ import annotations

import json
from datetime import date as ddate, datetime, timezone
from urllib.parse import urlencode

import httpx

from bay_area_courtbot.courtreserve import endpoints
from bay_area_courtbot.courtreserve.errors import (
    AllCourtsTaken,
    AuthExpired,
    CourtReserveError,
    RateLimited,
    SlotTaken,
    WindowNotOpen,
)
from bay_area_courtbot.courtreserve.parsing import SlotView, parse_confirmation, parse_read_expanded
from bay_area_courtbot.courtreserve.payloads import BookingCandidate, build_create_reservation_form
from bay_area_courtbot.logging import get_logger


class CourtReserveClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        org_id: int,
        cost_type_id: int | None = None,
        custom_scheduler_id: int | None = None,
        timezone_name: str = "America/Los_Angeles",
        reservation_min_interval: int = 60,
    ):
        self._http = http
        self._org_id = org_id
        self._cost_type_id = cost_type_id
        self._custom_scheduler_id = custom_scheduler_id
        self._tz = timezone_name
        self._min_interval = reservation_min_interval
        self._log = get_logger(facility=str(org_id), component="cr_client")

    def _read_consolidated_payload(self, day: ddate) -> dict:
        """Build the jsonData object the Kendo scheduler sends to ReadConsolidated.

        Format mirrors the JS in the rendered bookings page (verified live 2026-04-27).
        """
        midnight_utc = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        return {
            "startDate": midnight_utc.isoformat(),
            "orgId": str(self._org_id),
            "TimeZone": self._tz,
            "Date": midnight_utc.strftime("%a, %d %b %Y %H:%M:%S GMT"),
            "KendoDate": {"Year": day.year, "Month": day.month, "Day": day.day},
            "UiCulture": "en-US",
            "CostTypeId": str(self._cost_type_id) if self._cost_type_id else "",
            "CustomSchedulerId": str(self._custom_scheduler_id) if self._custom_scheduler_id else "",
            "ReservationMinInterval": str(self._min_interval),
        }

    async def read_consolidated(self, *, day: ddate) -> list[SlotView]:
        url = endpoints.read_consolidated(self._org_id)
        params = {"jsonData": json.dumps(self._read_consolidated_payload(day))}
        resp = await self._http.get(url, params=params)
        if resp.status_code in (401, 403):
            raise AuthExpired(f"{resp.status_code} on ReadConsolidated")
        if resp.status_code == 429:
            raise RateLimited("429 on ReadConsolidated")
        resp.raise_for_status()
        from bay_area_courtbot.courtreserve.parsing import parse_read_consolidated as _parse

        return _parse(resp.json() if resp.content else [])

    # Backwards-compat alias used by existing watcher code.
    async def read_expanded(self, *, day: ddate, extra_params: dict | None = None) -> list[SlotView]:
        return await self.read_consolidated(day=day)

    async def create_reservation_with_modal(
        self,
        cand: BookingCandidate,
        *,
        modal,
        extras: dict[str, str] | None = None,
    ) -> str:
        """Fast path: caller already has a fetched ModalState. Just build body + POST.

        Use this from the racer where one modal is pre-fetched and reused for every
        per-court attempt — saves ~1.5s of GETs per attempt.
        """
        body = build_create_reservation_form(
            cand,
            csrf_token=modal.csrf_token,
            hidden_fields=modal.hidden_fields,
            extras=extras,
        )
        encoded = urlencode(body)
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Origin": "https://reservations.courtreserve.com",
            "Referer": (
                f"https://app.courtreserve.com/Online/Reservations/Bookings/{self._org_id}"
            ),
        }
        resp = await self._http.post(modal.inner_form_url, content=encoded, headers=headers)
        return self._interpret_create(resp)

    async def create_reservation(
        self,
        cand: BookingCandidate,
        *,
        facility=None,
        court_type_id: int = 2,
        court_type: str = "Hard",
        extras: dict[str, str] | None = None,
        # Deprecated kwarg retained for older callers; ignored.
        csrf_token: str | None = None,
    ) -> str:
        """Two-step flow: GET modal to mint CSRF + RequestData + hidden fields, then POST.

        `facility` is the bay_area_courtbot Facility model. Required for the modal fetch (it has
        org_id + s_id). The booking POST replays every hidden field from the modal so
        per-org / per-modal MVC fields don't break us.
        """
        from bay_area_courtbot.courtreserve.modal import fetch_modal

        if facility is None:
            raise CourtReserveError("create_reservation requires the facility model")

        modal = await fetch_modal(
            self._http,
            facility,
            day=cand.date,
            start=cand.start,
            duration_minutes=cand.duration_minutes,
            court_type_id=court_type_id,
            court_type=court_type,
        )
        body = build_create_reservation_form(
            cand,
            csrf_token=modal.csrf_token,
            hidden_fields=modal.hidden_fields,
            extras=extras,
        )
        encoded = urlencode(body)
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Origin": "https://reservations.courtreserve.com",
            "Referer": (
                f"https://app.courtreserve.com/Online/Reservations/Bookings/{self._org_id}"
            ),
        }
        # The form action is an absolute URL on reservations.courtreserve.com — httpx will
        # ignore our base_url and use it directly. Cookies forward because the parent
        # domain is courtreserve.com.
        resp = await self._http.post(modal.inner_form_url, content=encoded, headers=headers)
        return self._interpret_create(resp)

    @staticmethod
    def _interpret_create(resp: httpx.Response) -> str:
        sc = resp.status_code
        text = resp.text or ""
        if sc == 401 or sc == 403:
            raise AuthExpired(f"{sc} on create_reservation")
        if sc == 429:
            raise RateLimited(text[:200])
        if sc >= 500:
            raise CourtReserveError(f"{sc} server error: {text[:200]}")
        # Success-ish: 200 may still indicate failure via JSON message.
        lower = text.lower()
        if (
            "not yet open" in lower
            or ("window" in lower and "open" in lower)
            or "only allowed to reserve up to" in lower
            or "advance" in lower and "reserve" in lower
        ):
            raise WindowNotOpen(text[:200])
        # GLOBAL exhaustion: every court at this time is gone — no point retrying.
        if "all courts of this type" in lower:
            raise AllCourtsTaken(text[:200])
        if "no longer available" in lower or "already reserved" in lower or "taken" in lower:
            raise SlotTaken(text[:200])
        if sc != 200:
            raise CourtReserveError(f"{sc}: {text[:200]}")
        cid = parse_confirmation(text)
        if not cid:
            raise CourtReserveError(f"could not parse confirmation id from response: {text[:200]}")
        return cid
