"""Open the Seattle ANC signin page in headless Chromium and dump the rendered DOM
so we can read the actual form-input names + button labels. The SPA renders JS-side
so the static HTML is empty.

Output:
  state/seattle_probe/login.png   — screenshot
  state/seattle_probe/login.html  — full rendered DOM after networkidle
  state/seattle_probe/inputs.json — every <input>/<button>/<form>/<a> with name/id/type/etc.
"""

import asyncio
import json
from pathlib import Path

OUT = Path("state/seattle_probe")
OUT.mkdir(parents=True, exist_ok=True)

URL = "https://anc.apm.activecommunities.com/seattle/signin"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


async def main() -> None:
    from playwright.async_api import async_playwright

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
            print(f"navigating: {URL}")
            await page.goto(URL, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(1500)

            await page.screenshot(path=str(OUT / "login.png"), full_page=True)
            html = await page.content()
            (OUT / "login.html").write_text(html)
            print(f"wrote {OUT / 'login.png'} ({len(html)} bytes html)")

            inventory = await page.evaluate(
                """() => {
                    const pick = el => ({
                        tag: el.tagName,
                        type: el.type || null,
                        name: el.name || null,
                        id: el.id || null,
                        className: typeof el.className === 'string' ? el.className : null,
                        autocomplete: el.autocomplete || null,
                        placeholder: el.placeholder || null,
                        text: (el.innerText || el.value || '').slice(0, 80),
                        action: el.action || null,
                        dataAttrs: Object.fromEntries(
                            Array.from(el.attributes || [])
                                .filter(a => a.name.startsWith('data-'))
                                .map(a => [a.name, a.value])
                        ),
                    });
                    return Array.from(
                        document.querySelectorAll('input, button, form, a')
                    ).map(pick);
                }"""
            )
            (OUT / "inputs.json").write_text(json.dumps(inventory, indent=2))
            print(f"wrote {OUT / 'inputs.json'} with {len(inventory)} elements")

            # Print just the interesting bits to stdout for quick inspection.
            print("\n=== inputs ===")
            for el in inventory:
                if el["tag"] == "INPUT":
                    print(
                        f"  type={el['type']:<10} name={el['name']!s:<25} "
                        f"id={el['id']!s:<25} placeholder={el['placeholder']!s}"
                    )
            print("\n=== buttons ===")
            for el in inventory:
                if el["tag"] == "BUTTON":
                    print(f"  type={el['type']!s:<10} text={el['text']!r}  data={el['dataAttrs']}")
            print("\n=== forms ===")
            for el in inventory:
                if el["tag"] == "FORM":
                    print(f"  action={el['action']}  id={el['id']}")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
