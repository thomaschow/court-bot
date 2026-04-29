"""Snapshot-diff helpers for the Seattle cancellation watcher.

Generic over the slice key shape — the actual SliceKey is defined where the
watcher loop lives so it can carry whatever ANC-specific identifiers turn out
to be relevant once Phase 2 maps the schedule API.
"""

from __future__ import annotations

from typing import Hashable


def find_new_keys(prev: set[Hashable], curr: set[Hashable]) -> set[Hashable]:
    """Slices that are in `curr` but were not in `prev`. The first cycle has an
    empty `prev` and returns nothing — that's the baseline establishment, which
    avoids booking everything that's already free at startup."""
    if not prev:
        return set()
    return curr - prev
