from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    """Resolve the repo root. Honours COURTBOT_ROOT for tests; otherwise walks up
    from this file. The Seattle package shares the same project root as
    bay_area_courtbot — they're both subpackages of the same court-bot repo."""
    env = os.environ.get("COURTBOT_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def config_path() -> Path:
    env = os.environ.get("SEATTLE_COURTBOT_CONFIG")
    if env:
        return Path(env).expanduser().resolve()
    return project_root() / "config" / "seattle.yaml"


def state_dir() -> Path:
    p = project_root() / "state"
    p.mkdir(parents=True, exist_ok=True)
    return p


def session_path() -> Path:
    """Single Seattle login session. Unlike Lifetime (where each org_id has its own
    auth), Seattle ANC uses one account across all facilities."""
    p = state_dir() / "session"
    p.mkdir(parents=True, exist_ok=True)
    return p / "seattle.json"


def ledger_path() -> Path:
    return state_dir() / "seattle.sqlite"


def log_path() -> Path:
    p = state_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p / "seattle-courtbot.jsonl"
