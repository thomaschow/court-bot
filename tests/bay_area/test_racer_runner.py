import time as time_mod
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx

from bay_area_courtbot.auth.session import SessionState, build_client
from bay_area_courtbot.config import (
    BookingWindow,
    Config,
    Court,
    Defaults,
    Facility,
    PreferenceRule,
    Preferences,
    TimeWindow,
)
from bay_area_courtbot.courtreserve.modal import ModalState
from bay_area_courtbot.courtreserve.payloads import BookingCandidate
from bay_area_courtbot.racer.prewarm import PrebuiltAttempt, RacerContext
from bay_area_courtbot.racer.runner import run_burst

POST_URL = "https://reservations.courtreserve.com//Online/ReservationsApi/CreateReservation/13234?uiCulture=en-US"


def _prebuilt(cands: list[BookingCandidate]) -> list[PrebuiltAttempt]:
    return [PrebuiltAttempt(candidate=c, encoded_body=f"CourtId={c.court_id}") for c in cands]


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
            modal=ModalState(csrf_token="TOK", inner_form_url=POST_URL, hidden_fields={}),
            prebuilt=_prebuilt(_candidates()),
            post_url=POST_URL,
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
            with patch("bay_area_courtbot.booking.service.notify_macos"):
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
            modal=ModalState(csrf_token="TOK", inner_form_url=POST_URL, hidden_fields={}),
            prebuilt=_prebuilt(_candidates()),
            post_url=POST_URL,
        )
        # Burst fanout fires 2 in parallel; first POST is "taken", second is "ReservationId 42".
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
            with patch("bay_area_courtbot.booking.service.notify_macos"):
                result = await run_burst(cfg, ctx, fire_at_utc=fire_at, dry_run=False)

    assert result.success is True
    assert result.candidate_index == 1
    assert result.confirmation_id == "42"


@pytest.mark.asyncio
async def test_burst_fires_in_parallel_not_sequential() -> None:
    """The whole point of the rebuild — POSTs in a burst must fire concurrently, so
    total burst time ≈ 1× POST latency (not Nx). We simulate slow 200ms POSTs and
    assert the 3-fanout burst completes in <400ms."""
    cfg = _cfg()
    f = _facility()
    fire_at = datetime.now(timezone.utc)

    async def _slow_response(req):
        await asyncio.sleep(0.2)
        return httpx.Response(200, text='{"isValid":false,"message":"taken"}')

    async with build_client(f, http2=False) as client:
        cands = [
            BookingCandidate(
                facility_id=f.id, org_id=f.org_id, member_id=f.member_id,
                membership_id=f.membership_id, reservation_type_id=f.reservation_type_id,
                court_id=cid, date=date(2026, 5, 2), start=time(9, 0), duration_minutes=60,
            )
            for cid in (101, 102, 103)
        ]
        ctx = RacerContext(
            facility=f, target_date=date(2026, 5, 2), client=client,
            session=SessionState(facility=f, csrf_token="TOK", last_verified_ts=time_mod.time()),
            modal=ModalState(csrf_token="TOK", inner_form_url=POST_URL, hidden_fields={}),
            prebuilt=_prebuilt(cands), post_url=POST_URL,
        )
        with respx.mock(assert_all_called=False) as mock:
            mock.post(url__startswith="https://reservations.courtreserve.com//Online/ReservationsApi/CreateReservation/").mock(
                side_effect=_slow_response
            )
            with patch("bay_area_courtbot.booking.service.notify_macos"):
                t0 = time_mod.perf_counter()
                result = await run_burst(cfg, ctx, fire_at_utc=fire_at, dry_run=False, parallel_fanout=3)
                elapsed = time_mod.perf_counter() - t0

    assert result.success is False
    # Sequential (3 × 200ms) would be ~600ms. Parallel ~200ms. Allow generous slack.
    assert elapsed < 0.40, f"burst was sequential not parallel: {elapsed*1000:.0f}ms"


@pytest.mark.asyncio
async def test_burst_short_circuits_on_all_courts_taken() -> None:
    """When the server returns 'all courts of this type have been reserved' even once,
    the racer must stop firing rather than walk the rest of the ladder — those POSTs
    will all fail the same way and just waste seconds."""
    cfg = _cfg()
    f = _facility()
    fire_at = datetime.now(timezone.utc)

    async with build_client(f, http2=False) as client:
        # 4 candidates spread across 2 bursts (fanout=3 default → 3 + 1).
        more_cands = [
            BookingCandidate(
                facility_id=f.id, org_id=f.org_id, member_id=f.member_id,
                membership_id=f.membership_id, reservation_type_id=f.reservation_type_id,
                court_id=cid, date=date(2026, 5, 2), start=time(9, 0), duration_minutes=60,
            )
            for cid in (101, 102, 103, 104)
        ]
        ctx = RacerContext(
            facility=f,
            target_date=date(2026, 5, 2),
            client=client,
            session=SessionState(facility=f, csrf_token="TOK", last_verified_ts=time_mod.time()),
            modal=ModalState(csrf_token="TOK", inner_form_url=POST_URL, hidden_fields={}),
            prebuilt=_prebuilt(more_cands),
            post_url=POST_URL,
        )
        with respx.mock(assert_all_called=False) as mock:
            post_mock = mock.post(url__startswith="https://reservations.courtreserve.com//Online/ReservationsApi/CreateReservation/").mock(
                return_value=httpx.Response(
                    200,
                    text='{"isValid":false,"message":"All courts of this type have been reserved."}',
                )
            )
            with patch("bay_area_courtbot.booking.service.notify_macos"):
                result = await run_burst(cfg, ctx, fire_at_utc=fire_at, dry_run=False, max_window_seconds=5.0)

    assert result.success is False
    # Fanout=3 means one burst = 3 POSTs. After the burst sees AllCourtsTaken, we stop.
    # Court 104 (the 4th candidate) must NOT have been attempted.
    assert post_mock.call_count == 3
    assert result.attempts == 3
