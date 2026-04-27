from datetime import date, time

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
from courtbot.racer.strategy import rank_candidates


def _cfg(*, court_whitelist=None, two_facilities=False) -> Config:
    facilities = [
        Facility(
            id="santa-clara",
            org_id=13234,
            booking_window=BookingWindow(days_ahead=7, opens_at_local=time(7, 0)),
            member_id=111,
            membership_id=1,
            reservation_type_id=2,
            courts=[Court(id=103, name="C3"), Court(id=104, name="C4")],
        )
    ]
    if two_facilities:
        facilities.append(
            Facility(
                id="sunnyvale",
                org_id=99999,
                booking_window=BookingWindow(days_ahead=7, opens_at_local=time(7, 0)),
                member_id=222,
                membership_id=1,
                reservation_type_id=2,
                courts=[Court(id=201, name="C1")],
            )
        )
    return Config(
        defaults=Defaults(),
        facilities=facilities,
        preferences=Preferences(
            facility_rank=["santa-clara", "sunnyvale"] if two_facilities else ["santa-clara"],
            rules=[
                PreferenceRule(
                    name="r",
                    day_of_week=["Sat"],
                    time_windows=[TimeWindow(start=time(9, 0), end=time(11, 0))],
                    duration_minutes=60,
                    court_whitelist=court_whitelist or [],
                )
            ],
        ),
    )


def test_rank_candidates_filters_by_day_and_time() -> None:
    cfg = _cfg()
    saturday = date(2026, 5, 2)
    starts = [time(8, 0), time(9, 0), time(9, 30), time(10, 0), time(11, 0)]
    out = rank_candidates(cfg, saturday, candidate_starts=starts)
    starts_seen = sorted({c.start for _, c in out})
    assert starts_seen == [time(9, 0), time(9, 30), time(10, 0)]


def test_rank_filters_by_weekday() -> None:
    cfg = _cfg()
    sunday = date(2026, 5, 3)
    out = rank_candidates(cfg, sunday, candidate_starts=[time(9, 0)])
    assert out == []


def test_facility_rank_orders_results() -> None:
    cfg = _cfg(two_facilities=True)
    saturday = date(2026, 5, 2)
    out = rank_candidates(cfg, saturday, candidate_starts=[time(9, 0)])
    assert out[0][1].facility_id == "santa-clara"
    assert any(c.facility_id == "sunnyvale" for _, c in out)


def test_court_whitelist_excludes_others() -> None:
    cfg = _cfg(court_whitelist=[103])
    saturday = date(2026, 5, 2)
    out = rank_candidates(cfg, saturday, candidate_starts=[time(9, 0)])
    assert {c.court_id for _, c in out} == {103}


def test_skips_facilities_missing_member_id() -> None:
    cfg = _cfg()
    cfg.facilities[0].member_id = None
    saturday = date(2026, 5, 2)
    assert rank_candidates(cfg, saturday, candidate_starts=[time(9, 0)]) == []
