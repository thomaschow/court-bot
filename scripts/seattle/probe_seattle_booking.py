"""Drive the Seattle ANC booking modal end-to-end with a logged-in session,
intercept every POST that could be a booking submission, and ABORT each before
it reaches the server. The intent is to capture the booking endpoint URL,
request body, and headers without ever creating a real reservation.

Output:
  state/seattle_probe/booking_*.png        — screenshots at each step
  state/seattle_probe/booking_responses.json — every captured network call
  state/seattle_probe/booking_aborted.json   — POSTs we intercepted+aborted

Defaults: court 1146 (Alki Playfield 01), date = today + 7 days, 6-9 PM PT.
"""

import asyncio
import json
import sys
from datetime import date, datetime, timedelta, time
from pathlib import Path

OUT = Path("state/seattle_probe")
OUT.mkdir(parents=True, exist_ok=True)

RESOURCE_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 1146
TARGET_DATE = date.today() + timedelta(days=7)
START = time(18, 0)   # 6 PM
END = time(21, 0)     # 9 PM

DETAIL_URL = (
    f"https://anc.apm.activecommunities.com/seattle/reservation/search/detail/{RESOURCE_ID}"
)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
STORAGE_STATE = "state/session/seattle.json"


# Any POST to a path containing these substrings is treated as a "potential
# booking submit" and aborted (so we capture the request without committing).
# Note: /resource/validation and /resource/proceed are pre-flight steps that don't
# create a reservation — we let those continue. The final submit happens after the
# /reservation/form step, on a yet-unknown URL — we abort anything that looks like
# a final commit (cart-add, checkout, payment, save).
ABORT_SUBSTRINGS = (
    "/rest/reservation/form/reserve/",   # ← THE final booking-submit (verified in JS)
    "/rest/reservation/checkout",
    "/rest/reservation/cart",
    "/rest/reservation/booking",
    "/rest/reservation/submit",
    "/rest/reservation/save",
    "/rest/reservation/place",
    "/rest/reservation/confirm",
    "/rest/reservation/payment",
    "/rest/reservation/finalize",
    "/rest/reservation/complete",
    "/rest/reservation/process",
)


async def main() -> None:
    from playwright.async_api import async_playwright

    if not Path(STORAGE_STATE).exists():
        raise SystemExit(f"no session at {STORAGE_STATE}; run seattle-courtbot login first")

    captured: list[dict] = []
    aborted: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            context = await browser.new_context(
                storage_state=STORAGE_STATE,
                user_agent=USER_AGENT,
                viewport={"width": 1440, "height": 900},
                locale="en-US",
                timezone_id="America/Los_Angeles",
            )
            await context.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            )
            page = await context.new_page()

            async def handle_route(route):
                req = route.request
                u = req.url
                if req.method == "POST" and any(s in u for s in ABORT_SUBSTRINGS):
                    body = req.post_data or ""
                    aborted.append({
                        "url": u,
                        "method": req.method,
                        "headers": dict(await req.all_headers()),
                        "post_data": body,
                    })
                    print(f"\n!! ABORTED POST {u}")
                    print(f"   body bytes: {len(body)}")
                    if body:
                        print(f"   preview: {body[:400]}")
                    await route.abort()
                    return
                await route.continue_()

            await page.route("**/*", handle_route)

            async def on_response(resp):
                ct = resp.headers.get("content-type", "")
                u = resp.url
                if "json" in ct or "/rest/" in u:
                    try:
                        body = await resp.text()
                    except Exception:
                        body = "<unreadable>"
                    req_body = None
                    try:
                        req_body = resp.request.post_data
                    except Exception:
                        pass
                    captured.append({
                        "url": u, "method": resp.request.method, "status": resp.status,
                        "request_body": req_body, "body_preview": body[:8000],
                    })

            page.on("response", on_response)
            print(f"navigating: {DETAIL_URL}")
            await page.goto(DETAIL_URL, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(3000)
            await page.screenshot(path=str(OUT / "booking_01_loaded.png"), full_page=True)

            # --- Step 1: Open the event-type dropdown and pick "Tennis - Outdoor" ---
            try:
                evt = page.locator('select, [role="combobox"]').filter(
                    has_text="Please select"
                ).first
                await evt.wait_for(state="visible", timeout=5000)
                await evt.click(force=True)
                await page.wait_for_timeout(500)
                await page.locator('text=Tennis - Outdoor').first.click(force=True)
                print("selected event_type = Tennis - Outdoor")
            except Exception as exc:
                print(f"event-type pick failed: {exc}")

            # --- Step 2: Set attendees ---
            try:
                attendees = page.locator('select, [role="combobox"]').filter(
                    has_text="Please select"
                ).first
                await attendees.click(force=True)
                await page.wait_for_timeout(300)
                await page.locator('text="2"').first.click(force=True)
            except Exception:
                pass

            # --- Step 3: Click "Add dates and times" → opens a date/time modal ---
            try:
                add_btn = page.locator('text="Add dates and times"').first
                await add_btn.wait_for(state="visible", timeout=5000)
                await add_btn.click(force=True)
                await page.wait_for_timeout(1500)
                await page.screenshot(path=str(OUT / "booking_02_modal.png"), full_page=True)
                # Always dump the full page after the modal opens — the modal is part
                # of the same DOM and we'll find its selectors offline.
                (OUT / "booking_after_modal.html").write_text(await page.content())
                # Also dump every visible button/clickable for inspection.
                inv = await page.evaluate(
                    """() => Array.from(document.querySelectorAll(
                        'button, a, [role="button"], [role="link"], [role="gridcell"], '
                        + 'td.an-calendar-table-cell, .timeslots__avaliable, '
                        + '.an-calendar-table-cell, .available-day, .available'
                    )).filter(el => {
                        const r = el.getBoundingClientRect();
                        return r.width > 0 && r.height > 0;
                    }).map(el => ({
                        tag: el.tagName, role: el.getAttribute('role'),
                        text: (el.innerText || '').trim().slice(0, 60),
                        aria: el.getAttribute('aria-label'),
                        cls: typeof el.className === 'string' ? el.className.slice(0, 100) : null,
                    }))"""
                )
                # Keep date cells, timeslot links, and reserve-flow buttons.
                interesting = [
                    e for e in inv
                    if (e["aria"] and ("available" in e["aria"].lower()
                                       or "weekday" in e["aria"].lower()
                                       or any(d in (e["aria"] or "")
                                              for d in ["Apr", "May", "Jun"])))
                    or "timeslots" in (e["cls"] or "").lower()
                    or "calendar-table" in (e["cls"] or "").lower()
                    or any(k in (e["text"] or "").lower()
                           for k in ("check availability", "reserve", "add to cart",
                                     "next", "confirm", "set"))
                ]
                (OUT / "booking_modal_clickables.json").write_text(json.dumps(interesting, indent=2))
                print(f"wrote {len(interesting)} clickables to booking_modal_clickables.json")
            except Exception as exc:
                print(f"date-modal open failed: {exc}")

            # --- Step 4: Click the first available time slot in the calendar ---
            try:
                slot = page.locator('a.timeslots__avaliable').first
                await slot.wait_for(state="visible", timeout=5000)
                slot_text = await slot.inner_text()
                print(f"clicking time slot: {slot_text}")
                await slot.click(force=True)
                await page.wait_for_timeout(1500)
                await page.screenshot(path=str(OUT / "booking_03_slot_picked.png"), full_page=True)
            except Exception as exc:
                print(f"time-slot click failed: {exc}")

            # --- Step 4b: Narrow the time range to 6-9 PM (within 3h max). The right
            # panel has two text inputs for start/end. Clear and fill them.
            try:
                # Look for time inputs in the right-side "Selected dates and times" panel.
                # The two inputs are siblings; the first is start, the second is end.
                time_inputs = page.locator('input[placeholder*="AM"], input[placeholder*="PM"], input[type="text"][value*="AM"], input[type="text"][value*="PM"]')
                count = await time_inputs.count()
                print(f"found {count} time inputs")
                if count >= 2:
                    await time_inputs.nth(0).fill("6:00 PM")
                    await time_inputs.nth(1).fill("9:00 PM")
                    await page.wait_for_timeout(500)
                    print("set range to 6:00 PM - 9:00 PM")
            except Exception as exc:
                print(f"time-input fill failed: {exc}")

            # --- Step 5: Click "Apply" to confirm the picked time range ---
            for label in ("Apply", "OK", "Save", "Set", "Confirm", "Done"):
                try:
                    btn = page.locator(f'button:has-text("{label}")').first
                    if await btn.is_visible():
                        print(f"clicking sub-modal: {label}")
                        await btn.click(force=True)
                        await page.wait_for_timeout(1500)
                        break
                except Exception:
                    continue

            await page.screenshot(path=str(OUT / "booking_04_after_sub.png"), full_page=True)

            # --- Step 6: Click "Check availability" (the main blue button on the form) ---
            try:
                check_btn = page.locator('button:has-text("Check availability")').first
                if await check_btn.is_visible():
                    print("clicking Check availability")
                    await check_btn.click(force=True)
                    await page.wait_for_timeout(3000)
                    await page.screenshot(path=str(OUT / "booking_05_checked.png"), full_page=True)
            except Exception as exc:
                print(f"check-availability failed: {exc}")

            # --- Step 7: Click "Proceed" to advance to the checkout form ---
            try:
                pb = page.locator('button:has-text("Proceed")').first
                await pb.wait_for(state="visible", timeout=5000)
                print("clicking Proceed → advances to /reservation/form")
                await pb.click(force=True)
                await page.wait_for_timeout(4000)
                await page.screenshot(path=str(OUT / "booking_06_form.png"), full_page=True)
                (OUT / "booking_form_page.html").write_text(await page.content())
            except Exception as exc:
                print(f"Proceed click failed: {exc}")

            # --- Step 8a: Fill the required "Event name" text input.
            try:
                evt_name = page.locator(
                    'input[placeholder*="event name" i], input[placeholder*="Please enter" i]'
                ).first
                await evt_name.wait_for(state="visible", timeout=5000)
                await evt_name.fill("probe — DO NOT BOOK")
                print("filled event name")
            except Exception as exc:
                print(f"event-name fill failed: {exc}")

            # --- Step 9: Inventory all visible buttons on the form page so we know
            # what selector to click (final submit). Dump for inspection.
            try:
                btn_inventory = await page.evaluate(
                    """() => Array.from(document.querySelectorAll('button, [role="button"]'))
                        .filter(el => {
                            const r = el.getBoundingClientRect();
                            return r.width > 0 && r.height > 0;
                        })
                        .map(el => ({
                            text: (el.innerText || '').trim().slice(0, 60),
                            cls: typeof el.className === 'string' ? el.className.slice(0, 100) : null,
                            disabled: el.disabled || false,
                        }))"""
                )
                (OUT / "booking_form_buttons.json").write_text(json.dumps(btn_inventory, indent=2))
                print(f"\n=== form-page buttons ===")
                for b in btn_inventory:
                    if b["text"]:
                        print(f"  text={b['text']!r:<40}  disabled={b['disabled']}  cls={(b['cls'] or '')[:50]}")
            except Exception as exc:
                print(f"button inventory failed: {exc}")

            # --- Step 10: Click "Add to cart" — the final submit button on Seattle ANC.
            # The abort filter (ABORT_SUBSTRINGS includes /cart) will intercept the POST
            # and prevent it from reaching the server, so no real reservation is created.
            try:
                btn = page.locator('button:has-text("Add to cart")').first
                await btn.wait_for(state="visible", timeout=5000)
                print("clicking 'Add to cart' (POST will be intercepted+aborted)")
                await btn.click(force=True)
                await page.wait_for_timeout(5000)
            except Exception as exc:
                print(f"Add to cart click failed: {exc}")

            await page.wait_for_timeout(2000)
            await page.screenshot(path=str(OUT / "booking_99_final.png"), full_page=True)
            (OUT / "booking_responses.json").write_text(json.dumps(captured, indent=2))
            (OUT / "booking_aborted.json").write_text(json.dumps(aborted, indent=2))
            print(f"\n=== captured {len(captured)} responses, aborted {len(aborted)} POSTs ===")
            for r in captured[-15:]:
                print(f"  {r['status']:>3} {r['method']:<5} {r['url'][:140]}")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
