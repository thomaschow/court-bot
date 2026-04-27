from datetime import datetime, time

from courtbot.config import (
    PreferenceRule,
    Preferences,
    TimeWindow,
)
from courtbot.courtreserve.parsing import SlotView
from courtbot.watcher.diff import find_new_openings


def _slot(court_id: int, hour: int, *, available: bool = True) -> SlotView:
    return SlotView(
        court_id=court_id,
        start=datetime(2026, 5, 2, hour, 0),  # Saturday
        end=datetime(2026, 5, 2, hour + 1, 0),
        is_available=available,
        raw={},
    )


def _prefs(*, court_whitelist=None) -> Preferences:
    return Preferences(
        facility_rank=["santa-clara"],
        rules=[
            PreferenceRule(
                name="r",
                day_of_week=["Sat"],
                time_windows=[TimeWindow(start=time(8, 0), end=time(11, 0))],
                duration_minutes=60,
                court_whitelist=court_whitelist or [],
            )
        ],
    )


def test_baseline_first_snapshot_returns_nothing() -> None:
    prefs = _prefs()
    curr = [_slot(103, 9)]
    assert find_new_openings("santa-clara", [], curr, prefs) == []


def test_detects_newly_available_slot() -> None:
    prefs = _prefs()
    prev = [_slot(103, 9, available=False)]
    curr = [_slot(103, 9, available=True)]
    out = find_new_openings("santa-clara", prev, curr, prefs)
    assert len(out) == 1 and out[0].court_id == 103


def test_ignores_already_available_slot() -> None:
    prefs = _prefs()
    prev = [_slot(103, 9, available=True)]
    curr = [_slot(103, 9, available=True)]
    assert find_new_openings("santa-clara", prev, curr, prefs) == []


def test_filters_by_preference_window() -> None:
    prefs = _prefs()
    prev = [_slot(103, 14, available=False)]
    curr = [_slot(103, 14, available=True)]  # 2pm — outside 8-11 window
    assert find_new_openings("santa-clara", prev, curr, prefs) == []


def test_filters_by_court_whitelist() -> None:
    prefs = _prefs(court_whitelist=[104])
    prev = [_slot(103, 9, available=False), _slot(104, 9, available=False)]
    curr = [_slot(103, 9, available=True), _slot(104, 9, available=True)]
    out = find_new_openings("santa-clara", prev, curr, prefs)
    assert len(out) == 1 and out[0].court_id == 104
