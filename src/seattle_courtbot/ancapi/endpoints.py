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


# Phase 2 endpoints captured live 2026-04-29 via probe_seattle_click_into.py.

def login_user_ext(tenant: str = "seattle") -> str:
    """GET: returns the logged-in user's customer id (`body.user.customerid`),
    encoded id, name, email, and family-member ids."""
    return f"/{tenant}/rest/system/loginuserext?locale=en-US"


def availability_daily(resource_id: int, tenant: str = "seattle") -> str:
    """GET: per-day availability for one court. Query params: start_date,
    end_date (YYYY-MM-DD), customer_id, company_id, attendee, event_type_id.
    Response body shape: `{details: {resource_id, reservation_unit, daily_details:
    [{date, status, times:[{start_time, end_time, available, is_cross_day}], ...}]}}`."""
    return f"/{tenant}/rest/reservation/resource/availability/daily/{resource_id}"


def resource_detail(resource_id: int, tenant: str = "seattle") -> str:
    """GET: per-resource detail page payload (event types, rules, etc.)."""
    return f"/{tenant}/rest/reservation/resource/detail/{resource_id}"


def resource_form_data(resource_id: int, tenant: str = "seattle") -> str:
    """POST: returns booking form context — family members, event types, time groups."""
    return f"/{tenant}/rest/reservation/resource/detail/{resource_id}/formdata?locale=en-US"


def reservation_validation(tenant: str = "seattle") -> str:
    """POST: pre-flight validation of a reservation request. Body includes
    customer_id, resource_id, reservation_time_groups, event_type_id, etc."""
    return f"/{tenant}/rest/reservation/resource/validation?locale=en-US"


def proceed_to_form(tenant: str = "seattle") -> str:
    """POST: advances a validated reservation request to the checkout form.
    Body is the same shape as validation; response: `{status:"success",
    next_page:"reservation/form"}`."""
    return f"/{tenant}/rest/reservation/resource/proceed?locale=en-US"


def form(tenant: str = "seattle") -> str:
    """GET: checkout form state (event types, schedules, fees, timestamp).
    Path is `/reservation/form/0` for new reservations (reno=0)."""
    return f"/{tenant}/rest/reservation/form/0?locale=en-US"


def form_participants(tenant: str = "seattle") -> str:
    """GET: family members + companies available as reservation participants."""
    return f"/{tenant}/rest/reservation/form/participants?locale=en-US"


def form_reserve(reno: int, timestamp: int, tenant: str = "seattle") -> str:
    """POST: FINAL booking submission. `reno` = reservation number (0 for new),
    `timestamp` = the integer returned in the GET /form/0 response body. URL
    pattern verified live in the SPA's JS bundle:
    `/reservation/form/reserve/{reno}/{timestamp}`."""
    return f"/{tenant}/rest/reservation/form/reserve/{reno}/{timestamp}?locale=en-US"
