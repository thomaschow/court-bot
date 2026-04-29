from __future__ import annotations


class AncError(RuntimeError):
    pass


class AuthExpired(AncError):
    pass


class RateLimited(AncError):
    pass


class SlotTaken(AncError):
    pass


class WindowNotOpen(AncError):
    pass


class ApiResponseError(AncError):
    """ANC returned a non-success response_code in the JSON envelope."""

    def __init__(self, code: str, message: str, raw: dict | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.raw = raw
