from __future__ import annotations


class CourtReserveError(RuntimeError):
    pass


class SlotTaken(CourtReserveError):
    """The specific (court, time) slot we asked for is taken — try another court."""


class AllCourtsTaken(CourtReserveError):
    """All courts of the requested type at the requested time are gone — stop trying."""


class RateLimited(CourtReserveError):
    pass


class AuthExpired(CourtReserveError):
    pass


class WindowNotOpen(CourtReserveError):
    pass
