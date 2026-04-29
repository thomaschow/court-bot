import textwrap
from pathlib import Path

import pytest
import yaml

from bay_area_courtbot.config import Config, load_config


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(body))
    return p


VALID = """
version: 1
facilities:
  - id: santa-clara
    org_id: 13234
    booking_window:
      days_ahead: 7
      opens_at_local: "07:00:00"
preferences:
  facility_rank: [santa-clara]
  rules:
    - name: "Sat AM"
      day_of_week: [Sat]
      time_windows: [{start: "08:00", end: "11:00"}]
      duration_minutes: 60
"""


def test_load_minimal_valid_config(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, VALID))
    assert isinstance(cfg, Config)
    assert cfg.facility("santa-clara").org_id == 13234


def test_facility_rank_must_reference_known_facility(tmp_path: Path) -> None:
    bad = VALID.replace("[santa-clara]", "[unknown-place]")
    with pytest.raises(ValueError, match="unknown facilities"):
        load_config(_write(tmp_path, bad))


def test_time_window_start_before_end(tmp_path: Path) -> None:
    bad = VALID.replace('start: "08:00", end: "11:00"', 'start: "11:00", end: "08:00"')
    with pytest.raises(ValueError, match="must be before"):
        load_config(_write(tmp_path, bad))


def test_invalid_day_of_week(tmp_path: Path) -> None:
    bad = VALID.replace("[Sat]", "[Funday]")
    with pytest.raises(ValueError, match="invalid day_of_week"):
        load_config(_write(tmp_path, bad))


def test_court_whitelist_must_match_known_courts(tmp_path: Path) -> None:
    body = yaml.safe_load(VALID)
    body["facilities"][0]["courts"] = [{"id": 101, "name": "Court 1"}]
    body["preferences"]["rules"][0]["court_whitelist"] = [999]
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(body))
    with pytest.raises(ValueError, match="unknown court IDs"):
        load_config(p)
