from __future__ import annotations

import asyncio
import time as time_mod
from dataclasses import dataclass
from datetime import datetime

from courtbot.booking.service import BookResult, book
from courtbot.config import Config
from courtbot.courtreserve.errors import (
    AuthExpired,
    CourtReserveError,
    RateLimited,
    SlotTaken,
    WindowNotOpen,
)
from courtbot.logging import get_logger
from courtbot.racer.prewarm import RacerContext

# Fixed retry delays (seconds). Tight, not exponential — race window is 5s.
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
) -> RaceResult:
    """Sleep until fire_at_utc (corrected for server clock skew), then walk the candidate
    ladder firing one booking attempt at a time. Returns on first success or when the
    window-budget is exhausted.
    """
    log = get_logger(
        facility=ctx.facility.id,
        mode="racer",
        phase="burst",
        target_date=ctx.target_date.isoformat(),
    )

    skew_ms = ctx.session.server_clock_offset_ms
    fire_ts = fire_at_utc.timestamp() - (skew_ms / 1000.0) + 0.010  # +10ms safety
    deadline = fire_ts + max_window_seconds

    delay = fire_ts - time_mod.time()
    if delay > 0:
        log.info("racer.burst.sleep", delay_ms=int(delay * 1000))
        await asyncio.sleep(max(0.0, delay - 0.005))
        # Tight spin-loop for last 5ms to land on the boundary precisely.
        while time_mod.time() < fire_ts:
            await asyncio.sleep(0)

    started = time_mod.perf_counter()
    log.info(
        "racer.burst.fire",
        candidates=len(ctx.candidates),
        skew_ms=skew_ms,
        dry_run=dry_run,
    )

    attempts = 0
    last_error: str | None = None
    for idx, cand in enumerate(ctx.candidates):
        if time_mod.time() >= deadline:
            log.warning("racer.burst.deadline", idx=idx, attempts=attempts)
            break
        backoff_iter = iter(_RATE_LIMIT_BACKOFF)
        while True:
            attempts += 1
            try:
                result: BookResult = await book(
                    cfg,
                    ctx.facility,
                    cand,
                    mode="racer",
                    dry_run=dry_run,
                    client=ctx.client,
                    session=ctx.session,
                )
            except RateLimited as exc:
                last_error = f"RateLimited: {exc}"
                wait = next(backoff_iter, None)
                if wait is None or time_mod.time() + wait >= deadline:
                    break
                log.warning("racer.attempt.rate_limited", idx=idx, wait_s=wait)
                await asyncio.sleep(wait)
                continue
            except WindowNotOpen as exc:
                last_error = f"WindowNotOpen: {exc}"
                wait = 0.10
                if time_mod.time() + wait >= deadline:
                    break
                log.info("racer.attempt.window_not_open", idx=idx)
                await asyncio.sleep(wait)
                continue
            except AuthExpired as exc:
                last_error = f"AuthExpired: {exc}"
                log.error("racer.attempt.auth_expired", idx=idx)
                return RaceResult(
                    success=False,
                    candidate_index=None,
                    confirmation_id=None,
                    elapsed_ms=int((time_mod.perf_counter() - started) * 1000),
                    attempts=attempts,
                    last_error=last_error,
                )
            except CourtReserveError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                log.warning("racer.attempt.error", idx=idx, error=last_error)
                break

            if result.status in ("confirmed", "dry_run"):
                log.info(
                    "racer.success",
                    idx=idx,
                    elapsed_ms=int((time_mod.perf_counter() - started) * 1000),
                    confirmation_id=result.confirmation_id,
                )
                return RaceResult(
                    success=True,
                    candidate_index=idx,
                    confirmation_id=result.confirmation_id,
                    elapsed_ms=int((time_mod.perf_counter() - started) * 1000),
                    attempts=attempts,
                )
            if result.status == "duplicate":
                log.info("racer.attempt.duplicate", idx=idx)
                break
            if result.status == "failed":
                last_error = result.error
                if "SlotTaken" in (result.error or ""):
                    log.info("racer.attempt.slot_taken", idx=idx)
                    break
                # Generic failure: don't retry the same candidate.
                break

    elapsed_ms = int((time_mod.perf_counter() - started) * 1000)
    log.warning("racer.failure", attempts=attempts, elapsed_ms=elapsed_ms, error=last_error)
    return RaceResult(
        success=False,
        candidate_index=None,
        confirmation_id=None,
        elapsed_ms=elapsed_ms,
        attempts=attempts,
        last_error=last_error,
    )
