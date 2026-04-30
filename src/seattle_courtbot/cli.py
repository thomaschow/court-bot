from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console

from seattle_courtbot import logging as log_setup
from seattle_courtbot.config import load_config
from seattle_courtbot.paths import config_path

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Seattle tennis booking bot.")
console = Console()


@app.callback()
def _root(log_level: str = typer.Option("INFO", "--log-level")) -> None:
    log_setup.configure(level=log_level)


@app.command()
def login(
    headful: bool = typer.Option(False, "--headful", help="Show the browser"),
    manual: bool = typer.Option(False, "--manual", help="User completes login by hand"),
) -> None:
    """Log in to Seattle ANC and persist session cookies."""
    from seattle_courtbot.auth.playwright_login import login as do_login
    out = asyncio.run(do_login(headful=headful, manual=manual))
    console.print(f"[green]Logged in.[/green] storage_state -> {out}")


@app.command()
def discover() -> None:
    """Enumerate Seattle tennis facilities + extract member ID from the session.
    Phase 1: basic — populates whatever can be parsed from the SPA shell. Richer
    discovery lands after the probe scripts capture the API surface."""
    from seattle_courtbot.discover.probe import discover as do_discover
    res = asyncio.run(do_discover())
    console.print(f"member_id: [cyan]{res.member_id}[/cyan]")
    console.print(f"facilities found: {len(res.facilities)}")
    if res.facilities:
        for f in res.facilities:
            console.print(f"  - {f.id} ({f.name})  facility_id={f.facility_id}  {len(f.courts)} courts")
    console.print("\n[bold]YAML snippet (paste into config/seattle.yaml under top-level keys):[/bold]")
    console.print(res.to_yaml_snippet())


@app.command("validate-config")
def validate_config(
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Load and validate the Seattle config file."""
    cfg = load_config(config or config_path())
    console.print(
        f"[green]OK[/green] - {len(cfg.facilities)} facility(ies); "
        f"window {cfg.preferences.time_window.start}–{cfg.preferences.time_window.end}"
    )


@app.command()
def book(
    facility: str = typer.Option(..., "--facility", "-f", help="Facility id from config"),
    court: int = typer.Option(..., "--court", help="Court resource_id"),
    date: str = typer.Option(..., "--date", help="YYYY-MM-DD"),
    start: str = typer.Option(..., "--start", help="HH:MM local"),
    duration: int = typer.Option(60, "--duration", min=60, max=180),
    attendees: int = typer.Option(2, "--attendees", min=1),
    event_name: str = typer.Option("Tennis booking", "--event-name"),
    dry_run: bool = typer.Option(True, "--dry-run/--commit",
                                  help="With --commit you WILL be charged the booking fee."),
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Book a Seattle tennis court. Defaults to --dry-run (validate + show fee, no charge)."""
    import asyncio as _asyncio
    from datetime import date as _ddate, datetime as _dt

    from seattle_courtbot.ancapi.booking import BookingRequest, book as do_book
    from seattle_courtbot.ancapi.csrf import fetch_csrf_token
    from seattle_courtbot.auth.session import build_client
    from seattle_courtbot.paths import session_path

    cfg = load_config(config or config_path())
    f = cfg.facility(facility)
    if cfg.member_id is None:
        raise typer.Exit("config.member_id is null — run `seattle-courtbot discover` first")

    req = BookingRequest(
        customer_id=cfg.member_id, resource_id=court,
        event_type_id=152,            # Tennis - Outdoor
        attendee_count=attendees,
        date=_ddate.fromisoformat(date),
        start=_dt.strptime(start, "%H:%M").time(),
        duration_minutes=duration,
        event_name=event_name,
    )

    async def _run():
        token = await fetch_csrf_token(storage_state_path=str(session_path()))
        async with build_client(http2=False) as client:
            return await do_book(client, req, csrf=token, dry_run=dry_run)

    result = _asyncio.run(_run())
    if dry_run:
        if result.success:
            console.print(f"[yellow]Dry-run OK[/yellow] — fee would be ${result.fee_total}; "
                          f"re-run with --commit to actually book.")
        else:
            console.print(f"[red]Validation failed[/red]: {result.raw}")
            raise typer.Exit(code=1)
    else:
        if result.success:
            console.print(f"[green]Booked[/green] — confirmation #{result.confirmation_id}")
        else:
            console.print(f"[red]Failed[/red]: {result.raw}")
            raise typer.Exit(code=1)


@app.command()
def watch() -> None:
    """Long-running cancellation watcher. (Phase 3 — not yet implemented.)"""
    raise typer.Exit("Phase 3: not yet implemented")


if __name__ == "__main__":
    app()
