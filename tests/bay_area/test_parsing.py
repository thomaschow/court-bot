from bay_area_courtbot.courtreserve.parsing import (
    parse_confirmation,
    parse_read_consolidated,
    parse_read_expanded,
)


def test_consolidated_fans_out_per_court() -> None:
    payload = {
        "Data": [
            {
                "Id": "Hard04/27/2026 15:00:00",
                "Start": "/Date(1777302000000)/",
                "End": "/Date(1777303800000)/",
                "CourtType": "Hard",
                "AvailableCourts": 4,
                "AvailableCourtIds": [52099, 52101, 52102, 52103],
                "IsClosed": False,
                "IsInPast": False,
            }
        ]
    }
    slots = parse_read_consolidated(payload)
    assert {s.court_id for s in slots} == {52099, 52101, 52102, 52103}
    assert all(s.is_available for s in slots)
    assert all(s.start.timestamp() == 1777302000.0 for s in slots)


def test_consolidated_skips_closed_and_past() -> None:
    payload = {
        "Data": [
            {
                "Start": "/Date(1777302000000)/",
                "End": "/Date(1777303800000)/",
                "AvailableCourtIds": [1],
                "IsClosed": True,
                "IsInPast": False,
            },
            {
                "Start": "/Date(1777302000000)/",
                "End": "/Date(1777303800000)/",
                "AvailableCourtIds": [2],
                "IsClosed": False,
                "IsInPast": True,
            },
            {
                "Start": "/Date(1777302000000)/",
                "End": "/Date(1777303800000)/",
                "AvailableCourtIds": [3],
                "IsClosed": False,
                "IsInPast": False,
            },
        ]
    }
    slots = parse_read_consolidated(payload)
    assert {s.court_id for s in slots} == {3}


def test_read_expanded_back_compat_legacy_shape() -> None:
    payload = [
        {
            "CourtId": 103,
            "Start": "2026-05-03T09:00:00",
            "End": "2026-05-03T10:00:00",
            "IsAvailable": True,
        }
    ]
    slots = parse_read_expanded(payload)
    assert len(slots) == 1 and slots[0].court_id == 103


def test_read_expanded_dispatches_to_consolidated_when_shape_matches() -> None:
    payload = {
        "Data": [
            {
                "Start": "/Date(1777302000000)/",
                "End": "/Date(1777303800000)/",
                "AvailableCourtIds": [99],
                "IsClosed": False,
                "IsInPast": False,
            }
        ]
    }
    slots = parse_read_expanded(payload)
    assert len(slots) == 1 and slots[0].court_id == 99


def test_parse_confirmation_from_json() -> None:
    assert parse_confirmation('{"ReservationId": 555, "Ok": true}') == "555"


def test_parse_confirmation_from_loose_text() -> None:
    assert parse_confirmation('something "Id": "9999" extra') == "9999"


def test_parse_confirmation_none() -> None:
    assert parse_confirmation("nope") is None
    assert parse_confirmation("") is None
