"""Strategy annotations and forward simulation controls for the local workspace.

The allowlisted adapters produce research-only paper intents. A source bundle
is frozen before a run can start. No request accepts code, exchange credentials,
production flags, entry/exit thresholds or a configurable cost assumption.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid
import zipfile

from fastapi import HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal, Optional
from yoyo.contracts.research import PipelineRef

from .paper_store import PaperStore, FILL_POLICY, STEP_MS, clock_ms
from .strategies import catalog, get_plugin
from .paper_source import source_manifest

SOURCES = [
    {"name": "Freqtrade · 策略与 dry-run", "url": "https://www.freqtrade.io/en/stable/strategy-customization/"},
    {"name": "NautilusTrader · 共用策略与成交模型", "url": "https://nautilustrader.io/docs/latest/concepts/behavioral_models/"},
    {"name": "QuantConnect LEAN · 策略模块", "url": "https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/overview"},
]


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    strategy_id: str = Field(min_length=1, max_length=100)
    symbol_scope: Optional[Literal["okx_all_usdt", "custom"]] = None
    symbols: Optional[list[str]] = Field(default=None, min_length=1, max_length=2000)
    timeframes: list[str] = Field(min_length=1, max_length=4)
    request_id: str = Field(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9-]+$")
    pipeline_ref: Optional[PipelineRef] = None


class StrategyNote(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_revision: int = Field(default=0, ge=0)
    stage: Literal["research", "rejected", "archived"] = "research"
    notes: str = Field(default="", max_length=12000)
    factor_ids: list[str] = Field(default_factory=list, max_length=40)
    experiment_ids: list[str] = Field(default_factory=list, max_length=40)


def launch(store, root, monitor_runtime):
    """Separate process/OS lock; never add work to the monitor scan pulse."""
    with (store.runtime / "worker.log").open("ab") as stream:
        subprocess.Popen([sys.executable, "-m", "yoyo.research_workspace.paper_worker", "--root", str(root),
                          "--runtime", str(store.runtime), "--monitor-runtime", str(monitor_runtime)],
                         cwd=root, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream, start_new_session=True)


def freeze_sources(root, store, manifest):
    directory = store.runtime / "sources"
    directory.mkdir(exist_ok=True)
    target = directory / (manifest["hash"] + ".zip")
    if target.exists():
        return target
    temp = directory / (uuid.uuid4().hex + ".tmp")
    try:
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for name, sha in manifest["files"].items():
                path = (root / name).resolve()
                if not path.is_relative_to(root):
                    raise ValueError("策略源文件必须位于本仓")
                content = path.read_bytes()
                if hashlib.sha256(content).hexdigest() != sha:
                    raise ValueError("策略代码同时发生更新，请重新创建运行")
                archive.writestr(name, content)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    return target


def install(api, app, root, monitor_runtime, workspace_store, all_factors, all_experiments, launch_worker):
    store = PaperStore(workspace_store.runtime / "paper")
    app.state.paper_store = store

    def strategies():
        notes = workspace_store.notes("strategy")
        items = []
        for entry in catalog(root):
            note = notes.get(entry["id"], {})
            value = dict(entry, **{k: v for k, v in note.items() if k != "status"})
            value["status"] = note.get("stage", entry.get("status", "research"))
            value["paper_supported"] = bool(entry.get("paper_supported")) and value["status"] != "archived"
            items.append(value)
        return items

    def selected(strategy_id):
        for entry in strategies():
            if entry["id"] == strategy_id:
                return entry
        raise HTTPException(404, "策略插件未登记")

    def compact(run):
        return dict(run, spec={k: v for k, v in run["spec"].items() if k != "source_manifest"})

    def get_run(run_id):
        try:
            return store.run(run_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc))

    @api.get("/strategies")
    def strategy_list():
        return {"items": strategies(), "sources": SOURCES}

    @api.put("/strategies/{strategy_id}")
    def annotate(strategy_id: str, payload: StrategyNote):
        selected(strategy_id)
        if (set(payload.factor_ids) - {x["id"] for x in all_factors()}
                or set(payload.experiment_ids) - {x["experiment_id"] for x in all_experiments()}):
            raise HTTPException(400, "关联因子或实验不存在")
        if payload.stage == "archived" and any(r["strategy_id"] == strategy_id for r in store.runs(active_only=True)):
            raise HTTPException(409, "请先结束此策略的模拟运行，再归档")
        try:
            workspace_store.save_note("strategy", strategy_id, payload.model_dump(exclude={"expected_revision"}), payload.expected_revision)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        return selected(strategy_id)

    @api.get("/paper/runs")
    def runs():
        return {"items": [compact(r) for r in store.runs()], "now_ms": clock_ms()}

    @api.post("/paper/runs", status_code=201)
    def create(payload: RunRequest):
        strategy = selected(payload.strategy_id)
        if not strategy.get("paper_supported"):
            raise HTTPException(409, strategy.get("notes") or "此策略尚未接入完整模拟适配器")
        # Missing selection means the whole USDT universe, not a hidden BTC/ETH
        # fallback. Older clients with an explicit list retain that exact scope.
        scope = payload.symbol_scope or ("custom" if payload.symbols is not None else "okx_all_usdt")
        if scope == "okx_all_usdt" and payload.symbols is not None:
            raise HTTPException(400, "全市场模式不接受自选合约列表，请切换为自选合约")
        if scope == "custom" and not payload.symbols:
            raise HTTPException(400, "自选模式需至少填写一个 OKX USDT 永续合约")
        symbols = sorted(set(payload.symbols)) if payload.symbols is not None else None
        if symbols is not None and (len(symbols) != len(payload.symbols) or any(not re.fullmatch(r"[A-Z0-9]{1,25}-USDT-SWAP", s) for s in symbols)):
            raise HTTPException(400, "请选择不重复的 OKX USDT 永续合约，例如 BTC-USDT-SWAP")
        tfs = sorted(set(payload.timeframes))
        if len(tfs) != len(payload.timeframes) or set(tfs) - set(strategy["timeframes"]):
            raise HTTPException(400, "所选周期未由此插件支持")
        pipeline = None
        if payload.pipeline_ref:
            try:
                pipeline = app.state.research_platform.admit(payload.pipeline_ref, "paper", payload)
            except KeyError as exc:
                raise HTTPException(404, str(exc))
            except ValueError as exc:
                raise HTTPException(409, str(exc))
        try:
            from .paper_source import MonitorSource
            source = MonitorSource(monitor_runtime)
            at = clock_ms()
            if scope == "okx_all_usdt":
                # Validate the existing signal reader without decoding every
                # market's history. The worker validates each event's causal
                # checkpoint before admission; later listings are included.
                source.events(get_plugin(payload.strategy_id), at, None, tfs)
            for symbol in symbols or []:
                for tf in tfs:
                    checkpoint = source.checkpoint(symbol, tf, at)
                    if not checkpoint or not checkpoint["candles"]:
                        raise ValueError(f"{symbol} {tf} 尚无可用闭合行情，请检查运行状态")
                    from yoyo.monitor import FRESH_MS
                    # A normal closed 4H candle ages for four hours while the
                    # next candle forms. Signal freshness is checked separately.
                    if at - checkpoint["candles"][-1]["t"] - STEP_MS[tf] > STEP_MS[tf] + FRESH_MS:
                        raise ValueError(f"{symbol} {tf} 行情已过期，请等监控恢复后创建运行")
            manifest = source_manifest(root)
            freeze_sources(root, store, manifest)
            spec = {"symbol_scope": scope, "symbols": symbols, "timeframes": tfs, "cost_bp": 20,
                    "universe_policy": "dynamic_monitor_usdt_perpetuals" if scope == "okx_all_usdt" else "explicit_symbols",
                    "entry_rule": strategy["entry_rule"], "exit_rule": strategy["exit_rule"],
                    "fill_policy": FILL_POLICY, "exit_fill_policy": "precommitted-closed-bar-rules-event-time-v1",
                    "data_source": {"venue": "okx", "kind": "existing_monitor_closed_checkpoints"},
                    "source_hash": manifest["hash"], "source_manifest": manifest,
                    "factor_ids": strategy.get("factor_ids", []), "experiment_ids": strategy.get("experiment_ids", []),
                    "funding_included": False, "orderbook_slippage_included": False,
                    "position_policy": "one_position_per_symbol_timeframe_independent_unit_notional",
                    "production_eligible": False}
            if pipeline:
                spec["pipeline"] = pipeline
            run = store.create(strategy, spec, payload.request_id, at=clock_ms())
        except (ValueError, OSError, KeyError) as exc:
            raise HTTPException(409, str(exc))
        if launch_worker:
            launch(store, root, monitor_runtime)
        return compact(run)

    @api.get("/paper/runs/{run_id}")
    def detail(run_id: str, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        return dict(compact(get_run(run_id)), **store.decisions(run_id, limit, offset))

    @api.get("/paper/runs/{run_id}/export")
    def export(run_id: str):
        get_run(run_id)
        return Response(json.dumps(store.export(run_id), ensure_ascii=False, indent=2, allow_nan=False), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="paper-{run_id}.json"'})

    @api.get("/paper/runs/{run_id}/sources")
    def sources(run_id: str):
        run = get_run(run_id)
        target = store.runtime / "sources" / (run["spec"]["source_hash"] + ".zip")
        if not target.exists():
            raise HTTPException(404, "源码快照缺失")
        return FileResponse(target, filename="paper-source-" + run_id + ".zip")

    @api.post("/paper/runs/{run_id}/{action}")
    def control(run_id: str, action: Literal["pause", "resume", "stop"]):
        get_run(run_id)
        try:
            if action == "resume" and get_run(run_id)["spec"]["source_hash"] != source_manifest(root)["hash"]:
                raise ValueError("策略代码版本已变化，请新建运行；旧运行记录保留")
            result = store.action(run_id, action)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        if launch_worker and action != "stop":
            launch(store, root, monitor_runtime)
        return compact(result)

    if launch_worker and store.runs(active_only=True):
        launch(store, root, monitor_runtime)
    return {"strategies": strategies, "create": create}
