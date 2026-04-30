from datetime import date, time

from seattle_courtbot.ancapi.parsing import AvailabilityRange
from seattle_courtbot.watcher.converter import ranges_to_slices


def test_one_open_range_yields_30min_slices() -> None:
    r = AvailabilityRange(
        resource_id=1146, date="2026-05-06",
        start_time="18:00:00", end_time="21:00:00", available=True,
    )
    slices = ranges_to_slices("alki", [r])
    starts = sorted(s.start_minutes for s in slices)
    # 6:00, 6:30, 7:00, 7:30, 8:00, 8:30 (six 30-min slices spanning 18:00-21:00)
    assert starts == [18*60, 18*60+30, 19*60, 19*60+30, 20*60, 20*60+30]
    assert all(s.court_id == 1146 for s in slices)


def test_unavailable_range_yields_no_slices() -> None:
    r = AvailabilityRange(
        resource_id=1, date="2026-05-06", start_time="18:00:00",
        end_time="21:00:00", available=False,
    )
    assert ranges_to_slices("alki", [r]) == set()


def test_window_clipping() -> None:
    # Wide-open day, but the window is 6-9 PM only
    r = AvailabilityRange(
        resource_id=1, date="2026-05-06",
        start_time="08:30:00", end_time="23:00:00", available=True,
    )
    slices = ranges_to_slices(
        "alki", [r],
        window_start=time(18, 0), window_end=time(21, 0),
    )
    starts = sorted(s.start_minutes for s in slices)
    assert starts == [18*60, 18*60+30, 19*60, 19*60+30, 20*60, 20*60+30]


def test_misaligned_start_snaps_to_30min_boundary() -> None:
    # ANC sometimes reports half-hour starts (e.g., 8:30); a 18:15 start would
    # need snapping. Confirm we don't emit a slice that starts on a non-boundary.
    r = AvailabilityRange(
        resource_id=1, date="2026-05-06",
        start_time="18:15:00", end_time="20:15:00", available=True,
    )
    slices = ranges_to_slices("alki", [r])
    # Snapped to 18:30 → [18:30, 19:00, 19:30] (each + 30 ≤ 20:15)
    starts = sorted(s.start_minutes for s in slices)
    assert starts == [18*60+30, 19*60, 19*60+30]


def test_two_ranges_with_gap() -> None:
    """Day with a 6-9 PM booking carved out → two ranges."""
    rs = [
        AvailabilityRange(resource_id=1, date="2026-05-06",
                          start_time="08:30:00", end_time="18:00:00", available=True),
        AvailabilityRange(resource_id=1, date="2026-05-06",
                          start_time="21:00:00", end_time="23:00:00", available=True),
    ]
    slices = ranges_to_slices("alki", rs,
                              window_start=time(18, 0), window_end=time(22, 0))
    starts = sorted(s.start_minutes for s in slices)
    # First range ends at 18:00 (out of window), second starts at 21:00 → 21:00, 21:30
    assert starts == [21*60, 21*60+30]
