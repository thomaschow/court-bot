from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

from courtbot.config import Config, Facility
from courtbot.logging import get_logger
from courtbot.paths import project_root, state_dir
from courtbot.timeutil import next_window_open

LAUNCHAGENTS = Path.home() / "Library" / "LaunchAgents"
RACER_TMPL = project_root() / "launchd" / "ai.zipline.courtbot.racer.plist.tmpl"


def render_racer_plist(
    facility: Facility,
    *,
    courtbot_bin: str,
    config_path: str,
    log_dir: str,
    fire_at_local: datetime,
) -> str:
    template = RACER_TMPL.read_text()
    return (
        template.replace("{{ORG_ID}}", str(facility.org_id))
        .replace("{{FACILITY_ID}}", facility.id)
        .replace("{{COURTBOT_BIN}}", courtbot_bin)
        .replace("{{CONFIG_PATH}}", config_path)
        .replace("{{LOG_DIR}}", log_dir)
        .replace("{{FIRE_HOUR}}", str(fire_at_local.hour))
        .replace("{{FIRE_MINUTE}}", str(fire_at_local.minute))
    )


def install_racer_for_facility(
    facility: Facility,
    *,
    fire_at_local: datetime,
    courtbot_bin: str,
    config_path: str,
) -> Path:
    log = get_logger(facility=facility.id, mode="schedule")
    LAUNCHAGENTS.mkdir(parents=True, exist_ok=True)
    out = LAUNCHAGENTS / f"ai.zipline.courtbot.racer.{facility.org_id}.plist"
    plist = render_racer_plist(
        facility,
        courtbot_bin=courtbot_bin,
        config_path=config_path,
        log_dir=str(state_dir() / "logs"),
        fire_at_local=fire_at_local,
    )
    out.write_text(plist)
    label = out.stem
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"], check=False)
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(out)], check=True)
    log.info(
        "schedule.racer.loaded",
        plist=str(out),
        fire_hour=fire_at_local.hour,
        fire_minute=fire_at_local.minute,
    )
    return out


def schedule_pmset_wake(when_local: datetime) -> None:
    """Schedule a one-shot wake-from-sleep via pmset. Requires sudo NOPASSWD."""
    if not shutil.which("pmset"):
        raise RuntimeError("pmset not found")
    when = when_local.strftime("%m/%d/%y %H:%M:%S")
    cmd = ["sudo", "-n", "/usr/bin/pmset", "schedule", "wake", when]
    log = get_logger(mode="schedule")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        log.warning(
            "schedule.pmset.failed",
            stderr=res.stderr.strip(),
            hint="add a sudoers entry: yourname ALL=(root) NOPASSWD: /usr/bin/pmset schedule wake *",
        )
        raise RuntimeError(f"pmset schedule wake failed: {res.stderr.strip()}")
    log.info("schedule.pmset.ok", when=when)


def plan_next_firings(cfg: Config, *, lookahead_hours: int = 24) -> list[tuple[Facility, datetime]]:
    """For each facility, compute the next booking-window-open moment within lookahead."""
    out: list[tuple[Facility, datetime]] = []
    for f in cfg.facilities:
        opens = next_window_open(
            days_ahead=f.booking_window.days_ahead,
            opens_at_local=f.booking_window.opens_at_local,
            tz=cfg.defaults.timezone,
        )
        if (opens - datetime.now(opens.tzinfo)) <= timedelta(hours=lookahead_hours):
            out.append((f, opens))
    return out


def fire_at_for_launchd(opens_at: datetime, *, prewarm_seconds: int = 60) -> datetime:
    """Earliest time we want launchd to fire the racer process: T - prewarm_seconds."""
    return opens_at - timedelta(seconds=prewarm_seconds)
