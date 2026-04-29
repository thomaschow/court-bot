from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from bay_area_courtbot.config import (
    BookingWindow,
    Config,
    Defaults,
    Facility,
    PreferenceRule,
    Preferences,
    TimeWindow,
)
from bay_area_courtbot.scheduling import fire_at_for_launchd, plan_next_firings, render_racer_plist


TZ = "America/Los_Angeles"


def _cfg() -> Config:
    return Config(
        defaults=Defaults(timezone=TZ),
        facilities=[
            Facility(
                id="santa-clara",
                org_id=13234,
                booking_window=BookingWindow(days_ahead=7, opens_at_local=time(7, 0)),
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


def test_plan_next_firings_returns_facility() -> None:
    cfg = _cfg()
    plan = plan_next_firings(cfg, lookahead_hours=48)
    assert len(plan) == 1
    facility, when = plan[0]
    assert facility.id == "santa-clara"
    assert when.tzinfo is not None
    assert when.time() == time(7, 0)


def test_fire_at_for_launchd_minus_60s() -> None:
    opens = datetime(2026, 5, 1, 7, 0, tzinfo=ZoneInfo(TZ))
    fire = fire_at_for_launchd(opens, prewarm_seconds=60)
    assert opens - fire == timedelta(seconds=60)


def test_render_racer_plist_substitutes_fields() -> None:
    f = _cfg().facility("santa-clara")
    out = render_racer_plist(
        f,
        courtbot_bin="/usr/local/bin/bay_area_courtbot",
        config_path="/cfg.yaml",
        log_dir="/var/log/bay_area_courtbot",
        fire_at_local=datetime(2026, 5, 1, 6, 59),
    )
    assert "ai.zipline.bay-area-courtbot.racer.13234" in out
    assert "<string>santa-clara</string>" in out
    assert "<integer>6</integer>" in out  # FIRE_HOUR
    assert "<integer>59</integer>" in out  # FIRE_MINUTE
    assert "/usr/local/bin/bay_area_courtbot" in out
    assert "/cfg.yaml" in out
    assert "{{" not in out, "all template placeholders should be substituted"
