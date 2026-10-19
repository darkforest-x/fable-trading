"""Independent Pro frontend, sharing the existing monitor's APIs and workers.

The upstream is a fixed loopback service, never supplied by a browser. Mutations
retain the original API's headers and confirmation contracts. Pro-only reading
state is versioned in a separate SQLite file; it cannot authorize exchange writes.
"""
from contextlib import asynccontextmanager, closing
from pathlib import Path
import json
import asyncio
import re
import sqlite3
import fcntl
import os
import time
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from .delivery import ASSETS, install

UPSTREAM = "http://127.0.0.1:8766"
DEFAULT_STATE = Path.home() / ".local/share/spike-pro/workspace.sqlite3"


def origin_guard(request):
    if request.method not in {"GET", "HEAD"}:
        if (request.headers.get("origin") != f"{request.url.scheme}://{request.url.netloc}"
                or request.headers.get("sec-fetch-site", "same-origin") != "same-origin"):
            raise HTTPException(403, "请从 Pro 页面提交操作")


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    revision: int = Field(ge=0)
    value: dict


class Workspace:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ValueError("unsafe workspace path")
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS records (key TEXT PRIMARY KEY, revision INTEGER, value TEXT, updated_ms INTEGER)")
        self.path.chmod(0o600)

    def connect(self):
        return closing(sqlite3.connect(self.path, timeout=5))

    def get(self, key):
        with self.connect() as db:
            row = db.execute("SELECT revision,value,updated_ms FROM records WHERE key=?", (key,)).fetchone()
        return dict(key=key, revision=row[0], value=json.loads(row[1]), updated_ms=row[2]) if row else dict(key=key, revision=0, value={}, updated_ms=None)

    def put(self, key, record):
        encoded = json.dumps(record.value, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > 256_000:
            raise HTTPException(413, "记录过大")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT revision FROM records WHERE key=?", (key,)).fetchone()
            if (old[0] if old else 0) != record.revision:
                raise HTTPException(409, "内容已在其他页面更新，请刷新后再保存")
            db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?,?)", (key, record.revision+1, encoded, int(time.time()*1000)))
            db.commit()
        return self.get(key)


def create_app(*, upstream_transport=None, state_path=DEFAULT_STATE):
    @asynccontextmanager
    async def lifespan(app):
        app.state.client = httpx.AsyncClient(base_url=UPSTREAM, trust_env=False, follow_redirects=False,
                                            timeout=httpx.Timeout(60, connect=3), transport=upstream_transport,
                                            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10))
        from .paper_plans import Plans
        app.state.plans = Plans(store, app.state.client)
        # Two frontend ports may share state during upgrades; only one process
        # may dispatch persisted paper slices. The upstream receipt remains the
        # second idempotency boundary, not a substitute for a single dispatcher.
        lock_path=Path(str(store.path)+'.worker.lock')
        fd=os.open(lock_path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        task=None
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            task=asyncio.create_task(app.state.plans.run())
        except BlockingIOError:
            pass
        app.state.paper_dispatcher=task is not None
        try:
            yield
        finally:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            os.close(fd)
            await app.state.client.aclose()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    store = Workspace(state_path)
    install(app, connected=True)
    from .paper_plans import install as install_plans
    install_plans(app, origin_guard)
    from .management import install as install_management
    install_management(app, origin_guard)
    from .live_api import install as install_live
    install_live(app, origin_guard, directory=Path(state_path).parent / "live")

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/")
    def home():
        return RedirectResponse("/pro/market")

    @app.get("/api/pro/workspace/{key}")
    def read(key: str):
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,120}", key):
            raise HTTPException(404)
        return store.get(key)

    @app.put("/api/pro/workspace/{key}")
    def save(key: str, body: Record, request: Request):
        origin_guard(request)
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,120}", key):
            raise HTTPException(404)
        return store.put(key, body)

    @app.get("/api/pro/library")
    def library():
        # Curated local book material only; no arbitrary file access.
        return json.loads((ASSETS.parent / "trading-books.json").read_text())

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def forward(path: str, request: Request):
        origin_guard(request)
        if (not re.fullmatch(r"[A-Za-z0-9_./-]+", path) or any(p in {"", ".", ".."} for p in path.split("/"))
                or path.split("/")[0] not in {"health", "status", "markets", "chart", "candles", "market-workstation", "market-sync", "v13", "signals", "signal-ledger", "candidates", "lines", "shadow", "research", "manual-live", "yolo-detect", "vision", "notifications"}):
            raise HTTPException(404, "接口不存在")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 18*1024*1024:
                raise HTTPException(413)
        headers = {k: v for k, v in request.headers.items() if k in {"accept", "content-type", "x-fable-manual", "x-spike-action"}}
        if request.method != "GET":
            headers.update(origin=UPSTREAM, **{"sec-fetch-site": "same-origin"})
        try:
            result = await request.app.state.client.request(request.method, "/api/"+path, params=request.query_params, content=bytes(body), headers=headers)
        except httpx.HTTPError:
            raise HTTPException(503, "共享后台暂不可达；操作结果未知时请先核对记录，不要重复提交") from None
        return Response(result.content, status_code=result.status_code,
                        headers={k: v for k, v in result.headers.items() if k in {"content-type", "content-disposition", "x-image-sha256", "x-visible-end-ms"}})

    app.mount("/static/pro", StaticFiles(directory=ASSETS), name="pro-assets")
    return app


if __name__ == "__main__":
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8767)
    uvicorn.run(create_app(), host="127.0.0.1", port=parser.parse_args().port, access_log=False)
