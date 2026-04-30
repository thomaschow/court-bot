"""Find the CSRF header name ANC expects on POSTs.

Steps:
  1. GET the search page to extract `window.__csrfToken` from the HTML.
  2. Send a validation POST trying each candidate header name; the one that
     gets us past response_code 0012 ("Invalid CSRF token") wins.
"""

import asyncio
import json
import re
import uuid
from datetime import date, timedelta

from seattle_courtbot.auth.session import build_client


async def main() -> None:
    # Use Playwright with the saved login session to read window.__csrfToken
    # post-render — the SPA injects it after JS executes.
    from playwright.async_api import async_playwright

    token = None
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        try:
            ctx = await browser.new_context(
                storage_state="state/session/seattle.json",
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1440, "height": 900},
            )
            page = await ctx.new_page()
            await page.goto(
                "https://anc.apm.activecommunities.com/seattle/reservation/search/detail/1146",
                wait_until="networkidle", timeout=30000,
            )
            token = await page.evaluate("window.__csrfToken")
        finally:
            await browser.close()
    if not token:
        raise SystemExit("could not read window.__csrfToken via Playwright")
    print(f"CSRF token: {token}")

    async with build_client(http2=False) as client:

        # 2) Try candidate headers.
        target_date = date.today() + timedelta(days=7)
        body = {
            "customer_id": 422698,
            "company_id": 0,
            "participant_type": 2,
            "attendee": 2,
            "resource_id": 1146,
            "reservation_unit": "minute",
            "reservation_time_groups": [{
                "short_summary": "",
                "summary": "",
                "reservation_times": [{
                    "start_event_datetime": f"{target_date} 18:00:00",
                    "end_event_datetime": f"{target_date} 21:00:00",
                    "availability": "Available",
                    "booking_identifier": str(uuid.uuid4()),
                }],
                "adjusted_message": "",
                "group_id": 2,
                "availability": "Available",
            }],
            "reno": 0,
            "event_type_id": 152,
            "is_clear_group": False,
        }

        candidate_headers = [
            "X-CSRF-Token",
            "X-CSRFToken",
            "X-XSRF-TOKEN",
            "X-CSRF-TOKEN",
            "CSRF-Token",
            "X-Requested-With-CSRF",
            "X-AN-Csrf-Token",
            "Csrf-Token",
        ]
        for name in candidate_headers:
            r = await client.post(
                "/seattle/rest/reservation/resource/validation?locale=en-US",
                json=body,
                headers={name: token},
            )
            data = r.json()
            code = data.get("headers", {}).get("response_code")
            msg = data.get("headers", {}).get("response_message", "")
            tag = "✓" if code != "0012" else "✗"
            print(f"  {tag}  header={name:<30}  code={code}  msg={msg[:50]}")


if __name__ == "__main__":
    asyncio.run(main())
