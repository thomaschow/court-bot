"""Async HTTP client for Seattle ANC."""

from __future__ import annotations

import httpx

from seattle_courtbot.ancapi import endpoints
from seattle_courtbot.ancapi.errors import (
    AncError,
    AuthExpired,
    RateLimited,
)
from seattle_courtbot.ancapi.parsing import CourtItem, parse_resource_search


class AncClient:
    """Thin wrapper around an httpx.AsyncClient. Phase 1: search-only.
    Schedule reads + booking arrive in Phase 2 once the per-resource detail page
    and booking flow are reverse-engineered."""

    def __init__(self, http: httpx.AsyncClient, *, tenant: str = "seattle"):
        self._http = http
        self._tenant = tenant

    async def _search_page(
        self, keyword: str, *, start_index: int, page_size: int = 20,
    ) -> list[CourtItem]:
        body = {
            "name": keyword,
            "attendee": 0,
            "date_times": [],
            "event_type_ids": [],
            "facility_type_ids": [],
            "reservation_group_ids": [],
            "amenity_ids": [],
            "facility_id": 0,
            "equipment_id": 0,
            "center_id": 0,
            "resource_type": 0,
            "client_coordinate": "",
            "order_by_field": "name",
            "order_direction": "asc",
            "page_size": page_size,
            "start_index": start_index,
            "search_client_id": "",
            "date_time_length": None,
            "full_day_booking": False,
            "center_ids": [],
            "specify_start_and_end_times": False,
        }
        resp = await self._http.post(
            endpoints.search_resources(self._tenant),
            json=body,
            headers={
                "Content-Type": "application/json;charset=utf-8",
                "Accept": "application/json, text/plain, */*",
            },
        )
        if resp.status_code in (401, 403):
            raise AuthExpired(f"{resp.status_code} on search_courts")
        if resp.status_code == 429:
            raise RateLimited("429 on search_courts")
        resp.raise_for_status()
        return parse_resource_search(resp.json())

    async def search_courts(
        self,
        keyword: str = "tennis",
        *,
        max_pages: int = 30,
    ) -> list[CourtItem]:
        """Search for reservable resources by keyword. Server caps page_size at 20
        regardless of what we ask, and returns inaccurate total_records, so we
        paginate until we see an empty page or hit `max_pages`."""
        all_items: list[CourtItem] = []
        seen: set[int] = set()
        for page in range(max_pages):
            items = await self._search_page(keyword, start_index=page * 20)
            if not items:
                break
            new = [it for it in items if it.resource_id not in seen]
            if not new:
                break
            all_items.extend(new)
            seen.update(it.resource_id for it in new)
        return all_items
