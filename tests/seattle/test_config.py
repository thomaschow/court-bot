import textwrap
from pathlib import Path

import pytest

from seattle_courtbot.config import Config, load_config


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "seattle.yaml"
    p.write_text(textwrap.dedent(body))
    return p


VALID = """
version: 1
preferences:
  time_window:
    start: "18:00"
    end: "21:00"
"""


def test_load_minimal_valid(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, VALID))
    assert isinstance(cfg, Config)
    assert cfg.preferences.time_window.start.hour == 18
    assert cfg.preferences.time_window.end.hour == 21


def test_window_start_must_be_before_end(tmp_path: Path) -> None:
    bad = VALID.replace('start: "18:00"', 'start: "22:00"')
    with pytest.raises(ValueError, match="must be before"):
        load_config(_write(tmp_path, bad))


def test_facility_rank_must_match_known_facility(tmp_path: Path) -> None:
    body = VALID + 'facilities:\n  - {id: ll, name: "Lower Loaded", facility_id: 1}\n'
    body += 'preferences:\n  time_window: {start: "18:00", end: "21:00"}\n  facility_rank: [unknown]\n'
    p = tmp_path / "seattle.yaml"
    p.write_text(textwrap.dedent(body))
    with pytest.raises(ValueError, match="unknown facilities"):
        load_config(p)


def test_duration_min_le_max(tmp_path: Path) -> None:
    body = VALID + "  duration_min_minutes: 120\n  duration_max_minutes: 60\n"
    p = tmp_path / "seattle.yaml"
    p.write_text(textwrap.dedent(body))
    with pytest.raises(ValueError):
        load_config(p)


def test_pairing_defaults_match_bay_area_rule(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, VALID))
    p = cfg.preferences.pairing
    assert p.slot_duration_min == 30
    assert p.max_any_court_gap_min == 0
    assert p.max_same_court_gap_min == 30
