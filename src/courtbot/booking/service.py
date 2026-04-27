from __future__ import annotations

from dataclasses import dataclass

import httpx

from courtbot.auth.session import SessionState, build_client, hydrate
from courtbot.config import Config, Facility
from courtbot.courtreserve.client import CourtReserveClient
from courtbot.courtreserve.errors import (
    AuthExpired,
    CourtReserveError,
    RateLimited,
    SlotTaken,
    WindowNotOpen,
)
from courtbot.courtreserve.payloads import BookingCandidate
from courtbot.ledger import (
    AlreadyConfirmed,
    is_already_confirmed,
    record_attempt,
    record_confirmed,
)
from courtbot.logging import get_logger
from courtbot.notify import notify_macos


@dataclass
class BookResult:
    candidate: BookingCandidate
    confirmation_id: str | None
    status: str  # confirmed | failed | dry_run | duplicate
    error: str | None = None


def _ledger_kw(cand: BookingCandidate, mode: str) -> dict:
    return dict(
        facility=cand.facility_id,
        date=cand.date.isoformat(),
        start_time=cand.start.strftime("%H:%M"),
        duration_minutes=cand.duration_minutes,
        court_id=cand.court_id,
        mode=mode,
    )


async def book(
    cfg: Config,
    facility: Facility,
    cand: BookingCandidate,
    *,
    mode: str = "manual",
    dry_run: bool = False,
    client: httpx.AsyncClient | None = None,
    session: SessionState | None = None,
    extras: dict[str, str] | None = None,
) -> BookResult:
    """Book a single slot end-to-end.

    Returns:
        BookResult with status:
          - 'confirmed' (with confirmation_id)
          - 'dry_run' (logged + recorded as attempt, no POST)
          - 'duplicate' (already-confirmed in ledger; no POST attempted)
          - 'failed' (POST returned a terminal error like SlotTaken)

    Raises:
        RateLimited / WindowNotOpen / AuthExpired — the racer/watcher are expected to
        catch these and decide whether to retry / refresh session / abort.
    """
    log = get_logger(
        facility=facility.id,
        mode=mode,
        court=cand.court_id,
        date=cand.date.isoformat(),
        start=cand.start.isoformat(),
    )
    kw = _ledger_kw(cand, mode)

    if is_already_confirmed(
        facility=kw["facility"],
        date=kw["date"],
        start_time=kw["start_time"],
        court_id=kw["court_id"],
    ):
        log.info("booking.skip.already_confirmed")
        return BookResult(
            candidate=cand,
            confirmation_id=None,
            status="duplicate",
            error="already confirmed in ledger",
        )

    if dry_run:
        record_attempt(**kw, status="dry_run")
        log.info("booking.dry_run")
        if cfg.notifications.on_dry_run:
            notify_macos("courtbot dry-run", f"{facility.id} {cand.date} {cand.start}")
        return BookResult(candidate=cand, confirmation_id=None, status="dry_run")

    own_client = False
    if client is None:
        client = build_client(facility)
        own_client = True
    # We no longer require a CSRF token from the bookings page — the modal-fetch flow
    # mints its own per-reservation CSRF. Keeping the hydrate call only to surface
    # session expiry early and to measure server clock skew (which the racer uses).
    if session is None:
        try:
            session = await hydrate(client, facility)
        except Exception as exc:
            record_attempt(**kw, status="failed", error=f"session hydrate failed: {exc}")
            if own_client:
                await client.aclose()
            return BookResult(
                candidate=cand,
                confirmation_id=None,
                status="failed",
                error=f"session hydrate failed: {exc}",
            )

    cr = CourtReserveClient(
        client,
        org_id=facility.org_id,
        cost_type_id=facility.cost_type_id,
        custom_scheduler_id=facility.s_id,
        timezone_name=facility.timezone,
        reservation_min_interval=facility.reservation_min_interval,
    )
    try:
        confirmation = await cr.create_reservation(cand, facility=facility, extras=extras)
        try:
            record_confirmed(**kw, confirmation_id=confirmation)
        except AlreadyConfirmed:
            # Another process beat us to recording success; same slot already booked.
            log.info("booking.race_lost", confirmation_id=confirmation)
            return BookResult(
                candidate=cand,
                confirmation_id=confirmation,
                status="duplicate",
                error="ledger race lost",
            )
        log.info("booking.confirmed", confirmation_id=confirmation)
        if cfg.notifications.on_success:
            notify_macos("courtbot booked", f"{facility.id} {cand.date} {cand.start} #{confirmation}")
        return BookResult(candidate=cand, confirmation_id=confirmation, status="confirmed")
    except (RateLimited, WindowNotOpen, AuthExpired) as exc:
        record_attempt(**kw, status="failed", error=f"{type(exc).__name__}: {exc}")
        log.warning("booking.signal", error=str(exc), kind=type(exc).__name__)
        if own_client:
            await client.aclose()
        raise
    except CourtReserveError as exc:
        record_attempt(**kw, status="failed", error=f"{type(exc).__name__}: {exc}")
        log.warning("booking.failed", error=str(exc), kind=type(exc).__name__)
        if cfg.notifications.on_failure and not isinstance(exc, SlotTaken):
            notify_macos("courtbot failed", f"{facility.id} {cand.date} {cand.start}: {exc}")
        return BookResult(
            candidate=cand,
            confirmation_id=None,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        if own_client:
            await client.aclose()
