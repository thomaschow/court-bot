"""Fetch the *inner* booking modal HTML from reservations.courtreserve.com (after the
outer wrapper page loads it via AJAX) and dump its form structure."""

import asyncio
import re
from pathlib import Path
from urllib.parse import unquote

from courtbot.auth.session import build_client
from courtbot.config import load_config
from courtbot.paths import config_path

OUT = Path("state/booking_capture")


async def main() -> None:
    cfg = load_config(config_path())
    f = cfg.facility("santa-clara")

    # Step 1: GET the wrapper to grab a fresh requestData token.
    params_outer = {
        "start": "5/3/2026 9:00 AM",
        "end": "5/3/2026 10:00 AM",
        "customSchedulerId": str(f.s_id),
        "courtTypeId": "2",
        "courtType": "Hard",
    }

    async with build_client(f, http2=False) as client:
        wrapper_url = f"/Online/Reservations/CreateReservation/{f.org_id}"
        r = await client.get(wrapper_url, params=params_outer)
        wrapper_html = r.text

        # Extract the inner AJAX URL from the wrapper.
        m = re.search(r"fixUrl\(['\"]([^'\"]+)['\"]\)", wrapper_html)
        if not m:
            raise SystemExit("could not find fixUrl(...) inner URL in wrapper HTML")
        inner_url = m.group(1).replace("&amp;", "&")
        print("inner URL:", inner_url[:200], "...")

        # The wrapper loads the modal cross-origin; we need to hit that host with our cookies.
        # build_client is bound to app.courtreserve.com — make a one-off httpx call that
        # forwards the cookies to reservations.courtreserve.com.
        import httpx

        async with httpx.AsyncClient(
            cookies=client.cookies,
            headers=dict(client.headers),
            timeout=15.0,
            follow_redirects=False,
        ) as inner_client:
            r2 = await inner_client.get(inner_url)
            print(f"inner GET -> {r2.status_code} ({len(r2.text)} bytes)")
            (OUT / "inner_modal.html").write_text(r2.text)

            # Find the form action + method.
            for m in re.finditer(r"<form[^>]*>", r2.text):
                print("FORM:", m.group(0)[:300])
            # Find AJAX POST URLs in JS.
            for pat in (
                r'\.post\(\s*["\'][^"\']{0,200}',
                r'url\s*:\s*["\'][^"\']{0,200}',
                r'action\s*=\s*["\'][^"\']{0,200}',
            ):
                seen = set()
                for m in re.finditer(pat, r2.text):
                    s = m.group(0)
                    if any(kw in s for kw in ("CreateReservation", "Reserve", "Booking")):
                        if s not in seen and len(seen) < 3:
                            seen.add(s)
                            print("JS:", s[:240])
            # All form-shaped inputs
            print("\n--- visible form fields ---")
            seen_names = set()
            for m in re.finditer(r"<(input|select|textarea)[^>]+>", r2.text):
                tag = m.group(0)
                name_m = re.search(r'name="([^"]+)"', tag)
                if not name_m:
                    continue
                name = name_m.group(1)
                if name in seen_names:
                    continue
                seen_names.add(name)
                if 'type="hidden"' in tag and name not in (
                    "__RequestVerificationToken",
                    "Id",
                    "OrgId",
                    "MemberId",
                    "OwnerId",
                ):
                    continue
                print(tag[:240])


if __name__ == "__main__":
    asyncio.run(main())
