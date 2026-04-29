from __future__ import annotations

import os
from dataclasses import dataclass

import keyring

from bay_area_courtbot.config import Credentials


@dataclass(frozen=True)
class Creds:
    username: str
    password: str


def _env_key(facility_id: str, suffix: str) -> str:
    return f"COURTBOT_{facility_id.upper().replace('-', '_')}_{suffix}"


def get_credentials(facility_id: str, cfg: Credentials) -> Creds:
    if cfg.source == "keyring":
        username = keyring.get_password(cfg.keyring_service, f"{facility_id}:username")
        password = keyring.get_password(cfg.keyring_service, f"{facility_id}:password")
        if username and password:
            return Creds(username=username, password=password)
    username = os.environ.get(_env_key(facility_id, "USERNAME"))
    password = os.environ.get(_env_key(facility_id, "PASSWORD"))
    if username and password:
        return Creds(username=username, password=password)
    raise RuntimeError(
        f"No credentials for facility '{facility_id}'. Set with:\n"
        f"  keyring set {cfg.keyring_service} {facility_id}:username\n"
        f"  keyring set {cfg.keyring_service} {facility_id}:password\n"
        f"Or export {_env_key(facility_id, 'USERNAME')} / {_env_key(facility_id, 'PASSWORD')}."
    )


def set_credentials(facility_id: str, cfg: Credentials, username: str, password: str) -> None:
    keyring.set_password(cfg.keyring_service, f"{facility_id}:username", username)
    keyring.set_password(cfg.keyring_service, f"{facility_id}:password", password)
