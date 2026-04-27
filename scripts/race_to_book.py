"""One-off racer for a specific (date, start, duration, court) slot.

Pre-warms the session NOW, sleeps until the configured fire time, then fires the
booking POST. On WindowNotOpen, retries with fixed sub-second backoff until success
or the 10-second budget elapses. Walks alternate court IDs on SlotTaken.
"""

import asyncio
import time as time_mod
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from courtbot.auth.session import build_client, hydrate
from courtbot.booking.service import book
from courtbot.config import load_config
from courtbot.courtreserve.errors import (
    AuthExpired,
    CourtReserveError,
    RateLimited,
    SlotTaken,
    WindowNotOpen,
)
from courtbot.courtreserve.payloads import BookingCandidate
from courtbot.paths import config_path

LOCAL = ZoneInfo("America/Los_Angeles")

# CONFIG —— edit if needed.
TARGET_DATE = date(2026, 5, 4)
START = time(18, 0)              # 6:00 PM
DURATION = 120                   # 2 hours
RESERVATION_TYPE_ID = 69711      # "Recreational Play - Tennis"
COURT_LADDER = [52101, 52102, 52103, 52099, 52097, 52098, 52096]  # try in order
FIRE_AT_LOCAL = datetime(2026, 4, 27, 12, 0, 0, tzinfo=LOCAL)
MAX_BUDGET_S = 30.0
RATE_RETRY_S = (0.10, 0.25, 0.50, 1.0, 2.0)


async def main() -> None:
    cfg = load_config(config_path())
    f = cfg.facility("santa-clara")

    candidates = [
        BookingCandidate(
            facility_id=f.id,
            org_id=f.org_id,
            member_id=f.member_id,
            membership_id=f.cost_type_id,
            reservation_type_id=RESERVATION_TYPE_ID,
            court_id=cid,
            date=TARGET_DATE,
            start=START,
            duration_minutes=DURATION,
        )
        for cid in COURT_LADDER
    ]

    async with build_client(f, http2=False) as client:
        print(f"prewarm @ {datetime.now(LOCAL).isoformat()}")
        # Hydrate to verify session and measure clock skew.
        try:
            session = await hydrate(client, f)
            print(f"  session ok, server clock offset: {session.server_clock_offset_ms}ms")
        except Exception as exc:
            print(f"  session hydrate failed: {exc}")
            return

        # Sleep until fire moment, corrected for server clock skew.
        skew_s = session.server_clock_offset_ms / 1000.0
        # If server is AHEAD (positive offset), we should fire slightly later than our
        # local clock thinks, but skew_ms here was measured as (server - local). When
        # server is ahead of us by 200ms, we should fire 200ms LATER on our clock so
        # the request arrives at server-local fire time. So add skew to the wall target.
        target_ts = FIRE_AT_LOCAL.timestamp() + skew_s + 0.010  # +10ms safety
        delay = target_ts - time_mod.time()
        if delay > 0:
            print(f"  sleeping {delay:.3f}s (skew {skew_s:+.3f}s)…")
            await asyncio.sleep(max(0.0, delay - 0.050))
            while time_mod.time() < target_ts:
                await asyncio.sleep(0)

        print(f"\nFIRE @ {datetime.now(LOCAL).isoformat()}")
        budget_end = time_mod.time() + MAX_BUDGET_S
        attempts = 0
        for cand in candidates:
            if time_mod.time() >= budget_end:
                print("  budget exhausted")
                break
            backoff = iter(RATE_RETRY_S)
            while True:
                attempts += 1
                t0 = time_mod.time()
                try:
                    result = await book(
                        cfg, f, cand, mode="manual_race", client=client, session=session,
                    )
                except WindowNotOpen as exc:
                    elapsed = time_mod.time() - t0
                    wait = next(backoff, None)
                    if wait is None or time_mod.time() + wait >= budget_end:
                        print(f"  attempt {attempts} ({elapsed*1000:.0f}ms): window still closed → giving up on this court")
                        break
                    print(f"  attempt {attempts} ({elapsed*1000:.0f}ms): window not open, retry in {wait}s")
                    await asyncio.sleep(wait)
                    continue
                except RateLimited as exc:
                    wait = next(backoff, None) or 1.0
                    print(f"  attempt {attempts}: rate limited, retry in {wait}s")
                    if time_mod.time() + wait >= budget_end:
                        break
                    await asyncio.sleep(wait)
                    continue
                except (AuthExpired, CourtReserveError) as exc:
                    print(f"  attempt {attempts}: error {type(exc).__name__}: {exc}")
                    break

                elapsed = time_mod.time() - t0
                if result.status == "confirmed":
                    print(
                        f"\n✓ BOOKED court {cand.court_id} @ {cand.start} for {cand.duration_minutes} min"
                        f" — confirmation #{result.confirmation_id} (attempt {attempts}, {elapsed*1000:.0f}ms)"
                    )
                    return
                if result.status == "duplicate":
                    print(f"  attempt {attempts}: already in ledger, skipping court {cand.court_id}")
                    break
                if result.status == "failed":
                    err = (result.error or "").lower()
                    if "slottaken" in err:
                        print(f"  attempt {attempts}: court {cand.court_id} taken, trying next court")
                        break
                    print(f"  attempt {attempts}: failed - {result.error}")
                    break

        print(f"\n✗ no booking after {attempts} attempt(s)")


if __name__ == "__main__":
    asyncio.run(main())
