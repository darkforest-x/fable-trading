"""Local-only FastAPI surface for the Fable signal console model adapter."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from yoyo.console.market_data import MarketDataError, fetch_recent_candles
from yoyo.console.model_adapter import (
    adapter_status_payload,
    inference_payload,
    replay_image_path,
)

app = FastAPI(title="Fable signal console model bridge")


class InferenceBody(BaseModel):
    """A symbol selects replay data; model identity remains server-controlled."""

    symbol: str = Field(..., min_length=1, max_length=32)
    force: bool = False

    class Config:
        extra = "forbid"


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "service": "fable-signal-console-model-bridge"}


@app.get("/api/model/status")
def model_status() -> dict:
    return adapter_status_payload()


@app.get("/api/market/candles/{symbol}")
def market_candles(symbol: str, bar: str = "15m", limit: int = 300):
    """Serve shared public OKX candles without model-scoring live data."""
    try:
        return fetch_recent_candles(symbol, bar=bar, limit=limit)
    except (MarketDataError, OSError, ValueError) as exc:
        return JSONResponse({"ok": False, "detail": str(exc)}, status_code=502)


@app.post("/api/model/infer")
def model_infer(body: InferenceBody):
    payload = inference_payload(body.symbol, force=body.force)
    if payload.get("busy"):
        return JSONResponse(payload, status_code=409)
    if not payload.get("ok"):
        return JSONResponse(payload, status_code=422)
    return payload


@app.get("/api/model/replay-image/{symbol}")
def replay_image(symbol: str):
    try:
        path = replay_image_path(symbol)
    except Exception as exc:  # noqa: BLE001 -- API boundary is fail-closed
        return JSONResponse({"ok": False, "detail": str(exc)}, status_code=404)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})
