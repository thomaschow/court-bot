from __future__ import annotations


def login(org_id: int) -> str:
    return f"/Online/Account/LogIn/{org_id}"


def bookings(org_id: int, s_id: int | None = None) -> str:
    base = f"/Online/Reservations/Bookings/{org_id}"
    return f"{base}?sId={s_id}" if s_id else base


def read_consolidated(org_id: int) -> str:
    return f"/Online/Reservations/ReadConsolidated/{org_id}"


# Backwards-compat alias for older callers; remove after watcher migration is verified live.
def read_expanded(org_id: int) -> str:
    return read_consolidated(org_id)


# The booking flow has two GETs followed by a POST. Endpoints verified live 2026-04-27.
RESERVATIONS_API_HOST = "https://reservations.courtreserve.com"


def create_reservation_wrapper(org_id: int) -> str:
    """Outer page on app.courtreserve.com that emits a fixUrl(...) pointing at the inner modal."""
    return f"/Online/Reservations/CreateReservation/{org_id}"


def create_reservation_post(org_id: int) -> str:
    """Absolute URL the form POSTs to. The double-slash matches CourtReserve's actual HTML."""
    return f"{RESERVATIONS_API_HOST}//Online/ReservationsApi/CreateReservation/{org_id}?uiCulture=en-US"


def reservation_types(org_id: int) -> str:
    return f"/Online/AjaxReservation/GetAvailableReservationTypes/{org_id}"


# Backwards-compat alias retained until racer/runner are migrated to the two-step flow.
def create_reservation(org_id: int) -> str:
    return create_reservation_post(org_id)


def rules(org_id: int) -> str:
    return f"/Online/Reservations/Rules/{org_id}"
