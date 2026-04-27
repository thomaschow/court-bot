from datetime import date, time

from courtbot.courtreserve.payloads import BookingCandidate, build_create_reservation_form


def _cand(**over):
    base = dict(
        facility_id="santa-clara",
        org_id=13234,
        member_id=999111,
        membership_id=42,
        reservation_type_id=69711,
        court_id=103,
        date=date(2026, 5, 3),
        start=time(9, 0),
        duration_minutes=60,
    )
    base.update(over)
    return BookingCandidate(**base)


def _hidden() -> dict[str, str]:
    return {
        "Id": "13234",
        "OrgId": "13234",
        "MemberId": "8462028",
        "MembershipId": "141172",
        "CustomSchedulerId": "16995",
        "Date": "5/3/2026 12:00:00 AM",
        "SelectedCourtType": "Hard",
        "SelectedCourtTypeId": "2",
        "CourtTypeEnum": "2",
        "RequestData": "TOKEN_FROM_MODAL",
        "ReservationLotteryGuid": "abc-123",
        "IsConsolidatedScheduler": "True",
        "IsConsolidated": "True",
    }


def test_form_replays_hidden_fields_verbatim() -> None:
    fields = build_create_reservation_form(
        _cand(), csrf_token="CSRF", hidden_fields=_hidden()
    )
    keys = [k for k, _ in fields]
    for h in ("RequestData", "ReservationLotteryGuid", "Date", "SelectedCourtTypeId"):
        assert h in keys, f"missing replayed hidden field {h}"


def test_form_token_first() -> None:
    fields = build_create_reservation_form(_cand(), csrf_token="TOK", hidden_fields=_hidden())
    assert fields[0] == ("__RequestVerificationToken", "TOK")


def test_form_overrides_user_fields() -> None:
    fields = dict(build_create_reservation_form(_cand(), csrf_token="TOK", hidden_fields=_hidden()))
    assert fields["StartTime"] == "09:00:00"
    assert fields["EndTime"] == "10:00 AM"
    assert fields["Duration"] == "60"
    assert fields["ReservationTypeId"] == "69711"
    assert fields["DisclosureAgree"] == "true"
    # CourtId carries the candidate's court — IsCourtRequired=False is misleading;
    # the server still requires CourtId at the API level.
    assert fields["CourtId"] == "103"


def test_form_drops_overridden_hidden_fields() -> None:
    """If the modal's hidden_fields includes StartTime/Duration/etc., we must override
    them, not double-write them."""
    hidden = _hidden() | {
        "StartTime": "ignored",
        "Duration": "ignored",
        "ReservationTypeId": "0",
        "EndTime": "ignored",
        "CourtId": "ignored",
    }
    fields = build_create_reservation_form(_cand(), csrf_token="TOK", hidden_fields=hidden)
    keys = [k for k, _ in fields]
    assert keys.count("StartTime") == 1
    assert keys.count("Duration") == 1
    assert keys.count("ReservationTypeId") == 1
    fmap = dict(fields)
    assert fmap["StartTime"] != "ignored"
    assert fmap["ReservationTypeId"] == "69711"


def test_form_with_no_hidden_still_has_required() -> None:
    fields = dict(build_create_reservation_form(_cand(), csrf_token="TOK"))
    for k in ("__RequestVerificationToken", "StartTime", "EndTime", "Duration", "ReservationTypeId"):
        assert k in fields


def test_extras_appended() -> None:
    fields = build_create_reservation_form(
        _cand(), csrf_token="TOK", hidden_fields=_hidden(), extras={"X": "Y"}
    )
    assert fields[-1] == ("X", "Y")
