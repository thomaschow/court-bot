from __future__ import annotations

from datetime import time as dtime
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class Credentials(BaseModel):
    source: str = "keyring"
    keyring_service: str = "seattle-courtbot"


class Defaults(BaseModel):
    timezone: str = "America/Los_Angeles"
    log_level: str = "INFO"


class Court(BaseModel):
    """A single ANC reservable resource (one tennis court). The fields populated
    here come from the discover/probe.py output. `resource_id` is ANC's primary
    key for the court; some sites also expose a `subgroup_id` (court-type bucket)."""
    id: int                     # ANC resource_id
    name: str
    subgroup_id: int | None = None


class Facility(BaseModel):
    """A single Seattle Parks tennis site. ANC organises resources by `facility_id`
    (a tennis center) and each facility owns N courts. `tenant_slug` defaults to
    "seattle"; the same adapter could later target other ANC tenants (Cupertino,
    San Jose, etc.) by overriding it per-facility."""
    id: str                     # human-readable slug, e.g., "lower-woodland"
    name: str
    facility_id: int            # ANC's internal numeric id
    tenant_slug: str = "seattle"
    courts: list[Court] = Field(default_factory=list)


class TimeWindow(BaseModel):
    start: dtime
    end: dtime

    @model_validator(mode="after")
    def _ordered(self) -> "TimeWindow":
        if self.start >= self.end:
            raise ValueError(f"window start ({self.start}) must be before end ({self.end})")
        return self


class PairingRule(BaseModel):
    slot_duration_min: int = 30
    max_any_court_gap_min: int = 0
    max_same_court_gap_min: int = 30


class Preferences(BaseModel):
    time_window: TimeWindow
    date_horizon_days: int = Field(default=14, ge=1, le=60)
    duration_min_minutes: int = Field(default=60, ge=30, le=240)
    duration_max_minutes: int = Field(default=120, ge=30, le=240)
    pairing: PairingRule = Field(default_factory=PairingRule)
    facility_rank: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_durations(self) -> "Preferences":
        if self.duration_min_minutes > self.duration_max_minutes:
            raise ValueError("duration_min_minutes must be ≤ duration_max_minutes")
        return self


class Polling(BaseModel):
    interval_seconds: int = Field(default=30, ge=10)
    jitter_frac: float = 0.2
    max_runtime_hours: int = 24
    max_posts_per_cycle: int = 4


class Notifications(BaseModel):
    macos: bool = True
    webhook_url: str | None = None


class Config(BaseModel):
    version: int = 1
    credentials: Credentials = Field(default_factory=Credentials)
    defaults: Defaults = Field(default_factory=Defaults)
    member_id: int | None = None
    facilities: list[Facility] = Field(default_factory=list)
    preferences: Preferences
    polling: Polling = Field(default_factory=Polling)
    notifications: Notifications = Field(default_factory=Notifications)

    @model_validator(mode="after")
    def _cross_refs(self) -> "Config":
        ids = {f.id for f in self.facilities}
        unknown = set(self.preferences.facility_rank) - ids
        if unknown:
            raise ValueError(
                f"preferences.facility_rank references unknown facilities: {sorted(unknown)}"
            )
        return self

    def facility(self, facility_id: str) -> Facility:
        for f in self.facilities:
            if f.id == facility_id:
                return f
        raise KeyError(f"facility '{facility_id}' not found in config")


def load_config(path: Path | str) -> Config:
    with Path(path).open("r") as fh:
        data = yaml.safe_load(fh)
    return Config.model_validate(data)


def save_config(cfg: Config, path: Path | str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    backup = p.with_suffix(p.suffix + ".bak")
    if p.exists():
        backup.write_bytes(p.read_bytes())
    with p.open("w") as fh:
        yaml.safe_dump(
            cfg.model_dump(mode="json", exclude_none=False),
            fh,
            sort_keys=False,
            default_flow_style=False,
        )
