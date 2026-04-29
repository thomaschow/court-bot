"""Seattle ANC REST endpoints. URLs verified live 2026-04-28 via
scripts/seattle/probe_seattle_search.py. Tenant slug parameterised so the same
adapter could later target other ANC tenants (Cupertino, San Jose, etc.)."""

from __future__ import annotations


def search_resources(tenant: str = "seattle") -> str:
    """POST: search for reservable resources. JSON body controls filters; the
    response wraps an `items` list inside `body`."""
    return f"/{tenant}/rest/reservation/resource?locale=en-US"


def login_check(tenant: str = "seattle") -> str:
    """GET: returns 202 if the session is unauthenticated (response_code=0021),
    or 200 with customer details if logged in."""
    return f"/{tenant}/rest/common/logincheck?locale=en-US"


def filter_options(tenant: str = "seattle") -> str:
    """GET: dropdown values for the search filters (event types, facility types, etc.)."""
    return f"/{tenant}/rest/reservation/resource/option/filteroptions?locale=en-US"


def search_options(tenant: str = "seattle") -> str:
    """GET: catalog of event types (Tennis - Outdoor, Pickleball, etc.) + ids."""
    return f"/{tenant}/rest/reservation/resource/searchoptions?locale=en-US"


# Phase 2 placeholders — the schedule + booking endpoints will be added once
# scripts/seattle/probe_seattle_resource_detail.py and probe_seattle_booking.py
# capture them from the per-resource detail page and the booking modal.
