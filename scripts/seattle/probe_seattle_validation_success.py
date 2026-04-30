"""Send a successful validation POST (3-hour reservation) and inspect the
response body for hints about the next booking step. Also probe candidate
booking-submit endpoints. All requests are GETs or aborted-POSTs — no real
booking is created.

Output:
  state/seattle_probe/validation_success.json
  state/seattle_probe/booking_endpoint_probes.json
"""

import asyncio
import json
import uuid
from datetime import date, timedelta
from pathlib import Path

import httpx

from seattle_courtbot.auth.session import build_client

OUT = Path("state/seattle_probe")
OUT.mkdir(parents=True, exist_ok=True)

RESOURCE_ID = 1146                # Alki Playfield Court 01
CUSTOMER_ID = 422698              # Pinoe Chu
EVENT_TYPE_ID = 152               # Tennis - Outdoor
TARGET_DATE = date.today() + timedelta(days=7)


async def main() -> None:
    body = {
        "customer_id": CUSTOMER_ID,
        "company_id": 0,
        "participant_type": 2,
        "attendee": 2,
        "resource_id": RESOURCE_ID,
        "reservation_unit": "minute",
        "reservation_time_groups": [{
            "short_summary": "",
            "summary": "",
            "reservation_times": [{
                "start_event_datetime": f"{TARGET_DATE} 18:00:00",
                "end_event_datetime": f"{TARGET_DATE} 21:00:00",
                "availability": "Available",
                "booking_identifier": str(uuid.uuid4()),
            }],
            "adjusted_message": "",
            "group_id": 2,
            "availability": "Available",
        }],
        "reno": 0,
        "event_type_id": EVENT_TYPE_ID,
        "is_clear_group": False,
    }

    # Pull the CSRF token via Playwright (it's a JS-set global on rendered pages).
    from playwright.async_api import async_playwright

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
                f"https://anc.apm.activecommunities.com/seattle/reservation/search/detail/{RESOURCE_ID}",
                wait_until="networkidle", timeout=30000,
            )
            csrf = await page.evaluate("window.__csrfToken")
        finally:
            await browser.close()
    print(f"CSRF: {csrf}")
    headers = {"X-CSRF-Token": csrf}

    async with build_client(http2=False) as client:
        # 1) Validation with a *valid* 3h range
        r = await client.post(
            "/seattle/rest/reservation/resource/validation?locale=en-US",
            json=body, headers=headers,
        )
        print(f"validation POST → {r.status_code}")
        envelope = r.json()
        (OUT / "validation_success.json").write_text(json.dumps(envelope, indent=2))
        rcode = envelope.get("headers", {}).get("response_code")
        rmsg = envelope.get("headers", {}).get("response_message")
        status = envelope.get("body", {}).get("status")
        booking_errors = envelope.get("body", {}).get("booking_errors", {})
        print(f"  response_code = {rcode}  status = {status}")
        if booking_errors:
            print(f"  booking_errors: {booking_errors}")
        else:
            print("  ✓ validation passed (no booking_errors)")
        print(f"  full body keys: {list(envelope.get('body', {}).keys())}")

        # 2) Probe candidate booking-submit endpoints with the same body — look for any that
        # respond differently (200 with confirmation, 400 with hint, etc.).
        candidates = [
            "/seattle/rest/reservation/save?locale=en-US",
            "/seattle/rest/reservation/cart/add?locale=en-US",
            "/seattle/rest/reservation/cart?locale=en-US",
            "/seattle/rest/reservation/checkout?locale=en-US",
            "/seattle/rest/reservation/submit?locale=en-US",
            "/seattle/rest/reservation/place?locale=en-US",
            "/seattle/rest/reservation/confirm?locale=en-US",
            "/seattle/rest/reservation?locale=en-US",
            "/seattle/rest/reservation/finalize?locale=en-US",
            "/seattle/rest/reservation/proceed?locale=en-US",
        ]
        probes = []
        for path in candidates:
            try:
                r = await client.post(path, json=body, headers=headers)
                preview = (r.text or "")[:300]
            except httpx.HTTPError as exc:
                probes.append({"url": path, "error": str(exc)})
                continue
            probes.append({
                "url": path, "status": r.status_code,
                "ct": r.headers.get("content-type", ""),
                "preview": preview,
            })
            print(f"  {r.status_code:>3}  POST {path:<60} → {preview[:120]}")
        (OUT / "booking_endpoint_probes.json").write_text(json.dumps(probes, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
