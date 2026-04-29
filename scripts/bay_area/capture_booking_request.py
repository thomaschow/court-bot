"""Drive the CourtReserve booking modal via Playwright and capture (without submitting)
the booking POST request. Aborts the request before it hits the server, so no real
reservation is made.

Output:
  state/booking_capture/request.json   - URL, method, headers, post_data
  state/booking_capture/page.html      - full DOM at the moment of capture
  state/booking_capture/modal.html     - just the modal's inner HTML
  state/booking_capture/modal.png      - screenshot of the modal
"""

import asyncio
import json
import re
from pathlib import Path

from bay_area_courtbot.config import load_config
from bay_area_courtbot.paths import config_path

OUT = Path("state/booking_capture")
OUT.mkdir(parents=True, exist_ok=True)


async def main() -> None:
    from playwright.async_api import async_playwright

    cfg = load_config(config_path())
    f = cfg.facility("santa-clara")
    bookings_url = f"{f.base_url}/Online/Reservations/Bookings/{f.org_id}?sId={f.s_id}"

    captured: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            storage_state="state/session/13234.json",
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        page = await context.new_page()

        async def handle_route(route):
            req = route.request
            url = req.url
            host = url.split("/")[2] if "://" in url else ""
            if (
                req.method == "POST"
                and host.endswith("courtreserve.com")
                and ("Reservation" in url or "Reserve" in url)
                and "ReadConsolidated" not in url
                and "Read" not in url.split("/")[-1]
            ):
                body = req.post_data or ""
                captured.append({
                    "url": url,
                    "method": req.method,
                    "headers": dict(await req.all_headers()),
                    "post_data": body,
                })
                print(f"\nCAPTURED POST → {url}")
                print(f"  body bytes: {len(body)}")
                await route.abort()
                return
            await route.continue_()

        await page.route("**/*", handle_route)

        print(f"navigating: {bookings_url}")
        await page.goto(bookings_url, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)
        await page.screenshot(path=str(OUT / "01_loaded.png"), full_page=True)

        # Try to advance the Kendo scheduler to a future date so we don't pick a past slot.
        # The scheduler exposes a "nextArrow" navigation button.
        for sel in (
            'button[title="next"]',
            'a.k-nav-next',
            '.k-scheduler-toolbar .k-nav-next',
            'button:has-text("Next")',
        ):
            try:
                btn = page.locator(sel).first
                await btn.wait_for(state="visible", timeout=2000)
                for _ in range(3):
                    await btn.click()
                    await page.wait_for_timeout(500)
                print(f"advanced calendar via: {sel}")
                break
            except Exception:
                continue

        await page.screenshot(path=str(OUT / "02_navigated.png"), full_page=True)

        # Click a cell that says "Reserve" — that's how Lifetime's consolidated view
        # exposes available slots (verified in screenshot 02 above).
        slot_selectors = [
            'text="Reserve"',
            '.k-scheduler-content :text("Reserve")',
            'td:has-text("Reserve"):not(:has-text("Reserved"))',
            'div:has-text("Reserve"):not(:has-text("Reserved"))',
        ]
        slot_clicked = False
        for sel in slot_selectors:
            try:
                cells = page.locator(sel)
                count = await cells.count()
                if count == 0:
                    continue
                target = cells.first
                await target.scroll_into_view_if_needed(timeout=2000)
                await target.click(force=True, timeout=3000)
                print(f"clicked slot via {sel} (count={count})")
                slot_clicked = True
                break
            except Exception as exc:
                print(f"  slot selector failed {sel}: {exc}")
                continue
        await page.wait_for_timeout(2000)
        await page.screenshot(path=str(OUT / "03_after_slot_click.png"), full_page=True)

        # Capture any modal that appeared.
        modal_loc = page.locator('.k-window, .modal, [role="dialog"]').first
        try:
            await modal_loc.wait_for(state="visible", timeout=5000)
            modal_html = await modal_loc.evaluate("el => el.outerHTML")
            (OUT / "modal.html").write_text(modal_html)
            print(f"modal captured: {len(modal_html)} bytes -> {OUT / 'modal.html'}")
        except Exception:
            print("(no modal appeared after slot click)")

        # Look for a Reserve / Save / Create button inside any modal.
        submit_selectors = [
            'button:has-text("Reserve")',
            'button:has-text("Book")',
            'button:has-text("Save")',
            'button:has-text("Create")',
            'button:has-text("Submit")',
            'input[type="submit"][value*="Reserve" i]',
            '.k-window button.k-primary',
            '.k-window button[type="submit"]',
        ]
        for sel in submit_selectors:
            try:
                btn = page.locator(sel).first
                await btn.wait_for(state="visible", timeout=1500)
                print(f"clicking submit via {sel}")
                await btn.click(force=True)
                break
            except Exception:
                continue

        # Give the request interceptor a moment to fire.
        await page.wait_for_timeout(3000)
        await page.screenshot(path=str(OUT / "04_after_submit.png"), full_page=True)
        full_html = await page.content()
        (OUT / "page.html").write_text(full_html)

        if captured:
            (OUT / "request.json").write_text(json.dumps(captured, indent=2))
            print(f"\nWROTE {OUT / 'request.json'} with {len(captured)} request(s)")
        else:
            print(f"\nNO booking POST captured. Inspect screenshots in {OUT}/")
            print("Likely the slot click / modal submit selectors need tuning for this UI.")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
