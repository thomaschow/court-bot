from __future__ import annotations

from dataclasses import dataclass
from datetime import date as ddate, datetime, time as dtime, timedelta


@dataclass(frozen=True)
class BookingCandidate:
    facility_id: str
    org_id: int
    member_id: int
    membership_id: int | None
    reservation_type_id: int
    court_id: int
    date: ddate
    start: dtime
    duration_minutes: int

    @property
    def end(self) -> dtime:
        return (datetime.combine(self.date, self.start) + timedelta(minutes=self.duration_minutes)).time()


def _fmt_end_display(t: dtime) -> str:
    """Lifetime's form expects e.g. '10:00 AM' for the EndTime display field (12-hour, no leading zero)."""
    return datetime.combine(ddate(2000, 1, 1), t).strftime("%-I:%M %p")


def build_create_reservation_form(
    cand: BookingCandidate,
    *,
    csrf_token: str,
    hidden_fields: dict[str, str] | None = None,
    extras: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Build the application/x-www-form-urlencoded body for the CreateReservation POST.

    Replay every hidden field from the modal (`hidden_fields`) — they include per-modal
    values like `RequestData`, `ReservationLotteryGuid`, `Date`, plus the org's True/False
    feature flags. Then layer on the user-driven overrides.
    """
    fields: list[tuple[str, str]] = []

    # 1. CSRF first (matches what the rendered form does).
    fields.append(("__RequestVerificationToken", csrf_token))

    # 2. Replay every hidden field from the modal verbatim, EXCLUDING the token (which we
    # already wrote) and the fields we are about to override.
    overrides_by_us = {
        "__RequestVerificationToken",
        "StartTime",
        "ReservationTypeId",
        "Duration",
        "EndTime",
        "CourtId",
        "OwnersDropdown",
        "SelectedNumberOfGuests",
        "DisclosureAgree",
        "FeeResponsibility",
    }
    for k, v in (hidden_fields or {}).items():
        if k in overrides_by_us:
            continue
        fields.append((k, v))

    # 3. Override the user-controlled fields.
    fields.append(("StartTime", cand.start.strftime("%H:%M:%S")))
    fields.append(("ReservationTypeId", str(cand.reservation_type_id)))
    fields.append(("Duration", str(cand.duration_minutes)))
    fields.append(("EndTime", _fmt_end_display(cand.end)))
    # The UI's IsCourtRequired/CanSelectCourt flags are misleading — the booking API
    # *does* require a CourtId. The Kendo dropdown loads valid IDs from
    # /Online/AjaxController/GetAvailableCourtsMemberPortal at modal open. We send the
    # candidate's court_id (caller is responsible for picking from the available set).
    fields.append(("CourtId", str(cand.court_id) if cand.court_id else ""))
    fields.append(("OwnersDropdown", ""))
    fields.append(("SelectedNumberOfGuests", ""))
    # DisclosureAgree is required even when the disclosure text is empty.
    fields.append(("DisclosureAgree", "true"))

    if extras:
        fields.extend(extras.items())
    return fields
