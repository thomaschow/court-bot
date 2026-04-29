import pytest

from seattle_courtbot.ancapi.errors import ApiResponseError
from seattle_courtbot.ancapi.parsing import parse_resource_search, unwrap


def _envelope(body: dict, code: str = "0000", msg: str = "Successful") -> dict:
    return {
        "headers": {
            "response_code": code,
            "response_message": msg,
            "page_info": {},
        },
        "body": body,
    }


def test_unwrap_success_returns_body() -> None:
    out = unwrap(_envelope({"items": [1, 2]}))
    assert out == {"items": [1, 2]}


def test_unwrap_raises_on_non_success() -> None:
    with pytest.raises(ApiResponseError) as excinfo:
        unwrap(_envelope({}, code="0021", msg="User not login"))
    assert excinfo.value.code == "0021"
    assert "User not login" in str(excinfo.value)


def test_parse_resource_search_extracts_courts() -> None:
    payload = _envelope({"items": [
        {
            "id": 1146, "name": "Alki Playfield Tennis Court 01",
            "resource_type": 0, "type_id": 39,
            "type_name": "Tennis Court - Outdoor (Citywide)",
            "site_id": 51, "center_id": 134, "center_name": "Alki Playfield",
            "max_capacity": 4, "no_internet_permits": False,
            "event_type_list": [
                {"id": 152, "event_name": "Tennis - Outdoor"},
                {"id": 110, "event_name": "Pickleball"},
            ],
        },
    ]})
    out = parse_resource_search(payload)
    assert len(out) == 1
    c = out[0]
    assert c.resource_id == 1146
    assert c.center_id == 134
    assert c.center_name == "Alki Playfield"
    assert 152 in c.event_type_ids
    assert c.no_internet_permits is False
    assert c.is_indoor is False


def test_parse_resource_search_indoor_flag() -> None:
    payload = _envelope({"items": [
        {
            "id": 1, "name": "AYTC Indoor Court 1",
            "type_id": 1, "type_name": "Tennis Court - Indoor (AYTC)",
            "site_id": 1, "center_id": 1, "center_name": "AYTC",
            "max_capacity": 4, "no_internet_permits": False,
            "event_type_list": [],
        },
    ]})
    out = parse_resource_search(payload)
    assert out[0].is_indoor is True
