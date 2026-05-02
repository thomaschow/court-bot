"""Daily booking digest.

Formats today's confirmed bookings (across all facilities) into a short text
message and sends it via macOS Messages.app. Designed for a 9 AM PT
launchd-scheduled fire.

Send path uses AppleScript via `osascript`:
  tell application "Messages"
      send "<text>" to buddy "<phone>" of (1st service whose service type = iMessage)

If iMessage isn't available for that buddy, AppleScript will silently no-op.
The user can enable "Text Message Forwarding" on their iPhone → Mac to route
SMS through the same buddy, in which case Messages picks the SMS service
automatically when iMessage isn't an option.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from bay_area_courtbot.ledger import BookingRecord, list_confirmed_on_date
from bay_area_courtbot.logging import get_logger

LOCAL = ZoneInfo("America/Los_Angeles")


@dataclass(frozen=True)
class DigestResult:
    date_str: str
    bookings: list[BookingRecord]
    message: str
    sent: bool
    error: str | None = None


def _format_message(date_str: str, bookings: list[BookingRecord]) -> str:
    weekday = datetime.fromisoformat(date_str).strftime("%a")
    if not bookings:
        return f"Court bot ({weekday} {date_str}): no bookings today."
    lines = [f"Court bot ({weekday} {date_str}) — {len(bookings)} booking(s):"]
    for b in bookings:
        try:
            start = datetime.strptime(b.start_time, "%H:%M").strftime("%-I:%M %p")
        except ValueError:
            start = b.start_time
        end_min = (
            datetime.strptime(b.start_time, "%H:%M").hour * 60
            + datetime.strptime(b.start_time, "%H:%M").minute
            + (b.duration_minutes or 0)
        )
        end_h, end_m = divmod(end_min, 60)
        end = datetime(2000, 1, 1, end_h % 24, end_m).strftime("%-I:%M %p")
        lines.append(
            f"• {start}–{end} {b.facility} ct{b.court_id} #{b.confirmation_id or '?'}"
        )
    return "\n".join(lines)


def _send_via_messages(phone: str, text: str) -> tuple[bool, str | None]:
    """Send via macOS Messages.app. Returns (ok, error)."""
    log = get_logger(mode="digest")
    if not shutil.which("osascript"):
        return False, "osascript not on PATH (not on macOS?)"
    # Escape backslashes + double-quotes for AppleScript.
    safe_text = text.replace("\\", "\\\\").replace('"', '\\"')
    script = (
        'tell application "Messages"\n'
        '  set targetService to 1st service whose service type = iMessage\n'
        f'  set targetBuddy to buddy "{phone}" of targetService\n'
        f'  send "{safe_text}" to targetBuddy\n'
        'end tell'
    )
    try:
        res = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        return False, "osascript timed out"
    if res.returncode != 0:
        log.warning("digest.send.failed", stderr=res.stderr.strip())
        return False, res.stderr.strip() or f"osascript exit {res.returncode}"
    return True, None


def build_and_send(
    phone: str | None,
    *,
    dry_run: bool = False,
    target_date: str | None = None,
) -> DigestResult:
    """Build today's digest text and (unless dry_run) send via Messages.

    `target_date` defaults to today in America/Los_Angeles. `phone` may be
    None for a dry-run that just prints the message.
    """
    log = get_logger(mode="digest")
    date_str = target_date or datetime.now(LOCAL).date().isoformat()
    bookings = list_confirmed_on_date(date=date_str)
    text = _format_message(date_str, bookings)
    log.info("digest.built", date=date_str, count=len(bookings))
    if dry_run:
        return DigestResult(date_str=date_str, bookings=bookings, message=text,
                             sent=False)
    if not phone:
        return DigestResult(date_str=date_str, bookings=bookings, message=text,
                             sent=False, error="no phone configured")
    ok, err = _send_via_messages(phone, text)
    return DigestResult(date_str=date_str, bookings=bookings, message=text,
                         sent=ok, error=err)
