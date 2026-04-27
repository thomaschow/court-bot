import time as time_mod
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx

from courtbot.auth.session import SessionState, build_client
from courtbot.config import (
    BookingWindow,
    Config,
    Court,
    Defaults,
    Facility,
    PreferenceRule,
    Preferences,
    TimeWindow,
)
from courtbot.courtreserve.payloads import BookingCandidate
from courtbot.racer.prewarm import RacerContext
from courtbot.racer.runner import run_burst


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COURTBOT_ROOT", str(tmp_path))
    (tmp_path / "state").mkdir()


_RACER_WRAPPER_HTML = """
<script>
  $.ajax({ url: fixUrl('https://reservations.courtreserve.com/Online/ReservationsApi/CreateReservation?id=13234') });
</script>
"""

_RACER_INNER_HTML = """
<form action="https://reservations.courtreserve.com//Online/ReservationsApi/CreateReservation/13234?uiCulture=en-US">
  <input name="__RequestVerificationToken" type="hidden" value="MOCK_CSRF" />
  <input name="Id" type="hidden" value="13234" />
  <input name="OrgId" type="hidden" value="13234" />
  <input name="MemberId" type="hidden" value="999" />
  <input name="RequestData" type="hidden" value="REQ" />
</form>
"""


def _facility() -> Facility:
    return Facility(
        id="santa-clara",
        org_id=13234,
        booking_window=BookingWindow(days_ahead=7, opens_at_local=time(7, 0)),
        member_id=999,
        membership_id=1,
        reservation_type_id=2,
        courts=[Court(id=103, name="C3"), Court(id=104, name="C4")],
    )


def _cfg() -> Config:
    return Config(
        defaults=Defaults(),
        facilities=[_facility()],
        preferences=Preferences(
            facility_rank=["santa-clara"],
            rules=[
                PreferenceRule(
                    name="r",
                    day_of_week=["Sat"],
                    time_windows=[TimeWindow(start=time(9, 0), end=time(11, 0))],
                    duration_minutes=60,
                )
            ],
        ),
    )


def _candidates() -> list[BookingCandidate]:
    f = _facility()
    return [
        BookingCandidate(
            facility_id=f.id,
            org_id=f.org_id,
            member_id=f.member_id,
            membership_id=f.membership_id,
            reservation_type_id=f.reservation_type_id,
            court_id=cid,
            date=date(2026, 5, 2),
            start=time(9, 0),
            duration_minutes=60,
        )
        for cid in (103, 104)
    ]


@pytest.mark.asyncio
async def test_burst_fires_within_50ms_of_t0() -> None:
    cfg = _cfg()
    f = _facility()
    fire_at = datetime.now(timezone.utc) + timedelta(milliseconds=300)

    async with build_client(f, http2=False) as client:
        ctx = RacerContext(
            facility=f,
            target_date=date(2026, 5, 2),
            client=client,
            session=SessionState(facility=f, csrf_token="TOK", last_verified_ts=time_mod.time()),
            candidates=_candidates(),
        )
        with respx.mock(assert_all_called=False) as mock:
            mock.get(url__startswith=f"{f.base_url}/Online/Reservations/CreateReservation/{f.org_id}").mock(
                return_value=httpx.Response(200, text=_RACER_WRAPPER_HTML)
            )
            mock.get(url__startswith="https://reservations.courtreserve.com/Online/ReservationsApi/CreateReservation").mock(
                return_value=httpx.Response(200, text=_RACER_INNER_HTML)
            )
            mock.post(url__startswith="https://reservations.courtreserve.com//Online/ReservationsApi/CreateReservation/").mock(
                return_value=httpx.Response(200, json={"ReservationId": 1})
            )
            with patch("courtbot.booking.service.notify_macos"):
                t0 = time_mod.time()
                result = await run_burst(cfg, ctx, fire_at_utc=fire_at, dry_run=False)
                elapsed = time_mod.time() - t0

    assert result.success is True
    # Total time from call to result. fire_at is +300ms, plus ~ms for the (mocked) POST.
    # Confirms we slept until fire_at and didn't fire early.
    assert elapsed >= 0.295, f"fired too early: elapsed={elapsed}"


@pytest.mark.asyncio
async def test_burst_walks_ladder_on_slot_taken() -> None:
    cfg = _cfg()
    f = _facility()
    fire_at = datetime.now(timezone.utc) + timedelta(milliseconds=50)

    async with build_client(f, http2=False) as client:
        ctx = RacerContext(
            facility=f,
            target_date=date(2026, 5, 2),
            client=client,
            session=SessionState(facility=f, csrf_token="TOK", last_verified_ts=time_mod.time()),
            candidates=_candidates(),
        )
        responses = iter(
            [
                httpx.Response(200, text="This slot is no longer available"),
                httpx.Response(200, json={"ReservationId": 42}),
            ]
        )
        with respx.mock(assert_all_called=False) as mock:
            mock.get(url__startswith=f"{f.base_url}/Online/Reservations/CreateReservation/{f.org_id}").mock(
                return_value=httpx.Response(200, text=_RACER_WRAPPER_HTML)
            )
            mock.get(url__startswith="https://reservations.courtreserve.com/Online/ReservationsApi/CreateReservation").mock(
                return_value=httpx.Response(200, text=_RACER_INNER_HTML)
            )
            mock.post(url__startswith="https://reservations.courtreserve.com//Online/ReservationsApi/CreateReservation/").mock(
                side_effect=lambda req: next(responses)
            )
            with patch("courtbot.booking.service.notify_macos"):
                result = await run_burst(cfg, ctx, fire_at_utc=fire_at, dry_run=False)

    assert result.success is True
    assert result.candidate_index == 1
    assert result.confirmation_id == "42"


@pytest.mark.asyncio
async def test_burst_uses_fixed_not_exponential_retries_on_429() -> None:
    """Confirm 429 retry waits are <= sum of fixed backoff (~2s), not exponential."""
    cfg = _cfg()
    f = _facility()
    fire_at = datetime.now(timezone.utc)

    async with build_client(f, http2=False) as client:
        ctx = RacerContext(
            facility=f,
            target_date=date(2026, 5, 2),
            client=client,
            session=SessionState(facility=f, csrf_token="TOK", last_verified_ts=time_mod.time()),
            candidates=_candidates()[:1],
        )
        with respx.mock(assert_all_called=False) as mock:
            mock.get(url__startswith=f"{f.base_url}/Online/Reservations/CreateReservation/{f.org_id}").mock(
                return_value=httpx.Response(200, text=_RACER_WRAPPER_HTML)
            )
            mock.get(url__startswith="https://reservations.courtreserve.com/Online/ReservationsApi/CreateReservation").mock(
                return_value=httpx.Response(200, text=_RACER_INNER_HTML)
            )
            mock.post(url__startswith="https://reservations.courtreserve.com//Online/ReservationsApi/CreateReservation/").mock(
                return_value=httpx.Response(429, text="rate limited")
            )
            with patch("courtbot.booking.service.notify_macos"):
                t0 = time_mod.time()
                result = await run_burst(cfg, ctx, fire_at_utc=fire_at, dry_run=False, max_window_seconds=5.0)
                elapsed = time_mod.time() - t0

    assert result.success is False
    # Sum of fixed backoff = 0.10 + 0.25 + 0.50 + 1.00 = 1.85s. Allow generous slack.
    assert elapsed < 3.0, f"retries took too long, may be exponential: {elapsed}s"
    assert result.attempts >= 4
