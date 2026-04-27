from __future__ import annotations

from dataclasses import dataclass
from datetime import date as ddate, datetime, time as dtime
from typing import Iterable

from courtbot.config import Config, Facility, PreferenceRule
from courtbot.courtreserve.payloads import BookingCandidate


@dataclass(frozen=True)
class CandidateKey:
    """Sort key for ranking candidates. Lower tuple = higher priority."""
    facility_rank: int
    day_match: int  # 0 if matches preferred days, else 1
    time_offset_min: int  # minutes from preferred window start (smaller = better)
    court_rank: int  # 0 if in whitelist, else 1
    duration_diff: int  # |actual - preferred|


def _weekday_abbr(d: ddate) -> str:
    return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d.weekday()]


def _matches_rule(rule: PreferenceRule, target_date: ddate, start: dtime) -> bool:
    if _weekday_abbr(target_date) not in rule.day_of_week:
        return False
    return any(w.start <= start < w.end for w in rule.time_windows)


def rank_candidates(
    cfg: Config,
    target_date: ddate,
    *,
    candidate_starts: Iterable[dtime],
    durations: Iterable[int] = (60,),
) -> list[tuple[CandidateKey, BookingCandidate]]:
    """Generate and rank booking candidates across all facilities.

    `candidate_starts` is the set of slot start times to consider (from a ReadExpanded
    snapshot or from a fixed grid). The returned list is sorted best-first.
    """
    rank_map = {fid: i for i, fid in enumerate(cfg.preferences.facility_rank)}
    out: list[tuple[CandidateKey, BookingCandidate]] = []

    for facility in cfg.facilities:
        if facility.member_id is None or facility.reservation_type_id is None:
            continue
        f_rank = rank_map.get(facility.id, len(rank_map))
        for rule in cfg.preferences.rules:
            for start in candidate_starts:
                if not _matches_rule(rule, target_date, start):
                    continue
                pref_start = min(w.start for w in rule.time_windows if w.start <= start)
                offset_min = (
                    datetime.combine(target_date, start) - datetime.combine(target_date, pref_start)
                ).total_seconds() // 60
                for duration in durations:
                    for court in (facility.courts or [_FakeCourt(0)]):
                        court_in_wl = (
                            not rule.court_whitelist or court.id in rule.court_whitelist
                        )
                        if rule.court_whitelist and not court_in_wl:
                            continue
                        key = CandidateKey(
                            facility_rank=f_rank,
                            day_match=0,
                            time_offset_min=int(offset_min),
                            court_rank=0 if court_in_wl else 1,
                            duration_diff=abs(duration - rule.duration_minutes),
                        )
                        cand = BookingCandidate(
                            facility_id=facility.id,
                            org_id=facility.org_id,
                            member_id=facility.member_id,
                            membership_id=facility.membership_id,
                            reservation_type_id=facility.reservation_type_id,
                            court_id=court.id,
                            date=target_date,
                            start=start,
                            duration_minutes=duration,
                        )
                        out.append((key, cand))

    out.sort(key=lambda kc: tuple(kc[0].__dict__.values()))
    return out


@dataclass(frozen=True)
class _FakeCourt:
    """Fallback when a facility has no discovered courts yet (any-court attempt)."""
    id: int


def first_available(
    ranked: list[tuple[CandidateKey, BookingCandidate]],
    available_keys: set[tuple[str, int, ddate, dtime]] | None = None,
) -> BookingCandidate | None:
    """Return the highest-ranked candidate that is currently available, or None."""
    for _, cand in ranked:
        if available_keys is None:
            return cand
        key = (cand.facility_id, cand.court_id, cand.date, cand.start)
        if key in available_keys:
            return cand
    return None
