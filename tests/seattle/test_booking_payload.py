from datetime import date, time

from seattle_courtbot.ancapi.booking import BookingRequest, _datetime_str, _validation_body


def _req(**over) -> BookingRequest:
    base = dict(
        customer_id=422698, resource_id=1146, event_type_id=152,
        attendee_count=2, date=date(2026, 5, 6),
        start=time(18, 0), duration_minutes=180,
    )
    base.update(over)
    return BookingRequest(**base)


def test_end_computed_from_duration() -> None:
    r = _req()
    assert r.end == time(21, 0)


def test_validation_body_shape() -> None:
    body = _validation_body(_req(), booking_identifier="abc-123")
    assert body["customer_id"] == 422698
    assert body["resource_id"] == 1146
    assert body["event_type_id"] == 152
    assert body["attendee"] == 2
    assert body["reservation_unit"] == "minute"
    assert body["reno"] == 0
    assert body["is_clear_group"] is False
    rt = body["reservation_time_groups"][0]["reservation_times"][0]
    assert rt["start_event_datetime"] == "2026-05-06 18:00:00"
    assert rt["end_event_datetime"] == "2026-05-06 21:00:00"
    assert rt["booking_identifier"] == "abc-123"


def test_datetime_str_format() -> None:
    assert _datetime_str(date(2026, 5, 6), time(18, 0)) == "2026-05-06 18:00:00"
    assert _datetime_str(date(2026, 5, 6), time(8, 30, 15)) == "2026-05-06 08:30:15"
