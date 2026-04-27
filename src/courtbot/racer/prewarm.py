from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date as ddate
from urllib.parse import urlencode

import httpx

from courtbot.auth.session import SessionState, build_client, hydrate
from courtbot.config import Config, Facility
from courtbot.courtreserve.modal import ModalState, fetch_modal
from courtbot.courtreserve.payloads import BookingCandidate, build_create_reservation_form
from courtbot.logging import get_logger
from courtbot.racer.strategy import rank_candidates


@dataclass
class PrebuiltAttempt:
    candidate: BookingCandidate
    encoded_body: str  # url-encoded form body, ready to POST


@dataclass
class RacerContext:
    facility: Facility
    target_date: ddate
    client: httpx.AsyncClient
    session: SessionState
    modal: ModalState | None
    prebuilt: list[PrebuiltAttempt]
    post_url: str

    @property
    def candidates(self) -> list[BookingCandidate]:  # backwards-compat
        return [p.candidate for p in self.prebuilt]

    async def aclose(self) -> None:
        await self.client.aclose()


async def prewarm(
    cfg: Config,
    facility: Facility,
    target_date: ddate,
    *,
    candidate_starts,
    court_type_id: int = 2,
    court_type: str = "Hard",
) -> RacerContext:
    """Pre-warm: hydrate session, fetch ONE booking modal, pre-build per-court POST bodies.

    The modal mints a per-(start, end, courtType) `RequestData` token + CSRF that's
    valid for several minutes — long enough for the racer to fetch at T-30s and fire
    at T-0 with no per-attempt GET overhead. All N candidate POSTs share the same
    modal state; only `CourtId` differs between bodies.
    """
    log = get_logger(facility=facility.id, mode="racer", phase="prewarm")
    client = build_client(facility, http2=True)
    session = await hydrate(client, facility)
    log.info(
        "racer.prewarm.session_ready",
        has_csrf=session.csrf_token is not None,
        server_clock_offset_ms=session.server_clock_offset_ms,
    )

    ranked = rank_candidates(cfg, target_date, candidate_starts=candidate_starts)
    candidates = [c for _, c in ranked if c.facility_id == facility.id]
    log.info("racer.prewarm.candidates", count=len(candidates))

    modal: ModalState | None = None
    prebuilt: list[PrebuiltAttempt] = []
    post_url = ""

    if candidates:
        # All candidates with the same (date, start, duration) share one modal. Group
        # by that triple, fetch one modal per group, pre-build per-court bodies.
        first = candidates[0]
        modal = await fetch_modal(
            client, facility,
            day=first.date, start=first.start, duration_minutes=first.duration_minutes,
            court_type_id=court_type_id, court_type=court_type,
        )
        post_url = modal.inner_form_url
        log.info(
            "racer.prewarm.modal_ready",
            hidden_fields=len(modal.hidden_fields),
            post_url=post_url,
        )
        for cand in candidates:
            body = build_create_reservation_form(
                cand, csrf_token=modal.csrf_token, hidden_fields=modal.hidden_fields,
            )
            prebuilt.append(PrebuiltAttempt(candidate=cand, encoded_body=urlencode(body)))
        log.info("racer.prewarm.bodies_built", count=len(prebuilt))

    return RacerContext(
        facility=facility,
        target_date=target_date,
        client=client,
        session=session,
        modal=modal,
        prebuilt=prebuilt,
        post_url=post_url,
    )


async def refresh_csrf(ctx: RacerContext) -> None:
    """Re-fetch the bookings page to refresh CSRF + clock skew (best-effort)."""
    new_session = await hydrate(ctx.client, ctx.facility)
    ctx.session.csrf_token = new_session.csrf_token
    ctx.session.server_clock_offset_ms = new_session.server_clock_offset_ms


async def keepalive(ctx: RacerContext, interval: float = 5.0, stop_after: float = 30.0) -> None:
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
