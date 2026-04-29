from datetime import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from bay_area_courtbot import ledger as L
from bay_area_courtbot.config import (
    BookingWindow,
    Config,
    Defaults,
    Facility,
    PreferenceRule,
    Preferences,
    TimeWindow,
)
from bay_area_courtbot.web.app import create_app


def _write_config(tmp_path: Path) -> Path:
    cfg = Config(
        defaults=Defaults(),
        facilities=[
            Facility(
                id="santa-clara",
                org_id=13234,
                booking_window=BookingWindow(days_ahead=7, opens_at_local=time(7, 0)),
                member_id=999,
                membership_id=1,
                reservation_type_id=2,
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
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(cfg.model_dump(mode="json")))
    return p


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COURTBOT_ROOT", str(tmp_path))
    (tmp_path / "state").mkdir()


def test_status_page_renders(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path)
    client = TestClient(create_app(cfg_path))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "santa-clara" in resp.text
    assert "Next window opens" in resp.text


def test_bookings_page_empty(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path)
    client = TestClient(create_app(cfg_path))
    resp = client.get("/bookings")
    assert resp.status_code == 200
    assert "No bookings yet" in resp.text


def test_bookings_page_with_rows(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path)
    L.record_confirmed(
        facility="santa-clara",
        date="2026-05-03",
        start_time="09:00",
        duration_minutes=60,
        court_id=103,
        mode="manual",
        confirmation_id="ABC",
    )
    client = TestClient(create_app(cfg_path))
    resp = client.get("/bookings")
    assert resp.status_code == 200
    assert "ABC" in resp.text
    assert "santa-clara" in resp.text


def test_config_page_shows_yaml(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path)
    client = TestClient(create_app(cfg_path))
    resp = client.get("/config")
    assert resp.status_code == 200
    assert "santa-clara" in resp.text


def test_config_save_validates_and_writes(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path)
    client = TestClient(create_app(cfg_path))
    new_yaml = cfg_path.read_text().replace("days_ahead: 7", "days_ahead: 5")
    resp = client.post("/config", data={"yaml_text": new_yaml}, follow_redirects=False)
    assert resp.status_code == 303
    assert "days_ahead: 5" in cfg_path.read_text()


def test_config_save_rejects_invalid(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path)
    client = TestClient(create_app(cfg_path))
    resp = client.post("/config", data={"yaml_text": "not: [valid: config"})
    assert resp.status_code == 400
