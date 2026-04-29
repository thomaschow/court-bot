from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from bay_area_courtbot import logging as log_setup
from bay_area_courtbot.config import Config, load_config
from bay_area_courtbot.paths import config_path

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Tennis court booking bot.")
console = Console()


def _load(cfg_path: Path | None) -> Config:
    return load_config(cfg_path or config_path())


@app.callback()
def _root(
    log_level: str = typer.Option("INFO", "--log-level", help="Log level"),
) -> None:
    log_setup.configure(level=log_level)


@app.command()
def login(
    facility: str = typer.Option(..., "--facility", "-f", help="Facility id from config"),
    headful: bool = typer.Option(False, "--headful", help="Show browser for debugging"),
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Log in via Playwright and persist session cookies."""
    from bay_area_courtbot.auth.playwright_login import login as do_login

    cfg = _load(config)
    f = cfg.facility(facility)
    out = asyncio.run(do_login(cfg, f, headful=headful))
    console.print(f"[green]Logged in.[/green] storage_state -> {out}")


@app.command()
def discover(
    facility: str = typer.Option(..., "--facility", "-f"),
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Discover member id, courts, reservation types for a facility."""
    from bay_area_courtbot.discover.probe import discover as do_discover

    cfg = _load(config)
    f = cfg.facility(facility)
    res = asyncio.run(do_discover(cfg, f))

    t = Table(title=f"Discover: {f.id} (org {f.org_id})")
    t.add_column("Field")
    t.add_column("Value")
    t.add_row("member_id", str(res.member_id))
    t.add_row("membership_id", str(res.membership_id))
    t.add_row("courts", ", ".join(f"{c.id}:{c.name}" for c in res.courts) or "(none)")
    t.add_row(
        "reservation_types",
        ", ".join(f"{k}={v}" for k, v in res.reservation_type_ids.items()) or "(none)",
    )
    console.print(t)
    if res.rules_text:
        console.print("\n[bold]Rules excerpt:[/bold]")
        console.print(res.rules_text[:600])
    console.print("\n[bold]YAML snippet (paste into config under facilities):[/bold]")
    console.print(res.to_yaml_snippet())


@app.command("validate-config")
def validate_config(
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Load and validate the config file."""
    cfg = _load(config)
    console.print(
        f"[green]OK[/green] - {len(cfg.facilities)} facility, "
        f"{len(cfg.preferences.rules)} preference rule(s)"
    )


@app.command()
def book(
    facility: str = typer.Option(..., "--facility", "-f"),
    date: str = typer.Option(..., "--date", help="YYYY-MM-DD"),
    start: str = typer.Option(..., "--start", help="HH:MM local"),
    duration: int = typer.Option(60, "--duration"),
    court: int = typer.Option(..., "--court", help="Court ID from `discover`"),
    reservation_type: int | None = typer.Option(
        None, "--reservation-type", help="Override config reservation_type_id"
    ),
    dry_run: bool = typer.Option(False, "--dry-run"),
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Manually book a single slot."""
    from datetime import date as ddate, datetime, time as dtime

    from bay_area_courtbot.booking.service import book as do_book
    from bay_area_courtbot.courtreserve.payloads import BookingCandidate

    cfg = _load(config)
    f = cfg.facility(facility)
    if f.member_id is None:
        raise typer.Exit("facility.member_id is null — run `bay_area_courtbot discover` first")
    rtype = reservation_type or f.reservation_type_id
    if rtype is None:
        raise typer.Exit("no reservation_type_id in config and none passed via --reservation-type")

    cand = BookingCandidate(
        facility_id=f.id,
        org_id=f.org_id,
        member_id=f.member_id,
        membership_id=f.membership_id,
        reservation_type_id=rtype,
        court_id=court,
        date=ddate.fromisoformat(date),
        start=datetime.strptime(start, "%H:%M").time() if len(start) == 5 else dtime.fromisoformat(start),
        duration_minutes=duration,
    )
    result = asyncio.run(do_book(cfg, f, cand, mode="manual", dry_run=dry_run))
    if result.status == "confirmed":
        console.print(f"[green]Booked[/green] - confirmation #{result.confirmation_id}")
    elif result.status == "dry_run":
        console.print("[yellow]Dry-run[/yellow] - no booking submitted (ledger updated)")
    elif result.status == "duplicate":
        console.print(f"[yellow]Skipped[/yellow] - already in ledger ({result.error})")
    else:
        console.print(f"[red]Failed[/red] - {result.error}")
        raise typer.Exit(code=1)


@app.command()
def race(
    facility: str = typer.Option(..., "--facility", "-f"),
    target_date: str | None = typer.Option(None, "--date", help="YYYY-MM-DD; default = today + days_ahead"),
    once: bool = typer.Option(True, "--once/--daemon"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Race the booking-window-open moment for a facility."""
    from datetime import date as ddate, datetime, time as dtime, timedelta

    from bay_area_courtbot.racer.prewarm import prewarm
    from bay_area_courtbot.racer.runner import run_burst
    from bay_area_courtbot.timeutil import next_window_open

    cfg = _load(config)
    f = cfg.facility(facility)

    fire_at = next_window_open(
        days_ahead=f.booking_window.days_ahead,
        opens_at_local=f.booking_window.opens_at_local,
        tz=cfg.defaults.timezone,
    )
    if target_date:
        td = ddate.fromisoformat(target_date)
    else:
        td = fire_at.date() + timedelta(days=f.booking_window.days_ahead)

    # Build a coarse 30-minute candidate grid covering 6am - 11pm local; the strategy
    # filter narrows it to preference rules.
    starts = [dtime(h, m) for h in range(6, 23) for m in (0, 30)]

    async def _run():
        ctx = await prewarm(cfg, f, td, candidate_starts=starts)
        try:
            return await run_burst(cfg, ctx, fire_at_utc=fire_at, dry_run=dry_run)
        finally:
            await ctx.aclose()

    result = asyncio.run(_run())
    if result.success:
        console.print(
            f"[green]Booked[/green] candidate #{result.candidate_index} "
            f"in {result.elapsed_ms}ms (#{result.confirmation_id})"
        )
    else:
        console.print(
            f"[red]Race failed[/red] after {result.attempts} attempt(s) "
            f"in {result.elapsed_ms}ms - last error: {result.last_error}"
        )
        raise typer.Exit(code=1)


@app.command("schedule-wake")
def schedule_wake(
    config: Path | None = typer.Option(None, "--config", "-c"),
    skip_pmset: bool = typer.Option(False, "--skip-pmset", help="Render plists but don't run pmset"),
) -> None:
    """Render per-facility racer plists and (best-effort) schedule pmset wake events."""
    from bay_area_courtbot.scheduling import (
        fire_at_for_launchd,
        install_racer_for_facility,
        plan_next_firings,
        schedule_pmset_wake,
    )

    cfg = _load(config)
    cfg_path_resolved = (config or config_path()).resolve()
    bin_path = (Path(__file__).resolve().parents[3] / ".venv" / "bin" / "bay_area_courtbot")
    if not bin_path.exists():
        bin_path = Path("bay_area_courtbot")

    plan = plan_next_firings(cfg)
    if not plan:
        console.print("No upcoming racer firings within the next 24h.")
        return

    for facility, opens_at in plan:
        fire_at = fire_at_for_launchd(opens_at)
        path = install_racer_for_facility(
            facility,
            fire_at_local=fire_at,
            courtbot_bin=str(bin_path),
            config_path=str(cfg_path_resolved),
        )
        console.print(
            f"[green]Scheduled racer[/green] {facility.id} "
            f"opens={opens_at.isoformat()} fire={fire_at.isoformat()} -> {path}"
        )
        if not skip_pmset:
            try:
                schedule_pmset_wake(opens_at - timedelta(seconds=120))
                console.print(f"[green]Wake scheduled[/green] for {opens_at.isoformat()}")
            except RuntimeError as exc:
                console.print(f"[yellow]Wake skipped[/yellow]: {exc}")


@app.command()
def watch(
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Long-running cancellation watcher: polls per-facility for new openings."""
    from bay_area_courtbot.watcher.poller import run_watcher

    cfg = _load(config)
    asyncio.run(run_watcher(cfg))


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8787, "--port"),
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Start the local dashboard at http://127.0.0.1:8787/"""
    from bay_area_courtbot.web.app import serve

    if host not in ("127.0.0.1", "localhost", "::1"):
        console.print(
            "[yellow]Warning[/yellow]: binding to non-loopback host. The dashboard "
            "has no auth — do not expose without a reverse proxy + auth."
        )
    serve(host=host, port=port)


if __name__ == "__main__":
    app()
