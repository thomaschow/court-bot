from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date as ddate, timedelta

import httpx
import yaml

from courtbot.auth.session import build_client, hydrate
from courtbot.config import Config, Court, Facility
from courtbot.courtreserve import endpoints
from courtbot.courtreserve.parsing import parse_read_consolidated
from courtbot.logging import get_logger


@dataclass
class DiscoverResult:
    facility_id: str
    org_id: int
    member_id: int | None = None
    courts: list[Court] = field(default_factory=list)
    reservation_type_ids: dict[str, int] = field(default_factory=dict)
    membership_id: int | None = None
    cost_type_id: int | None = None
    rules_text: str | None = None
    raw_html_excerpt: str = ""

    def to_yaml_snippet(self) -> str:
        block = {
            "id": self.facility_id,
            "org_id": self.org_id,
            "member_id": self.member_id,
            "courts": [c.model_dump() for c in self.courts],
            "reservation_type_id": next(iter(self.reservation_type_ids.values()), None),
            "membership_id": self.membership_id,
            "cost_type_id": self.cost_type_id,
        }
        return yaml.safe_dump([block], sort_keys=False)


# These patterns target Lifetime's CourtReserve template (verified 2026-04-27).
_MEMBER_ID_PATTERNS = [
    re.compile(r"&memberId=(\d+)"),
    re.compile(r"&userId=(\d+)"),
    re.compile(r'myFamMembers\.push\(Number\("(\d+)"\)\)'),
    re.compile(r'data-member-id="(\d+)"'),
    re.compile(r"var\s+memberId\s*=\s*(\d+)"),
    re.compile(r'"MemberId"\s*:\s*(\d+)'),
]

_MEMBERSHIP_ID_PATTERNS = [
    re.compile(r"&membershipId=(\d+)"),
    re.compile(r'"MembershipId"\s*:\s*(\d+)'),
    re.compile(r"var\s+membershipId\s*=\s*(\d+)"),
]

_COST_TYPE_ID_PATTERNS = [
    re.compile(r"""CostTypeId\s*:\s*['"](\d+)['"]"""),
    re.compile(r'"CostTypeId"\s*:\s*"?(\d+)"?'),
    re.compile(r"""costTypeId\s*=\s*['"]?(\d+)"""),
]

_RES_TYPE_PATTERNS = [
    # Capture e.g. {"Id":7,"Name":"Doubles"} fragments embedded in JS config.
    re.compile(
        r'\{[^{}]*?"Id"\s*:\s*(\d+)[^{}]*?"Name"\s*:\s*"([^"]*(?:Singles|Doubles|Lesson|Practice|Open Play|Drill)[^"]*)"',
        re.IGNORECASE,
    ),
]


def _first_match(patterns: list[re.Pattern[str]], text: str) -> int | None:
    for pat in patterns:
        m = pat.search(text)
        if m:
            return int(m.group(1))
    return None


def _parse_reservation_types(page_html: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for pat in _RES_TYPE_PATTERNS:
        for m in pat.finditer(page_html):
            type_id = int(m.group(1))
            label = m.group(2).strip().lower()
            out.setdefault(label, type_id)
    return out


async def _discover_courts_via_consolidated(
    client: httpx.AsyncClient, facility: Facility, cost_type_id: int | None
) -> list[Court]:
    """Call ReadConsolidated for the next 7 days and union the AvailableCourtIds we see.

    CourtReserve doesn't expose a flat 'court list' endpoint; the authoritative inventory
    comes out of the schedule. ReadConsolidated does not include friendly names — we name
    them numerically by the order we observe them; the user can rename in config.yaml.
    """
    from courtbot.courtreserve.client import CourtReserveClient

    cr = CourtReserveClient(
        client,
        org_id=facility.org_id,
        cost_type_id=cost_type_id,
        custom_scheduler_id=facility.s_id,
        timezone_name=facility.timezone,
        reservation_min_interval=facility.reservation_min_interval,
    )
    seen_ids: list[int] = []
    today = ddate.today()
    for delta in range(7):
        day = today + timedelta(days=delta)
        try:
            slots = await cr.read_consolidated(day=day)
        except Exception:
            continue
        for s in slots:
            if s.court_id not in seen_ids:
                seen_ids.append(s.court_id)
    return [Court(id=cid, name=f"Court {i + 1}") for i, cid in enumerate(sorted(seen_ids))]


async def discover(cfg: Config, facility: Facility) -> DiscoverResult:
    log = get_logger(facility=facility.id, mode="discover")
    async with build_client(facility, http2=False) as client:
        state = await hydrate(client, facility)
        page_html = state.extras["bookings_html"]

        rules_resp = None
        for path in (
            f"/Online/Reservations/Rules/{facility.org_id}",
            f"/Online/Information/Rules/{facility.org_id}",
        ):
            try:
                r = await client.get(path)
                if r.status_code == 200:
                    rules_resp = r
                    break
            except httpx.HTTPError:
                pass

        member_id = _first_match(_MEMBER_ID_PATTERNS, page_html)
        membership_id = _first_match(_MEMBERSHIP_ID_PATTERNS, page_html)
        cost_type_id = _first_match(_COST_TYPE_ID_PATTERNS, page_html)
        res_types = _parse_reservation_types(page_html)
        courts = await _discover_courts_via_consolidated(client, facility, cost_type_id)

    rules_text = None
    if rules_resp is not None:
        try:
            from lxml import html as _html

            tree = _html.fromstring(rules_resp.text)
            rules_text = " ".join(tree.xpath("//body//text()")).strip()[:2000]
        except Exception:
            rules_text = rules_resp.text[:2000]

    log.info(
        "discover.parsed",
        member_id=member_id,
        court_count=len(courts),
        reservation_types=list(res_types.keys()),
        membership_id=membership_id,
        cost_type_id=cost_type_id,
        rules_found=rules_text is not None,
    )

    return DiscoverResult(
        facility_id=facility.id,
        org_id=facility.org_id,
        member_id=member_id,
        courts=courts,
        reservation_type_ids=res_types,
        membership_id=membership_id,
        cost_type_id=cost_type_id,
        rules_text=rules_text,
        raw_html_excerpt=page_html[:4000],
    )
