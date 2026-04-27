from __future__ import annotations

import asyncio
import random
from datetime import date as ddate, datetime, timedelta
from typing import Any

import httpx

from courtbot.auth.session import build_client, hydrate
from courtbot.booking.service import book
from courtbot.config import Config, Facility
from courtbot.courtreserve.client import CourtReserveClient
from courtbot.courtreserve.errors import AuthExpired, RateLimited
from courtbot.courtreserve.parsing import SlotView
from courtbot.courtreserve.payloads import BookingCandidate
from courtbot.logging import get_logger
from courtbot.timeutil import in_quiet_hours, local_now
from courtbot.watcher.diff import find_new_openings


class WatcherDaemon:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._snapshots: dict[str, dict[ddate, list[SlotView]]] = {}
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._intervals: dict[str, float] = {
            f.id: float(f.polling.interval_seconds) for f in cfg.facilities
        }
        self._stops: dict[str, asyncio.Event] = {}
        self._log = get_logger(mode="watcher")

    async def run(self) -> None:
        self._log.info(
            "watcher.start", facilities=[f.id for f in self.cfg.facilities]
        )
        try:
            await asyncio.gather(*(self._poll_facility(f) for f in self.cfg.facilities))
        finally:
            for c in self._clients.values():
                await c.aclose()

    async def _poll_facility(self, facility: Facility) -> None:
        log = self._log.bind(facility=facility.id)
        self._snapshots[facility.id] = {}
        consecutive_5xx = 0
        while True:
            now_local = local_now(self.cfg.defaults.timezone)
            if in_quiet_hours(now_local, facility.polling.quiet_hours_local):
                await self._sleep_jittered(60)
                continue

            try:
                openings = await self._tick_once(facility)
                consecutive_5xx = 0
                for slot in openings:
                    await self._attempt_booking(facility, slot)
                # Decay rate-limit interval back toward configured baseline.
                base = float(facility.polling.interval_seconds)
                self._intervals[facility.id] = max(
                    base, self._intervals[facility.id] * 0.9
                )
            except RateLimited as exc:
                self._intervals[facility.id] = min(
                    300.0, self._intervals[facility.id] * 2.0
                )
                log.warning(
                    "watcher.rate_limited",
                    error=str(exc),
                    new_interval=self._intervals[facility.id],
                )
            except AuthExpired:
                log.warning("watcher.auth_expired", facility=facility.id)
                # Drop client; next tick rebuilds. Real recovery requires Playwright re-login.
                if facility.id in self._clients:
                    await self._clients[facility.id].aclose()
                    del self._clients[facility.id]
                await self._sleep_jittered(120)
                continue
            except httpx.HTTPError as exc:
                log.warning("watcher.http_error", error=str(exc))
                consecutive_5xx += 1
                if consecutive_5xx >= 3:
                    log.error("watcher.circuit_break", facility=facility.id, minutes=10)
                    await self._sleep_jittered(600)
                    consecutive_5xx = 0
                    continue

            await self._sleep_jittered(self._intervals[facility.id])

    async def _client(self, facility: Facility) -> httpx.AsyncClient:
        c = self._clients.get(facility.id)
        if c is None or c.is_closed:
            c = build_client(facility)
            self._clients[facility.id] = c
        return c

    async def _tick_once(self, facility: Facility) -> list[SlotView]:
        log = self._log.bind(facility=facility.id)
        client = await self._client(facility)
        # Re-hydrate periodically to refresh CSRF + clock skew.
        await hydrate(client, facility)
        cr = CourtReserveClient(
            client,
            org_id=facility.org_id,
            cost_type_id=facility.cost_type_id,
            custom_scheduler_id=facility.s_id,
            timezone_name=facility.timezone,
            reservation_min_interval=facility.reservation_min_interval,
        )

        openings: list[SlotView] = []
        today = local_now(self.cfg.defaults.timezone).date()
        for delta in range(facility.polling.horizon_days + 1):
            day = today + timedelta(days=delta)
            curr = await cr.read_expanded(day=day)
            prev = self._snapshots[facility.id].get(day, [])
            new = find_new_openings(facility.id, prev, curr, self.cfg.preferences)
            self._snapshots[facility.id][day] = curr
            if new:
                log.info("watcher.found_opening", date=day.isoformat(), count=len(new))
                openings.extend(new)
        log.info("watcher.tick", days=facility.polling.horizon_days + 1)
        return openings

    async def _attempt_booking(self, facility: Facility, slot: SlotView) -> None:
        if facility.member_id is None or facility.reservation_type_id is None:
            return
        cand = BookingCandidate(
            facility_id=facility.id,
            org_id=facility.org_id,
            member_id=facility.member_id,
            membership_id=facility.membership_id,
            reservation_type_id=facility.reservation_type_id,
            court_id=slot.court_id,
            date=slot.start.date(),
            start=slot.start.time(),
            duration_minutes=int((slot.end - slot.start).total_seconds() // 60),
        )
        client = await self._client(facility)
        try:
            await book(self.cfg, facility, cand, mode="watcher", client=client)
        except (RateLimited, AuthExpired):
            raise
        except Exception as exc:
            self._log.warning(
                "watcher.attempt_error", facility=facility.id, error=str(exc)
            )

    async def _sleep_jittered(self, base: float) -> None:
        jitter = random.uniform(-0.2, 0.2) * base
        await asyncio.sleep(max(1.0, base + jitter))


async def run_watcher(cfg: Config) -> None:
    daemon = WatcherDaemon(cfg)
    await daemon.run()
