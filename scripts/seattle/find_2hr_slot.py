"""Scan all configured Seattle facilities for 2-hour continuous blocks
starting between 5 PM and 7 PM PDT in the next 14 days. Print the soonest
matches so the user can pick one.

Usage:
  python scripts/seattle/find_2hr_slot.py
"""

import asyncio
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from seattle_courtbot.ancapi.client import AncClient
from seattle_courtbot.ancapi.parsing import AvailabilityRange
from seattle_courtbot.auth.session import build_client
from seattle_courtbot.config import load_config
from seattle_courtbot.paths import config_path

LOCAL = ZoneInfo("America/Los_Angeles")
DURATION = 120                        # 2 hours
START_MIN = time(17, 0)               # 5 PM earliest start
START_MAX = time(19, 0)               # 7 PM latest start (so end ≤ 9 PM)


def _t2m(t: time) -> int:
    return t.hour * 60 + t.minute


def _hms_to_minutes(s: str) -> int:
    p = [int(x) for x in s.split(":")]
    return p[0] * 60 + p[1]


def find_2hr_starts(ranges: list[AvailabilityRange], court_id: int) -> list[tuple[date, time]]:
    """Return (date, start) pairs for which a 2hr reservation fits inside an
    available range and starts in [17:00, 19:00]."""
    out = []
    for r in ranges:
        if not r.available or r.resource_id != court_id:
            continue
        try:
            d = date.fromisoformat(r.date)
        except ValueError:
            continue
        rs = _hms_to_minutes(r.start_time)
        re = _hms_to_minutes(r.end_time)
        # Try every 30-min start within the range that satisfies window + duration.
        s = max(rs, _t2m(START_MIN))
        # snap up to next 30-min boundary
        if s % 30:
            s += 30 - (s % 30)
        while s + DURATION <= re and s <= _t2m(START_MAX):
            out.append((d, time(s // 60, s % 60)))
            s += 30
    return out


async def main() -> None:
    cfg = load_config(config_path())
    today = datetime.now(LOCAL).date()
    start_d = today + timedelta(days=1)
    end_d = today + timedelta(days=14)
    print(f"Scanning {len(cfg.facilities)} facilities, {sum(len(f.courts) for f in cfg.facilities)} courts, "
          f"{start_d}..{end_d}")

    matches: list[tuple[date, time, str, int, str]] = []
    async with build_client(http2=False) as client:
        anc = AncClient(client)
        # Process courts in parallel batches to keep it under a minute.
        sem = asyncio.Semaphore(8)

        async def scan_court(facility, court):
            async with sem:
                try:
                    ranges = await anc.read_availability(
                        resource_id=court.id,
                        customer_id=cfg.member_id or 0,
                        start_date=start_d, end_date=end_d,
                    )
                except Exception as exc:
                    print(f"  [{facility.id}] ct{court.id}: error {type(exc).__name__}")
                    return
                hits = find_2hr_starts(ranges, court.id)
                for d, st in hits:
                    matches.append((d, st, facility.id, court.id, court.name))

        tasks = [scan_court(f, c) for f in cfg.facilities for c in f.courts]
        await asyncio.gather(*tasks)

    matches.sort(key=lambda m: (m[0], m[1], m[2]))
    print(f"\n{len(matches)} matching (date, start, court) combos\n")
    print(f"{'DATE':<12}{'START':<10}{'END':<10}FACILITY / COURT")
    for d, st, fid, cid, cname in matches:
        end_m = (datetime.combine(d, st) + timedelta(minutes=DURATION)).time()
        print(f"{str(d):<12}{st.strftime('%-I:%M %p'):<10}{end_m.strftime('%-I:%M %p'):<10}"
              f"{fid} / {cname} (ct{cid})")


if __name__ == "__main__":
    asyncio.run(main())
