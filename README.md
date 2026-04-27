# courtbot

Tennis court reservation bot for **CourtReserve** (Lifetime Activities — Santa Clara, Sunnyvale, etc.).

Two operating modes running side-by-side on a macOS laptop:

1. **Racer** — wakes the machine and fires at the exact instant the booking window opens, claims preferred slots in sub-second time.
2. **Watcher** — long-running daemon polling for cancellations matching your preferences.

Plus a local **web dashboard** (loopback only) for status, bookings, logs, and one-off actions.

## Status

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Project skeleton + login + `discover` | ✅ verified live against Lifetime Santa Clara |
| 2 | Manual one-off booking + ledger | ✅ end-to-end dry-run verified; POST not yet live-tested |
| 3 | Standing racer + launchd | ✅ unit-tested; awaits live booking-window test |
| 4 | Cancellation watcher | ✅ unit-tested |
| 5 | Web dashboard | ✅ FastAPI app at `127.0.0.1:8787` smoke-tested |

### Verified live findings (2026-04-27)

- Login uses a Lifetime-branded Ant Design page; selectors `input[name="email"]` / `input[name="password"]` / `button[data-testid="Continue"]`.
- Schedule data: `GET /Online/Reservations/ReadConsolidated/{orgId}` with a `jsonData` URL-encoded JSON blob that includes `KendoDate`, `CostTypeId`, `CustomSchedulerId`, `ReservationMinInterval`.
- Booking is **two-step**: (a) `GET /Online/Reservations/CreateReservation/{orgId}?…` returns a wrapper that contains a `fixUrl(...)` pointing at (b) `https://reservations.courtreserve.com/Online/ReservationsApi/CreateReservation?…` — that inner page renders the actual form with a fresh CSRF token + per-modal `RequestData` + `ReservationLotteryGuid`.
- Booking POST goes to `https://reservations.courtreserve.com//Online/ReservationsApi/CreateReservation/{orgId}?uiCulture=en-US` (the double-slash is in their actual HTML).
- At Lifetime Santa Clara: `IsCourtRequired=False`, `CanSelectCourt=False` — the **server picks the court**; we can't pre-select. Court whitelist therefore only filters which slots count as "matching" after-the-fact.
- Discovered values for Santa Clara: `member_id=8462028`, `cost_type_id=141172`, 7 hard courts (IDs `52096-52103` with a gap at 52100), reservation type `69711` ("Recreational Play - Tennis").

See `~/.claude/plans/yes-i-have-an-bright-gem.md` for the full plan.

## Setup

Requires Python 3.12+ (uses 3.13 here).

```bash
cd /Users/thomaschow/code/court-bot
python3.13 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/playwright install chromium
cp config/config.example.yaml config/config.yaml
```

### Credentials

Preferred — macOS Keychain:

```bash
.venv/bin/keyring set courtbot santa-clara:username
.venv/bin/keyring set courtbot santa-clara:password
```

Or fallback — environment variables (see `.env.example`):

```bash
export COURTBOT_SANTA_CLARA_USERNAME=...
export COURTBOT_SANTA_CLARA_PASSWORD=...
```

## CLI

```bash
.venv/bin/courtbot login    --facility santa-clara          # one-time, persists session
.venv/bin/courtbot discover --facility santa-clara          # member id, courts, types
.venv/bin/courtbot validate-config                          # sanity check config.yaml
.venv/bin/courtbot book     --facility ... --date ... --start ... --court ...   # phase 2
.venv/bin/courtbot race     --facility ... [--once]         # phase 3
.venv/bin/courtbot watch                                    # phase 4 daemon
.venv/bin/courtbot web                                      # phase 5 dashboard
```

## Layout

```
config/      user-edited config.yaml + example
state/       session cookies, sqlite ledger, logs (gitignored)
launchd/     plist templates
scripts/     install / wake helpers
src/courtbot/
  cli.py             Typer entry points
  config.py          Pydantic schema + load/save
  auth/              Playwright login, session, CSRF
  courtreserve/      endpoints, payloads, parsing, errors
  discover/          probe.py — member/courts/types/rules
  booking/           shared book() + ledger
  racer/             prewarm + sub-second runner
  watcher/           polling daemon
  web/               FastAPI + Jinja + HTMX dashboard
tests/
```

## Notes / risks

- CourtReserve ToS likely prohibits automation. Use a single account, sane rate limits, no proxies. Account suspension is the primary risk.
- Captcha on login is not handled automatically — re-run with `--headful` and complete it manually; the saved `storage_state.json` is then reused.
- `pmset schedule wake` requires `sudo`; the install script will prompt to add a NOPASSWD sudoers rule for that one command.
- Bookings are real money/spots — there is a `--dry-run` flag throughout. Do not run untested code at a live booking-window moment.
