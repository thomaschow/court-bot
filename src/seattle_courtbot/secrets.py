from __future__ import annotations

import os
from dataclasses import dataclass

import keyring

KEYRING_SERVICE = "seattle-courtbot"


@dataclass(frozen=True)
class Creds:
    username: str
    password: str


def get_credentials() -> Creds:
    """Look up Seattle ANC credentials. Keyring first, then env vars
    (SEATTLE_USERNAME / SEATTLE_PASSWORD) as a fallback."""
    username = keyring.get_password(KEYRING_SERVICE, "username")
    password = keyring.get_password(KEYRING_SERVICE, "password")
    if username and password:
        return Creds(username=username, password=password)
    username = os.environ.get("SEATTLE_USERNAME")
    password = os.environ.get("SEATTLE_PASSWORD")
    if username and password:
        return Creds(username=username, password=password)
    raise RuntimeError(
        "No Seattle credentials configured. Set with:\n"
        f"  keyring set {KEYRING_SERVICE} username\n"
        f"  keyring set {KEYRING_SERVICE} password\n"
        "Or export SEATTLE_USERNAME / SEATTLE_PASSWORD."
    )


def set_credentials(username: str, password: str) -> None:
    keyring.set_password(KEYRING_SERVICE, "username", username)
    keyring.set_password(KEYRING_SERVICE, "password", password)
