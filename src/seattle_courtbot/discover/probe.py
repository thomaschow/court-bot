"""Discover Seattle ANC facilities + courts + member ID.

Phase-1 status: this module is a stub that returns whatever it can extract from the
public search page. The richer discovery (per-facility resource lists, member ID
extraction from the SPA's bootstrap payload) lands once the probe scripts capture
the SPA's network calls and we know the exact endpoints.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx
import yaml

from seattle_courtbot.auth.session import build_client
from seattle_courtbot.config import Court, Facility
from seattle_courtbot.logging import get_logger


@dataclass
class DiscoverResult:
    facilities: list[Facility] = field(default_factory=list)
    member_id: int | None = None
    rules_text: str | None = None

    def to_yaml_snippet(self) -> str:
        block = {
            "member_id": self.member_id,
            "facilities": [f.model_dump() for f in self.facilities],
        }
        return yaml.safe_dump(block, sort_keys=False)


# Pulled from observation of ANC search URLs; refined empirically in Phase 1.
SEARCH_URL = (
    "/seattle/reservation/search?keyword={kw}&resourceType=0&equipmentQty=0"
)
KEYWORDS = ["tennis court", "tennis"]


_MEMBER_ID_PATTERNS = [
    re.compile(r'"customerId"\s*:\s*"?(\d+)"?'),
    re.compile(r'"customer_id"\s*:\s*"?(\d+)"?'),
    re.compile(r'"memberId"\s*:\s*"?(\d+)"?'),
    re.compile(r'"member_id"\s*:\s*"?(\d+)"?'),
    re.compile(r'data-customer-id="(\d+)"'),
]


def _first_match(text: str) -> int | None:
    for pat in _MEMBER_ID_PATTERNS:
        m = pat.search(text)
        if m:
            return int(m.group(1))
    return None


async def discover() -> DiscoverResult:
    """Best-effort facility + member-id extraction from a logged-in session.

    Implementation note: the real discovery has to happen via Playwright
    (the search results page is React-rendered) — this httpx-only path can't
    enumerate facilities. The CLI calls into a richer probe-driven flow when
    available. For now, return whatever we can extract from the SPA shell.
    """
    log = get_logger(mode="discover")
    out = DiscoverResult()
    async with build_client(http2=False) as client:
        # The home page bootstrap payload often contains the logged-in customer's id.
        try:
            r = await client.get("/seattle/home")
            r.raise_for_status()
            mid = _first_match(r.text)
            if mid:
                out.member_id = mid
        except httpx.HTTPError as exc:
            log.warning("discover.home_fetch_failed", error=str(exc))
    log.info("discover.basic", member_id=out.member_id)
    return out
