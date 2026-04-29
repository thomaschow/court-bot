"""Tightened multi-facility racer for a contested booking-window-open moment.

Architecture (rebuild post-2026-04-27 12:00 PDT failure):

  T-180s   prewarm: hydrate sessions for every facility, measure clock skew (per facility)
  T-30s    fetch ONE modal per (facility, time, court_type) → grab CSRF + RequestData
           pre-build N urlencoded POST bodies per facility
  T-0      fire bursts of `parallel_fanout` POSTs concurrently, walking the priority
           ladder. First success wins; the rest are short-circuited.
  T+~1s    if a facility responds AllCourtsTaken, drop it from the ladder and continue
           with remaining facilities.

Edit the CONFIG section below to pick date/time/duration/preferences.
"""

import asyncio
import time as time_mod
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx

from bay_area_courtbot.auth.session import build_client, hydrate
from bay_area_courtbot.config import Facility, load_config
from bay_area_courtbot.courtreserve.client import CourtReserveClient
from bay_area_courtbot.courtreserve.errors import (
    AllCourtsTaken,
    AuthExpired,
    CourtReserveError,
    RateLimited,
    SlotTaken,
    WindowNotOpen,
)
from bay_area_courtbot.courtreserve.modal import ModalState, fetch_modal
from bay_area_courtbot.courtreserve.payloads import BookingCandidate, build_create_reservation_form
from bay_area_courtbot.ledger import is_already_confirmed, record_attempt, record_confirmed
from bay_area_courtbot.paths import config_path

LOCAL = ZoneInfo("America/Los_Angeles")

# === CONFIG ===
TARGET_DATE = date(2026, 5, 4)
START = time(18, 0)              # 6:00 PM PDT
DURATION = 120                   # 2 hours
COURT_TYPE_ID = 2                # "Hard"
COURT_TYPE = "Hard"
# Facility priority. Each tuple = (facility_id, [court_priority_list]).
# Empty court list = use the facility's discovered courts in order.
FACILITY_LADDER = [
    ("santa-clara", []),
    ("sunnyvale",   []),
]
FIRE_AT_LOCAL = datetime(2026, 5, 4, 12, 0, 0, tzinfo=LOCAL)  # next Mon 12:00 PDT
PARALLEL_FANOUT = 5              # POSTs fired concurrently per burst (per facility)
MAX_BURSTS_PER_FACILITY = 2


@dataclass
class FacilityBurst:
    facility: Facility
    client: httpx.AsyncClient
    modal: ModalState
    prebuilt: list[tuple[BookingCandidate, str]]  # (candidate, encoded body)
    skew_s: float


async def _prewarm_facility(cfg, facility_id: str) -> FacilityBurst:
    f = cfg.facility(facility_id)
    print(f"[{f.id}] prewarm…")
    client = build_client(f, http2=True)
    session = await hydrate(client, f)
    skew_s = session.server_clock_offset_ms / 1000.0
    print(f"[{f.id}] session ok, server clock skew {skew_s:+.3f}s")

    courts = [c.id for c in f.courts]
    if not courts:
        raise RuntimeError(f"facility {f.id} has no courts in config — run discover")
    candidates = [
        BookingCandidate(
            facility_id=f.id, org_id=f.org_id, member_id=f.member_id,
            membership_id=f.cost_type_id, reservation_type_id=f.reservation_type_id,
            court_id=cid, date=TARGET_DATE, start=START, duration_minutes=DURATION,
        )
        for cid in courts
    ]

    modal = await fetch_modal(
        client, f,
        day=TARGET_DATE, start=START, duration_minutes=DURATION,
        court_type_id=COURT_TYPE_ID, court_type=COURT_TYPE,
    )
    print(f"[{f.id}] modal ready: {len(modal.hidden_fields)} hidden fields")

    prebuilt = []
    for cand in candidates:
        body = build_create_reservation_form(
            cand, csrf_token=modal.csrf_token, hidden_fields=modal.hidden_fields,
        )
        prebuilt.append((cand, urlencode(body)))
    print(f"[{f.id}] pre-built {len(prebuilt)} POST bodies")

    return FacilityBurst(facility=f, client=client, modal=modal, prebuilt=prebuilt, skew_s=skew_s)


def _interpret_response_text(status: int, text: str) -> tuple[str, str]:
    """Return (kind, message) where kind ∈ {'success', 'all-taken', 'taken', 'window', 'error'}."""
    lower = (text or "").lower()
    if status == 200 and '"reservationid"' in lower:
        import re
        m = re.search(r'"(?:Reservation|Confirmation)?Id"\s*:\s*"?(\d+)"?', text)
        if m:
            return ("success", m.group(1))
    if "all courts of this type" in lower:
        return ("all-taken", text[:200])
    if "only allowed to reserve up to" in lower or ("not yet open" in lower):
        return ("window", text[:200])
    if "no longer available" in lower or "already reserved" in lower or "taken" in lower:
        return ("taken", text[:200])
    return ("error", text[:200])


async def _fire_bursts(burst: FacilityBurst, deadline: float) -> tuple[BookingCandidate, str] | None:
    f = burst.facility
    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Origin": "https://reservations.courtreserve.com",
        "Referer": f"https://app.courtreserve.com/Online/Reservations/Bookings/{f.org_id}",
    }
    queue = list(burst.prebuilt)
    bursts_fired = 0
    while queue and bursts_fired < MAX_BURSTS_PER_FACILITY:
        if time_mod.time() >= deadline:
            print(f"[{f.id}] deadline; stopping")
            break
        chunk, queue = queue[:PARALLEL_FANOUT], queue[PARALLEL_FANOUT:]
        bursts_fired += 1
        court_ids = [c.court_id for c, _ in chunk]
        print(f"[{f.id}] burst {bursts_fired}: firing {len(chunk)} POSTs in parallel "
              f"(courts={court_ids})")
        t_burst = time_mod.perf_counter()
        responses = await asyncio.gather(
            *(burst.client.post(burst.modal.inner_form_url, content=body, headers=headers)
              for _, body in chunk),
            return_exceptions=True,
        )
        burst_ms = (time_mod.perf_counter() - t_burst) * 1000

        all_taken = False
        for (cand, _), r in zip(chunk, responses):
            if isinstance(r, Exception):
                print(f"  [{f.id}] court {cand.court_id}: TRANSPORT ERROR ({type(r).__name__})")
                continue
            kind, msg = _interpret_response_text(r.status_code, r.text or "")
            if kind == "success":
                print(f"  [{f.id}] court {cand.court_id}: ✓ #{msg}  ({burst_ms:.0f}ms burst)")
                if not is_already_confirmed(
                    facility=cand.facility_id, date=cand.date.isoformat(),
                    start_time=cand.start.strftime("%H:%M"), court_id=cand.court_id,
                ):
                    record_confirmed(
                        facility=cand.facility_id, date=cand.date.isoformat(),
                        start_time=cand.start.strftime("%H:%M"),
                        duration_minutes=cand.duration_minutes, court_id=cand.court_id,
                        mode="manual_race", confirmation_id=msg,
                    )
                return (cand, msg)
            if kind == "all-taken":
                print(f"  [{f.id}] court {cand.court_id}: ALL COURTS TAKEN")
                all_taken = True
            elif kind == "taken":
                print(f"  [{f.id}] court {cand.court_id}: TAKEN")
            elif kind == "window":
                print(f"  [{f.id}] court {cand.court_id}: WINDOW NOT OPEN — {msg[:60]}")
            else:
                print(f"  [{f.id}] court {cand.court_id}: ERROR — {msg[:80]}")
            record_attempt(
                facility=cand.facility_id, date=cand.date.isoformat(),
                start_time=cand.start.strftime("%H:%M"),
                duration_minutes=cand.duration_minutes, court_id=cand.court_id,
                mode="manual_race", status="failed", error=f"{kind}: {msg[:200]}",
            )
        if all_taken:
            print(f"[{f.id}] all courts of {COURT_TYPE} taken → giving up on this facility")
            return None

    return None


async def main() -> None:
    cfg = load_config(config_path())

    # ---- prewarm all facilities in parallel ----
    bursts: list[FacilityBurst] = []
    try:
        # Hydrate + modal-fetch ~30s before fire (modal RequestData stays valid for minutes).
        # First, just hydrate all facilities now.
        skew_collect: list[FacilityBurst] = []
        for facility_id, _ in FACILITY_LADDER:
            f = cfg.facility(facility_id)
            client = build_client(f, http2=True)
            try:
                session = await hydrate(client, f)
                print(f"[{f.id}] session ok, skew {session.server_clock_offset_ms:+d}ms")
                skew_collect.append(FacilityBurst(
                    facility=f, client=client, modal=None, prebuilt=[],
                    skew_s=session.server_clock_offset_ms / 1000.0,
                ))
            except Exception as exc:
                print(f"[{f.id}] hydrate FAILED: {exc} — skipping this facility")
                await client.aclose()

        if not skew_collect:
            print("no facilities ready; aborting")
            return

        # Sleep until ~30s before fire, then mint modals on all facilities in parallel.
        avg_skew = sum(b.skew_s for b in skew_collect) / len(skew_collect)
        target_ts = FIRE_AT_LOCAL.timestamp() + avg_skew + 0.010
        prefetch_ts = target_ts - 30.0
        wait = prefetch_ts - time_mod.time()
        if wait > 0:
            print(f"[all] sleeping {wait:.1f}s before modal fetch (~30s before fire)")
            await asyncio.sleep(wait)
        elif wait < -120:
            print(f"[all] fire time is {-wait:.1f}s in the past; aborting")
            for b in skew_collect:
                await b.client.aclose()
            return

        async def _build_modal(b: FacilityBurst):
            f = b.facility
            print(f"[{f.id}] fetching modal…")
            modal = await fetch_modal(
                b.client, f, day=TARGET_DATE, start=START, duration_minutes=DURATION,
                court_type_id=COURT_TYPE_ID, court_type=COURT_TYPE,
            )
            candidates = [
                BookingCandidate(
                    facility_id=f.id, org_id=f.org_id, member_id=f.member_id,
                    membership_id=f.cost_type_id, reservation_type_id=f.reservation_type_id,
                    court_id=c.id, date=TARGET_DATE, start=START, duration_minutes=DURATION,
                )
                for c in f.courts
            ]
            prebuilt = [
                (c, urlencode(build_create_reservation_form(
                    c, csrf_token=modal.csrf_token, hidden_fields=modal.hidden_fields,
                )))
                for c in candidates
            ]
            b.modal = modal
            b.prebuilt = prebuilt
            print(f"[{f.id}] {len(prebuilt)} bodies pre-built")
            return b

        await asyncio.gather(*(_build_modal(b) for b in skew_collect), return_exceptions=False)
        bursts = skew_collect

        # ---- sleep until exact fire time ----
        delay = target_ts - time_mod.time()
        if delay > 0:
            print(f"\n[all] armed; sleeping {delay:.3f}s until fire")
            await asyncio.sleep(max(0.0, delay - 0.010))
            while time_mod.time() < target_ts:
                await asyncio.sleep(0)

        print(f"\n[FIRE] @ {datetime.now(LOCAL).isoformat(timespec='milliseconds')}")
        t_fire = time_mod.perf_counter()
        budget_end = time_mod.time() + 10.0  # 10s total budget across all facilities

        # Fire facilities in priority order, stopping at first success.
        for facility_id, _ in FACILITY_LADDER:
            burst = next((b for b in bursts if b.facility.id == facility_id), None)
            if burst is None:
                continue
            result = await _fire_bursts(burst, deadline=budget_end)
            if result:
                cand, conf = result
                elapsed = (time_mod.perf_counter() - t_fire) * 1000
                print(f"\n✓ BOOKED {cand.facility_id}/court {cand.court_id} "
                      f"{cand.start.strftime('%-I:%M %p')}–{cand.end.strftime('%-I:%M %p')} "
                      f"on {cand.date} (#{conf}) in {elapsed:.0f}ms")
                return

        elapsed = (time_mod.perf_counter() - t_fire) * 1000
        print(f"\n✗ no booking after {elapsed:.0f}ms across {len(bursts)} facilities")

    finally:
        for b in bursts:
            try:
                await b.client.aclose()
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())
