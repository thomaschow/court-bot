from bay_area_courtbot.discover.probe import (
    _MEMBER_ID_PATTERNS,
    _MEMBERSHIP_ID_PATTERNS,
    _first_match,
    _parse_reservation_types,
)


def test_member_id_from_lifetime_js() -> None:
    # Pattern verified live against Lifetime Santa Clara on 2026-04-27.
    html = "var url = 'https://api4.courtreserve.com/Online/Utils/...?orgId=13234&userId=8462028';"
    assert _first_match(_MEMBER_ID_PATTERNS, html) == 8462028


def test_member_id_from_my_fam_members() -> None:
    html = 'myFamMembers.push(Number("8462028"));'
    assert _first_match(_MEMBER_ID_PATTERNS, html) == 8462028


def test_member_id_from_data_attr() -> None:
    assert _first_match(_MEMBER_ID_PATTERNS, '<div data-member-id="555111">') == 555111


def test_membership_id_from_url_param() -> None:
    html = "url + '&membershipId=1500758&isFamilyLevel=True'"
    assert _first_match(_MEMBERSHIP_ID_PATTERNS, html) == 1500758


def test_parse_reservation_types_singles_and_doubles() -> None:
    js = '{"Id":7,"Name":"Singles","Other":1},{"Id":8,"Name":"Doubles","Other":2}'
    out = _parse_reservation_types(js)
    assert out == {"singles": 7, "doubles": 8}
