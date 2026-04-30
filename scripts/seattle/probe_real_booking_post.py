"""Drive the SPA cleanly past the form page and capture the EXACT POST body
the SPA sends to `/form/reserve/`. Aborts the request before it reaches the
server — no booking is created. This runs against a logged-in session that
has 1 pending booking (the May 7 court 358 one from prior failed attempts);
the SPA picks that up automatically when we hit /reservation/form."""

import asyncio
import json
from pathlib import Path

OUT = Path("state/seattle_probe")
OUT.mkdir(parents=True, exist_ok=True)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Navigate directly to the resource detail page so the SPA bootstraps a fresh
# proceed flow (rather than reading any leftover cart state).
DETAIL_URL = "https://anc.apm.activecommunities.com/seattle/reservation/search/detail/358"


async def main() -> None:
    from playwright.async_api import async_playwright

    captured = None
    aborted: list[dict] = []
    other_posts: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            ctx = await browser.new_context(
                storage_state="state/session/seattle.json",
                user_agent=USER_AGENT, viewport={"width": 1440, "height": 900},
                locale="en-US", timezone_id="America/Los_Angeles",
            )
            await ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            )
            page = await ctx.new_page()

            async def handle_route(route):
                req = route.request
                u = req.url
                if req.method == "POST" and "/rest/reservation/form/reserve/" in u:
                    body = req.post_data or ""
                    aborted.append({"url": u, "body": body, "headers": dict(await req.all_headers())})
                    print(f"!! INTERCEPTED form/reserve POST")
                    print(f"   url: {u}")
                    print(f"   body bytes: {len(body)}")
                    if body:
                        try:
                            print(f"   body JSON:\n{json.dumps(json.loads(body), indent=2)[:5000]}")
                        except Exception:
                            print(f"   body raw: {body[:1500]}")
                    await route.abort()
                    return
                await route.continue_()

            await page.route("**/*", handle_route)

            print(f"navigating: {DETAIL_URL}")
            await page.goto(DETAIL_URL, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2500)

            # Pick event type
            try:
                evt = page.locator('[role="combobox"]').filter(has_text="Please select").first
                await evt.click(force=True)
                await page.wait_for_timeout(300)
                await page.locator('text=Tennis - Outdoor').first.click(force=True)
                await page.wait_for_timeout(500)
            except Exception as e:
                print(f"event type pick failed: {e}")

            # Click "Add dates and times"
            try:
                await page.locator('text="Add dates and times"').first.click(force=True)
                await page.wait_for_timeout(1500)
            except Exception as e:
                print(f"add-dates failed: {e}")

            # Click a future-date timeslot — pick first available
            try:
                slot = page.locator('a.timeslots__avaliable').first
                await slot.click(force=True)
                await page.wait_for_timeout(1500)
            except Exception as e:
                print(f"timeslot click failed: {e}")

            # Set the time range to 6-8 PM
            try:
                inputs = page.locator(
                    'input[placeholder*="AM"], input[placeholder*="PM"], '
                    'input[type="text"][value*="AM"], input[type="text"][value*="PM"]'
                )
                if await inputs.count() >= 2:
                    await inputs.nth(0).fill("6:00 PM")
                    await inputs.nth(1).fill("8:00 PM")
            except Exception as e:
                print(f"time fill failed: {e}")

            # Click Apply
            try:
                await page.locator('button:has-text("Apply")').first.click(force=True)
                await page.wait_for_timeout(1500)
            except Exception:
                pass

            # Click Proceed
            try:
                await page.locator('button:has-text("Proceed")').first.click(force=True)
                await page.wait_for_timeout(4000)
            except Exception as e:
                print(f"proceed click failed: {e}")

            await page.screenshot(path=str(OUT / "real_booking_form.png"), full_page=True)

            # Fill event name + click Add to cart
            for sel in (
                'input[placeholder*="event name" i]',
                'input[placeholder*="enter an event" i]',
                'input[type="text"][required]',
            ):
                try:
                    fld = page.locator(sel).first
                    await fld.wait_for(state="visible", timeout=3000)
                    await fld.fill("probe — DO NOT BOOK")
                    print(f"filled event name via {sel}")
                    break
                except Exception:
                    continue

            await page.wait_for_timeout(500)
            try:
                btn = page.locator('button:has-text("Add to cart")').first
                await btn.wait_for(state="visible", timeout=5000)
                print("clicking Add to cart (will be intercepted)…")
                await btn.click(force=True)
                await page.wait_for_timeout(5000)
            except Exception as e:
                print(f"Add to cart click failed: {e}")

            (OUT / "real_booking_aborted.json").write_text(json.dumps(aborted, indent=2))
            print(f"\n=== {len(aborted)} aborted form/reserve POSTs ===")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
