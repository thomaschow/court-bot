from pathlib import Path

import pytest

from courtbot import ledger as L


def _kw(**over):
    base = dict(
        facility="santa-clara",
        date="2026-05-03",
        start_time="09:00",
        duration_minutes=60,
        court_id=103,
        mode="manual",
    )
    base.update(over)
    return base


def test_record_confirmed(tmp_path: Path) -> None:
    p = tmp_path / "ledger.sqlite"
    L.record_confirmed(path=p, **_kw(), confirmation_id="C1")
    rows = L.list_recent(path=p)
    assert len(rows) == 1
    assert rows[0].status == "confirmed"
    assert rows[0].confirmation_id == "C1"


def test_already_confirmed_check(tmp_path: Path) -> None:
    p = tmp_path / "ledger.sqlite"
    assert L.is_already_confirmed(
        facility="santa-clara", date="2026-05-03", start_time="09:00", court_id=103, path=p
    ) is False
    L.record_confirmed(path=p, **_kw(), confirmation_id="C1")
    assert L.is_already_confirmed(
        facility="santa-clara", date="2026-05-03", start_time="09:00", court_id=103, path=p
    ) is True


def test_duplicate_record_confirmed_raises(tmp_path: Path) -> None:
    p = tmp_path / "ledger.sqlite"
    L.record_confirmed(path=p, **_kw(), confirmation_id="C1")
    with pytest.raises(L.AlreadyConfirmed):
        L.record_confirmed(path=p, **_kw(), confirmation_id="C2")


def test_record_attempt_does_not_raise_on_duplicate(tmp_path: Path) -> None:
    p = tmp_path / "ledger.sqlite"
    for _ in range(5):
        L.record_attempt(path=p, **_kw(), status="failed", error="rate limited")
    rows = L.list_recent(path=p)
    assert len([r for r in rows if r.status == "failed"]) == 5


def test_different_court_is_not_duplicate(tmp_path: Path) -> None:
    p = tmp_path / "ledger.sqlite"
    L.record_confirmed(path=p, **_kw(), confirmation_id="C1")
    L.record_confirmed(path=p, **_kw(court_id=104), confirmation_id="C2")
    assert len(L.list_recent(path=p)) == 2
