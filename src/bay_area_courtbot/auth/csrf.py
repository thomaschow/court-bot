from __future__ import annotations

from lxml import html


def extract_token(page_html: str) -> str | None:
    """Extract the __RequestVerificationToken hidden input value from an MVC page.

    Returns None if not found. CourtReserve renders one or more such tokens; we
    take the first which is sufficient for AJAX POSTs from the bookings page.
    """
    if not page_html:
        return None
    try:
        tree = html.fromstring(page_html)
    except (ValueError, html.etree.ParserError):
        return None
    nodes = tree.xpath('//input[@name="__RequestVerificationToken"]/@value')
    if not nodes:
        return None
    return str(nodes[0])
