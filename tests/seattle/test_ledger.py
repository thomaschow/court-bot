from pathlib import Path

import pytest

from seattle_courtbot import ledger as L


def _kw(**over):
    base = dict(
        facility="lower-woodland", date="2026-05-04", start_time="18:00",
        duration_minutes=60, court_id=355, mode="manual",
    )
    base.update(over)
    return base


def test_record_confirmed(tmp_path: Path) -> None:
    p = tmp_path / "ledger.sqlite"
    L.record_confirmed(path=p, **_kw(), confirmation_id="C1")
    rows = L.list_recent(path=p)
    assert len(rows) == 1 and rows[0].confirmation_id == "C1"


def test_already_confirmed_check(tmp_path: Path) -> None:
    p = tmp_path / "ledger.sqlite"
    assert not L.is_already_confirmed(
        facility="lower-woodland", date="2026-05-04", start_time="18:00", court_id=355, path=p
    )
    L.record_confirmed(path=p, **_kw(), confirmation_id="C1")
    assert L.is_already_confirmed(
        facility="lower-woodland", date="2026-05-04", start_time="18:00", court_id=355, path=p
    )


def test_duplicate_record_confirmed_raises(tmp_path: Path) -> None:
    p = tmp_path / "ledger.sqlite"
    L.record_confirmed(path=p, **_kw(), confirmation_id="A")
    with pytest.raises(L.AlreadyConfirmed):
        L.record_confirmed(path=p, **_kw(), confirmation_id="B")


def test_discarded_with_neighbors(tmp_path: Path) -> None:
    p = tmp_path / "ledger.sqlite"
    L.record_discarded(
        facility="lower-woodland", date="2026-05-04", start_time="20:30",
        court_id=355, duration_minutes=30, reason="no_partner",
        neighbors='[{"court_id": 356, "start": "20:00", "delta_min": -30, "qualifies_under": ["adjacent_any_court"]}]',
        path=p,
    )
    rows = L.list_discarded(path=p)
    assert rows[0].reason == "no_partner"
    assert "adjacent_any_court" in rows[0].neighbors
