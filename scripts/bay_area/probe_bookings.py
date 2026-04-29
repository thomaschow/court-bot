"""Dump rendered bookings page so we can find member_id, courts, reservation types."""

import asyncio
import json
from pathlib import Path

from bay_area_courtbot.auth.session import build_client
from bay_area_courtbot.config import load_config
from bay_area_courtbot.paths import config_path


async def main() -> None:
    cfg = load_config(config_path())
    f = cfg.facility("santa-clara")
    out = Path("state/bookings_dump")
    out.mkdir(parents=True, exist_ok=True)

    async with build_client(f, http2=False) as client:
        # 1) Static HTML response (what the discover code currently parses).
        url = f"/Online/Reservations/Bookings/{f.org_id}"
        if f.s_id:
            url += f"?sId={f.s_id}"
        r = await client.get(url)
        (out / "static.html").write_text(r.text)
        print("static html:", len(r.text), "bytes ->", out / "static.html")

        # 2) Probe network: render via Playwright and capture all responses.
        from playwright.async_api import async_playwright

        captured = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                storage_state="state/session/13234.json",
                viewport={"width": 1440, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                ),
            )

            async def on_response(resp):
                ct = resp.headers.get("content-type", "")
                if (
                    "json" in ct
                    or resp.url.endswith(".json")
                    or "ReadExpanded" in resp.url
                    or "Reservations" in resp.url
                ):
                    try:
                        body = await resp.text()
                    except Exception:
                        body = "<unreadable>"
                    captured.append({
                        "url": resp.url,
                        "status": resp.status,
                        "content_type": ct,
                        "body_preview": body[:1000],
                    })

            page = await context.new_page()
            page.on("response", on_response)
            full_url = f.base_url + url
            await page.goto(full_url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)

            html = await page.content()
            (out / "rendered.html").write_text(html)
            print("rendered html:", len(html), "bytes ->", out / "rendered.html")

            (out / "responses.json").write_text(json.dumps(captured, indent=2))
            print("captured responses:", len(captured), "->", out / "responses.json")

            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
