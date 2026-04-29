"""Drive the actual booking flow against the real CourtReserve, but stop short of the
POST. We GET the wrapper + inner modal (so the server sees normal browsing traffic),
build the form body, and print it — without ever submitting. This verifies the
modal-fetch and payload assembly end-to-end without booking anything.
"""

import asyncio
from datetime import date, time
from urllib.parse import urlencode

from bay_area_courtbot.auth.session import build_client
from bay_area_courtbot.config import load_config
from bay_area_courtbot.courtreserve.modal import fetch_modal
from bay_area_courtbot.courtreserve.payloads import BookingCandidate, build_create_reservation_form
from bay_area_courtbot.paths import config_path


async def main() -> None:
    cfg = load_config(config_path())
    f = cfg.facility("santa-clara")

    cand = BookingCandidate(
        facility_id=f.id,
        org_id=f.org_id,
        member_id=f.member_id,
        membership_id=f.cost_type_id,  # Lifetime's MembershipId field = our cost_type_id
        reservation_type_id=69711,  # "Recreational Play - Tennis"
        court_id=0,
        date=date(2026, 5, 3),
        start=time(9, 0),
        duration_minutes=60,
    )

    async with build_client(f, http2=False) as client:
        modal = await fetch_modal(
            client,
            f,
            day=cand.date,
            start=cand.start,
            duration_minutes=cand.duration_minutes,
            court_type_id=2,
            court_type="Hard",
        )
        print(f"modal CSRF: {modal.csrf_token[:30]}…")
        print(f"inner form action: {modal.inner_form_url}")
        print(f"hidden field count: {len(modal.hidden_fields)}")

        body = build_create_reservation_form(
            cand, csrf_token=modal.csrf_token, hidden_fields=modal.hidden_fields
        )
        encoded = urlencode(body)
        print(f"\nencoded body length: {len(encoded)}")
        print("\n--- key fields the POST would send ---")
        b = dict(body)
        for k in (
            "__RequestVerificationToken",
            "Id",
            "OrgId",
            "MemberId",
            "MembershipId",
            "CustomSchedulerId",
            "Date",
            "StartTime",
            "EndTime",
            "Duration",
            "ReservationTypeId",
            "CourtId",
            "SelectedCourtType",
            "SelectedCourtTypeId",
            "CourtTypeEnum",
            "RequestData",
            "ReservationLotteryGuid",
            "DisclosureAgree",
        ):
            v = b.get(k, "<missing>")
            display = v[:40] + ("…" if len(v) > 40 else "")
            print(f"  {k:30} = {display}")

        print("\nDRY-RUN: not submitting the POST.")


if __name__ == "__main__":
    asyncio.run(main())
