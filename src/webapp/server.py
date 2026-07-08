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
import re
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from src.backtest.run import (
    ACCEPT_START, BASE_COST, DEFAULT_DATA, MAX_CONCURRENT,
    build_signals, window_metrics,
)
from src.judgment.labeling import SL_ATR_MULT, TP_ATR_MULT
from src.data.loader import FETCHED_DIR, list_series, load_series

PROJECT_DIR = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_DIR / "analysis" / "output"
SCORE_CACHE = PROJECT_DIR / "data" / "scored_signals.csv"
SCORE_META = PROJECT_DIR / "data" / "scored_signals_meta.json"
TRADES_CSV = OUTPUT_DIR / "p3_trades.csv"

EMA_SPANS = (8, 13, 21, 34, 55, 144, 200)  # the judgment layer's MA set
CACHE_COLUMNS = ["source", "symbol", "signal_time", "entry_time", "exit_time",
                 "score", "outcome", "realized_ret", "entry_price", "label",
                 "atr_pct", "dense_run_len"]
PF_COST_GRID = [round(c, 4) for c in np.arange(0.001, 0.00501, 0.0005)]

app = FastAPI(title="fable-trading dashboard")


@app.middleware("http")
async def no_cache_static(request, call_next):
    """The dashboard is a living tool; stale cached JS has bitten us once."""
    response = await call_next(request)
    if not request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-cache"
    return response

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
        rebuild = True
        if SCORE_CACHE.exists() and SCORE_META.exists():
            cached = pd.read_csv(SCORE_CACHE, parse_dates=["signal_time", "entry_time", "exit_time"])
            if set(CACHE_COLUMNS) <= set(cached.columns):  # stale schema -> rebuild
                _signals = cached
                _threshold = json.loads(SCORE_META.read_text())["threshold"]
                rebuild = False
        if rebuild:
            print("scoring signals (first boot, ~10s)...", flush=True)
            signals, threshold = build_signals(DEFAULT_DATA)
            signals[CACHE_COLUMNS].to_csv(SCORE_CACHE, index=False)
            SCORE_META.write_text(json.dumps({"threshold": threshold}))
            _signals, _threshold = signals[CACHE_COLUMNS], threshold
    return _signals, float(_threshold)


def trades() -> pd.DataFrame:
    global _trades
    if _trades is None:
        if not TRADES_CSV.exists():
            raise HTTPException(503, "p3_trades.csv missing -- run python3 -m src.backtest.run first")
        _trades = pd.read_csv(TRADES_CSV, parse_dates=["entry_time", "exit_time"])
    return _trades


def _equity_points(w: pd.DataFrame, cost: float) -> tuple[list[dict], list[dict]]:
    """Equity (% of capital) and underwater drawdown, one point per exit ts."""
    s = w.sort_values("exit_time")
    net = s["gross_ret"].to_numpy() - cost
    eq: dict[int, float] = {}
    for ts, v in zip(s["exit_time"], np.cumsum(net)):
        eq[int(ts.timestamp())] = round(100 * v / MAX_CONCURRENT, 4)
    points = [{"time": t, "value": v} for t, v in sorted(eq.items())]
    peak, dd = 0.0, []
    for p in points:
        peak = max(peak, p["value"])
        dd.append({"time": p["time"], "value": round(p["value"] - peak, 4)})
    return points, dd


@app.get("/api/overview")
def overview() -> dict:
    p2b = _load_json("p2b_v2_expanded_final_metrics.json")
    p3 = _load_json("p3_backtest.json")
    hold = p2b.get("holdout", {})
    base = p3.get("cost_sweep_accept_window", {}).get("0.003", {})

    n_files, n_rows = 0, 0
    if FETCHED_DIR.is_dir():
        for f in FETCHED_DIR.glob("okx_*_15m_*.csv"):
            m = re.search(r"_(\d+)\.csv$", f.name)
            if m:
                n_files += 1
                n_rows += int(m.group(1))
    signals, threshold = scored_signals()
    t = trades()
    accept = t[t["entry_time"] >= ACCEPT_START]
    spark, _ = _equity_points(accept, BASE_COST)

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
        "coverage": [
            {"label": "K 线数据", "value": f"{n_rows / 1e6:.1f}M", "sub": f"{n_files} 个币种 × 400 天 15m（新拉取）"},
            {"label": "候选信号", "value": f"{len(signals):,}", "sub": "expanded 池，13 个月"},
            {"label": "入场阈值", "value": f"{threshold:.3f}", "sub": "val 分数 90 分位，事前定死"},
            {"label": "回测成交", "value": f"{len(t):,}", "sub": f"验收窗口 {len(accept)} 笔"},
        ],
        "sparkline": spark,
        "acceptance": p3.get("acceptance_check_base_cost", {}),
        "next": "路线 D（已按推荐执行）：maker 成本模型 + 前向数据积累；验收窗口禁止参数级调优",
    }


@app.get("/api/backtest")
def backtest(cost: float = BASE_COST) -> dict:
    t = trades()
    signals, _ = scored_signals()
    accept_t = t[t["entry_time"] >= ACCEPT_START]
    out: dict = {"cost": cost,
                 "pf_curve": [{"cost": c, "pf": window_metrics(accept_t, c).get("profit_factor")}
                              for c in PF_COST_GRID]}
    for name, w, sig in (
        ("accept", accept_t, signals[signals["entry_time"] >= ACCEPT_START]),
        ("full", t, signals),
    ):
        m = window_metrics(w, cost)
        m["equity"], m["drawdown"] = _equity_points(w, cost)

        s = w.copy()
        s["net"] = s["gross_ret"] - cost
        month = s.groupby(s["exit_time"].dt.strftime("%Y-%m"))["net"].sum()
        m["monthly"] = [{"month": k, "value": round(100 * v / MAX_CONCURRENT, 4)}
                        for k, v in month.items()]

        by_sym = s.groupby("symbol")["net"].agg(["sum", "size"]).sort_values("sum")
        rows = [{"symbol": i, "net": round(100 * r["sum"], 3), "n": int(r["size"])}
                for i, r in by_sym.iterrows()]
        m["per_symbol"] = {"best": rows[-8:][::-1], "worst": rows[:8]}

        # score-decile mean net over ALL candidates in window (model ranking power)
        d = sig.copy()
        d["net"] = d["realized_ret"] - cost
        d["decile"] = (d["score"].rank(pct=True) * 10).clip(upper=9.999).astype(int) + 1
        dec = d.groupby("decile")["net"].agg(["mean", "size"])
        m["decile"] = [{"decile": int(i), "mean_net": round(100 * r["mean"], 4), "n": int(r["size"])}
                       for i, r in dec.iterrows()]
        out[name] = m
    return out


@app.get("/api/trades")
def trade_rows(window: str = "accept", limit: int = 1000, cost: float = BASE_COST,
               symbol: str = "") -> list[dict]:
    t = trades()
    if window == "accept":
        t = t[t["entry_time"] >= ACCEPT_START]
    if symbol:
        t = t[t["symbol"] == symbol]
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
    rows.sort(key=lambda r: (-r["n_trades"], -r["n_eligible"]))
    return rows


@app.get("/api/chart/{source}/{symbol}")
def chart(source: str, symbol: str, bars: int = 3000) -> dict:
    groups = list_series()
    key = (source, symbol)
    if key not in groups:
        raise HTTPException(404, f"unknown series {source}:{symbol}")
    frame = load_series(groups[key]).tail(min(max(bars, 300), 40000)).reset_index(drop=True)
    if frame.empty:
        raise HTTPException(404, "empty series")
    # NOT astype(int64)//1e9: pandas may store datetime64 in us (not ns)
    # depending on version, silently shifting times by 1000x. Timedelta
    # division is unit-safe.
    ts = ((frame["open_time"] - pd.Timestamp("1970-01-01", tz="UTC"))
          // pd.Timedelta(seconds=1)).astype(int)
    candles = [
        {"time": int(t), "open": float(o), "high": float(h), "low": float(l),
         "close": float(c), "volume": float(v) if np.isfinite(v) else 0.0}
        for t, o, h, l, c, v in zip(ts, frame["open"], frame["high"], frame["low"],
                                    frame["close"], frame["volume"])
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
    traded_times = set(t[(t["source"] == source) & (t["symbol"] == symbol)]["entry_time"])
    markers = []
    for row in sig.itertuples():
        markers.append({
            "time": int(pd.Timestamp(row.signal_time).timestamp()),
            "entry_time": int(pd.Timestamp(row.entry_time).timestamp()),
            "exit_time": int(pd.Timestamp(row.exit_time).timestamp()),
            "eligible": bool(row.score >= threshold),
            "traded": bool(row.entry_time in traded_times),
            "score": round(float(row.score), 4),
            "outcome": row.outcome,
            "ret": round(float(row.realized_ret), 5),
            "entry_price": round(float(row.entry_price), 8),
            "atr_pct": round(float(row.atr_pct), 6),
            "dense_len": int(row.dense_run_len),
        })
    return {"candles": candles, "emas": emas, "markers": markers,
            "threshold": round(threshold, 4),
            "tp_mult": TP_ATR_MULT, "sl_mult": SL_ATR_MULT}


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
