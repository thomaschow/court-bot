from __future__ import annotations

import json
from datetime import date as ddate, datetime, time as dtime, timedelta
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from bay_area_courtbot import ledger as L
from bay_area_courtbot.timeutil import next_window_open
from bay_area_courtbot.web.areas import DEFAULT_AREA, Area, all_areas, get_area

WEB_DIR = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))


def _from_json(s):
    if not s:
        return []
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return []


TEMPLATES.env.filters["fromjson"] = _from_json


def _load_area_config(area: Area):
    """Best-effort area-config loader. The Bay Area is the only area with a fully
    wired Config schema today; Seattle's config will land with phase 1 of the
    seattle_courtbot package. Until then, return None and let the templates render
    a "not yet configured" state."""
    if not area.config_path.exists():
        return None
    if area.facility_module == "bay_area_courtbot":
        from bay_area_courtbot.config import load_config
        try:
            return load_config(area.config_path)
        except Exception:
            return None
    if area.facility_module == "seattle_courtbot":
        try:
            from seattle_courtbot.config import load_config  # type: ignore
            return load_config(area.config_path)
        except Exception:
            return None
    return None


def _common_ctx(area: Area) -> dict:
    return {
        "area": area,
        "areas": all_areas(),
    }


def create_app(config_file: Path | None = None) -> FastAPI:
    """Create the dashboard FastAPI app. `config_file` overrides the Bay Area's
    config_path everywhere — both for the legacy un-prefixed routes and for the
    area-prefixed `/areas/bay-area/...` routes — so tests that pass a tmp-path get
    consistent behaviour across URL structures."""
    app = FastAPI(title="courtbot dashboard")
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

    def _resolve_area(area_id: str) -> Area:
        a = get_area(area_id)
        if config_file is not None and a.id == "bay-area":
            return Area(
                id=a.id, label=a.label,
                config_path=config_file,
                ledger_path=a.ledger_path,
                log_path=a.log_path,
                facility_module=a.facility_module,
            )
        return a

    # ---------------------------------------------------------------------------
    # Top-level redirects to the default area, plus legacy backwards-compat routes.
    # ---------------------------------------------------------------------------
    @app.get("/")
    async def root() -> RedirectResponse:
        return RedirectResponse(url=f"/areas/{DEFAULT_AREA}/", status_code=302)

    # ---------------------------------------------------------------------------
    # Area-scoped routes.
    # ---------------------------------------------------------------------------
    @app.get("/areas/{area_id}/", response_class=HTMLResponse)
    async def area_status(request: Request, area_id: str) -> HTMLResponse:
        area = _resolve_area(area_id)
        cfg = _load_area_config(area)
        cards = []
        if cfg is not None:
            now = datetime.now()
            for f in cfg.facilities:
                # Bay-area facilities have a `booking_window`; Seattle facilities don't
                # (Seattle's release schedule varies per facility-type and is empirical).
                booking_window = getattr(f, "booking_window", None)
                next_open = None
                next_open_in = None
                if booking_window is not None:
                    next_open = next_window_open(
                        days_ahead=booking_window.days_ahead,
                        opens_at_local=booking_window.opens_at_local,
                        tz=cfg.defaults.timezone,
                    )
                    next_open_in = (next_open - now.astimezone(next_open.tzinfo)).total_seconds()
                org_id = getattr(f, "org_id", None) or getattr(f, "facility_id", None)
                session_file = None
                session_exists = False
                if org_id is not None:
                    session_file = f"state/session/{org_id}.json"
                    session_exists = (
                        area.config_path.parents[1] / "state" / "session" / f"{org_id}.json"
                    ).exists()
                cards.append({
                    "facility": f,
                    "next_open": next_open,
                    "next_open_in": next_open_in,
                    "session_path": session_file,
                    "session_exists": session_exists,
                })
        return TEMPLATES.TemplateResponse(
            request, "status.html",
            {**_common_ctx(area), "cfg": cfg, "cards": cards},
        )

    @app.get("/areas/{area_id}/bookings", response_class=HTMLResponse)
    async def area_bookings(request: Request, area_id: str) -> HTMLResponse:
        area = _resolve_area(area_id)
        rows = L.list_recent(limit=200, path=area.ledger_path)
        return TEMPLATES.TemplateResponse(
            request, "bookings.html",
            {**_common_ctx(area), "rows": rows},
        )

    @app.get("/areas/{area_id}/discarded", response_class=HTMLResponse)
    async def area_discarded(request: Request, area_id: str) -> HTMLResponse:
        area = _resolve_area(area_id)
        rows = L.list_discarded(limit=500, path=area.ledger_path)
        return TEMPLATES.TemplateResponse(
            request, "discarded.html",
            {**_common_ctx(area), "rows": rows},
        )

    @app.get("/areas/{area_id}/logs", response_class=HTMLResponse)
    async def area_logs(request: Request, area_id: str) -> HTMLResponse:
        area = _resolve_area(area_id)
        return TEMPLATES.TemplateResponse(
            request, "logs.html", _common_ctx(area),
        )

    @app.get("/areas/{area_id}/logs/tail")
    async def area_logs_tail(
        area_id: str,
        n: int = 100,
        facility: str | None = None,
        mode: str | None = None,
        level: str | None = None,
    ) -> HTMLResponse:
        area = _resolve_area(area_id)
        if not area.log_path.exists():
            return HTMLResponse("<em>no logs yet</em>")
        lines = area.log_path.read_text().splitlines()[-2000:]
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
            "_log_tail.html", {"request": None, "items": items},
        )

    @app.get("/areas/{area_id}/config", response_class=HTMLResponse)
    async def area_config(request: Request, area_id: str) -> HTMLResponse:
        area = _resolve_area(area_id)
        raw = area.config_path.read_text() if area.config_path.exists() else ""
        return TEMPLATES.TemplateResponse(
            request, "config.html",
            {**_common_ctx(area), "raw": raw, "path": str(area.config_path)},
        )

    @app.post("/areas/{area_id}/config")
    async def area_config_save(area_id: str, yaml_text: str = Form(...)) -> RedirectResponse:
        area = _resolve_area(area_id)
        # Validate before writing.
        import yaml as _yaml

        try:
            data = _yaml.safe_load(yaml_text)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid yaml: {exc}")
        if area.facility_module == "bay_area_courtbot":
            from bay_area_courtbot.config import Config, save_config
            try:
                new_cfg = Config.model_validate(data)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"invalid config: {exc}")
            save_config(new_cfg, area.config_path)
        else:
            # Seattle: schema not yet wired — just write the YAML straight through. Add
            # validation when seattle_courtbot.config lands.
            area.config_path.parent.mkdir(parents=True, exist_ok=True)
            area.config_path.write_text(yaml_text)
        return RedirectResponse(url=f"/areas/{area_id}/config", status_code=303)

    @app.post("/areas/{area_id}/actions/dry-run-race")
    async def area_dry_run_race(
        area_id: str, facility: str = Form(...), date_str: str = Form(...),
    ) -> dict:
        area = _resolve_area(area_id)
        if area.facility_module != "bay_area_courtbot":
            raise HTTPException(status_code=400, detail="dry-run race is only wired for bay-area")
        from bay_area_courtbot.config import load_config
        from bay_area_courtbot.racer.prewarm import prewarm
        from bay_area_courtbot.racer.runner import run_burst

        c = load_config(area.config_path)
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

    # ---------------------------------------------------------------------------
    # Backwards-compat routes used by existing tests (un-prefixed). These map to
    # the bay-area area implicitly so test_web.py and any external bookmark of /
    # /bookings /config etc. keep working.
    # ---------------------------------------------------------------------------
    def _bay_area() -> Area:
        a = get_area("bay-area")
        if config_file is not None:
            return Area(
                id=a.id, label=a.label,
                config_path=config_file,
                ledger_path=a.ledger_path,
                log_path=a.log_path,
                facility_module=a.facility_module,
            )
        return a

    # Old-style routes (kept for tests + bookmark compatibility).
    @app.get("/bookings", response_class=HTMLResponse, include_in_schema=False)
    async def _legacy_bookings(request: Request) -> HTMLResponse:
        ba = _bay_area()
        rows = L.list_recent(limit=200, path=ba.ledger_path)
        return TEMPLATES.TemplateResponse(
            request, "bookings.html",
            {**_common_ctx(ba), "rows": rows},
        )

    @app.get("/discarded", response_class=HTMLResponse, include_in_schema=False)
    async def _legacy_discarded(request: Request) -> HTMLResponse:
        ba = _bay_area()
        rows = L.list_discarded(limit=500, path=ba.ledger_path)
        return TEMPLATES.TemplateResponse(
            request, "discarded.html",
            {**_common_ctx(ba), "rows": rows},
        )

    @app.get("/config", response_class=HTMLResponse, include_in_schema=False)
    async def _legacy_config(request: Request) -> HTMLResponse:
        ba = _bay_area()
        return TEMPLATES.TemplateResponse(
            request, "config.html",
            {**_common_ctx(ba), "raw": ba.config_path.read_text(), "path": str(ba.config_path)},
        )

    @app.post("/config", include_in_schema=False)
    async def _legacy_config_save(yaml_text: str = Form(...)) -> RedirectResponse:
        import yaml as _yaml

        from bay_area_courtbot.config import Config, save_config

        try:
            data = _yaml.safe_load(yaml_text)
            new_cfg = Config.model_validate(data)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid config: {exc}")
        save_config(new_cfg, _bay_area().config_path)
        return RedirectResponse(url="/config", status_code=303)

    return app


def serve(host: str = "127.0.0.1", port: int = 8787) -> None:
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port, log_level="info")
