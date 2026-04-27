from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    env = os.environ.get("COURTBOT_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def config_path() -> Path:
    env = os.environ.get("COURTBOT_CONFIG")
    if env:
        return Path(env).expanduser().resolve()
    return project_root() / "config" / "config.yaml"


def state_dir() -> Path:
    p = project_root() / "state"
    p.mkdir(parents=True, exist_ok=True)
    return p


def session_path(org_id: int) -> Path:
    p = state_dir() / "session"
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{org_id}.json"


def ledger_path() -> Path:
    return state_dir() / "booked.sqlite"


def log_path() -> Path:
    p = state_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p / "court-bot.jsonl"
