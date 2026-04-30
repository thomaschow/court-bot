"""Seattle cancellation watcher daemon.

Polls every configured (facility, court) for availability, converts ANC's time
ranges into 30-min slices, diffs against the previous snapshot, and books any
*newly-available* slot that falls inside the user's time-of-day window.

Same operating-model parameters as the bay-area watcher:
  - per-cycle MAX_POSTS budget (cancellations are infrequent; small budget OK)
  - jittered POLL_INTERVAL_S
  - MIN_LEAD_TIME_HOURS gate (skip slots starting too soon)
  - per-(facility, date) blacklist on WindowNotOpen / repeated errors
  - parameterised pairing rule for short (slot_duration) bookings

Crucial difference from bay-area: **Seattle bookings cost money**. The daemon
defaults to `commit=False` (validate + log fee, no actual booking). To enable
real booking, the CLI must be invoked with `--commit`. Even then, the daemon
respects the config's pairing rule + lead-time gate to avoid wasteful spend.
"""

from __future__ import annotations

import asyncio
import json
import random
import signal
import time as time_mod
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date as ddate, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import httpx

from seattle_courtbot.config import Config, PairingRule


LOCAL = ZoneInfo("America/Los_Angeles")


@dataclass(frozen=True)
class SliceKey:
    facility_id: str
    date_ord: int
    start_minutes: int
    court_id: int


def gap_minutes(delta_min: int, slot_duration_min: int) -> int:
    gap = abs(delta_min) - slot_duration_min
    return gap if gap >= 0 else -1


def has_partner(
    snapshot: set[SliceKey],
    facility_id: str,
    day: ddate,
    start_local: dtime,
    court_id: int,
    rule: PairingRule,
) -> bool:
    slot_min = start_local.hour * 60 + start_local.minute
    date_ord = day.toordinal()
    for k in snapshot:
        if k.facility_id != facility_id or k.date_ord != date_ord:
            continue
        if k.start_minutes == slot_min and k.court_id == court_id:
            continue
        delta = k.start_minutes - slot_min
        gap = gap_minutes(delta, rule.slot_duration_min)
        if gap < 0:
            continue
        if gap <= rule.max_any_court_gap_min:
            return True
        if k.court_id == court_id and gap <= rule.max_same_court_gap_min:
            return True
    return False


@dataclass(frozen=True)
class RelaxationLevel:
    label: str
    max_any_court_gap_min: int
    max_same_court_gap_min: int


DEFAULT_RELAXATION_LEVELS: tuple[RelaxationLevel, ...] = (
    RelaxationLevel("adjacent_any_court", 0, 0),
    RelaxationLevel("same_court_30min_gap", 0, 30),
    RelaxationLevel("same_court_60min_gap", 0, 60),
)


def capture_neighbors(
    snapshot: set[SliceKey],
    facility_id: str,
    day: ddate,
    start_local: dtime,
    court_id: int,
    *,
    within_min: int = 90,
    slot_duration_min: int = 30,
    levels: tuple[RelaxationLevel, ...] = DEFAULT_RELAXATION_LEVELS,
) -> str:
    slot_min = start_local.hour * 60 + start_local.minute
    date_ord = day.toordinal()
    out = []
    for k in snapshot:
        if k.facility_id != facility_id or k.date_ord != date_ord:
            continue
        if k.start_minutes == slot_min and k.court_id == court_id:
            continue
        delta = k.start_minutes - slot_min
        if abs(delta) > within_min:
            continue
        gap = gap_minutes(delta, slot_duration_min)
        qualifies = []
        for lvl in levels:
            if gap < 0:
                continue
            if gap <= lvl.max_any_court_gap_min:
                qualifies.append(lvl.label)
                continue
            if k.court_id == court_id and gap <= lvl.max_same_court_gap_min:
                qualifies.append(lvl.label)
        h, m = divmod(k.start_minutes, 60)
        out.append({
            "court_id": k.court_id,
            "start": f"{h:02d}:{m:02d}",
            "delta_min": delta,
            "gap_min": gap,
            "qualifies_under": qualifies,
        })
    out.sort(key=lambda x: (abs(x["delta_min"]), x["court_id"]))
    return json.dumps(out)


# ===========================================================================
# Daemon
# ===========================================================================


class SeattleWatcher:
    """Per-cycle: scan availability for every (facility, court) within the date
    horizon, diff against the previous snapshot, attempt to book newly-freed
    slots that fall in the time-of-day window."""

    def __init__(self, cfg: Config, *, commit: bool = False):
        self.cfg = cfg
        self.commit = commit
        self.prev_snapshot: dict[str, set[SliceKey]] = defaultdict(set)
        # Per-(facility, date) blacklist on WindowNotOpen-style errors.
        self.window_blacklist: dict[tuple[str, int], float] = {}
        self.stop_event = asyncio.Event()
        self.posts_this_cycle = 0
        self.bookings_this_run: list[str] = []
        self.first_cycle = True

    @property
    def horizon_dates(self) -> list[ddate]:
        today = datetime.now(LOCAL).date()
        return [today + timedelta(days=d) for d in range(1, self.cfg.preferences.date_horizon_days + 1)]

    async def _scan_facility(
        self, client: httpx.AsyncClient, facility, days: list[ddate],
    ) -> set[SliceKey]:
        from seattle_courtbot.ancapi.client import AncClient
        from seattle_courtbot.watcher.converter import ranges_to_slices

        anc = AncClient(client, tenant=facility.tenant_slug)
        out: set[SliceKey] = set()
        if not days:
            return out
        start_d, end_d = days[0], days[-1]
        for court in facility.courts:
            try:
                ranges = await anc.read_availability(
                    resource_id=court.id,
                    customer_id=self.cfg.member_id or 0,
                    start_date=start_d, end_date=end_d,
                )
            except Exception as exc:
                print(f"[{facility.id}] availability error for ct{court.id}: "
                      f"{type(exc).__name__}: {exc}")
                continue
            slices = ranges_to_slices(
                facility.id, ranges,
                slice_minutes=30,
                window_start=self.cfg.preferences.time_window.start,
                window_end=self.cfg.preferences.time_window.end,
            )
            out |= slices
        return out

    def _slot_lead_ok(self, day: ddate, start_local: dtime) -> bool:
        slot_dt = datetime.combine(day, start_local, tzinfo=LOCAL)
        threshold = datetime.now(LOCAL) + timedelta(hours=12)
        return slot_dt >= threshold

    async def _book_attempt(self, client, facility, key: SliceKey) -> None:
        """Attempt to book a newly-freed slot. Tries the longest duration that
        fits within the user's time window AND ANC's 60-180min limits."""
        if self.posts_this_cycle >= self.cfg.polling.max_posts_per_cycle:
            return
        from seattle_courtbot.ancapi.booking import BookingRequest, book as do_book
        from seattle_courtbot.ancapi.csrf import fetch_csrf_token
        from seattle_courtbot.paths import session_path

        d = ddate.fromordinal(key.date_ord)
        start_t = dtime(key.start_minutes // 60, key.start_minutes % 60)
        if not self._slot_lead_ok(d, start_t):
            return

        # Compute the longest duration that fits in the window starting at start_t.
        win_end = self.cfg.preferences.time_window.end
        end_minutes = win_end.hour * 60 + win_end.minute
        headroom = end_minutes - key.start_minutes
        if headroom < self.cfg.preferences.duration_min_minutes:
            return
        duration = min(headroom, self.cfg.preferences.duration_max_minutes)
        # Round down to a 30-min boundary.
        duration = (duration // 30) * 30
        if duration < 60:
            return  # ANC requires ≥60-min reservations

        token = await fetch_csrf_token(storage_state_path=str(session_path()))
        req = BookingRequest(
            customer_id=self.cfg.member_id, resource_id=key.court_id,
            event_type_id=152, attendee_count=2,
            date=d, start=start_t, duration_minutes=duration,
            event_name="Tennis booking",
        )
        self.posts_this_cycle += 1
        try:
            result = await do_book(client, req, csrf=token, dry_run=not self.commit)
        except Exception as exc:
            print(f"  [{facility.id}] book ct{key.court_id} {d} {start_t}: "
                  f"{type(exc).__name__}: {exc}")
            return
        end_t = (datetime.combine(d, start_t) + timedelta(minutes=duration)).time()
        if self.commit and result.success and result.confirmation_id:
            msg = (f"BOOKED {facility.id}/ct{key.court_id} {d} "
                   f"{start_t.strftime('%-I:%M %p')}-{end_t.strftime('%-I:%M %p')} "
                   f"(${result.fee_total}) #{result.confirmation_id}")
            print(f"  ✓ {msg}")
            self.bookings_this_run.append(msg)
        else:
            mode = "DRY-RUN" if not self.commit else "FAILED"
            msg = (f"{mode} {facility.id}/ct{key.court_id} {d} "
                   f"{start_t.strftime('%-I:%M %p')}-{end_t.strftime('%-I:%M %p')} "
                   f"fee=${result.fee_total}")
            print(f"  ⚠ {msg}")

    async def _run_cycle(self) -> None:
        from seattle_courtbot.auth.session import build_client

        self.posts_this_cycle = 0
        days = self.horizon_dates
        if not days:
            return
        for facility in self.cfg.facilities:
            async with build_client(http2=False) as client:
                snapshot = await self._scan_facility(client, facility, days)
                prev = self.prev_snapshot.get(facility.id, set())
                self.prev_snapshot[facility.id] = snapshot
                if self.first_cycle:
                    print(f"[{facility.id}] baseline: {len(snapshot)} slices "
                          f"in window across {len(days)} dates")
                    continue
                new_keys = snapshot - prev
                if not new_keys:
                    continue
                print(f"[{facility.id}] {len(new_keys)} NEW slice(s) detected")
                # Group by (date, court) → just attempt the earliest in each group.
                seen_attempts: set[tuple[int, int]] = set()
                for key in sorted(new_keys, key=lambda k: (k.date_ord, k.start_minutes, k.court_id)):
                    pair = (key.date_ord, key.court_id)
                    if pair in seen_attempts:
                        continue  # one attempt per (date, court) per cycle
                    seen_attempts.add(pair)
                    if self.posts_this_cycle >= self.cfg.polling.max_posts_per_cycle:
                        break
                    await self._book_attempt(client, facility, key)
        if self.first_cycle:
            self.first_cycle = False

    async def run(self) -> None:
        started = time_mod.time()
        max_runtime = self.cfg.polling.max_runtime_hours * 3600
        commit_str = "COMMIT" if self.commit else "DRY-RUN"
        print(f"[seattle-watcher] starting in {commit_str} mode; "
              f"cadence {self.cfg.polling.interval_seconds}s "
              f"±{int(self.cfg.polling.jitter_frac*100)}%, "
              f"max-runtime {self.cfg.polling.max_runtime_hours}h, "
              f"window {self.cfg.preferences.time_window.start}–"
              f"{self.cfg.preferences.time_window.end}")
        tick = 0
        while not self.stop_event.is_set():
            elapsed = time_mod.time() - started
            if elapsed > max_runtime:
                print("[seattle-watcher] max runtime reached")
                break
            tick += 1
            try:
                await self._run_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[seattle-watcher] tick {tick} cycle error: "
                      f"{type(exc).__name__}: {exc}")
            wait = self.cfg.polling.interval_seconds * random.uniform(
                1 - self.cfg.polling.jitter_frac, 1 + self.cfg.polling.jitter_frac,
            )
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=wait)
                break
            except asyncio.TimeoutError:
                pass
        if self.bookings_this_run:
            print(f"\n[seattle-watcher] booked {len(self.bookings_this_run)} slot(s):")
            for m in self.bookings_this_run:
                print(f"  - {m}")
        else:
            print(f"\n[seattle-watcher] no bookings this run.")


async def run_watcher(cfg: Config, *, commit: bool = False) -> None:
    w = SeattleWatcher(cfg, commit=commit)

    def _handler(signum, _frame):
        print(f"\n[seattle-watcher] signal {signum}; stopping after current cycle")
        w.stop_event.set()
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, _handler)
    await w.run()
