"""Find 2-hour blocks on a given date by checking 4 consecutive 30-min slots."""

import asyncio
from collections import defaultdict
from datetime import date as ddate, datetime, timedelta
from zoneinfo import ZoneInfo

from bay_area_courtbot.auth.session import build_client
from bay_area_courtbot.config import load_config
from bay_area_courtbot.courtreserve.client import CourtReserveClient
from bay_area_courtbot.paths import config_path

TARGET = ddate(2026, 5, 4)
DURATION_MIN = 120  # 2 hours
SLOT_MIN = 30
LOCAL = ZoneInfo("America/Los_Angeles")


async def main() -> None:
    cfg = load_config(config_path())
    f = cfg.facility("santa-clara")

    async with build_client(f, http2=False) as client:
        cr = CourtReserveClient(
            client,
            org_id=f.org_id,
            cost_type_id=f.cost_type_id,
            custom_scheduler_id=f.s_id,
            timezone_name=f.timezone,
        )
        # Pull both the previous and target days because ReadConsolidated keys by UTC.
        all_slots = []
        for d in (TARGET - timedelta(days=1), TARGET, TARGET + timedelta(days=1)):
            try:
                all_slots.extend(await cr.read_consolidated(day=d))
            except Exception:
                continue

    if not all_slots:
        print(f"No slots returned. Booking window may not have opened yet for {TARGET}.")
        return

    # Convert to local time, keep only slots whose LOCAL date == TARGET.
    by_start_local: dict[datetime, set[int]] = defaultdict(set)
    for s in all_slots:
        local_start = s.start.astimezone(LOCAL)
        if local_start.date() != TARGET:
            continue
        by_start_local[local_start].add(s.court_id)

    if not by_start_local:
        print(f"No slots on {TARGET} (local PDT).")
        return

    starts = sorted(by_start_local.keys())
    print(f"Found {len(starts)} unique 30-min slots on {TARGET} local PDT.\n")

    needed = DURATION_MIN // SLOT_MIN
    blocks: list[tuple[datetime, set[int]]] = []
    for i in range(len(starts) - needed + 1):
        seq = starts[i : i + needed]
        if any(seq[j + 1] - seq[j] != timedelta(minutes=SLOT_MIN) for j in range(needed - 1)):
            continue
        common = set.intersection(*(by_start_local[t] for t in seq))
        if common:
            blocks.append((seq[0], common))

    if not blocks:
        print(f"No 2-hour blocks available on {TARGET}.")
        return

    print(f"=== 2-hour blocks available on {TARGET} (local PDT) ===")
    for start, courts in blocks:
        end = start + timedelta(minutes=DURATION_MIN)
        court_str = ", ".join(str(c) for c in sorted(courts))
        print(
            f"  {start.strftime('%-I:%M %p')} - {end.strftime('%-I:%M %p')}     "
            f"({len(courts)} courts available: {court_str})"
        )


if __name__ == "__main__":
    asyncio.run(main())
