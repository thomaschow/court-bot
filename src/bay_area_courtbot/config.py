from __future__ import annotations

from datetime import time as dtime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class Credentials(BaseModel):
    source: Literal["keyring", "env"] = "keyring"
    keyring_service: str = "bay_area_courtbot"


class Defaults(BaseModel):
    timezone: str = "America/Los_Angeles"
    dry_run: bool = False
    log_level: str = "INFO"


class BookingWindow(BaseModel):
    days_ahead: int = Field(ge=1, le=30)
    opens_at_local: dtime
    server_clock_offset_ms: int = 0


class Polling(BaseModel):
    interval_seconds: int = Field(default=45, ge=10)
    quiet_hours_local: tuple[dtime, dtime] | None = None
    horizon_days: int = Field(default=7, ge=1, le=30)


class Court(BaseModel):
    id: int
    name: str
    surface: str | None = None


class Facility(BaseModel):
    id: str
    org_id: int
    s_id: int | None = None
    base_url: str = "https://app.courtreserve.com"
    booking_window: BookingWindow
    polling: Polling = Field(default_factory=Polling)
    member_id: int | None = None
    courts: list[Court] = Field(default_factory=list)
    reservation_type_id: int | None = None
    membership_id: int | None = None
    cost_type_id: int | None = None
    timezone: str = "America/Los_Angeles"
    reservation_min_interval: int = 60


class TimeWindow(BaseModel):
    start: dtime
    end: dtime

    @model_validator(mode="after")
    def _ordered(self) -> "TimeWindow":
        if self.start >= self.end:
            raise ValueError(f"time window start ({self.start}) must be before end ({self.end})")
        return self


class PreferenceRule(BaseModel):
    name: str
    day_of_week: list[str]
    time_windows: list[TimeWindow]
    duration_minutes: int = Field(ge=30, le=240)
    court_whitelist: list[int] = Field(default_factory=list)
    reservation_type: str = "singles"
    participants: int | None = None

    @field_validator("day_of_week")
    @classmethod
    def _valid_days(cls, v: list[str]) -> list[str]:
        valid = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
        bad = set(v) - valid
        if bad:
            raise ValueError(f"invalid day_of_week values: {sorted(bad)}")
        return v


class Preferences(BaseModel):
    facility_rank: list[str]
    rules: list[PreferenceRule]


class Notifications(BaseModel):
    macos: bool = True
    webhook_url: str | None = None
    on_success: bool = True
    on_failure: bool = True
    on_dry_run: bool = False


class Config(BaseModel):
    version: int = 1
    credentials: Credentials = Field(default_factory=Credentials)
    defaults: Defaults = Field(default_factory=Defaults)
    facilities: list[Facility]
    preferences: Preferences
    notifications: Notifications = Field(default_factory=Notifications)

    @model_validator(mode="after")
    def _cross_refs(self) -> "Config":
        ids = {f.id for f in self.facilities}
        unknown = set(self.preferences.facility_rank) - ids
        if unknown:
            raise ValueError(f"preferences.facility_rank references unknown facilities: {unknown}")
        all_court_ids = {c.id for f in self.facilities for c in f.courts}
        for rule in self.preferences.rules:
            if rule.court_whitelist and all_court_ids:
                bad = set(rule.court_whitelist) - all_court_ids
                if bad:
                    raise ValueError(
                        f"rule '{rule.name}' references unknown court IDs: {sorted(bad)}"
                    )
        return self

    def facility(self, facility_id: str) -> Facility:
        for f in self.facilities:
            if f.id == facility_id:
                return f
        raise KeyError(f"facility '{facility_id}' not found in config")


def load_config(path: Path | str) -> Config:
    path = Path(path)
    with path.open("r") as fh:
        data = yaml.safe_load(fh)
    return Config.model_validate(data)


def save_config(cfg: Config, path: Path | str) -> None:
    path = Path(path)
    backup = path.with_suffix(path.suffix + ".bak")
    if path.exists():
        backup.write_bytes(path.read_bytes())
    with path.open("w") as fh:
        yaml.safe_dump(
            cfg.model_dump(mode="json", exclude_none=False),
            fh,
            sort_keys=False,
            default_flow_style=False,
        )
