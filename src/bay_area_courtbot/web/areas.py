"""Area registry for the dashboard.

Each area has its own config file, ledger DB, and log file. The dashboard renders a
top-level area-tab strip and scopes every page to the active area's data.

Areas are resolved lazily so that tests using `monkeypatch.setenv("COURTBOT_ROOT", ...)`
get the right paths even though this module imports earlier than the test fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bay_area_courtbot.paths import project_root


@dataclass(frozen=True)
class Area:
    id: str
    label: str
    config_path: Path
    ledger_path: Path
    log_path: Path
    facility_module: str


DEFAULT_AREA = "bay-area"

_AREA_DEFS = (
    {
        "id": "bay-area",
        "label": "Bay Area",
        "config_rel": "config/config.yaml",
        "ledger_rel": "state/bay_area.sqlite",
        "log_rel": "state/logs/bay-area-courtbot.jsonl",
        "facility_module": "bay_area_courtbot",
    },
    {
        "id": "seattle",
        "label": "Seattle",
        "config_rel": "config/seattle.yaml",
        "ledger_rel": "state/seattle.sqlite",
        "log_rel": "state/logs/seattle-courtbot.jsonl",
        "facility_module": "seattle_courtbot",
    },
)


def _build_area(d: dict) -> Area:
    root = project_root()
    return Area(
        id=d["id"],
        label=d["label"],
        config_path=root / d["config_rel"],
        ledger_path=root / d["ledger_rel"],
        log_path=root / d["log_rel"],
        facility_module=d["facility_module"],
    )


def all_areas() -> list[Area]:
    return [_build_area(d) for d in _AREA_DEFS]


def get_area(area_id: str) -> Area:
    for d in _AREA_DEFS:
        if d["id"] == area_id:
            return _build_area(d)
    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail=f"unknown area: {area_id}")
