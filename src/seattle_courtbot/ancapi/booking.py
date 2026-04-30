"""Seattle ANC booking flow.

The 5-step flow (verified live 2026-04-29):

  1. POST /resource/validation        — confirms slot + attendee count
  2. POST /resource/proceed           — advances to checkout form
  3. GET  /reservation/form/0         — returns fees + timestamp
  4. GET  /reservation/form/participants — family-member list
  5. POST /reservation/form/reserve/{reno}/{timestamp}   ← FINAL submit

Steps 1-2 are pre-flight; they don't create a reservation. Step 5 commits.

Phase-2 status: steps 1-2 are fully wired. Step 5's request body shape is
inferred from the JS bundle + the proceed body — the first real `book()` call
should be done with `--dry-run` so the user can confirm the captured request
matches expectations before committing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date as ddate, datetime, time as dtime, timedelta

import httpx

from seattle_courtbot.ancapi import endpoints
from seattle_courtbot.ancapi.csrf import CSRF_HEADER_NAME
from seattle_courtbot.ancapi.errors import ApiResponseError, AuthExpired, RateLimited


@dataclass(frozen=True)
class BookingRequest:
    customer_id: int
    resource_id: int
    event_type_id: int                  # 152 = Tennis - Outdoor
    attendee_count: int
    date: ddate
    start: dtime
    duration_minutes: int               # 60-180 per Seattle rules
    event_name: str = "Tennis booking"

    @property
    def end(self) -> dtime:
        return (datetime.combine(self.date, self.start)
                + timedelta(minutes=self.duration_minutes)).time()


def _datetime_str(d: ddate, t: dtime) -> str:
    return f"{d.isoformat()} {t.strftime('%H:%M:%S')}"


def _validation_body(req: BookingRequest, *, booking_identifier: str) -> dict:
    return {
        "customer_id": req.customer_id,
        "company_id": 0,
        "participant_type": 2,
        "attendee": req.attendee_count,
        "resource_id": req.resource_id,
        "reservation_unit": "minute",
        "reservation_time_groups": [{
            "short_summary": "",
            "summary": "",
            "reservation_times": [{
                "start_event_datetime": _datetime_str(req.date, req.start),
                "end_event_datetime": _datetime_str(req.date, req.end),
                "availability": "Available",
                "booking_identifier": booking_identifier,
            }],
            "adjusted_message": "",
            "group_id": 2,
            "availability": "Available",
        }],
        "reno": 0,
        "event_type_id": req.event_type_id,
        "is_clear_group": False,
    }


async def validate(client: httpx.AsyncClient, req: BookingRequest, *, csrf: str,
                   booking_identifier: str | None = None) -> tuple[bool, dict]:
    """Run the validation pre-flight. Returns (ok, response_body)."""
    bid = booking_identifier or str(uuid.uuid4())
    body = _validation_body(req, booking_identifier=bid)
    r = await client.post(
        endpoints.reservation_validation(), json=body, headers={CSRF_HEADER_NAME: csrf},
    )
    if r.status_code in (401, 403):
        raise AuthExpired(f"{r.status_code} on validation")
    if r.status_code == 429:
        raise RateLimited("429 on validation")
    r.raise_for_status()
    payload = r.json()
    code = payload.get("headers", {}).get("response_code")
    msg = payload.get("headers", {}).get("response_message", "")
    body_obj = payload.get("body") or {}
    booking_errors = body_obj.get("booking_errors") or {}
    if code != "0000" or booking_errors:
        raise ApiResponseError(code or "?", msg or str(booking_errors), raw=payload)
    return body_obj.get("status") == "success", body_obj


async def proceed(client: httpx.AsyncClient, req: BookingRequest, *, csrf: str,
                  booking_identifier: str) -> dict:
    """Advance the validated request to the checkout form. Server response is
    `{status:"success", next_page:"reservation/form"}`."""
    body = _validation_body(req, booking_identifier=booking_identifier)
    r = await client.post(
        endpoints.proceed_to_form(), json=body, headers={CSRF_HEADER_NAME: csrf},
    )
    r.raise_for_status()
    payload = r.json()
    code = payload.get("headers", {}).get("response_code")
    if code != "0000":
        raise ApiResponseError(code or "?",
                               payload.get("headers", {}).get("response_message", ""),
                               raw=payload)
    return payload.get("body") or {}


async def fetch_form(client: httpx.AsyncClient) -> dict:
    """GET the checkout form payload (returns timestamp + fee summary)."""
    r = await client.get(endpoints.form())
    r.raise_for_status()
    payload = r.json()
    return payload.get("body") or {}


@dataclass
class BookingResult:
    success: bool
    confirmation_id: str | None
    fee_total: float | None
    raw: dict


async def book(
    client: httpx.AsyncClient, req: BookingRequest, *, csrf: str, dry_run: bool = True,
) -> BookingResult:
    """End-to-end booking flow. With `dry_run=True` (default), validates +
    proceeds to the form + reads fees, but DOES NOT call form/reserve. Set
    dry_run=False to commit (will be charged the form's reported fee).
    """
    bid = str(uuid.uuid4())
    ok, _ = await validate(client, req, csrf=csrf, booking_identifier=bid)
    if not ok:
        return BookingResult(success=False, confirmation_id=None, fee_total=None, raw={})
    proceed_resp = await proceed(client, req, csrf=csrf, booking_identifier=bid)
    if proceed_resp.get("status") != "success":
        return BookingResult(success=False, confirmation_id=None, fee_total=None, raw=proceed_resp)
    form_body = await fetch_form(client)
    fee_total = (form_body.get("event_fee") or {}).get("fee_summary", {}).get("total")
    timestamp = form_body.get("timestamp")
    if dry_run:
        return BookingResult(
            success=True, confirmation_id=None, fee_total=fee_total,
            raw={"form": form_body, "timestamp": timestamp},
        )
    if timestamp is None:
        raise ApiResponseError("?", "form/0 response missing timestamp")
    submit_body = _validation_body(req, booking_identifier=bid)
    submit_body["event_name"] = req.event_name
    r = await client.post(
        endpoints.form_reserve(reno=0, timestamp=int(timestamp)),
        json=submit_body, headers={CSRF_HEADER_NAME: csrf},
    )
    r.raise_for_status()
    payload = r.json()
    code = payload.get("headers", {}).get("response_code")
    if code != "0000":
        raise ApiResponseError(code or "?",
                               payload.get("headers", {}).get("response_message", ""),
                               raw=payload)
    body_obj = payload.get("body") or {}
    return BookingResult(
        success=True,
        confirmation_id=str(body_obj.get("confirmation_number") or body_obj.get("transaction_id") or ""),
        fee_total=fee_total,
        raw=body_obj,
    )
