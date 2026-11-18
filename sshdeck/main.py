"""Application factory and ASGI entrypoint."""
from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import api, config, db, racks, security

log = logging.getLogger("sshdeck")


def configure_logging() -> None:
    """Log to data/sshdeck.log so a failing portable install can send the file."""
    path = config.DATA_DIR / "sshdeck.log"
    if any(getattr(h, "baseFilename", "") == str(path) for h in log.handlers):
        return
    try:
        if path.exists() and path.stat().st_size > 2 * 1024 * 1024:
            path.replace(config.DATA_DIR / "sshdeck.log.1")
    except OSError:
        pass
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def create_app() -> FastAPI:
    config.ensure_dirs()
    configure_logging()
    db.init()

    app = FastAPI(title="MUXIVRA", version=__import__("sshdeck").__version__, docs_url=None, redoc_url=None)
    app.include_router(api.public)
    app.include_router(api.api)
    app.include_router(racks.router)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self' ws: wss:; font-src 'self' data:; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    if config.WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(config.WEB_DIR)), name="static")

    @app.get("/")
    async def index():
        return FileResponse(
            str(config.WEB_DIR / "index.html"),
            headers={"Cache-Control": "no-cache"},
        )

    @app.exception_handler(security.VaultError)
    async def vault_error(request: Request, exc: security.VaultError):
        log.error("vault failure on %s: %s", request.url.path, exc)
        return JSONResponse(status_code=500, content={"detail": f"vault:{exc}"})

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=500, content={"detail": f"internal:{type(exc).__name__}: {exc}"[:300]}
        )

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.websocket("/api/ws/terminal/{host_id}")
    async def ws_terminal(ws: WebSocket, host_id: int, cols: int = 120, rows: int = 32):
        try:
            await api.terminal_endpoint(ws, host_id, cols, rows)
        except Exception as exc:  # never leave the client with a bare socket close
            log.exception("terminal websocket failed for host %s", host_id)
            try:
                await ws.send_json({"type": "error", "code": "internal", "message": str(exc)})
                await ws.close(code=1011)
            except Exception:
                pass

    @app.websocket("/api/ws/echo")
    async def ws_echo(ws: WebSocket):  # pragma: no cover - test hook
        await ws.accept()
        try:
            while True:
                await ws.send_text(await ws.receive_text())
        except Exception:
            return

    return app


app = create_app()
