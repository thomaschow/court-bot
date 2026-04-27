"""Quick diagnostic: load the CourtReserve login page via Playwright and dump form fields."""

import asyncio

ORG_ID = 13234
URL = f"https://app.courtreserve.com/Online/Account/LogIn/{ORG_ID}"


async def main() -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(URL, wait_until="networkidle")
        # Dump all input fields after JS has rendered.
        inputs = await page.evaluate(
            """() => Array.from(document.querySelectorAll('input,form,button')).map(el => ({
                tag: el.tagName,
                type: el.type || null,
                name: el.name || null,
                id: el.id || null,
                placeholder: el.placeholder || null,
                action: el.action || null,
                autocomplete: el.autocomplete || null,
            }))"""
        )
        for el in inputs:
            print(el)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
