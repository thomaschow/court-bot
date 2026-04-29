"""Playwright login for Seattle ANC.

ANC's frontend is a React SPA. The login form selectors are not yet known — Phase 1
includes a probe step (`scripts/seattle/probe_seattle_login.py`) that opens the
login page in headless Chromium and dumps the rendered DOM so we can find the
right field/button selectors.

For now this module supports two flows:

  1. **Headless attempt with selector candidates** — tries a list of plausible
     selectors. If they all miss, saves a debug screenshot + HTML and raises
     RuntimeError with instructions to re-run with `--headful` or update the
     selector list once we've inspected the rendered page.
  2. **Headful manual** — `headful=True` opens a visible browser; the user
     completes the login by hand and presses Enter in the terminal. The
     storage_state is then captured.
"""

from __future__ import annotations

from pathlib import Path

from seattle_courtbot.logging import get_logger
from seattle_courtbot.paths import session_path
from seattle_courtbot.secrets import get_credentials

# Seattle ANC tenant URLs. The login screen is part of the SPA, accessed via
# `signin?...returnUrl=...`. Verified manually 2026-04-28.
ANC_HOST = "https://anc.apm.activecommunities.com"
TENANT_HOME = f"{ANC_HOST}/seattle/home"
SIGNIN_URL = f"{ANC_HOST}/seattle/signin"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


async def login(*, headful: bool = False, manual: bool = False) -> Path:
    """Log in to Seattle ANC and persist session cookies. Returns the storage_state path.

    `headful=True` shows the browser window (useful for debugging selectors and for
    completing captchas). `manual=True` waits for the user to log in by hand and
    press Enter in the terminal before capturing storage_state.
    """
    from playwright.async_api import async_playwright

    log = get_logger(mode="auth")
    out = session_path()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=not (headful or manual),
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
            log.info("auth.login.navigate", url=SIGNIN_URL, headful=headful, manual=manual)
            await page.goto(SIGNIN_URL, wait_until="networkidle", timeout=30000)

            if manual:
                # User completes the login interactively, then we save state.
                input("\n  Complete the Seattle login in the browser, then press Enter here…\n")
                await context.storage_state(path=str(out))
                log.info("auth.login.success_manual", storage_state=str(out))
                return out

            creds = get_credentials()
            # Verified live 2026-04-28 via scripts/seattle/probe_seattle_login.py.
            # Seattle ANC's signin page has no `name`/`id` attributes on the inputs,
            # so we match by placeholder + type. The submit is a <button type="submit">
            # with text "Sign in" (lowercase 'in').
            email_selectors = [
                'input[placeholder="Enter your Email address"]',
                'input[placeholder*="email" i][type="text"]',
                'input[type="email"]',
            ]
            password_selectors = [
                'input[type="password"]',
                'input[autocomplete="current-password"]',
            ]
            submit_selectors = [
                'button[type="submit"]:has-text("Sign in")',
                'button:has-text("Sign in")',
                'button[type="submit"]',
            ]

            email_loc = await _find_first(page, email_selectors, timeout_ms=20000)
            if email_loc is None:
                shot = out.with_suffix(".debug.png")
                html = out.with_suffix(".debug.html")
                await page.screenshot(path=str(shot), full_page=True)
                html.write_text(await page.content())
                raise RuntimeError(
                    "Could not find email input on Seattle ANC login page. "
                    "Re-run with `--headful` to inspect, or run "
                    "`python scripts/seattle/probe_seattle_login.py` to capture the "
                    f"rendered DOM. Debug saved: {shot}, {html}"
                )
            await email_loc.fill(creds.username)
            pwd_loc = await _find_first(page, password_selectors)
            if pwd_loc is None:
                raise RuntimeError("password input not found on login page")
            await pwd_loc.fill(creds.password)
            submit = await _find_first(page, submit_selectors)
            if submit is None:
                raise RuntimeError("submit button not found on login page")
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
                await submit.click()

            await page.goto(TENANT_HOME, wait_until="domcontentloaded")
            if "/signin" in page.url.lower():
                raise RuntimeError(
                    "Login appears to have failed (still on signin page). "
                    "Re-run with `--headful` or `--manual` to debug."
                )
            await context.storage_state(path=str(out))
            log.info("auth.login.success", storage_state=str(out))
            return out
        finally:
            await browser.close()


async def _find_first(page, selectors: list[str], timeout_ms: int = 5000):
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            await loc.wait_for(state="visible", timeout=timeout_ms)
            return loc
        except Exception:
            continue
    return None
