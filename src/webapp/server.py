"""FastAPI routes for the fable-trading dashboard.

Routes are intentionally thin: `dashboard_payloads` owns universe-scoped
overview/backtest/chart JSON, and `forward_payloads` owns forward validation
JSON. P2.5 Phase 0+1 adds ops auth + experiment registry + agenda (read-only).
Phase 2 adds hard-coded job whitelist runner under /api/ops/jobs*.
Phase 3 adds read-only data/model hubs under /api/ops/data-hub and /api/ops/model-hub.
Static assets are mounted last so API routes stay reachable.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.backtest.run import BASE_COST
from src.webapp.agenda_payloads import agenda_payload
from src.webapp.auth import verify_ops_request
from src.webapp.dashboard_cache import DEFAULT_UNIVERSE
from src.webapp.dashboard_payloads import (
    backtest_compare_payload,
    backtest_payload,
    chart_payload,
    overview_payload,
    symbols_payload,
    trade_rows_payload,
)
from src.webapp.data_hub import data_hub_payload
from src.webapp.experiment_registry import experiment_detail, list_experiments
from src.webapp.forward_payloads import FORWARD_COST, forward_payload
from src.webapp.jobs.runner import get_runner
from src.webapp.jobs.store import get_store
from src.webapp.jobs.whitelist import JobValidationError, list_job_types
from src.webapp.model_hub import model_hub_payload
from src.webapp.ops_flags import executor_enabled, ops_status_payload
from src.webapp.status_strip import status_strip_payload
from src.webapp.labeling_hub import labeling_hub_payload
from src.webapp.explore_payloads import explore_catalog, explore_chart_payload
from src.webapp.scout_mtf_payloads import (
    scout_mtf_latest,
    scout_mtf_chart,
    scout_mtf_open_positions,
    scout_mtf_paper_latest,
    scout_mtf_paper_run,
    scout_mtf_run,
    scout_mtf_status,
)
from src.eth_micro.payloads import eth_micro_payload
from src.short_tf.payloads import short_tf_payload

app = FastAPI(title="fable-trading dashboard")


class CreateJobBody(BaseModel):
    """Only job_type + constrained params. Extra free-cmd fields rejected."""

    job_type: str = Field(..., min_length=1, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class ScoutMtfRunBody(BaseModel):
    """Side-branch multi-TF radar controls (no secrets, no live orders)."""

    top: int = Field(default=12, ge=3, le=40)
    min_vol: float = Field(default=5_000_000.0, ge=0)
    include_loss: bool = True
    max_symbols: int | None = Field(default=None, ge=1, le=80)

    model_config = {"extra": "forbid"}


@app.middleware("http")
async def no_cache_static(request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/api/overview")
def overview(universe: str = DEFAULT_UNIVERSE) -> dict:
    return overview_payload(universe)


@app.get("/api/status-strip")
def status_strip() -> dict:
    """Owner detector + forward progress + scout freshness for the top strip."""
    return status_strip_payload()


@app.get("/api/labeling-hub")
def labeling_hub() -> dict:
    """Owner labeling entries, audit pages, and task-pack inventory (read-only)."""
    return labeling_hub_payload()


@app.get("/api/explore/catalog")
def explore_catalog_route(universe: str = DEFAULT_UNIVERSE) -> dict:
    """Beginner coin list + time ranges + howto steps."""
    return explore_catalog(universe)


@app.get("/api/explore/chart/{source}/{symbol}")
def explore_chart_route(
    source: str,
    symbol: str,
    bars: int = 2880,
    universe: str = DEFAULT_UNIVERSE,
) -> dict:
    """Candles + EMAs + dense-MA boxes for the beginner explore view."""
    return explore_chart_payload(source=source, symbol=symbol, bars=bars, universe=universe)


@app.get("/api/backtest")
def backtest(cost: float = BASE_COST, universe: str = DEFAULT_UNIVERSE) -> dict:
    return backtest_payload(cost=cost, universe=universe)


@app.get("/api/backtest/compare")
def backtest_compare(cost: float = BASE_COST) -> dict:
    """ACTIVE regression vs shadow binary stage-3 backtest table."""
    return backtest_compare_payload(cost=cost)


@app.get("/api/trades")
def trade_rows(
    window: str = "accept",
    limit: int = 1000,
    cost: float = BASE_COST,
    symbol: str = "",
    universe: str = DEFAULT_UNIVERSE,
) -> list[dict]:
    return trade_rows_payload(window=window, limit=limit, cost=cost, symbol=symbol, universe=universe)


@app.get("/api/symbols")
def symbols(universe: str = DEFAULT_UNIVERSE) -> list[dict]:
    return symbols_payload(universe)


@app.get("/api/chart/{source}/{symbol}")
def chart(source: str, symbol: str, bars: int = 3000, universe: str = DEFAULT_UNIVERSE) -> dict:
    return chart_payload(source=source, symbol=symbol, bars=bars, universe=universe)


@app.get("/api/forward")
def forward(cost: float = FORWARD_COST) -> dict:
    return forward_payload(cost)


@app.get("/api/eth-micro")
def eth_micro() -> dict:
    """ETH-only 1/2/3/5m channel: backtest table + monitor status + recent signals."""
    return eth_micro_payload()


@app.get("/api/short-tf")
def short_tf() -> dict:
    """Multi-symbol 1m/5m tip rules channel (isolated from 15m forward_log)."""
    return short_tf_payload()


# ---------- scout_mtf side branch (rank + multi-TF radar) ----------


@app.get("/api/scout-mtf/status")
def scout_mtf_status_route() -> dict:
    return scout_mtf_status()


@app.get("/api/scout-mtf/latest")
def scout_mtf_latest_route() -> dict:
    """Last scan result for the multi-TF console (may be empty)."""
    return scout_mtf_latest()


@app.post("/api/scout-mtf/run")
def scout_mtf_run_route(body: ScoutMtfRunBody | None = None) -> dict:
    """Run one multi-TF rank scan (blocking, 30–90s). Not mainline / not orders."""
    b = body or ScoutMtfRunBody()
    return scout_mtf_run(
        top=b.top,
        min_vol=b.min_vol,
        include_loss=b.include_loss,
        max_symbols=b.max_symbols,
    )


@app.get("/api/scout-mtf/paper")
def scout_mtf_paper_route() -> dict:
    """Latest paper-sim summary for radar picks."""
    return scout_mtf_paper_latest()


@app.post("/api/scout-mtf/paper-run")
def scout_mtf_paper_run_route() -> dict:
    """Paper-test A/B gainers from latest scan (no exchange orders)."""
    return scout_mtf_paper_run()


@app.get("/api/scout-mtf/positions")
def scout_mtf_positions_route() -> dict:
    """Read-only open SWAP positions (for drill-down on the radar console)."""
    return scout_mtf_open_positions()


@app.get("/api/scout-mtf/chart")
def scout_mtf_chart_route(inst_id: str, bar: str = "15m", limit: int = 300) -> dict:
    """OHLCV + display MAs for scout position / paper trade drill-down charts."""
    return scout_mtf_chart(inst_id, bar=bar, limit=limit)


# ---------- P2.5 Phase 0+1: ops (read-only) ----------


@app.get("/api/ops/status")
def ops_status() -> dict:
    """Public: whether ops auth is required (does not leak the token)."""
    return ops_status_payload()


@app.get("/api/ops/experiments")
def ops_experiments(
    request: Request,
    kind: str = "",
    q: str = "",
    sort: str = Query(default="mtime", pattern="^(mtime|val_auc|perm_p)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
) -> dict:
    verify_ops_request(request)
    return list_experiments(kind=kind, q=q, sort=sort, order=order)


@app.get("/api/ops/experiments/{exp_id}")
def ops_experiment_detail(exp_id: str, request: Request):
    verify_ops_request(request)
    detail = experiment_detail(exp_id)
    if detail is None:
        return JSONResponse({"detail": f"experiment not found: {exp_id}"}, status_code=404)
    return detail


@app.get("/api/ops/agenda")
def ops_agenda(request: Request) -> dict:
    verify_ops_request(request)
    return agenda_payload()


# ---------- P2.5 Phase 2: job runner (whitelist only) ----------


@app.get("/api/ops/job-types")
def ops_job_types(request: Request) -> dict:
    verify_ops_request(request)
    return {
        "items": list_job_types(),
        "executor_enabled": executor_enabled(),
    }


@app.get("/api/ops/jobs")
def ops_jobs_list(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status: str = "",
) -> dict:
    verify_ops_request(request)
    try:
        return get_store().list_jobs(
            limit=limit, offset=offset, status=status or None
        )
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)


@app.get("/api/ops/jobs/{job_id}")
def ops_job_detail(
    job_id: str,
    request: Request,
    log_lines: int = Query(default=200, ge=1, le=2000),
):
    verify_ops_request(request)
    job = get_store().get(job_id)
    if job is None:
        return JSONResponse({"detail": f"job not found: {job_id}"}, status_code=404)
    try:
        log_tail = get_runner().read_log_tail(job_id, max_lines=log_lines)
    except KeyError:
        log_tail = ""
    return {**job, "log_tail": log_tail}


@app.get("/api/ops/jobs/{job_id}/log")
def ops_job_log(
    job_id: str,
    request: Request,
    lines: int = Query(default=500, ge=1, le=5000),
):
    verify_ops_request(request)
    if get_store().get(job_id) is None:
        return JSONResponse({"detail": f"job not found: {job_id}"}, status_code=404)
    try:
        text = get_runner().read_log_tail(job_id, max_lines=lines)
    except KeyError:
        text = ""
    return PlainTextResponse(text or "", media_type="text/plain; charset=utf-8")


@app.post("/api/ops/jobs")
def ops_jobs_create(body: CreateJobBody, request: Request):
    verify_ops_request(request)
    if not executor_enabled():
        return JSONResponse(
            {
                "detail": "本实例已禁用任务执行器（ENABLE_JOB_EXECUTOR!=1；VPS 默认关闭）。"
            },
            status_code=403,
        )
    # Reject free-shell fields even if they sneak into params via nested misuse;
    # whitelist validate also blocks forbidden keys.
    if any(k in body.params for k in ("cmd", "shell", "argv", "command")):
        return JSONResponse(
            {"detail": "free cmd/shell/argv is not allowed; use job_type + params only"},
            status_code=400,
        )
    try:
        job = get_runner().enqueue(body.job_type, body.params)
    except JobValidationError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    except PermissionError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=403)
    return JSONResponse(job, status_code=201)


@app.post("/api/ops/jobs/{job_id}/cancel")
def ops_jobs_cancel(job_id: str, request: Request):
    verify_ops_request(request)
    if not executor_enabled():
        return JSONResponse(
            {
                "detail": "本实例已禁用任务执行器（ENABLE_JOB_EXECUTOR!=1；VPS 默认关闭）。"
            },
            status_code=403,
        )
    try:
        job = get_runner().cancel(job_id)
    except KeyError:
        return JSONResponse({"detail": f"job not found: {job_id}"}, status_code=404)
    return job


# ---------- P2.5 Phase 3: data + model hubs (read-only) ----------


@app.get("/api/ops/data-hub")
def ops_data_hub(request: Request) -> dict:
    """Coverage by bar, audit summary embed, forward log health. No writes."""
    verify_ops_request(request)
    return data_hub_payload()


@app.get("/api/ops/model-hub")
def ops_model_hub(request: Request) -> dict:
    """List frozen_* pairs + ACTIVE pointer. Promote POST not exposed yet."""
    verify_ops_request(request)
    return model_hub_payload()


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
