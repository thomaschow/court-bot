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
def book() -> None:
    """Manually book a slot. (Phase 2 — not yet implemented.)"""
    raise typer.Exit("Phase 2: not yet implemented")


@app.command()
def watch() -> None:
    """Long-running cancellation watcher. (Phase 3 — not yet implemented.)"""
    raise typer.Exit("Phase 3: not yet implemented")


if __name__ == "__main__":
    app()
