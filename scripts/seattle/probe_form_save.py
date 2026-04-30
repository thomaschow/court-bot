"""Drive the SPA past the form page, fill event name, and capture every PUT/POST
that fires WHILE the form is being filled. The form data must be sent to the
server somewhere — this probe finds where. Aborts the actual /form/reserve
POST so no booking happens."""

import asyncio
import json
from pathlib import Path

OUT = Path("state/seattle_probe")
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


async def main() -> None:
    from playwright.async_api import async_playwright

    captured_writes = []
    aborted_reserves = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        try:
            ctx = await browser.new_context(
                storage_state="state/session/seattle.json",
                user_agent=USER_AGENT, viewport={"width": 1440, "height": 900},
                locale="en-US", timezone_id="America/Los_Angeles",
            )
            await ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
            page = await ctx.new_page()

            async def handle_route(route):
                req = route.request
                u = req.url
                # Capture all writes (POST/PUT/PATCH) to /rest/reservation/
                if req.method in ("POST", "PUT", "PATCH") and "/rest/reservation/" in u:
                    body = req.post_data or ""
                    rec = {"url": u, "method": req.method, "body": body}
                    if "/form/reserve/" in u:
                        aborted_reserves.append(rec)
                        await route.abort()
                        return
                    captured_writes.append(rec)
                await route.continue_()

            await page.route("**/*", handle_route)

            await page.goto(
                "https://anc.apm.activecommunities.com/seattle/reservation/search/detail/358",
                wait_until="networkidle", timeout=30000,
            )
            await page.wait_for_timeout(3000)

            # Click event type dropdown via direct selector (not :has-text)
            try:
                # The combobox is actually a div with role=combobox, but try .ant-select first
                evt = page.locator('.ant-select').first
                await evt.click(force=True)
                await page.wait_for_timeout(400)
                await page.locator('text=Tennis - Outdoor').first.click(force=True)
                await page.wait_for_timeout(500)
            except Exception as e:
                print(f"event pick: {e}")

            # Increment attendees to 2
            try:
                inc = page.locator('button[aria-label="Increase number of attendees"]').first
                await inc.click(force=True)
                await page.wait_for_timeout(300)
            except Exception:
                pass

            # Open dates+times
            try:
                await page.locator('text="Add dates and times"').first.click(force=True)
                await page.wait_for_timeout(1500)
            except Exception:
                pass

            # Click first time slot
            try:
                await page.locator('a.timeslots__avaliable').first.click(force=True)
                await page.wait_for_timeout(1500)
            except Exception:
                pass

            # Adjust times
            try:
                ti = page.locator('input[type="text"][value*="AM"], input[type="text"][value*="PM"]')
                if await ti.count() >= 2:
                    await ti.nth(0).fill("6:00 PM")
                    await ti.nth(1).fill("8:00 PM")
            except Exception:
                pass

            # Apply
            try:
                await page.locator('button:has-text("Apply")').first.click(force=True)
                await page.wait_for_timeout(1500)
            except Exception:
                pass

            # Proceed
            try:
                await page.locator('button:has-text("Proceed")').first.click(force=True)
                await page.wait_for_timeout(4000)
            except Exception:
                pass

            await page.screenshot(path=str(OUT / "form_save_form_page.png"), full_page=True)

            # Fill event name + blur (so any onBlur save fires)
            try:
                en = page.locator('input[placeholder*="event name" i], input[placeholder*="Please enter" i]').first
                await en.wait_for(state="visible", timeout=8000)
                await en.fill("probe — DO NOT BOOK")
                # Tab away to trigger blur
                await page.keyboard.press("Tab")
                await page.wait_for_timeout(2000)
            except Exception as e:
                print(f"event name fill: {e}")

            # Click Add to cart (will be aborted via route)
            try:
                await page.locator('button:has-text("Add to cart")').first.click(force=True)
                await page.wait_for_timeout(3000)
            except Exception:
                pass

            print(f"\n=== {len(captured_writes)} non-reserve writes captured ===")
            for w in captured_writes:
                print(f"  {w['method']:<5} {w['url'][:140]}")
                if w['body']:
                    print(f"    body: {w['body'][:400]}")
            print(f"\n=== {len(aborted_reserves)} aborted form/reserve ===")
            for w in aborted_reserves:
                print(f"  {w['method']} {w['url']}")
                print(f"    body: {w['body'][:1500]}")
            (OUT / "form_save_writes.json").write_text(json.dumps({
                "writes": captured_writes, "aborted": aborted_reserves,
            }, indent=2))
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
