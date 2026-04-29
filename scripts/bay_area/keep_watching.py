"""All-day poller that books continuous 6–9 PM PDT coverage across the next 14 days.

Lifetime's CourtReserve caps a single reservation at 2 hours, so to cover 6–9 PM we
book two pieces per date: a 2-hour 6:00–8:00 PM reservation, then a 1-hour 8:00–9:00
PM reservation, preferring the same court for both. Each piece is tracked separately
in the ledger.

Behaviour:
  - Polls both facilities every ~90s ±20% jitter.
  - Per polling cycle, considers each date in the configured horizon. For dates that
    have at least one half (6-8 or 8-9) available and not yet booked or blacklisted,
    fires at most a small budget of POSTs.
  - On WindowNotOpen for a date, blacklists the date for ~30 minutes. The next Monday
    at noon PDT release is what would lift it; in steady state the blacklist saves
    roundtrips.
  - On AllCourtsTaken for a (date, half), blacklists that half for ~10 minutes (someone
    might cancel) and stops the per-cycle attempt.
  - Catches httpx transport errors instead of crashing — the poller stays alive across
    transient network or rate-limit blips.
  - Runs until MAX_RUNTIME_S elapses or interrupted.
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

from bay_area_courtbot.auth.session import build_client, hydrate
from bay_area_courtbot.booking.service import book
from bay_area_courtbot.config import Facility, load_config
from bay_area_courtbot.courtreserve.client import CourtReserveClient
from bay_area_courtbot.courtreserve.errors import (
    AllCourtsTaken, AuthExpired, CourtReserveError, RateLimited, SlotTaken, WindowNotOpen,
)
from bay_area_courtbot.courtreserve.payloads import BookingCandidate
from bay_area_courtbot.ledger import is_already_confirmed, list_recent
from bay_area_courtbot.notify import notify_macos
from bay_area_courtbot.paths import config_path

LOCAL = ZoneInfo("America/Los_Angeles")

# === CONFIG ===
# Two pieces per date (book both → 6 PM – 9 PM continuous coverage).
PIECES = [
    ("evening-2h", dtime(18, 0), 120),  # 6 PM – 8 PM
    ("evening-1h", dtime(20, 0), 60),   # 8 PM – 9 PM
]
DAY_DELTAS = list(range(1, 15))           # today + 1 .. today + 14
FACILITY_PRIORITY = ["santa-clara", "sunnyvale"]
COURT_TYPE_ID = 2
COURT_TYPE = "Hard"

POLL_INTERVAL_S = 90.0
POLL_JITTER_FRAC = 0.2
MAX_RUNTIME_S = 24 * 3600
MAX_POSTS_PER_CYCLE = 6
MIN_DELAY_BETWEEN_POSTS_S = 2.0

# Per-(facility, date) blacklist after WindowNotOpen.
WINDOW_BLACKLIST_S = 1800
# Per-(facility, date, piece) blacklist after AllCourtsTaken.
ALL_TAKEN_BLACKLIST_S = 600


@dataclass
class SlotKey:
    facility_id: str
    date: ddate
    piece_name: str  # 'evening-2h' or 'evening-1h'

    def __hash__(self) -> int:
        return hash((self.facility_id, self.date.toordinal(), self.piece_name))


def _now_ts() -> float:
    return time_mod.time()


class Poller:
    def __init__(self, cfg, facilities: list[Facility]):
        self.cfg = cfg
        self.facilities = facilities
        # facility_id, date.toordinal() → expiry ts (window-not-open)
        self.window_blacklist: dict[tuple[str, int], float] = {}
        # (facility_id, date_ord, piece_name) → expiry ts (all-courts-taken)
        self.taken_blacklist: dict[tuple[str, int, str], float] = {}
        self.stop_event = asyncio.Event()
        self.posts_this_cycle = 0
        self.bookings_made_this_run: list[str] = []

    def is_blacklisted(self, facility_id: str, day: ddate, piece: str | None = None) -> bool:
        now = _now_ts()
        if (facility_id, day.toordinal()) in self.window_blacklist:
            if self.window_blacklist[(facility_id, day.toordinal())] > now:
                return True
            self.window_blacklist.pop((facility_id, day.toordinal()), None)
        if piece:
            k = (facility_id, day.toordinal(), piece)
            if k in self.taken_blacklist:
                if self.taken_blacklist[k] > now:
                    return True
                self.taken_blacklist.pop(k, None)
        return False

    def blacklist_window(self, facility_id: str, day: ddate) -> None:
        self.window_blacklist[(facility_id, day.toordinal())] = _now_ts() + WINDOW_BLACKLIST_S

    def blacklist_taken(self, facility_id: str, day: ddate, piece: str) -> None:
        self.taken_blacklist[(facility_id, day.toordinal(), piece)] = _now_ts() + ALL_TAKEN_BLACKLIST_S

    async def scan_facility(self, facility: Facility, days: list[ddate], client: httpx.AsyncClient) -> dict:
        """Returns {(date, piece_name): set(court_ids)} for slots that are available
        across every 30-min slice of the piece."""
        cr = CourtReserveClient(
            client, org_id=facility.org_id, cost_type_id=facility.cost_type_id,
            custom_scheduler_id=facility.s_id, timezone_name=facility.timezone,
        )
        slots = []
        for d in days:
            if self.is_blacklisted(facility.id, d):
                continue
            try:
                slots.extend(await cr.read_consolidated(day=d))
            except (RateLimited, AuthExpired):
                # Window blacklist not the right tool here; just skip and try later.
                continue
            except (httpx.HTTPError, httpx.TransportError):
                continue
            except Exception:
                continue

        # date_ord → start_local → set(court_ids)
        by_start: dict[ddate, dict[datetime, set[int]]] = defaultdict(lambda: defaultdict(set))
        for s in slots:
            ls = s.start.astimezone(LOCAL)
            if ls.date() in days:
                by_start[ls.date()][ls].add(s.court_id)

        out: dict[tuple[ddate, str], set[int]] = {}
        for d, time_map in by_start.items():
            for piece_name, start_t, duration_min in PIECES:
                slices_needed = duration_min // 30
                slice_starts = [
                    datetime.combine(d, start_t, tzinfo=LOCAL) + timedelta(minutes=30 * i)
                    for i in range(slices_needed)
                ]
                if not all(s in time_map for s in slice_starts):
                    continue
                common = set.intersection(*(time_map[s] for s in slice_starts))
                if common:
                    out[(d, piece_name)] = common
        return out

    async def attempt_piece(
        self, facility: Facility, day: ddate, piece_name: str, court_ids: list[int],
    ) -> bool:
        """Attempt to book one piece on the first court whose slot isn't already in
        ledger. Returns True iff we got a confirmation."""
        start_t = next(s for n, s, _ in PIECES if n == piece_name)
        duration = next(d for n, _, d in PIECES if n == piece_name)
        for court_id in court_ids:
            if is_already_confirmed(
                facility=facility.id, date=day.isoformat(),
                start_time=start_t.strftime("%H:%M"), court_id=court_id,
            ):
                continue
            if self.posts_this_cycle >= MAX_POSTS_PER_CYCLE:
                print(f"  [{facility.id}] hit MAX_POSTS_PER_CYCLE={MAX_POSTS_PER_CYCLE}, deferring")
                return False
            cand = BookingCandidate(
                facility_id=facility.id, org_id=facility.org_id,
                member_id=facility.member_id, membership_id=facility.cost_type_id,
                reservation_type_id=facility.reservation_type_id,
                court_id=court_id, date=day, start=start_t, duration_minutes=duration,
            )
            self.posts_this_cycle += 1
            try:
                result = await book(self.cfg, facility, cand, mode="poller")
            except WindowNotOpen as exc:
                print(f"  [{facility.id}] {day} {piece_name} court {court_id}: WINDOW NOT OPEN — "
                      f"blacklisting date for {WINDOW_BLACKLIST_S//60}min")
                self.blacklist_window(facility.id, day)
                return False
            except RateLimited as exc:
                print(f"  [{facility.id}] {day} {piece_name} court {court_id}: RATE LIMITED — "
                      f"deferring")
                return False
            except AuthExpired as exc:
                print(f"  [{facility.id}] {day} {piece_name}: AUTH EXPIRED — re-login needed")
                return False
            except (httpx.HTTPError, httpx.TransportError) as exc:
                print(f"  [{facility.id}] {day} {piece_name}: TRANSPORT — {type(exc).__name__}: {exc}")
                return False
            except CourtReserveError as exc:
                # SlotTaken / AllCourtsTaken / generic → record and try next court
                if isinstance(exc, AllCourtsTaken):
                    print(f"  [{facility.id}] {day} {piece_name}: ALL COURTS TAKEN — "
                          f"blacklisting piece for {ALL_TAKEN_BLACKLIST_S//60}min")
                    self.blacklist_taken(facility.id, day, piece_name)
                    return False
                print(f"  [{facility.id}] {day} {piece_name} court {court_id}: "
                      f"{type(exc).__name__}: {str(exc)[:80]}")
                continue
            except Exception as exc:
                print(f"  [{facility.id}] {day} {piece_name}: UNEXPECTED — "
                      f"{type(exc).__name__}: {exc}")
                return False

            if result.status == "confirmed":
                msg = (f"BOOKED {facility.id} court {court_id} "
                       f"{day} {start_t.strftime('%-I:%M %p')}-"
                       f"{(datetime.combine(day, start_t) + timedelta(minutes=duration)).time().strftime('%-I:%M %p')} "
                       f"#{result.confirmation_id}")
                print(f"  ✓ {msg}")
                self.bookings_made_this_run.append(msg)
                try:
                    notify_macos("bay_area_courtbot booked", msg)
                except Exception:
                    pass
                return True
            if result.status == "duplicate":
                return False
            # 'failed' → try next court
            await asyncio.sleep(MIN_DELAY_BETWEEN_POSTS_S)
        return False

    async def run_cycle(self, days: list[ddate]) -> None:
        self.posts_this_cycle = 0
        for facility in self.facilities:
            async with build_client(facility, http2=True) as client:
                try:
                    await hydrate(client, facility)
                except Exception as exc:
                    print(f"[{facility.id}] hydrate error: {type(exc).__name__}: {exc}")
                    continue
                try:
                    available = await self.scan_facility(facility, days, client)
                except Exception as exc:
                    print(f"[{facility.id}] scan error: {type(exc).__name__}: {exc}")
                    continue
                if not available:
                    continue
                # Group by date so we attempt 6-8 + 8-9 together (and prefer same court).
                by_date: dict[ddate, dict[str, set[int]]] = defaultdict(dict)
                for (d, piece_name), courts in available.items():
                    by_date[d][piece_name] = courts
                for day in sorted(by_date.keys()):
                    pieces = by_date[day]
                    # Prefer courts that are available in BOTH halves.
                    common = (
                        set.intersection(*pieces.values())
                        if len(pieces) == len(PIECES)
                        else set()
                    )
                    print(f"[{facility.id}] {day}: pieces available "
                          f"{ {k: len(v) for k, v in pieces.items()} } "
                          f"(common-courts={sorted(common) or 'n/a'})")
                    for piece_name, _, _ in PIECES:
                        if piece_name not in pieces:
                            continue
                        if self.is_blacklisted(facility.id, day, piece_name):
                            continue
                        # Order: courts in 'common' first (they let us keep the same
                        # court across the 2h+1h pair), then the rest.
                        ordered = sorted(common) + sorted(pieces[piece_name] - common)
                        booked = await self.attempt_piece(facility, day, piece_name, ordered)
                        if booked:
                            await asyncio.sleep(MIN_DELAY_BETWEEN_POSTS_S)
                        if self.posts_this_cycle >= MAX_POSTS_PER_CYCLE:
                            return

    async def run(self) -> None:
        started = _now_ts()
        print(f"[poller] starting; cadence {POLL_INTERVAL_S}s ±{int(POLL_JITTER_FRAC*100)}%, "
              f"max runtime {MAX_RUNTIME_S//3600}h, "
              f"max-posts/cycle={MAX_POSTS_PER_CYCLE}, "
              f"facilities={[f.id for f in self.facilities]}")
        recent = list_recent(limit=20)
        today = datetime.now(LOCAL).date()
        confirmed_future = [
            r for r in recent if r.status == "confirmed" and ddate.fromisoformat(r.date) >= today
        ]
        if confirmed_future:
            print(f"[poller] ledger has {len(confirmed_future)} future confirmed booking(s):")
            for r in confirmed_future:
                print(f"           {r.date} {r.start_time} {r.facility} ct{r.court_id} "
                      f"#{r.confirmation_id}")

        tick = 0
        while not self.stop_event.is_set():
            elapsed = _now_ts() - started
            if elapsed > MAX_RUNTIME_S:
                print(f"[poller] max runtime reached ({MAX_RUNTIME_S}s)")
                break
            tick += 1
            today = datetime.now(LOCAL).date()
            days = sorted({today + timedelta(days=d) for d in DAY_DELTAS})
            print(f"\n[tick {tick}] {datetime.now(LOCAL).strftime('%H:%M:%S')} "
                  f"horizon={days[0]}→{days[-1]} "
                  f"(window-blacklist={len(self.window_blacklist)}, "
                  f"taken-blacklist={len(self.taken_blacklist)})")
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

        if self.bookings_made_this_run:
            print(f"\n[poller] stopped. Booked this run:")
            for msg in self.bookings_made_this_run:
                print(f"  - {msg}")
        else:
            print(f"\n[poller] stopped. No bookings this run.")


async def main() -> None:
    cfg = load_config(config_path())
    facilities = [cfg.facility(fid) for fid in FACILITY_PRIORITY]
    poller = Poller(cfg, facilities)

    def _handler(signum, _frame):
        print(f"\n[poller] received signal {signum}; stopping after current cycle")
        poller.stop_event.set()
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, _handler)

    await poller.run()


if __name__ == "__main__":
    asyncio.run(main())
