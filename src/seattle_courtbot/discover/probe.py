"""Discover Seattle ANC tennis courts + member ID.

Search is unauthenticated, so facility/court enumeration works without login. The
member-id extraction does require a logged-in session (it comes from the ANC
home page bootstrap payload after login).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

import httpx
import yaml

from seattle_courtbot.ancapi.client import AncClient
from seattle_courtbot.config import Court, Facility
from seattle_courtbot.logging import get_logger

# Tennis = event_type_id 152 ("Tennis - Outdoor"). Lessons + pickleball excluded by
# filtering on this — keeps the watcher focused on actual tennis-court bookings.
TENNIS_EVENT_TYPE_ID = 152
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
ANC_BASE = "https://anc.apm.activecommunities.com"


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


_MEMBER_ID_PATTERNS = [
    re.compile(r'"customer_id"\s*:\s*"?(\d+)"?'),
    re.compile(r'"customerId"\s*:\s*"?(\d+)"?'),
    re.compile(r'"member_id"\s*:\s*"?(\d+)"?'),
    re.compile(r'"memberId"\s*:\s*"?(\d+)"?'),
    re.compile(r'data-customer-id="(\d+)"'),
]


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "facility"


def _facilities_from_courts(items) -> list[Facility]:
    """Group resource-list items into Facility objects (one per center_id)."""
    by_center = defaultdict(list)
    for it in items:
        if it.no_internet_permits:
            continue
        if TENNIS_EVENT_TYPE_ID not in it.event_type_ids:
            continue
        by_center[it.center_id].append(it)
    facilities: list[Facility] = []
    for center_id, courts in by_center.items():
        center_name = courts[0].center_name
        courts.sort(key=lambda c: c.name)
        facilities.append(Facility(
            id=_slug(center_name),
            name=center_name,
            facility_id=center_id,
            courts=[Court(id=c.resource_id, name=c.name) for c in courts],
        ))
    facilities.sort(key=lambda f: f.name)
    return facilities


async def discover_facilities() -> list[Facility]:
    """Public-search-based enumeration of Seattle tennis facilities + courts. No
    login required — uses an unauthenticated httpx client."""
    log = get_logger(mode="discover")
    async with httpx.AsyncClient(
        base_url=ANC_BASE,
        headers={"User-Agent": USER_AGENT, "Origin": ANC_BASE,
                 "Referer": f"{ANC_BASE}/seattle/reservation/search"},
        timeout=httpx.Timeout(15.0, connect=5.0),
    ) as client:
        anc = AncClient(client, tenant="seattle")
        items = await anc.search_courts(keyword="tennis")
    log.info("discover.search_complete", item_count=len(items))
    facilities = _facilities_from_courts(items)
    log.info(
        "discover.facilities",
        count=len(facilities),
        court_count=sum(len(f.courts) for f in facilities),
    )
    return facilities


async def discover() -> DiscoverResult:
    """Full discover. Returns facilities + (best-effort) member_id from the
    saved login session."""
    facilities = await discover_facilities()
    member_id = await _try_member_id()
    return DiscoverResult(facilities=facilities, member_id=member_id)


async def _try_member_id() -> int | None:
    """Attempt to read member_id from a logged-in session. Returns None if no
    session or the home page bootstrap doesn't expose the customer id."""
    log = get_logger(mode="discover")
    try:
        from seattle_courtbot.auth.session import build_client
    except Exception:
        return None
    try:
        async with build_client(http2=False) as client:
            r = await client.get("/seattle/home")
            if r.status_code != 200:
                return None
            for pat in _MEMBER_ID_PATTERNS:
                m = pat.search(r.text)
                if m:
                    return int(m.group(1))
    except Exception as exc:
        log.info("discover.member_id_skip", reason=str(exc))
    return None
