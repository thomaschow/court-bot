"""Cancellation watcher: detects newly-available slices within the 6-9 PM PDT window
and immediately books the longest reservation (max 2h) that fits inside that window.

Differs from `keep_watching.py`:
  - Higher cadence (30s ±20%) — cancellations vanish quickly
  - Diff-based detection: only attempts slots that just became available, never slots
    that were already free in the previous snapshot. The first cycle establishes the
    baseline and books nothing.
  - Picks the *longest* possible reservation per newly-freed start (longest first):
      6:00 → 2h (6-8) | 6:30 → 2h (6:30-8:30) | 7:00 → 2h (7-9)
      7:30 → 1.5h (7:30-9) | 8:00 → 1h (8-9) | 8:30 → 30min (8:30-9)
  - Coordinates with `keep_watching.py` via the ledger: `is_already_confirmed` prevents
    duplicate bookings on the same (facility, date, start, court).

Stops on signal or after MAX_RUNTIME_S.
"""

import asyncio
import random
import signal
import time as time_mod
from collections import defaultdict
from dataclasses import dataclass
from datetime import date as ddate, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import httpx

from courtbot.auth.session import build_client, hydrate
from courtbot.booking.service import book
from courtbot.config import Facility, load_config
from courtbot.courtreserve.client import CourtReserveClient
from courtbot.courtreserve.errors import (
    AllCourtsTaken, AuthExpired, CourtReserveError, RateLimited, WindowNotOpen,
)
from courtbot.courtreserve.payloads import BookingCandidate
from courtbot.ledger import is_already_confirmed
from courtbot.notify import notify_macos
from courtbot.paths import config_path

LOCAL = ZoneInfo("America/Los_Angeles")

# === CONFIG ===
WINDOW_START = dtime(18, 0)            # 6 PM PDT
WINDOW_END = dtime(21, 0)              # 9 PM PDT
DAY_DELTAS = list(range(0, 15))        # today through today+14
FACILITY_PRIORITY = ["santa-clara", "sunnyvale"]
COURT_TYPE_ID = 2
COURT_TYPE = "Hard"

POLL_INTERVAL_S = 30.0
POLL_JITTER_FRAC = 0.2
MAX_RUNTIME_S = 24 * 3600
MAX_POSTS_PER_CYCLE = 4   # cancellations are rare; 4 attempts per cycle is plenty
MIN_DELAY_BETWEEN_POSTS_S = 1.0

# Per-(facility, date) blacklist on WindowNotOpen.
WINDOW_BLACKLIST_S = 1800
# Acceptable durations (longest first). API min is 30min, max 2h.
DURATIONS = (120, 90, 60, 30)


@dataclass(frozen=True)
class SliceKey:
    facility_id: str
    date_ord: int
    start_minutes: int  # minutes-from-midnight in local time
    court_id: int


def _slot_key(facility_id: str, ls: datetime, court_id: int) -> SliceKey:
    return SliceKey(
        facility_id=facility_id,
        date_ord=ls.date().toordinal(),
        start_minutes=ls.hour * 60 + ls.minute,
        court_id=court_id,
    )


def _max_duration_within_window(start_local: dtime) -> int | None:
    """Largest duration ≤ 120 minutes that doesn't push the END past WINDOW_END."""
    s_min = start_local.hour * 60 + start_local.minute
    e_min = WINDOW_END.hour * 60 + WINDOW_END.minute
    headroom = e_min - s_min
    if headroom < 30:
        return None
    return min(120, (headroom // 30) * 30)


class CancellationWatcher:
    def __init__(self, cfg, facilities: list[Facility]):
        self.cfg = cfg
        self.facilities = facilities
        # Last-seen availability: facility_id → date_ord → {SliceKey, ...}
        self.prev_snapshot: dict[str, dict[int, set[SliceKey]]] = defaultdict(dict)
        self.window_blacklist: dict[tuple[str, int], float] = {}
        self.stop_event = asyncio.Event()
        self.posts_this_cycle = 0
        self.confirmed: list[str] = []
        self.first_cycle = True

    def is_blacklisted(self, facility_id: str, day: ddate) -> bool:
        now = time_mod.time()
        key = (facility_id, day.toordinal())
        if key in self.window_blacklist:
            if self.window_blacklist[key] > now:
                return True
            self.window_blacklist.pop(key, None)
        return False

    def blacklist_window(self, facility_id: str, day: ddate) -> None:
        self.window_blacklist[(facility_id, day.toordinal())] = time_mod.time() + WINDOW_BLACKLIST_S

    async def _scan_facility(self, facility: Facility, days: list[ddate], client: httpx.AsyncClient):
        """Returns dict[date_ord -> set[SliceKey]] for slices within the 6-9 PM window."""
        cr = CourtReserveClient(
            client, org_id=facility.org_id, cost_type_id=facility.cost_type_id,
            custom_scheduler_id=facility.s_id, timezone_name=facility.timezone,
        )
        out: dict[int, set[SliceKey]] = {}
        for d in days:
            if self.is_blacklisted(facility.id, d):
                continue
            try:
                slots = await cr.read_consolidated(day=d)
            except (RateLimited, AuthExpired):
                continue
            except (httpx.HTTPError, httpx.TransportError):
                continue
            except Exception:
                continue
            keys: set[SliceKey] = set()
            for s in slots:
                ls = s.start.astimezone(LOCAL)
                if ls.date() != d:
                    continue
                if not (WINDOW_START <= ls.time() < WINDOW_END):
                    continue
                keys.add(_slot_key(facility.id, ls, s.court_id))
            out[d.toordinal()] = keys
        return out

    def _has_30min_partner(
        self,
        snapshot: dict[int, set[SliceKey]],
        facility_id: str,
        day: ddate,
        start_local: dtime,
        court_id: int,
    ) -> bool:
        """A standalone 30-min booking is allowed only if a 30-min "partner" slice
        exists in the current snapshot. Two ways to qualify:

          1. Time-adjacent partner on ANY court — start_local ± 30 min.
          2. Same court with gap ≤ 30 min between the two bookings. Since each booking
             is 30 min, a 30-min gap means the partner starts 60 min away (and an
             adjacent same-court partner is 30 min away). So the same-court case
             accepts |start_delta| ∈ {30, 60}.
        """
        slot_min = start_local.hour * 60 + start_local.minute
        date_set = snapshot.get(day.toordinal(), set())
        for k in date_set:
            if k.facility_id != facility_id:
                continue
            if k.start_minutes == slot_min and k.court_id == court_id:
                continue  # the slice itself
            delta = k.start_minutes - slot_min
            # Condition 1: adjacent in time (any court).
            if abs(delta) == 30:
                return True
            # Condition 2: same court, gap ≤ 30 min (start delta of 30 = adjacent same
            # court, 60 = 30-min gap same court).
            if k.court_id == court_id and abs(delta) in (30, 60):
                return True
        return False

    async def _try_book_at(
        self,
        facility: Facility,
        day: ddate,
        start_local: dtime,
        court_id: int,
        snapshot: dict[int, set[SliceKey]],
    ) -> bool:
        """Attempt the longest-possible reservation starting at (day, start_local) on
        the given court. Returns True if any duration succeeded.

        For 30-min durations specifically, applies the partner rule (see _has_30min_partner).
        """
        max_dur = _max_duration_within_window(start_local)
        if max_dur is None:
            return False
        for dur in DURATIONS:
            if dur > max_dur:
                continue
            if dur == 30 and not self._has_30min_partner(
                snapshot, facility.id, day, start_local, court_id
            ):
                # Standalone 30-min booking with no qualifying partner — skip per user rule.
                return False
            if is_already_confirmed(
                facility=facility.id, date=day.isoformat(),
                start_time=start_local.strftime("%H:%M"), court_id=court_id,
            ):
                return False
            if self.posts_this_cycle >= MAX_POSTS_PER_CYCLE:
                return False
            cand = BookingCandidate(
                facility_id=facility.id, org_id=facility.org_id,
                member_id=facility.member_id, membership_id=facility.cost_type_id,
                reservation_type_id=facility.reservation_type_id,
                court_id=court_id, date=day, start=start_local, duration_minutes=dur,
            )
            self.posts_this_cycle += 1
            try:
                result = await book(self.cfg, facility, cand, mode="cancellation_watcher")
            except WindowNotOpen:
                self.blacklist_window(facility.id, day)
                return False
            except RateLimited:
                return False
            except AuthExpired:
                return False
            except (httpx.HTTPError, httpx.TransportError):
                return False
            except CourtReserveError as exc:
                # SlotTaken / AllCourtsTaken → try shorter duration
                continue
            except Exception:
                continue

            if result.status == "confirmed":
                end_t = (datetime.combine(day, start_local) + timedelta(minutes=dur)).time()
                msg = (f"GRABBED {facility.id} ct{court_id} {day} "
                       f"{start_local.strftime('%-I:%M %p')}–{end_t.strftime('%-I:%M %p')} "
                       f"({dur}min) #{result.confirmation_id}")
                print(f"  ✓ {msg}")
                self.confirmed.append(msg)
                try:
                    notify_macos("courtbot grabbed cancellation", msg)
                except Exception:
                    pass
                return True
            if result.status == "duplicate":
                return False
            if result.status == "failed":
                # Try a shorter duration on the same court before giving up.
                continue
        return False

    async def run_cycle(self, days: list[ddate]) -> None:
        self.posts_this_cycle = 0
        for facility in self.facilities:
            async with build_client(facility, http2=True) as client:
                try:
                    await hydrate(client, facility)
                except Exception:
                    continue
                try:
                    snapshot = await self._scan_facility(facility, days, client)
                except Exception as exc:
                    print(f"[{facility.id}] scan error: {type(exc).__name__}: {exc}")
                    continue

                prev = self.prev_snapshot.get(facility.id, {})
                # Detect new openings (in current but not in prev). On the very first
                # cycle, treat everything as baseline — book nothing.
                new_openings: list[SliceKey] = []
                for date_ord, current_set in snapshot.items():
                    prev_set = prev.get(date_ord, set())
                    diff = current_set - prev_set
                    new_openings.extend(diff)
                self.prev_snapshot[facility.id] = snapshot

                if self.first_cycle:
                    print(f"[{facility.id}] baseline: {sum(len(v) for v in snapshot.values())} "
                          f"slices in 6-9 PM window across {len(snapshot)} dates")
                    continue

                if not new_openings:
                    continue

                # Sort: earliest date first, then earliest start, longest-duration intent.
                new_openings.sort(key=lambda k: (k.date_ord, k.start_minutes))
                print(f"[{facility.id}] {len(new_openings)} NEW opening(s):")
                for k in new_openings[:8]:
                    d = ddate.fromordinal(k.date_ord)
                    h, m = divmod(k.start_minutes, 60)
                    print(f"    {d} {h:02d}:{m:02d} ct{k.court_id}")
                for k in new_openings:
                    if self.posts_this_cycle >= MAX_POSTS_PER_CYCLE:
                        break
                    d = ddate.fromordinal(k.date_ord)
                    h, m = divmod(k.start_minutes, 60)
                    booked = await self._try_book_at(
                        facility, d, dtime(h, m), k.court_id, snapshot,
                    )
                    if booked:
                        await asyncio.sleep(MIN_DELAY_BETWEEN_POSTS_S)

        if self.first_cycle:
            self.first_cycle = False

    async def run(self) -> None:
        started = time_mod.time()
        print(f"[cancel-watcher] starting; cadence {POLL_INTERVAL_S}s "
              f"±{int(POLL_JITTER_FRAC*100)}%, max-runtime {MAX_RUNTIME_S//3600}h, "
              f"window {WINDOW_START}-{WINDOW_END}, max-posts/cycle={MAX_POSTS_PER_CYCLE}")
        tick = 0
        while not self.stop_event.is_set():
            elapsed = time_mod.time() - started
            if elapsed > MAX_RUNTIME_S:
                print("[cancel-watcher] max runtime reached; stopping")
                break
            tick += 1
            today = datetime.now(LOCAL).date()
            days = sorted({today + timedelta(days=d) for d in DAY_DELTAS})
            try:
                await self.run_cycle(days)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[tick {tick}] cycle error: {type(exc).__name__}: {exc}")

            wait = POLL_INTERVAL_S * random.uniform(1 - POLL_JITTER_FRAC, 1 + POLL_JITTER_FRAC)
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=wait)
                break
            except asyncio.TimeoutError:
                pass

        if self.confirmed:
            print(f"\n[cancel-watcher] grabbed {len(self.confirmed)} cancellation(s):")
            for m in self.confirmed:
                print(f"  - {m}")
        else:
            print(f"\n[cancel-watcher] no cancellations grabbed during this run.")


async def main() -> None:
    cfg = load_config(config_path())
    facilities = [cfg.facility(fid) for fid in FACILITY_PRIORITY]
    watcher = CancellationWatcher(cfg, facilities)

    def _handler(signum, _frame):
        print(f"\n[cancel-watcher] received signal {signum}; stopping after current cycle")
        watcher.stop_event.set()
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, _handler)

    await watcher.run()


if __name__ == "__main__":
    asyncio.run(main())
