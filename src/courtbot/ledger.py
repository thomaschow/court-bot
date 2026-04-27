from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from courtbot.paths import ledger_path

# Confirmed bookings get a UNIQUE constraint to prevent double-booking the same slot.
# Failed/dry-run/pending attempts are recorded separately for diagnostics with no
# constraint — the racer makes many sub-second retries that must not be blocked.
SCHEMA = """
CREATE TABLE IF NOT EXISTS confirmed_bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    facility TEXT NOT NULL,
    date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL,
    court_id INTEGER NOT NULL,
    mode TEXT NOT NULL,
    confirmation_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(facility, date, start_time, court_id)
);
CREATE INDEX IF NOT EXISTS ix_confirmed_date ON confirmed_bookings(date);

CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    facility TEXT NOT NULL,
    date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL,
    court_id INTEGER NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_attempts_date ON attempts(date);
"""


@dataclass
class BookingRecord:
    """Unified view used by the dashboard. Holds either a confirmed or a notable attempt."""
    facility: str
    date: str
    start_time: str
    duration_minutes: int
    court_id: int
    mode: str
    status: str  # confirmed | failed | dry_run
    confirmation_id: str | None
    error: str | None
    created_at: str


@contextmanager
def _conn(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    p = path or ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p, isolation_level=None)
    c.row_factory = sqlite3.Row
    try:
        c.executescript(SCHEMA)
        yield c
    finally:
        c.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_already_confirmed(
    *, facility: str, date: str, start_time: str, court_id: int, path: Path | None = None
) -> bool:
    with _conn(path) as c:
        row = c.execute(
            "SELECT 1 FROM confirmed_bookings WHERE facility=? AND date=? AND start_time=? AND court_id=?",
            (facility, date, start_time, court_id),
        ).fetchone()
        return row is not None


def record_confirmed(
    *,
    facility: str,
    date: str,
    start_time: str,
    duration_minutes: int,
    court_id: int,
    mode: str,
    confirmation_id: str,
    path: Path | None = None,
) -> None:
    """Insert a confirmed-booking row. Raises AlreadyConfirmed if the unique key already exists."""
    with _conn(path) as c:
        try:
            c.execute(
                """
                INSERT INTO confirmed_bookings
                  (facility, date, start_time, duration_minutes, court_id, mode,
                   confirmation_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (facility, date, start_time, duration_minutes, court_id, mode,
                 confirmation_id, _now()),
            )
        except sqlite3.IntegrityError as exc:
            raise AlreadyConfirmed(
                f"already confirmed: {facility}/{date}/{start_time}/court{court_id}"
            ) from exc


def record_attempt(
    *,
    facility: str,
    date: str,
    start_time: str,
    duration_minutes: int,
    court_id: int,
    mode: str,
    status: str,
    error: str | None = None,
    path: Path | None = None,
) -> None:
    """Append an attempt row (no uniqueness constraint). Used for failed and dry-run attempts."""
    with _conn(path) as c:
        c.execute(
            """
            INSERT INTO attempts
              (facility, date, start_time, duration_minutes, court_id, mode, status, error,
               created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (facility, date, start_time, duration_minutes, court_id, mode, status,
             (error or "")[:1000], _now()),
        )


def list_recent(limit: int = 50, path: Path | None = None) -> list[BookingRecord]:
    """Return recent confirmed + notable failed attempts merged, newest first."""
    with _conn(path) as c:
        rows = c.execute(
            """
            SELECT facility, date, start_time, duration_minutes, court_id, mode,
                   'confirmed' AS status, confirmation_id, NULL AS error, created_at
              FROM confirmed_bookings
            UNION ALL
            SELECT facility, date, start_time, duration_minutes, court_id, mode,
                   status, NULL AS confirmation_id, error, created_at
              FROM attempts
              WHERE status IN ('failed','dry_run')
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [BookingRecord(**dict(r)) for r in rows]


class AlreadyConfirmed(RuntimeError):
    pass
