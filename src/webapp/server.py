"""FastAPI backend for the fable-trading dashboard.

Read-only over the repo's own artifacts: analysis/output/*.json for stage
metrics, p3_trades.csv for the backtest, the judgment dataset + trained
model for per-symbol signal browsing, and data/kline_cache|kline_fetched
via src.data.loader for candles. Nothing here mutates experiment state.

Model scores are computed once (same train/val discipline as src.backtest.run)
and cached to data/scored_signals.csv + .json sidecar; delete those files to
force a rebuild after retraining.

Run:  python3 -m uvicorn src.webapp.server:app --port 8642
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from src.backtest.run import (
    ACCEPT_START, BASE_COST, COST_SWEEP, DEFAULT_DATA, MAX_CONCURRENT,
    build_signals, window_metrics,
)
from src.data.loader import list_series, load_series

PROJECT_DIR = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_DIR / "analysis" / "output"
SCORE_CACHE = PROJECT_DIR / "data" / "scored_signals.csv"
SCORE_META = PROJECT_DIR / "data" / "scored_signals_meta.json"
TRADES_CSV = OUTPUT_DIR / "p3_trades.csv"

EMA_SPANS = (8, 13, 21, 34, 55, 144, 200)  # the judgment layer's MA set

app = FastAPI(title="fable-trading dashboard")

_signals: pd.DataFrame | None = None
_threshold: float | None = None
_trades: pd.DataFrame | None = None


def _load_json(name: str) -> dict:
    path = OUTPUT_DIR / name
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def scored_signals() -> tuple[pd.DataFrame, float]:
    """All candidates with model scores; built once, cached on disk."""
    global _signals, _threshold
    if _signals is None:
        if SCORE_CACHE.exists() and SCORE_META.exists():
            _signals = pd.read_csv(SCORE_CACHE, parse_dates=["signal_time", "entry_time", "exit_time"])
            _threshold = json.loads(SCORE_META.read_text())["threshold"]
        else:
            print("scoring signals (first boot, ~10s)...", flush=True)
            signals, threshold = build_signals(DEFAULT_DATA)
            keep = ["source", "symbol", "signal_time", "entry_time", "exit_time",
                    "score", "outcome", "realized_ret", "label"]
            signals[keep].to_csv(SCORE_CACHE, index=False)
            SCORE_META.write_text(json.dumps({"threshold": threshold}))
            _signals, _threshold = signals[keep], threshold
    return _signals, float(_threshold)


def trades() -> pd.DataFrame:
    global _trades
    if _trades is None:
        if not TRADES_CSV.exists():
            raise HTTPException(503, "p3_trades.csv missing -- run python3 -m src.backtest.run first")
        _trades = pd.read_csv(TRADES_CSV, parse_dates=["entry_time", "exit_time"])
    return _trades


@app.get("/api/overview")
def overview() -> dict:
    p2b = _load_json("p2b_v2_expanded_final_metrics.json")
    p3 = _load_json("p3_backtest.json")
    hold = p2b.get("holdout", {})
    base = p3.get("cost_sweep_accept_window", {}).get("0.003", {})
    return {
        "stages": [
            {"id": "P0", "name": "P0 信号检验", "status": "done",
             "summary": "风险端有 alpha，收益端无——触发 triple-barrier 标签路线"},
            {"id": "2a", "name": "2a 检测层 YOLO", "status": "done",
             "summary": "冒烟验收通过 mAP50 0.835；正式验收（0.90）待全量训练"},
            {"id": "2b", "name": "2b 判断层 LightGBM", "status": "passed",
             "summary": "holdout 一次性评估通过：AUC %.3f / p=%.3f / top-decile 净 %+.3f%%" % (
                 hold.get("roc_auc", 0), p2b.get("holdout_permutation_p", 1),
                 100 * hold.get("top_decile", {}).get("mean_net_ret", 0))},
            {"id": "3", "name": "3 事件驱动回测", "status": "failed",
             "summary": "第一轮未通过：PF %.2f @0.3%% 成本（0.2%% 时 1.30 擦线）——单笔 edge 无成本安全边际" % base.get("profit_factor", 0)},
        ],
        "tiles": [
            {"label": "holdout AUC", "value": "%.3f" % hold.get("roc_auc", 0), "sub": "置换检验 p=0.001"},
            {"label": "top-decile 净收益/笔", "value": "%+.3f%%" % (100 * hold.get("top_decile", {}).get("mean_net_ret", 0)), "sub": "扣 0.2% 成本，holdout"},
            {"label": "回测 PF（0.3% 成本）", "value": "%.2f" % base.get("profit_factor", 0), "sub": "验收线 1.3，未达标"},
            {"label": "盈亏平衡成本", "value": "≈0.30%", "sub": "edge 与成本同数量级"},
        ],
        "acceptance": p3.get("acceptance_check_base_cost", {}),
        "next": "路线 D（已按推荐执行）：maker 成本模型 + 前向数据积累；验收窗口禁止参数级调优",
    }


@app.get("/api/backtest")
def backtest(cost: float = BASE_COST) -> dict:
    t = trades()
    accept = t[t["entry_time"] >= ACCEPT_START]
    windows = {"accept": accept, "full": t}
    out = {"cost": cost, "sweep": _load_json("p3_backtest.json").get("cost_sweep_accept_window", {})}
    for name, w in windows.items():
        m = window_metrics(w, cost)
        s = w.sort_values("exit_time")
        net = s["gross_ret"].to_numpy() - cost
        # one point per exit timestamp (lightweight-charts needs strictly
        # ascending times); simultaneous exits collapse to the last cumsum
        eq: dict[int, float] = {}
        for ts, v in zip(s["exit_time"], np.cumsum(net)):
            eq[int(ts.timestamp())] = round(100 * v / MAX_CONCURRENT, 4)
        m["equity"] = [{"time": t, "value": v} for t, v in sorted(eq.items())]
        out[name] = m
    return out


@app.get("/api/trades")
def trade_rows(window: str = "accept", limit: int = 500, cost: float = BASE_COST) -> list[dict]:
    t = trades()
    if window == "accept":
        t = t[t["entry_time"] >= ACCEPT_START]
    t = t.sort_values("entry_time", ascending=False).head(limit).copy()
    t["net_ret"] = t["gross_ret"] - cost
    t["entry_time"] = t["entry_time"].astype(str)
    t["exit_time"] = t["exit_time"].astype(str)
    return t.round(5).to_dict("records")


@app.get("/api/symbols")
def symbols() -> list[dict]:
    signals, threshold = scored_signals()
    t = trades()
    traded = t.groupby(["source", "symbol"]).size()
    rows = []
    for (source, symbol), g in signals.groupby(["source", "symbol"]):
        rows.append({
            "source": source, "symbol": symbol,
            "n_signals": int(len(g)),
            "n_eligible": int((g["score"] >= threshold).sum()),
            "n_trades": int(traded.get((source, symbol), 0)),
            "last_signal": str(g["signal_time"].max()),
        })
    rows.sort(key=lambda r: -r["n_trades"])
    return rows


@app.get("/api/chart/{source}/{symbol}")
def chart(source: str, symbol: str, bars: int = 1500) -> dict:
    groups = list_series()
    key = (source, symbol)
    if key not in groups:
        raise HTTPException(404, f"unknown series {source}:{symbol}")
    frame = load_series(groups[key]).tail(max(bars, 300)).reset_index(drop=True)
    if frame.empty:
        raise HTTPException(404, "empty series")
    ts = (frame["open_time"].astype("int64") // 10**9).astype(int)
    candles = [
        {"time": int(t), "open": float(o), "high": float(h), "low": float(l), "close": float(c)}
        for t, o, h, l, c in zip(ts, frame["open"], frame["high"], frame["low"], frame["close"])
    ]
    emas = {}
    for span in EMA_SPANS:
        line = frame["close"].ewm(span=span, adjust=False).mean().round(8)
        emas[str(span)] = [{"time": int(t), "value": float(v)} for t, v in zip(ts, line)]

    signals, threshold = scored_signals()
    t0 = frame["open_time"].iloc[0]
    sig = signals[(signals["source"] == source) & (signals["symbol"] == symbol)
                  & (signals["signal_time"] >= t0)]
    t = trades()
    traded_keys = set(zip(
        t[(t["source"] == source) & (t["symbol"] == symbol)]["entry_time"],
    ))
    markers = []
    for row in sig.itertuples():
        is_trade = (row.entry_time,) in traded_keys
        markers.append({
            "time": int(pd.Timestamp(row.signal_time).timestamp()),
            "eligible": bool(row.score >= threshold),
            "traded": bool(is_trade),
            "score": round(float(row.score), 4),
            "outcome": row.outcome,
            "ret": round(float(row.realized_ret), 5),
        })
    return {"candles": candles, "emas": emas, "markers": markers, "threshold": round(threshold, 4)}


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
