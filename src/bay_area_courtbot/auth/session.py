from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

from bay_area_courtbot.auth.csrf import extract_token
from bay_area_courtbot.auth.playwright_login import storage_state_cookies
from bay_area_courtbot.config import Facility
from bay_area_courtbot.logging import get_logger
from bay_area_courtbot.paths import session_path

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


@dataclass
class SessionState:
    facility: Facility
    csrf_token: str | None = None
    last_verified_ts: float = 0.0
    server_clock_offset_ms: int = 0
    extras: dict = field(default_factory=dict)


def build_client(facility: Facility, *, http2: bool = True) -> httpx.AsyncClient:
    cookies = httpx.Cookies()
    for c in storage_state_cookies(session_path(facility.org_id)):
        if "courtreserve.com" not in c.get("domain", ""):
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
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
        timeout=httpx.Timeout(15.0, connect=5.0),
        follow_redirects=False,
        base_url=facility.base_url,
    )


async def hydrate(client: httpx.AsyncClient, facility: Facility) -> SessionState:
    """GET the bookings page to verify session and parse CSRF token + clock skew."""
    log = get_logger(facility=facility.id, mode="auth")
    url = f"/Online/Reservations/Bookings/{facility.org_id}"
    if facility.s_id:
        url += f"?sId={facility.s_id}"

    t_send = time.time()
    resp = await client.get(url)
    t_recv = time.time()

    if resp.status_code in (301, 302) and "/Account/LogIn" in resp.headers.get("location", ""):
        raise SessionExpired(f"redirected to login: {resp.headers.get('location')}")
    if resp.status_code == 401:
        raise SessionExpired("401 Unauthorized")
    resp.raise_for_status()

    token = extract_token(resp.text)
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

    log.info(
        "auth.session.hydrate",
        has_csrf=token is not None,
        server_clock_offset_ms=offset_ms,
    )
    return SessionState(
        facility=facility,
        csrf_token=token,
        last_verified_ts=time.time(),
        server_clock_offset_ms=offset_ms,
        extras={"bookings_html": resp.text},
    )


class SessionExpired(RuntimeError):
    pass
