from seattle_courtbot.ancapi.parsing import AvailabilityRange, parse_availability_daily


def _envelope(body: dict) -> dict:
    return {"headers": {"response_code": "0000", "response_message": "Successful"},
            "body": body}


def test_parse_availability_extracts_ranges() -> None:
    payload = _envelope({"details": {
        "resource_id": 1146,
        "daily_details": [
            {
                "date": "2026-05-06", "status": 0,
                "times": [
                    {"id": 0, "start_time": "08:30:00", "end_time": "23:00:00",
                     "available": True, "is_cross_day": False},
                ],
            },
            {
                "date": "2026-05-07", "status": 0,
                "times": [
                    {"start_time": "08:30:00", "end_time": "12:00:00", "available": True},
                    {"start_time": "14:00:00", "end_time": "23:00:00", "available": True},
                ],
            },
            {"date": "2026-05-08", "status": 5, "times": []},
        ],
    }})
    out = parse_availability_daily(payload, resource_id=1146)
    assert len(out) == 3
    assert out[0] == AvailabilityRange(
        resource_id=1146, date="2026-05-06",
        start_time="08:30:00", end_time="23:00:00", available=True,
    )
    # Day with two ranges (a booking carved out the middle):
    days_with_two = [r for r in out if r.date == "2026-05-07"]
    assert len(days_with_two) == 2
