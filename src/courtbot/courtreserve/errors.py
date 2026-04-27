from __future__ import annotations


class CourtReserveError(RuntimeError):
    pass


class SlotTaken(CourtReserveError):
    pass


class RateLimited(CourtReserveError):
    pass


class AuthExpired(CourtReserveError):
    pass


class WindowNotOpen(CourtReserveError):
    pass
