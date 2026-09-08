"""Loopback-only FastAPI observer, using immutable server-side scan policy.

Sources: https://fastapi.tiangolo.com/tutorial/static-files/ and
https://fastapi.tiangolo.com/tutorial/handling-errors/ . No external messaging,
credential, order or market-config mutation endpoint is exposed to the browser.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import threading
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from yoyo.contracts.rotation import (EXPERIMENT, ROOT, SCHEMA, RotationConfig, RotationError,
                                    authorize, digest, iso, load_config, now_utc, source_identity)
from yoyo.rotation.journal import Journal
from yoyo.rotation.metadata import load_catalog
from yoyo.rotation.pipeline import run_scan

WEB = Path(__file__).with_name("web")


class Observer:
    """One in-process writer; a failed scan preserves the prior observation."""

    def __init__(self, config: RotationConfig, *, journal: Journal, catalog: dict,
                 receipt: Optional[dict] = None, scan_function=run_scan, catalog_path: Optional[Path] = None):
        self.config, self.journal, self.catalog = config, journal, catalog
        self.receipt, self.scan_function = receipt, scan_function
        self.catalog_path = catalog_path
        self.lock = threading.Lock()
        self.last_error = None

    def policy(self) -> dict:
        try:
            cutoff = now_utc() if self.config.mode == "live_observation" else self.config.as_of
            result = authorize(self.config, cutoff, source_hash=source_identity()["source_hash"], receipt=self.receipt)
            return {**result, "scan_allowed": True}
        except ValueError as exc:
            return {"scan_allowed": False, "reason": str(exc), "execution_eligible": False,
                    "training_eligible": False, "model_scored": False}

    def latest(self) -> dict:
        catalog = load_catalog(self.catalog_path) if self.catalog_path else self.catalog
        namespace = self.journal.namespace({"schema_version": SCHEMA, "mode": self.config.mode,
                     "config_hash": self.config.config_hash, "source_hash": source_identity()["source_hash"],
                     "catalog_hash": digest(catalog)})
        value = self.journal.latest(namespace=namespace)
        value["source_changed_since_scan"] = False
        value["last_scan_error"] = self.last_error
        return value

    def scan(self) -> dict:
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("scan_already_running")
        try:
            catalog = load_catalog(self.catalog_path) if self.catalog_path else self.catalog
            snapshot = self.scan_function(self.config, catalog=catalog, receipt=self.receipt)
            current_catalog = load_catalog(self.catalog_path) if self.catalog_path else self.catalog
            if digest(current_catalog) != snapshot["catalog_hash"]:
                raise RotationError("event catalog changed during scan; result not persisted")
            self.journal.save(snapshot)
            self.last_error = None
            return snapshot
        except Exception as exc:
            self.last_error = str(exc)
            raise
        finally:
            self.lock.release()


def candidate_csv(snapshot: dict) -> str:
    buffer = io.StringIO(newline="")
    fields = ["scan_id", "as_of", "mode", "venue", "config_hash", "source_hash", "catalog_hash", "symbol", "sector", "status", "review_status", "score",
              "return_7d", "return_30d", "rs_7d", "rs_30d", "relative_volume",
              "event_risk", "reference_entry", "reference_stop", "execution_eligible"]
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for item in snapshot["candidates"]:
        row = {k: item.get(k) for k in fields}
        row.update(scan_id=snapshot["scan_id"], as_of=snapshot["as_of"], mode=snapshot["mode"],
                   venue=snapshot.get("venue"), config_hash=snapshot["config_hash"], source_hash=snapshot["source_hash"], catalog_hash=snapshot["catalog_hash"],
                   return_7d=item["daily"].get("return_7d"), return_30d=item["daily"].get("return_30d"),
                   relative_volume=item["setup"].get("relative_volume"),
                   reference_entry=item["setup"].get("entry_reference"),
                   reference_stop=item["setup"].get("stop_reference"), execution_eligible=False)
        # CSV remains inert if a manually maintained sector contains a formula.
        for key, value in row.items():
            if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
                row[key] = "'" + value
        writer.writerow(row)
    return "\ufeff" + buffer.getvalue()


def create_app(observer: Observer) -> FastAPI:
    app = FastAPI(title="Fable Rotation Research Observer", docs_url="/api/docs", redoc_url=None)
    app.state.observer = observer
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def safety_headers(request: Request, call_next):
        if request.method == "POST":
            origin = request.headers.get("origin")
            expected = str(request.base_url).rstrip("/")
            if origin and origin.rstrip("/") != expected:
                return JSONResponse({"detail": "cross-origin scan requests are refused"}, status_code=403)
            if "application/json" not in request.headers.get("content-type", ""):
                return JSONResponse({"detail": "scan requests require application/json"}, status_code=415)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        return response

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html", media_type="text/html")

    @app.get("/healthz")
    def health():
        return {"ok": True, "service": "fable-rotation-research", "mode": observer.config.mode}

    @app.get("/api/config")
    def config():
        return {**observer.config.to_dict(), "config_hash": observer.config.config_hash,
                "source_hash": source_identity()["source_hash"], "heuristics_validated": False}

    @app.get("/api/status")
    def status():
        try:
            latest = observer.latest()
        except RotationError:
            latest = None
        policy = observer.policy()
        return {"service": "fable-rotation-research", "status": "ready" if policy["scan_allowed"] else "approval_required",
                "busy": observer.lock.locked(), "config": observer.config.to_dict(), "policy": policy,
                "latest_scan_id": latest["scan_id"] if latest else None,
                "last_scan_error": observer.last_error, "now": iso(now_utc())}

    @app.get("/api/snapshot")
    def snapshot():
        try:
            return observer.latest()
        except RotationError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/scan")
    def scan(body: dict):
        if body:
            raise HTTPException(status_code=422, detail="scan policy is server-controlled; body must be {}")
        try:
            return observer.scan()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/journal")
    def journal():
        try:
            current = observer.latest()
        except RotationError:
            return {"events": [], "total": 0}
        return observer.journal.events(namespace=observer.journal.namespace(current))

    @app.get("/api/export.csv")
    def export():
        try:
            current = observer.latest()
        except RotationError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(candidate_csv(current), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="rotation-observations.csv"'})

    app.mount("/static", StaticFiles(directory=str(WEB)), name="static")
    return app


def configured_observer(config_path: Path = EXPERIMENT / "config.json", *, receipt_path: Path = None,
                        directory: Path = EXPERIMENT / "runtime") -> Observer:
    config = load_config(config_path)
    receipt = json.loads(receipt_path.read_text()) if receipt_path else None
    return Observer(config, journal=Journal(directory), catalog=load_catalog(EXPERIMENT / "event_catalog.json"), receipt=receipt)
