from __future__ import annotations

import asyncio
import time as time_mod
from dataclasses import dataclass
from datetime import datetime

from bay_area_courtbot.config import Config
from bay_area_courtbot.courtreserve.client import CourtReserveClient
from bay_area_courtbot.courtreserve.errors import (
    AllCourtsTaken,
    AuthExpired,
    CourtReserveError,
    RateLimited,
    SlotTaken,
    WindowNotOpen,
)
from bay_area_courtbot.ledger import is_already_confirmed, record_attempt, record_confirmed
from bay_area_courtbot.logging import get_logger
from bay_area_courtbot.notify import notify_macos
from bay_area_courtbot.racer.prewarm import RacerContext

# Tight retries — race window is seconds, not minutes.
_RATE_LIMIT_BACKOFF = (0.10, 0.25, 0.50, 1.00)


@dataclass
class RaceResult:
    success: bool
    candidate_index: int | None
    confirmation_id: str | None
    elapsed_ms: int
    attempts: int
    last_error: str | None = None


async def run_burst(
    cfg: Config,
    ctx: RacerContext,
    *,
    fire_at_utc: datetime,
    dry_run: bool = False,
    max_window_seconds: float = 5.0,
    parallel_fanout: int = 3,
) -> RaceResult:
    """Sleep until fire_at_utc (corrected for server clock skew), then fire pre-built
    POST bodies in concurrent bursts of `parallel_fanout` until success or budget out.

    Pre-built bodies in `ctx.prebuilt` reuse a single CSRF + RequestData from the
    modal pre-fetch — this saves ~1.5s per attempt vs. fetching the modal each time.
    """
    log = get_logger(
        facility=ctx.facility.id,
        mode="racer",
        phase="burst",
        target_date=ctx.target_date.isoformat(),
    )

    if not ctx.prebuilt:
        log.warning("racer.burst.no_candidates")
        return RaceResult(success=False, candidate_index=None, confirmation_id=None,
                          elapsed_ms=0, attempts=0, last_error="no candidates")

    skew_ms = ctx.session.server_clock_offset_ms
    fire_ts = fire_at_utc.timestamp() + (skew_ms / 1000.0) + 0.010  # +10ms safety
    deadline = fire_ts + max_window_seconds

    delay = fire_ts - time_mod.time()
    if delay > 0:
        log.info("racer.burst.sleep", delay_ms=int(delay * 1000), skew_ms=skew_ms)
        await asyncio.sleep(max(0.0, delay - 0.005))
        while time_mod.time() < fire_ts:
            await asyncio.sleep(0)

    started = time_mod.perf_counter()
    log.info(
        "racer.burst.fire",
        candidates=len(ctx.prebuilt),
        skew_ms=skew_ms,
        dry_run=dry_run,
        parallel_fanout=parallel_fanout,
    )

    if dry_run:
        # Honor dry-run: log and return success without POSTing anywhere.
        elapsed_ms = int((time_mod.perf_counter() - started) * 1000)
        first = ctx.prebuilt[0].candidate
        log.info("racer.dry_run", court=first.court_id, elapsed_ms=elapsed_ms)
        return RaceResult(success=True, candidate_index=0, confirmation_id=None,
                          elapsed_ms=elapsed_ms, attempts=0)

    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Origin": "https://reservations.courtreserve.com",
        "Referer": (
            f"https://app.courtreserve.com/Online/Reservations/Bookings/{ctx.facility.org_id}"
        ),
    }

    attempts = 0
    last_error: str | None = None
    queue = list(ctx.prebuilt)
    while queue:
        if time_mod.time() >= deadline:
            log.warning("racer.burst.deadline", attempts=attempts)
            break
        burst, queue = queue[:parallel_fanout], queue[parallel_fanout:]
        attempts += len(burst)

        log.info("racer.burst.fanout", courts=[p.candidate.court_id for p in burst])

        async def _post(body: str):
            return await ctx.client.post(ctx.post_url, content=body, headers=headers)

        responses = await asyncio.gather(
            *(_post(p.encoded_body) for p in burst), return_exceptions=True,
        )

        global_exhausted = False
        for prebuilt, resp in zip(burst, responses):
            cand = prebuilt.candidate
            if isinstance(resp, Exception):
                last_error = f"{type(resp).__name__}: {resp}"
                log.warning("racer.attempt.transport_error", court=cand.court_id, error=last_error)
                continue
            try:
                confirmation = CourtReserveClient._interpret_create(resp)
            except AllCourtsTaken as exc:
                last_error = f"AllCourtsTaken: {exc}"
                log.warning("racer.attempt.all_courts_taken", court=cand.court_id)
                global_exhausted = True
                continue
            except SlotTaken:
                log.info("racer.attempt.slot_taken", court=cand.court_id)
                continue
            except WindowNotOpen as exc:
                last_error = f"WindowNotOpen: {exc}"
                log.warning("racer.attempt.window_not_open", court=cand.court_id)
                # Window not open is global; cancel the rest.
                return RaceResult(
                    success=False, candidate_index=None, confirmation_id=None,
                    elapsed_ms=int((time_mod.perf_counter() - started) * 1000),
                    attempts=attempts, last_error=last_error,
                )
            except RateLimited as exc:
                last_error = f"RateLimited: {exc}"
                log.warning("racer.attempt.rate_limited", court=cand.court_id)
                continue
            except (AuthExpired, CourtReserveError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                log.warning("racer.attempt.error", court=cand.court_id, error=last_error)
                continue

            # Success path.
            if not is_already_confirmed(
                facility=cand.facility_id, date=cand.date.isoformat(),
                start_time=cand.start.strftime("%H:%M"), court_id=cand.court_id,
            ):
                record_confirmed(
                    facility=cand.facility_id, date=cand.date.isoformat(),
                    start_time=cand.start.strftime("%H:%M"),
                    duration_minutes=cand.duration_minutes, court_id=cand.court_id,
                    mode="racer", confirmation_id=confirmation,
                )
            elapsed_ms = int((time_mod.perf_counter() - started) * 1000)
            log.info("racer.success", court=cand.court_id, confirmation_id=confirmation,
                     elapsed_ms=elapsed_ms)
            if cfg.notifications.on_success:
                notify_macos(
                    "bay_area_courtbot booked",
                    f"{cand.facility_id} {cand.date} {cand.start} ct{cand.court_id} #{confirmation}",
                )
            return RaceResult(
                success=True, candidate_index=ctx.prebuilt.index(prebuilt),
                confirmation_id=confirmation, elapsed_ms=elapsed_ms, attempts=attempts,
            )

        if global_exhausted:
            log.warning("racer.burst.short_circuit_all_courts_taken")
            break

    elapsed_ms = int((time_mod.perf_counter() - started) * 1000)
    log.warning("racer.failure", attempts=attempts, elapsed_ms=elapsed_ms, error=last_error)
    if cfg.notifications.on_failure:
        first = ctx.prebuilt[0].candidate
        notify_macos(
            "bay_area_courtbot race failed",
            f"{first.facility_id} {first.date} {first.start}: {last_error}",
        )
    return RaceResult(
        success=False, candidate_index=None, confirmation_id=None,
        elapsed_ms=elapsed_ms, attempts=attempts, last_error=last_error,
    )
