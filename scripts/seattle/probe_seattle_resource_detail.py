"""Probe the per-resource detail page in a logged-in Seattle ANC session.

Opens one tennis court's detail page, lets the SPA render + fetch its data,
intercepts every network call (request bodies + response bodies), and dumps
them locally. This is what unlocks Phase 2 — we need to see the schedule API
shape and the booking POST shape.

Output:
  state/seattle_probe/resource_detail.html
  state/seattle_probe/resource_detail.png
  state/seattle_probe/resource_detail_responses.json
  state/seattle_probe/resource_detail_response_urls.txt

Defaults to resource id 355 (Lower Woodland Playfield Tennis Court 03) — the
exact court doesn't matter; we're just trying to capture endpoint shapes.
"""

import asyncio
import json
import sys
from pathlib import Path

OUT = Path("state/seattle_probe")
OUT.mkdir(parents=True, exist_ok=True)

# Lower Woodland Court 03 by default; CLI override: scripts/seattle/probe_seattle_resource_detail.py 1146
RESOURCE_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 355
URL = (
    f"https://anc.apm.activecommunities.com/seattle/reservation/landing/quick"
    f"?groupId=0&locale=en-US&resourceId={RESOURCE_ID}"
)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
STORAGE_STATE = "state/session/seattle.json"


async def main() -> None:
    from playwright.async_api import async_playwright

    if not Path(STORAGE_STATE).exists():
        raise SystemExit(
            f"no storage_state at {STORAGE_STATE} — run `seattle-courtbot login` first"
        )

    captured: list[dict] = []

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

            async def on_response(resp):
                ct = resp.headers.get("content-type", "")
                u = resp.url
                if (
                    "json" in ct
                    or "/rest/" in u
                    or "/api/" in u
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
            await page.screenshot(path=str(OUT / "resource_detail.png"), full_page=True)
            html = await page.content()
            (OUT / "resource_detail.html").write_text(html)
            (OUT / "resource_detail_responses.json").write_text(json.dumps(captured, indent=2))
            with (OUT / "resource_detail_response_urls.txt").open("w") as f:
                for r in captured:
                    f.write(f"{r['status']:>3}  {r['method']:<5}  {r['url']}\n")
            print(f"captured {len(captured)} JSON-shaped responses")
            print(f"\n=== distinct path prefixes ===")
            prefixes = sorted({
                "/".join(r["url"].split("/")[3:7])
                for r in captured if "://" in r["url"]
            })
            for p in prefixes:
                print(f"  /{p}")
            print(f"\n=== POST endpoints ===")
            for r in captured:
                if r["method"] == "POST":
                    print(f"  {r['url']}")
                    if r.get("request_body"):
                        print(f"    body: {r['request_body'][:240]}")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
