"""Async HTTP client for Seattle ANC."""

from __future__ import annotations

import httpx

from datetime import date as ddate

from seattle_courtbot.ancapi import endpoints
from seattle_courtbot.ancapi.errors import (
    AncError,
    AuthExpired,
    RateLimited,
)
from seattle_courtbot.ancapi.parsing import (
    AvailabilityRange,
    CourtItem,
    parse_availability_daily,
    parse_resource_search,
)


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

    async def read_availability(
        self,
        resource_id: int,
        *,
        start_date: ddate,
        end_date: ddate,
        customer_id: int,
        company_id: int = 0,
        attendee: int = 0,
        event_type_id: int = 0,
    ) -> list[AvailabilityRange]:
        """Per-day availability for one court over a date range. Verified live
        2026-04-29 — returns time RANGES per day (not 30-min slices); the
        watcher converts those into slices via `to_slices()`."""
        params = {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "customer_id": customer_id,
            "company_id": company_id,
            "attendee": attendee,
            "event_type_id": event_type_id,
            "locale": "en-US",
        }
        resp = await self._http.get(
            endpoints.availability_daily(resource_id), params=params,
        )
        if resp.status_code in (401, 403):
            raise AuthExpired(f"{resp.status_code} on availability")
        if resp.status_code == 429:
            raise RateLimited("429 on availability")
        resp.raise_for_status()
        return parse_availability_daily(resp.json(), resource_id)

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
