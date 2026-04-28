from pathlib import Path

from courtbot import ledger as L


def test_record_and_list_discarded(tmp_path: Path) -> None:
    p = tmp_path / "ledger.sqlite"
    L.record_discarded(
        facility="santa-clara", date="2026-05-04", start_time="20:30",
        court_id=52101, duration_minutes=30, reason="no_30min_partner", path=p,
    )
    L.record_discarded(
        facility="sunnyvale", date="2026-05-05", start_time="18:00",
        court_id=52028, duration_minutes=30, reason="no_30min_partner", path=p,
    )
    rows = L.list_discarded(path=p)
    assert len(rows) == 2
    # Newest first
    assert rows[0].facility == "sunnyvale"
    assert rows[0].court_id == 52028
    assert rows[1].reason == "no_30min_partner"


def test_list_discarded_respects_limit(tmp_path: Path) -> None:
    p = tmp_path / "ledger.sqlite"
    for i in range(5):
        L.record_discarded(
            facility="santa-clara", date=f"2026-05-{i+1:02d}", start_time="20:30",
            court_id=52101, reason="no_30min_partner", path=p,
        )
    assert len(L.list_discarded(limit=3, path=p)) == 3
