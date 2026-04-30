"""CSRF token helper for Seattle ANC.

ANC requires `X-CSRF-Token` on every POST. The token lives in `window.__csrfToken`
on every JS-rendered page, so we extract it via Playwright once per session and
cache it in memory. The token is tied to the JSESSIONID cookie — refresh both
together by re-running the login flow if the server returns response_code 0012
("Invalid CSRF token").

Verified live 2026-04-29:
  - Header name: `X-CSRF-Token` (case-insensitive)
  - Token format: UUID-like, e.g., "a3f8d6e0-3b0c-473a-880c-723149d17cf4"
  - Source: window.__csrfToken on any /seattle/reservation/... page
"""

from __future__ import annotations


CSRF_HEADER_NAME = "X-CSRF-Token"


async def fetch_csrf_token(*, storage_state_path: str) -> str:
    """Fetch the current CSRF token from a logged-in session via Playwright.

    Loads any /seattle/reservation/... page (we use the search page) and reads
    `window.__csrfToken` after the SPA has rendered.
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            ctx = await browser.new_context(
                storage_state=storage_state_path,
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1440, "height": 900},
            )
            page = await ctx.new_page()
            await page.goto(
                "https://anc.apm.activecommunities.com/seattle/reservation/search"
                "?keyword=tennis%20court&resourceType=0",
                wait_until="networkidle", timeout=30000,
            )
            token = await page.evaluate("window.__csrfToken")
            if not token:
                raise RuntimeError("could not read window.__csrfToken — session may be expired")
            return str(token)
        finally:
            await browser.close()
