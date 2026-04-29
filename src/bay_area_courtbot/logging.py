from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from bay_area_courtbot.paths import log_path

_REDACT_KEYS = {
    "password",
    "passwd",
    "__requestverificationtoken",
    "cookie",
    "set-cookie",
    "authorization",
}


def _redact(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for k in list(event_dict.keys()):
        if k.lower() in _REDACT_KEYS:
            event_dict[k] = "***"
    return event_dict


def configure(level: str = "INFO") -> None:
    log_path().parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(log_path())
    file_handler.setFormatter(logging.Formatter("%(message)s"))

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(logging.Formatter("%(message)s"))

    root = logging.getLogger()
    root.handlers = [file_handler, stderr_handler]
    root.setLevel(level.upper())

    shared = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.stdlib.add_log_level,
        _redact,
    ]

    structlog.configure(
        processors=[
            *shared,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper())),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(**ctx: Any) -> structlog.stdlib.BoundLogger:
    log = structlog.get_logger()
    if ctx:
        log = log.bind(**ctx)
    return log
