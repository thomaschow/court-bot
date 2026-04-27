from __future__ import annotations

import json
from datetime import date as ddate, datetime, time as dtime, timedelta
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from courtbot import ledger as L
from courtbot.config import Config, load_config
from courtbot.paths import config_path, log_path, project_root
from courtbot.timeutil import next_window_open

WEB_DIR = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))


def create_app(config_file: Path | None = None) -> FastAPI:
    app = FastAPI(title="courtbot dashboard")
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

    def cfg() -> Config:
        return load_config(config_file or config_path())

    @app.get("/", response_class=HTMLResponse)
    async def status_page(request: Request) -> HTMLResponse:
        c = cfg()
        cards = []
        now = datetime.now()
        for f in c.facilities:
            opens = next_window_open(
                days_ahead=f.booking_window.days_ahead,
                opens_at_local=f.booking_window.opens_at_local,
                tz=c.defaults.timezone,
            )
            cards.append({
                "facility": f,
                "next_open": opens,
                "next_open_in": (opens - now.astimezone(opens.tzinfo)).total_seconds(),
                "session_path": f"state/session/{f.org_id}.json",
                "session_exists": (project_root() / "state" / "session" / f"{f.org_id}.json").exists(),
            })
        return TEMPLATES.TemplateResponse(
            request,
            "status.html",
            {"cfg": c, "cards": cards},
        )

    @app.get("/bookings", response_class=HTMLResponse)
    async def bookings_page(request: Request) -> HTMLResponse:
        rows = L.list_recent(limit=200)
        return TEMPLATES.TemplateResponse(request, "bookings.html", {"rows": rows})

    @app.get("/logs", response_class=HTMLResponse)
    async def logs_page(request: Request) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(request, "logs.html", {})

    @app.get("/logs/tail")
    async def logs_tail(
        n: int = 100,
        facility: str | None = None,
        mode: str | None = None,
        level: str | None = None,
    ) -> HTMLResponse:
        path = log_path()
        if not path.exists():
            return HTMLResponse("<em>no logs yet</em>")
        lines = path.read_text().splitlines()[-2000:]
        items: list[dict] = []
        for line in lines:
            try:
                ev = json.loads(line)
            except (TypeError, ValueError):
                continue
            if facility and ev.get("facility") != facility:
                continue
            if mode and ev.get("mode") != mode:
                continue
            if level and ev.get("level", "").lower() != level.lower():
                continue
            items.append(ev)
        items = items[-n:]
        return TEMPLATES.TemplateResponse(
            "_log_tail.html",
            {"request": None, "items": items},
        )

    @app.get("/config", response_class=HTMLResponse)
    async def config_page(request: Request) -> HTMLResponse:
        path = config_file or config_path()
        return TEMPLATES.TemplateResponse(
            request,
            "config.html",
            {"raw": path.read_text(), "path": str(path)},
        )

    @app.post("/config")
    async def config_save(yaml_text: str = Form(...)) -> RedirectResponse:
        # Validate before writing — never persist invalid config.
        import yaml as _yaml

        from courtbot.config import save_config

        try:
            data = _yaml.safe_load(yaml_text)
            new_cfg = Config.model_validate(data)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid config: {exc}")
        save_config(new_cfg, config_file or config_path())
        return RedirectResponse(url="/config", status_code=303)

    @app.post("/actions/dry-run-race")
    async def dry_run_race(facility: str = Form(...), date_str: str = Form(...)) -> dict:
        from courtbot.racer.prewarm import prewarm
        from courtbot.racer.runner import run_burst

        c = cfg()
        f = c.facility(facility)
        td = ddate.fromisoformat(date_str)
        starts = [dtime(h, m) for h in range(6, 23) for m in (0, 30)]
        ctx = await prewarm(c, f, td, candidate_starts=starts)
        try:
            fire = datetime.now(opens := next_window_open(
                days_ahead=f.booking_window.days_ahead,
                opens_at_local=f.booking_window.opens_at_local,
                tz=c.defaults.timezone,
            ).tzinfo) + timedelta(milliseconds=200)
            result = await run_burst(c, ctx, fire_at_utc=fire, dry_run=True, max_window_seconds=2.0)
        finally:
            await ctx.aclose()
        return {
            "success": result.success,
            "attempts": result.attempts,
            "elapsed_ms": result.elapsed_ms,
            "candidate_index": result.candidate_index,
        }

    return app


def serve(host: str = "127.0.0.1", port: int = 8787) -> None:
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port, log_level="info")
