"""HTTPX session hydration for Seattle ANC.

Loads cookies from the Playwright storage_state JSON and constructs an async client
ready to talk to anc.apm.activecommunities.com. Unlike CourtReserve there is no
known per-page CSRF token requirement on read-side endpoints — we discover that
empirically in Phase 1.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from seattle_courtbot.logging import get_logger
from seattle_courtbot.paths import session_path

ANC_BASE = "https://anc.apm.activecommunities.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


@dataclass
class SessionState:
    member_id: int | None = None
    last_verified_ts: float = 0.0
    server_clock_offset_ms: int = 0
    extras: dict = field(default_factory=dict)


def _load_storage_state(path: Path) -> dict:
    if not path.exists():
        raise SessionExpired(f"no storage_state at {path} — run `seattle-courtbot login`")
    return json.loads(path.read_text())


def build_client(*, http2: bool = True) -> httpx.AsyncClient:
    state = _load_storage_state(session_path())
    cookies = httpx.Cookies()
    for c in state.get("cookies", []):
        if "activecommunities.com" not in c.get("domain", "") and "active.com" not in c.get("domain", ""):
            continue
        cookies.set(
            name=c["name"],
            value=c["value"],
            domain=c["domain"].lstrip("."),
            path=c.get("path", "/"),
        )
    return httpx.AsyncClient(
        http2=http2,
        cookies=cookies,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": ANC_BASE,
            "Referer": f"{ANC_BASE}/seattle/home",
        },
        timeout=httpx.Timeout(15.0, connect=5.0),
        follow_redirects=False,
        base_url=ANC_BASE,
    )


async def hydrate(client: httpx.AsyncClient) -> SessionState:
    """Sanity-check the session and measure server clock skew.

    Hits the tenant's home or a low-cost authenticated endpoint to verify cookies
    are still valid. Phase 1's probe scripts will identify the exact endpoint to
    use here; for now we just hit /seattle/home and confirm we don't redirect to
    /seattle/signin.
    """
    log = get_logger(mode="auth")
    t_send = time.time()
    resp = await client.get("/seattle/home")
    t_recv = time.time()

    if resp.status_code in (301, 302):
        loc = resp.headers.get("location", "")
        if "/signin" in loc.lower():
            raise SessionExpired(f"redirected to login: {loc}")
    if resp.status_code == 401:
        raise SessionExpired("401 Unauthorized")
    resp.raise_for_status()

    server_date = resp.headers.get("Date")
    offset_ms = 0
    if server_date:
        try:
            from email.utils import parsedate_to_datetime

            srv = parsedate_to_datetime(server_date).timestamp()
            local_mid = (t_send + t_recv) / 2
            offset_ms = int((srv - local_mid) * 1000)
        except (TypeError, ValueError):
            pass

    log.info("auth.session.hydrate", server_clock_offset_ms=offset_ms)
    return SessionState(
        last_verified_ts=time.time(),
        server_clock_offset_ms=offset_ms,
        extras={"home_status": resp.status_code},
    )


class SessionExpired(RuntimeError):
    pass
