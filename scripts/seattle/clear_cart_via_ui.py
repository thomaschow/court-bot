"""Drive the Seattle ANC reservation form via Playwright to click 'Remove all
resources', and capture the API call so we can do this directly via httpx
in the future. Side effect: clears the user's pending cart.
"""

import asyncio
import json
from pathlib import Path

OUT = Path("state/seattle_probe")
OUT.mkdir(parents=True, exist_ok=True)

URL = "https://anc.apm.activecommunities.com/seattle/reservation/form"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


async def main() -> None:
    from playwright.async_api import async_playwright

    captured = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
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

            async def on_response(resp):
                u = resp.url
                if "/rest/reservation/" in u:
                    try:
                        body = await resp.text()
                    except Exception:
                        body = ""
                    captured.append({
                        "url": u, "method": resp.request.method, "status": resp.status,
                        "request_body": resp.request.post_data,
                        "body_preview": body[:2000],
                    })

            page.on("response", on_response)
            print(f"navigating: {URL}")
            await page.goto(URL, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)
            await page.screenshot(path=str(OUT / "form_before_clear.png"), full_page=True)

            # Click "Remove all resources" — observed earlier in the form page DOM.
            try:
                btn = page.locator('text="Remove all resources"').first
                await btn.wait_for(state="visible", timeout=8000)
                print("clicking 'Remove all resources'")
                await btn.click(force=True)
                await page.wait_for_timeout(1000)
                # Confirm any modal that asks "are you sure?"
                for label in ("Yes", "Remove", "Confirm", "OK"):
                    try:
                        c = page.locator(f'button:has-text("{label}")').first
                        if await c.is_visible():
                            print(f"confirming: {label}")
                            await c.click(force=True)
                            break
                    except Exception:
                        continue
                await page.wait_for_timeout(2500)
            except Exception as exc:
                print(f"remove click failed: {exc}")

            await page.screenshot(path=str(OUT / "form_after_clear.png"), full_page=True)
            (OUT / "clear_cart_responses.json").write_text(json.dumps(captured, indent=2))
            print(f"\n=== captured {len(captured)} ANC requests ===")
            for r in captured[-10:]:
                print(f"  {r['status']:>3} {r['method']:<5} {r['url'][:140]}")
                if r.get('request_body'):
                    print(f"      body: {r['request_body'][:200]}")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
