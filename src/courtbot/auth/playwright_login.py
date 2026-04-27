from __future__ import annotations

import json
from pathlib import Path

from courtbot.config import Config, Facility
from courtbot.logging import get_logger
from courtbot.paths import session_path
from courtbot.secrets import get_credentials

LOGIN_PATH = "/Online/Account/LogIn/{org_id}"
BOOKINGS_PATH = "/Online/Reservations/Bookings/{org_id}"


async def login(cfg: Config, facility: Facility, headful: bool = False) -> Path:
    """Log in via headless Chromium, persist storage_state to disk, return its path.

    Raises RuntimeError on failure (captcha, wrong credentials, page changed).
    """
    from playwright.async_api import async_playwright

    log = get_logger(facility=facility.id, mode="auth")
    creds = get_credentials(facility.id, cfg.credentials)
    out = session_path(facility.org_id)

    user_agent = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=not headful,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        try:
            context = await browser.new_context(
                user_agent=user_agent,
                viewport={"width": 1440, "height": 900},
                locale="en-US",
                timezone_id="America/Los_Angeles",
            )
            # Defeat the most common navigator.webdriver detection.
            await context.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            )
            page = await context.new_page()
            login_url = facility.base_url + LOGIN_PATH.format(org_id=facility.org_id)
            log.info("auth.login.navigate", url=login_url, headful=headful)
            await page.goto(login_url, wait_until="networkidle", timeout=30000)

            email_selectors = [
                'input[name="email"]',
                'input[type="email"]',
                'input[name="UserNameOrEmail"]',
                'input[name="Username"]',
                'input[name="EmailAddress"]',
                'input[autocomplete="username"]',
            ]
            password_selectors = [
                'input[name="password"]',
                'input[type="password"]',
                'input[name="Password"]',
                'input[autocomplete="current-password"]',
            ]
            submit_selectors = [
                'button[data-testid="Continue"]',
                'button[type="submit"]',
                'input[type="submit"]',
                'button:has-text("Continue")',
                'button:has-text("Log In")',
                'button:has-text("Sign In")',
            ]

            email_loc = await _find_first(page, email_selectors, timeout_ms=20000)
            if email_loc is None:
                # Likely Cloudflare challenge or unknown layout. Save artifacts for debugging.
                shot_path = out.with_suffix(".debug.png")
                html_path = out.with_suffix(".debug.html")
                await page.screenshot(path=str(shot_path), full_page=True)
                html_path.write_text(await page.content())
                raise RuntimeError(
                    f"Could not find an email/username input on the login page. "
                    f"This usually means a Cloudflare challenge or layout change. "
                    f"Re-run with --headful to complete the login manually, then we'll "
                    f"persist the session. Debug saved: {shot_path}, {html_path}"
                )
            await email_loc.fill(creds.username)
            pwd_loc = await _find_first(page, password_selectors)
            await pwd_loc.fill(creds.password)
            submit = await _find_first(page, submit_selectors)
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
                await submit.click()

            bookings_url = facility.base_url + BOOKINGS_PATH.format(org_id=facility.org_id)
            await page.goto(bookings_url, wait_until="domcontentloaded")
            if "/Account/LogIn" in page.url:
                raise RuntimeError(
                    "Login appears to have failed (still on login page). "
                    "Check credentials or run with --headful to debug."
                )

            await context.storage_state(path=str(out))
            log.info("auth.login.success", storage_state=str(out))
            return out
        finally:
            await browser.close()


async def _find_first(page, selectors: list[str], timeout_ms: int = 5000):
    """Return the first selector that resolves to a visible element, or None."""
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            await loc.wait_for(state="visible", timeout=timeout_ms)
            return loc
        except Exception:
            continue
    return None


def storage_state_cookies(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return data.get("cookies", [])
