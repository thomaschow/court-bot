"""Fetch the CreateReservation modal form HTML and dump form fields + action URL."""

import asyncio
import re
from pathlib import Path

from bay_area_courtbot.auth.session import build_client
from bay_area_courtbot.config import load_config
from bay_area_courtbot.paths import config_path

OUT = Path("state/booking_capture")
OUT.mkdir(parents=True, exist_ok=True)


async def main() -> None:
    cfg = load_config(config_path())
    f = cfg.facility("santa-clara")

    # Use a far-future, valid time that's almost certainly available.
    params = {
        "start": "5/3/2026 9:00 AM",
        "end": "5/3/2026 10:00 AM",
        "customSchedulerId": str(f.s_id),
        "courtTypeId": "2",
        "courtType": "Hard",
    }

    async with build_client(f, http2=False) as client:
        url = f"/Online/Reservations/CreateReservation/{f.org_id}"
        r = await client.get(url, params=params)
        print(f"GET {url} -> {r.status_code} ({len(r.text)} bytes)")
        (OUT / "create_reservation.html").write_text(r.text)

        # Show form action and method
        for m in re.finditer(r'<form[^>]*>', r.text):
            print("FORM:", m.group(0))
        # Show all non-hidden inputs and selects
        for m in re.finditer(r'<(input|select|textarea)[^>]*>', r.text):
            tag = m.group(0)
            if 'type="hidden"' in tag and '__RequestVerificationToken' not in tag:
                continue
            print(tag[:200])

        # Show post URL pattern in JS
        for pat in [r'\.post\([^)]{0,200}', r'url\s*:\s*["\'][^"\']{0,200}', r'action\s*=\s*["\'][^"\']{0,200}']:
            for m in re.finditer(pat, r.text):
                s = m.group(0)
                if any(kw in s for kw in ("CreateReservation", "Reserve", "Booking")):
                    print("JS:", s[:200])


if __name__ == "__main__":
    asyncio.run(main())
