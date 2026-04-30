"""Logged-in search page: wait for results to render, then dump every clickable
element (links, buttons, click-handler-bearing divs) so we can identify the
detail-page navigation."""

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

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        try:
            context = await browser.new_context(
                storage_state="state/session/seattle.json",
                user_agent=USER_AGENT, viewport={"width": 1440, "height": 900},
                locale="en-US", timezone_id="America/Los_Angeles",
            )
            await context.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            )
            page = await context.new_page()
            await page.goto(URL, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(4000)
            (OUT / "search_loggedin.html").write_text(await page.content())
            await page.screenshot(path=str(OUT / "search_loggedin.png"), full_page=True)

            inventory = await page.evaluate(
                """() => Array.from(document.querySelectorAll(
                    'a, button, [role="button"], [onclick]'
                )).map(el => ({
                    tag: el.tagName,
                    href: el.href || null,
                    text: (el.innerText || '').trim().slice(0, 80),
                    aria: el.getAttribute('aria-label'),
                    cls: typeof el.className === 'string' ? el.className.slice(0, 80) : null,
                    data: Object.fromEntries(
                        Array.from(el.attributes).filter(a => a.name.startsWith('data-')).map(a => [a.name, a.value])
                    ),
                }))"""
            )
            tennis = [e for e in inventory if "tennis" in (e["text"] or "").lower() or "reserve" in (e["text"] or "").lower()]
            print(f"=== {len(tennis)} elements with 'tennis' or 'reserve' in text ===")
            for e in tennis[:20]:
                print(f"  <{e['tag']}>  text={e['text']!r:<50}  href={e['href']}")
                if e["data"]:
                    print(f"      data={e['data']}")
            (OUT / "search_loggedin_clickables.json").write_text(json.dumps(inventory, indent=2))
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
