from datetime import date, time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx

from bay_area_courtbot import paths
from bay_area_courtbot.booking.service import book
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
from bay_area_courtbot.courtreserve.payloads import BookingCandidate


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COURTBOT_ROOT", str(tmp_path))
    (tmp_path / "state").mkdir()


def _cfg() -> Config:
    return Config(
        defaults=Defaults(),
        facilities=[
            Facility(
                id="santa-clara",
                org_id=13234,
                booking_window=BookingWindow(days_ahead=7, opens_at_local=time(7, 0)),
                member_id=999111,
                membership_id=42,
                reservation_type_id=2,
                courts=[Court(id=103, name="Court 3")],
            )
        ],
        preferences=Preferences(
            facility_rank=["santa-clara"],
            rules=[
                PreferenceRule(
                    name="r",
                    day_of_week=["Sat"],
                    time_windows=[TimeWindow(start=time(8, 0), end=time(11, 0))],
                    duration_minutes=60,
                )
            ],
        ),
    )


def _cand() -> BookingCandidate:
    return BookingCandidate(
        facility_id="santa-clara",
        org_id=13234,
        member_id=999111,
        membership_id=42,
        reservation_type_id=2,
        court_id=103,
        date=date(2026, 5, 3),
        start=time(9, 0),
        duration_minutes=60,
    )


@pytest.mark.asyncio
async def test_dry_run_does_not_call_courtreserve() -> None:
    cfg = _cfg()
    with patch("bay_area_courtbot.booking.service.notify_macos"):
        result = await book(cfg, cfg.facility("santa-clara"), _cand(), mode="manual", dry_run=True)
    assert result.status == "dry_run"
    assert result.confirmation_id is None


@pytest.mark.asyncio
async def test_already_confirmed_short_circuits() -> None:
    """Once a slot is confirmed in the ledger, subsequent attempts return 'duplicate'."""
    from bay_area_courtbot import ledger as L

    cfg = _cfg()
    L.record_confirmed(
        facility="santa-clara",
        date="2026-05-03",
        start_time="09:00",
        duration_minutes=60,
        court_id=103,
        mode="manual",
        confirmation_id="PRIOR",
    )
    with patch("bay_area_courtbot.booking.service.notify_macos"):
        result = await book(cfg, cfg.facility("santa-clara"), _cand(), dry_run=True)
    assert result.status == "duplicate"


@pytest.mark.asyncio
async def test_dry_run_can_repeat() -> None:
    """Repeated dry-runs are NOT blocked — the ledger only locks confirmed bookings."""
    cfg = _cfg()
    with patch("bay_area_courtbot.booking.service.notify_macos"):
        first = await book(cfg, cfg.facility("santa-clara"), _cand(), dry_run=True)
        second = await book(cfg, cfg.facility("santa-clara"), _cand(), dry_run=True)
    assert first.status == "dry_run"
    assert second.status == "dry_run"


_INNER_MODAL_HTML = """
<html><body>
  <form action="https://reservations.courtreserve.com//Online/ReservationsApi/CreateReservation/13234?uiCulture=en-US">
    <input name="__RequestVerificationToken" type="hidden" value="MODAL_CSRF" />
    <input name="Id" type="hidden" value="13234" />
    <input name="OrgId" type="hidden" value="13234" />
    <input name="MemberId" type="hidden" value="999111" />
    <input name="MembershipId" type="hidden" value="42" />
    <input name="CustomSchedulerId" type="hidden" value="" />
    <input name="Date" type="hidden" value="5/3/2026 12:00:00 AM" />
    <input name="RequestData" type="hidden" value="REQ_TOKEN" />
    <input name="ReservationLotteryGuid" type="hidden" value="guid-1" />
  </form>
</body></html>
"""


def _wrapper_html() -> str:
    return """
    <html><body>
    <script>
      $.ajax({
        url: fixUrl('https://reservations.courtreserve.com/Online/ReservationsApi/CreateReservation?id=13234'),
        type: "GET"
      });
    </script>
    </body></html>
    """


@pytest.mark.asyncio
async def test_real_booking_path_with_mocked_endpoints() -> None:
    from bay_area_courtbot.auth.session import build_client

    cfg = _cfg()
    f = cfg.facility("santa-clara")

    async with build_client(f, http2=False) as client:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(url__startswith=f"{f.base_url}/Online/Reservations/Bookings/{f.org_id}").mock(
                return_value=httpx.Response(
                    200,
                    text='<input name="__RequestVerificationToken" value="HYDRATE_CSRF" />',
                )
            )
            mock.get(url__startswith=f"{f.base_url}/Online/Reservations/CreateReservation/{f.org_id}").mock(
                return_value=httpx.Response(200, text=_wrapper_html())
            )
            mock.get(url__startswith="https://reservations.courtreserve.com/Online/ReservationsApi/CreateReservation").mock(
                return_value=httpx.Response(200, text=_INNER_MODAL_HTML)
            )
            mock.post(
                url__startswith="https://reservations.courtreserve.com//Online/ReservationsApi/CreateReservation/"
            ).mock(return_value=httpx.Response(200, json={"ReservationId": 7777}))
            with patch("bay_area_courtbot.booking.service.notify_macos"):
                result = await book(cfg, f, _cand(), mode="manual", dry_run=False, client=client)

    assert result.status == "confirmed"
    assert result.confirmation_id == "7777"


@pytest.mark.asyncio
async def test_slot_taken_marks_failed() -> None:
    from bay_area_courtbot.auth.session import build_client

    cfg = _cfg()
    f = cfg.facility("santa-clara")
    async with build_client(f, http2=False) as client:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(url__startswith=f"{f.base_url}/Online/Reservations/Bookings/{f.org_id}").mock(
                return_value=httpx.Response(
                    200,
                    text='<input name="__RequestVerificationToken" value="HYDRATE_CSRF" />',
                )
            )
            mock.get(url__startswith=f"{f.base_url}/Online/Reservations/CreateReservation/{f.org_id}").mock(
                return_value=httpx.Response(200, text=_wrapper_html())
            )
            mock.get(url__startswith="https://reservations.courtreserve.com/Online/ReservationsApi/CreateReservation").mock(
                return_value=httpx.Response(200, text=_INNER_MODAL_HTML)
            )
            mock.post(
                url__startswith="https://reservations.courtreserve.com//Online/ReservationsApi/CreateReservation/"
            ).mock(return_value=httpx.Response(200, text="This slot is no longer available"))
            with patch("bay_area_courtbot.booking.service.notify_macos"):
                result = await book(cfg, f, _cand(), dry_run=False, client=client)
    assert result.status == "failed"
    assert "SlotTaken" in (result.error or "")
