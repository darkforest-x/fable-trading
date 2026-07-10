"""HTTP payload builders for dashboard overview, backtest, symbols, and charts.

Route handlers stay thin while this module converts experiment artifacts and
runtime score caches into JSON-safe dashboard payloads for the selected
spot/swap universe.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import HTTPException

from src.backtest.run import BASE_COST, MAX_CONCURRENT, window_metrics
from src.data.loader import FETCHED_DIR, list_series, load_series
from src.judgment.labeling import SL_ATR_MULT, TP_ATR_MULT
from src.webapp.dashboard_cache import (
    DEFAULT_UNIVERSE, UniverseSpec, load_json, relative_path, scored_signals,
    symbol_matches_universe, trades, universe_spec, validation_start,
)

MA_PERIODS = (20, 60, 120)
PF_COST_GRID = [round(c, 4) for c in np.arange(0.001, 0.00501, 0.0005)]


def overview_payload(universe: str = DEFAULT_UNIVERSE) -> dict:
    spec = universe_spec(universe)
    ma206 = load_json("p2b_ma206_mainline_20260710_metrics.json")
    p2a = load_json("p2a_val_metrics.json")
    p0 = load_json("p0_summary.json")
    signals, threshold = scored_signals(spec.key)
    all_trades = trades(spec.key)
    val_start = validation_start(spec.key)
    validation = all_trades[all_trades["entry_time"] >= val_start] if not all_trades.empty else all_trades
    base = window_metrics(validation, BASE_COST)
    n_files, n_rows = _fetched_coverage(spec)
    spark, _ = equity_points(validation, BASE_COST)
    pf = base.get("profit_factor", 0)
    return {
        "universe": spec.key,
        "universe_label": spec.label,
        "verdict": (
            f"当前宇宙：{spec.label}；MA206 验证集发现级 PF {pf:.2f} @ {BASE_COST * 100:.1f}% 成本，"
            f"交易 {base.get('n_trades', 0)} 笔。"
        ),
        "stages": _stage_rows(spec, p0, p2a, ma206, base),
        "tiles": _overview_tiles(spec, base, threshold),
        "coverage": _coverage_tiles(spec, n_files, n_rows, signals, threshold, all_trades, validation),
        "sparkline": spark,
        "acceptance": _acceptance(base),
        "next": (
            "下一硬闸门：冻结配置前向 maker-filled closed 样本累计到 100 笔后看 PF；"
            "MA206 holdout 曾意外读取 1 次并已隔离"
        ),
    }


def backtest_payload(cost: float = BASE_COST, universe: str = DEFAULT_UNIVERSE) -> dict:
    spec = universe_spec(universe)
    all_trades = trades(spec.key)
    signals, _ = scored_signals(spec.key)
    val_start = validation_start(spec.key)
    validation_t = all_trades[all_trades["entry_time"] >= val_start]
    out: dict = {
        "cost": cost,
        "universe": spec.key,
        "universe_label": spec.label,
        "pf_curve": [
            {"cost": c, "pf": window_metrics(validation_t, c).get("profit_factor")}
            for c in PF_COST_GRID
        ],
        "score_scope": "pre_holdout_only",
        "validation_start": str(val_start),
    }
    for name, window_trades, window_signals in (
        ("validation", validation_t, signals[signals["entry_time"] >= val_start]),
        ("preholdout", all_trades, signals),
    ):
        metrics = window_metrics(window_trades, cost)
        metrics["equity"], metrics["drawdown"] = equity_points(window_trades, cost)
        enriched = window_trades.copy()
        enriched["net"] = enriched["gross_ret"] - cost
        month = enriched.groupby(enriched["exit_time"].dt.strftime("%Y-%m"))["net"].sum()
        metrics["monthly"] = [{"month": k, "value": round(100 * v / MAX_CONCURRENT, 4)} for k, v in month.items()]
        by_sym = enriched.groupby("symbol")["net"].agg(["sum", "size"]).sort_values("sum")
        rows = [{"symbol": i, "net": round(100 * r["sum"], 3), "n": int(r["size"])} for i, r in by_sym.iterrows()]
        metrics["per_symbol"] = {"best": rows[-8:][::-1], "worst": rows[:8]}
        decile_source = window_signals.copy()
        decile_source["net"] = decile_source["realized_ret"] - cost
        decile_source["decile"] = (decile_source["score"].rank(pct=True) * 10).clip(upper=9.999).astype(int) + 1
        dec = decile_source.groupby("decile")["net"].agg(["mean", "size"])
        metrics["decile"] = [
            {"decile": int(i), "mean_net": round(100 * r["mean"], 4), "n": int(r["size"])}
            for i, r in dec.iterrows()
        ]
        out[name] = metrics
    return out


def trade_rows_payload(window: str = "validation", limit: int = 1000, cost: float = BASE_COST,
                       symbol: str = "", universe: str = DEFAULT_UNIVERSE) -> list[dict]:
    rows = trades(universe)
    if window == "validation":
        rows = rows[rows["entry_time"] >= validation_start(universe)]
    if symbol:
        rows = rows[rows["symbol"] == symbol]
    rows = rows.sort_values("entry_time", ascending=False).head(limit).copy()
    rows["net_ret"] = rows["gross_ret"] - cost
    rows["entry_time"] = rows["entry_time"].astype(str)
    rows["exit_time"] = rows["exit_time"].astype(str)
    return rows.round(5).to_dict("records")


def symbols_payload(universe: str = DEFAULT_UNIVERSE) -> list[dict]:
    signals, threshold = scored_signals(universe)
    all_trades = trades(universe)
    traded = all_trades.groupby(["source", "symbol"]).size()
    rows = []
    for (source, symbol), group in signals.groupby(["source", "symbol"]):
        rows.append({
            "source": source,
            "symbol": symbol,
            "n_signals": int(len(group)),
            "n_eligible": int((group["score"] >= threshold).sum()),
            "n_trades": int(traded.get((source, symbol), 0)),
            "last_signal": str(group["signal_time"].max()),
        })
    rows.sort(key=lambda r: (-r["n_trades"], -r["n_eligible"]))
    return rows


def chart_payload(source: str, symbol: str, bars: int = 3000, universe: str = DEFAULT_UNIVERSE) -> dict:
    spec = universe_spec(universe)
    groups = series_groups(spec)
    key = (source, symbol)
    if key not in groups:
        raise HTTPException(404, f"unknown {spec.key} series {source}:{symbol}")
    frame = load_series(groups[key]).tail(min(max(bars, 300), 40000)).reset_index(drop=True)
    if frame.empty:
        raise HTTPException(404, "empty series")
    ts = ((frame["open_time"] - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(seconds=1)).astype(int)
    candles = [
        {"time": int(t), "open": float(o), "high": float(h), "low": float(l), "close": float(c),
         "volume": float(v) if np.isfinite(v) else 0.0}
        for t, o, h, l, c, v in zip(ts, frame["open"], frame["high"], frame["low"], frame["close"], frame["volume"])
    ]
    moving_averages = {}
    for period in MA_PERIODS:
        moving_averages[f"sma{period}"] = [
            {"time": int(timestamp), "value": float(value)}
            for timestamp, value in zip(ts, frame["close"].rolling(period, min_periods=period).mean().round(8))
            if np.isfinite(value)
        ]
        moving_averages[f"ema{period}"] = [
            {"time": int(timestamp), "value": float(value)}
            for timestamp, value in zip(ts, frame["close"].ewm(span=period, adjust=False).mean().round(8))
        ]
    signals, threshold = scored_signals(spec.key)
    t0 = frame["open_time"].iloc[0]
    sig = signals[(signals["source"] == source) & (signals["symbol"] == symbol) & (signals["signal_time"] >= t0)]
    traded_times = set(trades(spec.key)[lambda df: (df["source"] == source) & (df["symbol"] == symbol)]["entry_time"])
    markers = [_marker_payload(row, threshold, traded_times) for row in sig.itertuples()]
    return {"candles": candles, "moving_averages": moving_averages, "markers": markers,
            "threshold": round(threshold, 4),
            "tp_mult": TP_ATR_MULT, "sl_mult": SL_ATR_MULT}


def equity_points(frame: pd.DataFrame, cost: float) -> tuple[list[dict], list[dict]]:
    ordered = frame.sort_values("exit_time")
    net = ordered["gross_ret"].to_numpy() - cost
    equity: dict[int, float] = {}
    for ts, value in zip(ordered["exit_time"], np.cumsum(net)):
        equity[int(ts.timestamp())] = round(100 * value / MAX_CONCURRENT, 4)
    points = [{"time": t, "value": v} for t, v in sorted(equity.items())]
    peak, drawdown = 0.0, []
    for point in points:
        peak = max(peak, point["value"])
        drawdown.append({"time": point["time"], "value": round(point["value"] - peak, 4)})
    return points, drawdown


def series_groups(spec: UniverseSpec) -> dict[tuple[str, str], list[Path]]:
    return {key: paths for key, paths in list_series().items() if symbol_matches_universe(key[1], spec.key)}


def _stage_rows(spec: UniverseSpec, p0: dict, p2a: dict, ma206: dict, base: dict) -> list[dict]:
    pf = base.get("profit_factor", 0)
    val = ma206.get("val", {})
    return [
        {"id": "P0", "name": "P0 信号检验", "status": "done", "summary": _p0_summary(p0)},
        {"id": "2a", "name": "2a 检测层 YOLO", "status": "done",
         "summary": "正式验收 mAP50 %.4f，低于 0.90；非关键路径暂停" % p2a.get("mAP50", 0)},
        {"id": "2b", "name": "2b MA206 判断层", "status": "pending",
         "summary": "val AUC %.3f / p=%.3f；holdout 意外读取已作废，等待前向 100 笔" % (
             val.get("roc_auc", 0), ma206.get("val_permutation_p", 1))},
        {"id": "3", "name": "3 事件驱动回测", "status": "passed" if pf >= 1.3 else "failed",
         "summary": "%s 验证集发现级：PF %.2f @%.1f%% 成本，%s 笔；不作为最终验收" % (
             spec.label, pf, BASE_COST * 100, base.get("n_trades", 0))},
    ]


def _overview_tiles(spec: UniverseSpec, base: dict, threshold: float) -> list[dict]:
    return [
        {"label": "当前宇宙", "value": spec.label, "sub": relative_path(spec.dataset_path)},
        {"label": "验证集发现级 PF", "value": "%.2f" % base.get("profit_factor", 0),
         "sub": f"{BASE_COST * 100:.1f}% 成本；研究参考线 1.3"},
        {"label": "验证集胜率", "value": "%.1f%%" % (100 * base.get("win_rate", 0)),
         "sub": f"{base.get('n_trades', 0)} 笔"},
        {"label": "模型阈值", "value": "%.3f" % threshold, "sub": "val 分数 90 分位，事前固定"},
    ]


def _coverage_tiles(spec: UniverseSpec, n_files: int, n_rows: int, signals: pd.DataFrame,
                    threshold: float, all_trades: pd.DataFrame, validation: pd.DataFrame) -> list[dict]:
    return [
        {
            "label": "K 线数据",
            "value": f"{n_rows / 1e6:.1f}M",
            "sub": f"{n_files} 个 {spec.label} 15m 新拉取文件",
        },
        {"label": "候选信号", "value": f"{len(signals):,}", "sub": "TP5/SL2 h72 数据集"},
        {
            "label": "合格信号",
            "value": f"{int((signals['score'] >= threshold).sum()):,}",
            "sub": "score ≥ 阈值",
        },
        {"label": "回测成交", "value": f"{len(all_trades):,}", "sub": f"验证集 {len(validation)} 笔"},
    ]


def _fetched_coverage(spec: UniverseSpec) -> tuple[int, int]:
    n_files, n_rows = 0, 0
    for paths in series_groups(spec).values():
        for path in paths:
            if path.parent != FETCHED_DIR:
                continue
            matched = re.search(r"_(\d+)(?:_latest)?\.csv$", path.name)
            if matched:
                n_files += 1
                n_rows += int(matched.group(1))
    return n_files, n_rows


def _acceptance(metrics: dict) -> dict[str, bool]:
    return {
        "net_positive": metrics.get("net_total_units", 0) > 0,
        "profit_factor_ge_1.3": metrics.get("profit_factor", 0) >= 1.3,
        "max_drawdown_le_20pct": metrics.get("max_drawdown_pct", 1) <= 0.20,
        "n_trades_ge_100": metrics.get("n_trades", 0) >= 100,
    }


def _p0_summary(payload: dict) -> str:
    if not payload:
        return "风险端有 alpha，收益端无——触发 triple-barrier 标签路线"
    best = payload.get("best_version") or payload.get("version") or "P0"
    return f"{best} 风险端 alpha 已确认；收益端不足，转入 triple-barrier 标签路线"


def _marker_payload(row, threshold: float, traded_times: set[pd.Timestamp]) -> dict:
    return {
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
    }
