"""Open the search results page in a logged-in session and click into the first
tennis court result. Captures the navigation URL + any subsequent network calls
(which include the schedule API and the booking modal endpoints)."""

import asyncio
import json
from pathlib import Path

OUT = Path("state/seattle_probe")
OUT.mkdir(parents=True, exist_ok=True)

URL = (
    "https://anc.apm.activecommunities.com/seattle/reservation/search"
    "?keyword=tennis%20court&resourceType=0&equipmentQty=0"
)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


async def main() -> None:
    from playwright.async_api import async_playwright

    captured: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        try:
            context = await browser.new_context(
                storage_state="state/session/seattle.json",
                user_agent=USER_AGENT,
                viewport={"width": 1440, "height": 900},
                locale="en-US",
                timezone_id="America/Los_Angeles",
            )
            await context.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            )
            page = await context.new_page()

            async def on_response(resp):
                ct = resp.headers.get("content-type", "")
                u = resp.url
                if "json" in ct or "/rest/" in u or "/api/" in u:
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
            print(f"navigating: {URL}")
            await page.goto(URL, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)
            initial_url = page.url
            print(f"on search page: {initial_url}")

            # Click the first reservable result row. ANC renders these as
            # <div role="link" class="card-package-actual__card item-searched-actual">
            # with id like "1146-0" (resource_id-index). Skip "Not reservable online" rows.
            selectors = [
                'div[role="link"][aria-label*="Tennis Court"]:not([aria-label*="Not reservable"])',
                '.item-searched-actual:not([aria-label*="Not reservable"])',
                'div[role="link"]',
            ]
            clicked = False
            for sel in selectors:
                try:
                    loc = page.locator(sel).first
                    await loc.wait_for(state="visible", timeout=3000)
                    href = await loc.get_attribute("href") or "<no href>"
                    print(f"clicking via {sel} → href={href}")
                    await loc.click(force=True)
                    clicked = True
                    break
                except Exception as exc:
                    print(f"  {sel} failed: {exc}")
                    continue

            if not clicked:
                print("(no result row found; SPA layout may be different)")
            await page.wait_for_timeout(4000)
            print(f"after click, URL: {page.url}")
            await page.screenshot(path=str(OUT / "click_into.png"), full_page=True)
            (OUT / "click_into.html").write_text(await page.content())
            (OUT / "click_into_responses.json").write_text(json.dumps(captured, indent=2))

            print(f"\n=== captured {len(captured)} JSON responses ===")
            for r in captured[-15:]:
                print(f"  {r['status']:>3} {r['method']:<5} {r['url'][:140]}")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
