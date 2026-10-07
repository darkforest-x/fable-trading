"""Loopback API for the Discord copier; the operator UI lives in the unified workbench.

Migrated from ~/discord-okx-copier (2026-10-07). The old service answered every
origin (``CORSMiddleware(allow_origins=["*"])``) and a cloudflared quick tunnel
published port 8080, so any web page the owner visited -- or anyone holding the
tunnel URL -- could submit a fake signal or switch the copier to live trading.
This entry point therefore:

* accepts only loopback Host headers (DNS-rebinding guard, as in yoyo.monitor);
* rejects state-changing requests that carry a browser Origin other than the
  Chrome extension or this service itself (the workbench proxy sends the latter);
* serves no admin SPA. ``/`` points at the workbench; ``/capture/orders-card`` is a
  small static page that ``admin_snapshot.capture_orders_card`` screenshots for
  Telegram.

Starlette TrustedHostMiddleware: https://www.starlette.io/middleware/#trustedhostmiddleware
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from yoyo.copier.api.auth import auth_enabled
from yoyo.copier.api.deps import init_deps
from yoyo.copier.api.routes import api_router
from yoyo.copier.config import app_config
from yoyo.copier.store.sqlite import Database

STATIC = Path(__file__).resolve().parent.parent / "static"
WORKBENCH_URL = "http://127.0.0.1:8766/#copier"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
LOOPBACK_HOSTS = ["127.0.0.1", "localhost", "testserver"]


def allowed_origin(origin: str | None, self_origins: set[str]) -> bool:
    """No Origin (curl, scripts), the extension, or this service itself."""
    if not origin:
        return True
    return origin.startswith("chrome-extension://") or origin in self_origins


def create_app(db: Database | None = None) -> FastAPI:
    database = db or Database()
    init_deps(database)
    app = FastAPI(title="Discord Trade Copier", version="0.3.0", docs_url=None, redoc_url=None)
    app.state.db = database
    port = app_config.admin.port
    self_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    @app.middleware("http")
    async def origin_guard(request: Request, call_next):
        if request.method not in SAFE_METHODS and not allowed_origin(request.headers.get("origin"), self_origins):
            return JSONResponse({"detail": "跨站写请求已拒绝；请从统一工作台或浏览器扩展提交。"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    # Added last so it runs first: a foreign Host never reaches the origin check.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOOPBACK_HOSTS)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "service": "discord-copier", "require_auth": auth_enabled()}

    app.include_router(api_router)

    @app.get("/")
    def index():
        return RedirectResponse(WORKBENCH_URL, status_code=302)

    @app.get("/capture/orders-card")
    def orders_card_capture():
        return FileResponse(STATIC / "capture.html", headers={"Cache-Control": "no-cache"})

    return app
