from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date as ddate

from courtbot.auth.session import SessionState, build_client, hydrate
from courtbot.config import Config, Facility
from courtbot.courtreserve.payloads import BookingCandidate
from courtbot.logging import get_logger
from courtbot.racer.strategy import rank_candidates


@dataclass
class RacerContext:
    facility: Facility
    target_date: ddate
    client: object  # httpx.AsyncClient (left untyped to keep import surface tight)
    session: SessionState
    candidates: list[BookingCandidate]

    async def aclose(self) -> None:
        await self.client.aclose()


async def prewarm(
    cfg: Config,
    facility: Facility,
    target_date: ddate,
    *,
    candidate_starts,
    refresh_csrf_every: float = 30.0,
) -> RacerContext:
    log = get_logger(facility=facility.id, mode="racer", phase="prewarm")
    client = build_client(facility)
    session = await hydrate(client, facility)
    log.info(
        "racer.prewarm.session_ready",
        has_csrf=session.csrf_token is not None,
        server_clock_offset_ms=session.server_clock_offset_ms,
    )

    ranked = rank_candidates(cfg, target_date, candidate_starts=candidate_starts)
    candidates = [c for _, c in ranked if c.facility_id == facility.id]
    log.info("racer.prewarm.candidates", count=len(candidates))

    return RacerContext(
        facility=facility,
        target_date=target_date,
        client=client,
        session=session,
        candidates=candidates,
    )


async def refresh_csrf(ctx: RacerContext) -> None:
    """Re-fetch the bookings page to grab a fresh CSRF token. Called T-5s before fire."""
    new_session = await hydrate(ctx.client, ctx.facility)
    ctx.session.csrf_token = new_session.csrf_token
    ctx.session.server_clock_offset_ms = new_session.server_clock_offset_ms


async def keepalive(ctx: RacerContext, interval: float = 5.0, stop_after: float = 30.0) -> None:
    """Periodic no-op GETs to prevent idle connection close in last 30s before fire."""
    elapsed = 0.0
    while elapsed < stop_after:
        await asyncio.sleep(interval)
        elapsed += interval
        try:
            await ctx.client.head(
                f"/Online/Reservations/Bookings/{ctx.facility.org_id}",
                follow_redirects=False,
            )
        except Exception:
            pass
