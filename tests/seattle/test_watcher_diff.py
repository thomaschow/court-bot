import json
from datetime import date, time

from seattle_courtbot.config import PairingRule
from seattle_courtbot.watcher.diff import find_new_keys
from seattle_courtbot.watcher.poller import (
    SliceKey,
    capture_neighbors,
    gap_minutes,
    has_partner,
)


def _key(facility="lower-woodland", court=355, hour=18, minute=0,
         d=date(2026, 5, 4)) -> SliceKey:
    return SliceKey(
        facility_id=facility, date_ord=d.toordinal(),
        start_minutes=hour * 60 + minute, court_id=court,
    )


def test_find_new_keys_baseline_returns_empty() -> None:
    assert find_new_keys(set(), {_key()}) == set()


def test_find_new_keys_returns_diff() -> None:
    a, b, c = _key(court=1), _key(court=2), _key(court=3)
    assert find_new_keys({a, b}, {a, b, c}) == {c}


def test_gap_minutes_adjacent_is_zero() -> None:
    assert gap_minutes(30, slot_duration_min=30) == 0
    assert gap_minutes(-30, slot_duration_min=30) == 0


def test_gap_minutes_30min_gap_with_30min_slots() -> None:
    assert gap_minutes(60, slot_duration_min=30) == 30


def test_gap_minutes_overlapping_returns_negative() -> None:
    assert gap_minutes(15, slot_duration_min=30) == -1


_RULE = PairingRule(slot_duration_min=30, max_any_court_gap_min=0, max_same_court_gap_min=30)


def test_has_partner_adjacent_any_court() -> None:
    snapshot = {_key(court=1), _key(court=2, minute=30)}
    assert has_partner(snapshot, "lower-woodland", date(2026, 5, 4), time(18, 0), 1, _RULE)


def test_has_partner_same_court_30min_gap() -> None:
    snapshot = {_key(court=1), _key(court=1, hour=19)}
    assert has_partner(snapshot, "lower-woodland", date(2026, 5, 4), time(18, 0), 1, _RULE)


def test_has_partner_diff_court_30min_gap_rejected() -> None:
    snapshot = {_key(court=1), _key(court=2, hour=19)}
    assert not has_partner(snapshot, "lower-woodland", date(2026, 5, 4), time(18, 0), 1, _RULE)


def test_has_partner_same_court_60min_gap_rejected_under_default_rule() -> None:
    snapshot = {_key(court=1), _key(court=1, hour=19, minute=30)}
    assert not has_partner(snapshot, "lower-woodland", date(2026, 5, 4), time(18, 0), 1, _RULE)


def test_capture_neighbors_qualifies_under() -> None:
    snapshot = {
        _key(court=1),
        _key(court=2, minute=30),               # adjacent any court → all 3 levels
        _key(court=1, hour=19),                  # same court 30-gap → levels 2, 3
        _key(court=1, hour=19, minute=30),        # same court 60-gap → level 3
    }
    raw = capture_neighbors(
        snapshot, "lower-woodland", date(2026, 5, 4), time(18, 0), court_id=1,
    )
    nbrs = {(n["court_id"], n["delta_min"]): n["qualifies_under"] for n in json.loads(raw)}
    assert "adjacent_any_court" in nbrs[(2, 30)]
    assert "same_court_30min_gap" in nbrs[(1, 60)]
    assert "same_court_60min_gap" in nbrs[(1, 90)]
    assert nbrs[(1, 60)] == ["same_court_30min_gap", "same_court_60min_gap"]
