"""Probe Seattle ANC's reservation search page.

The search page is public (no login required). Open it in headless Chromium with
the search term "tennis court", let the SPA render + fetch its data, intercept
every network response, and dump them locally for offline analysis.

Output:
  state/seattle_probe/search.html              — fully-rendered DOM
  state/seattle_probe/search.png               — screenshot
  state/seattle_probe/search_responses.json    — every captured XHR/fetch (JSON-only)
  state/seattle_probe/search_response_urls.txt — quick index (status + URL only)
"""

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
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        try:
            context = await browser.new_context(
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
                if (
                    "json" in ct
                    or "/api/" in u
                    or "/rest/" in u
                    or "/anc/" in u
                ):
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
                        "url": u,
                        "method": resp.request.method,
                        "status": resp.status,
                        "content_type": ct,
                        "request_body": req_body,
                        "request_headers": dict(resp.request.headers),
                        "body_preview": body[:8000],
                    })

            page.on("response", on_response)
            print(f"navigating: {URL}")
            await page.goto(URL, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(3000)
            await page.screenshot(path=str(OUT / "search.png"), full_page=True)
            html = await page.content()
            (OUT / "search.html").write_text(html)
            (OUT / "search_responses.json").write_text(json.dumps(captured, indent=2))
            with (OUT / "search_response_urls.txt").open("w") as f:
                for r in captured:
                    f.write(f"{r['status']:>3}  {r['method']:<5}  {r['url']}\n")
            print(f"wrote {OUT / 'search.html'} ({len(html)} bytes)")
            print(f"captured {len(captured)} JSON-shaped responses → {OUT / 'search_responses.json'}")
            print(f"\n=== distinct hosts ===")
            hosts = sorted({r["url"].split("/")[2] for r in captured if "://" in r["url"]})
            for h in hosts:
                print(f"  {h}")
            print(f"\n=== distinct path prefixes ===")
            prefixes = sorted({
                "/".join(r["url"].split("/")[3:6])
                for r in captured if "://" in r["url"]
            })
            for p in prefixes:
                print(f"  /{p}")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
