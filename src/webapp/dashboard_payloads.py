"""HTTP payload builders for dashboard overview, backtest, symbols, and charts.

Route handlers stay thin while this module converts experiment artifacts and
runtime score caches into JSON-safe dashboard payloads for the selected
spot/swap universe.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import HTTPException

from src.backtest.run import ACCEPT_START, BASE_COST, MAX_CONCURRENT, window_metrics
from src.data.loader import FETCHED_DIR, list_series, load_series
from src.judgment.labeling import SL_ATR_MULT, TP_ATR_MULT
from src.webapp.dashboard_cache import (
    DEFAULT_UNIVERSE, OUTPUT_DIR, UniverseSpec, load_json, relative_path, scored_signals,
    symbol_matches_universe, trades, universe_spec,
)

# Chart display only (Signals / Explore). Matches YOLO + TG stack:
# SMA/EMA 20·60·120 — NOT the judgment dense rule set (EMA 8–55–144–200).
CHART_MA_PERIODS = (20, 60, 120)
PF_COST_GRID = [round(c, 4) for c in np.arange(0.001, 0.00501, 0.0005)]
COMPARE_JSON = OUTPUT_DIR / "p3_ml_opt_backtest_compare.json"


def chart_ma_series(frame: pd.DataFrame, ts: pd.Series) -> dict[str, list[dict]]:
    """Causal SMA/EMA 20/60/120 series for lightweight-charts (display only)."""
    close = frame["close"].astype(float)
    out: dict[str, list[dict]] = {}
    for period in CHART_MA_PERIODS:
        sma = close.rolling(period, min_periods=period).mean()
        ema = close.ewm(span=period, adjust=False).mean()
        for name, series in ((f"sma{period}", sma), (f"ema{period}", ema)):
            out[name] = [
                {"time": int(t), "value": float(v)}
                for t, v in zip(ts, series.round(8))
                if pd.notna(v)
            ]
    return out


def overview_payload(universe: str = DEFAULT_UNIVERSE) -> dict:
    spec = universe_spec(universe)
    p2b = load_json("p2b_v2_expanded_final_metrics.json")
    p2a = load_json("p2a_val_metrics.json")
    p0 = load_json("p0_summary.json")
    hold = p2b.get("holdout", {})
    signals, threshold = scored_signals(spec.key)
    all_trades = trades(spec.key)
    accept = all_trades[all_trades["entry_time"] >= ACCEPT_START] if not all_trades.empty else all_trades
    base = window_metrics(accept, BASE_COST)
    n_files, n_rows = _fetched_coverage(spec)
    spark, _ = equity_points(accept, BASE_COST)
    pf = base.get("profit_factor", 0)
    return {
        "universe": spec.key,
        "universe_label": spec.label,
        # Keep stages/coverage in payload for API consumers; overview UI no longer renders them.
        "verdict": (
            f"{spec.label} · 验收回测 PF {pf:.2f} @ {BASE_COST * 100:.1f}% · "
            f"{base.get('n_trades', 0)} 笔"
        ),
        "stages": _stage_rows(spec, p0, p2a, p2b, hold, base),
        "tiles": _overview_tiles(spec, base, threshold),
        "coverage": _coverage_tiles(spec, n_files, n_rows, signals, threshold, all_trades, accept),
        "sparkline": spark,
        "acceptance": _acceptance(base),
        "next": "前向 maker-filled closed 满 100 笔再看 PF",
    }


def backtest_payload(cost: float = BASE_COST, universe: str = DEFAULT_UNIVERSE) -> dict:
    spec = universe_spec(universe)
    all_trades = trades(spec.key)
    signals, threshold = scored_signals(spec.key)
    accept_t = all_trades[all_trades["entry_time"] >= ACCEPT_START]
    score_min = float(signals["score"].min()) if not signals.empty else 0.0
    score_max = float(signals["score"].max()) if not signals.empty else 1.0
    out: dict = {
        "cost": cost,
        "universe": spec.key,
        "universe_label": spec.label,
        "score_threshold": threshold,
        "score_semantics": "predicted_realized_ret" if abs(threshold) < 0.2 else "class_probability",
        "score_range": {"min": score_min, "max": score_max},
        "pf_curve": [{"cost": c, "pf": window_metrics(accept_t, c).get("profit_factor")} for c in PF_COST_GRID],
    }
    for name, window_trades, window_signals in (
        ("accept", accept_t, signals[signals["entry_time"] >= ACCEPT_START]),
        ("full", all_trades, signals),
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


def backtest_compare_payload(cost: float = BASE_COST) -> dict:
    """ACTIVE vs shadow portfolio table from precomputed JSON.

    Marks the table *stale* when the frozen JSON no longer matches models/ACTIVE
    (dataset name or val-q90 threshold). Stale numbers stay visible for forensics
    but must not be read as current mainline.
    """
    if not COMPARE_JSON.exists():
        return {"available": False, "reason": "missing analysis/output/p3_ml_opt_backtest_compare.json"}
    raw = json.loads(COMPARE_JSON.read_text(encoding="utf-8"))
    variants = raw.get("variants") or {}
    cost_key = f"{cost:.3f}"
    rows = []
    for key, variant in variants.items():
        accept = (variant.get("cost_sweep_accept_window") or {}).get(cost_key) or {}
        full = variant.get("full_period_base_cost") or {}
        checks = variant.get("acceptance_check_base_cost") or {}
        rows.append({
            "key": key,
            "label": variant.get("variant") or key,
            "role": "ACTIVE" if key == raw.get("active") or "ACTIVE" in str(variant.get("variant", "")) else (
                "SHADOW" if "SHADOW" in str(variant.get("variant", "")) or key == raw.get("shadow") else "other"
            ),
            "objective": variant.get("objective"),
            "model_path": variant.get("model_path"),
            "threshold": variant.get("score_threshold_val_q90"),
            "n_eligible": variant.get("n_eligible"),
            "accept": accept,
            "full": full,
            "acceptance_check": checks,
        })
    # stable order: ACTIVE first
    rows.sort(key=lambda r: (0 if r["role"] == "ACTIVE" else 1 if r["role"] == "SHADOW" else 2, r["key"]))

    live = _live_active_judgment()
    compare_ds = raw.get("dataset")
    compare_thr = None
    for r in rows:
        if r["role"] == "ACTIVE" and r.get("threshold") is not None:
            compare_thr = float(r["threshold"])
            break
    stale_reasons: list[str] = []
    if live.get("dataset_name") and compare_ds:
        if Path(str(compare_ds)).name != live["dataset_name"]:
            stale_reasons.append(
                f"数据集不一致：对照表={Path(str(compare_ds)).name}，ACTIVE={live['dataset_name']}"
            )
    if live.get("threshold_val_q90") is not None and compare_thr is not None:
        if abs(float(live["threshold_val_q90"]) - compare_thr) > 1e-5:
            stale_reasons.append(
                f"阈值不一致：对照表={compare_thr:.5f}，ACTIVE={float(live['threshold_val_q90']):.5f}"
            )
    if live.get("artifact_id") and rows:
        active_models = {r.get("model_path") for r in rows if r["role"] == "ACTIVE"}
        # model_path may be relative; match stem
        live_stem = live["artifact_id"]
        if active_models and not any(
            m and live_stem in str(m) for m in active_models
        ):
            # soft check — only if compare stores a path that clearly differs
            pass
    stale = bool(stale_reasons)
    base_note = raw.get("generated_note") or "ACTIVE vs shadow judgment model, same stage-3 simulator"
    if stale:
        note = (
            "⚠️ 对照表已过期（非当前 ACTIVE）——数字仅供考古，请以总览/动态回测与 ACTIVE 阈值为准。"
            + " · " + "；".join(stale_reasons)
        )
    else:
        note = base_note

    return {
        "available": True,
        "cost": cost,
        "dataset": compare_ds,
        "note": note,
        "stale": stale,
        "stale_reasons": stale_reasons,
        "live_active": live,
        "active": raw.get("active"),
        "shadow": raw.get("shadow"),
        "detector_mainline": raw.get("detector_mainline"),
        "detector_previous": raw.get("detector_previous"),
        "generated_at": raw.get("generated_at"),
        "rows": rows,
    }


def tip_replay_payload() -> dict:
    """v16-era honest backtest: bar-by-bar tip replay (detector saw only past).

    Replaces the discarded stage-3 hindsight backtest (PF 6.61 measured a
    detector conditioned on the printed future). Reads the tip-replay JSON;
    prefers the holdout verdict, falls back to the pre-holdout discovery run,
    and reports a pending state while a run is in flight.
    """
    holdout = OUTPUT_DIR / "v16_holdout_verdict.json"
    discovery = OUTPUT_DIR / "v16_discovery_preholdout.json"
    src, kind = (holdout, "holdout") if holdout.exists() else (
        (discovery, "discovery") if discovery.exists() else (None, None))
    if src is None:
        return {
            "available": False,
            "state": "pending",
            "note": "v16 tip-replay 回测进行中（逐 bar 盘口视角，检测器只见过去）——完成后自动显示。",
            "protocol": "tip_replay: 检测器只见 bar≤t · 次根开盘入场 · TP5/SL2/72bar · maker 成本 · A′ 贴边门 · MIN_GAP 去重",
        }
    data = json.loads(src.read_text())
    s = data.get("summary", {})
    pf = s.get("profit_factor")
    net = s.get("total_net_units")
    gate = bool(
        (s.get("n_trades") or 0) >= 30
        and pf is not None and pf >= 1.3
        and net is not None and net > 0
    )
    return {
        "available": True,
        "state": "done",
        "kind": kind,  # holdout = clean verdict; discovery = in-sample, optimistic
        "clean": kind == "holdout",
        "window": s.get("window"),
        "weights": s.get("weights"),
        "n_symbols": s.get("n_symbols"),
        "n_trades": s.get("n_trades"),
        "win_rate": s.get("win_rate"),
        "profit_factor": pf,
        "mean_net_per_trade": s.get("mean_net_per_trade"),
        "total_net_units": net,
        "fire_per_1k_bars": s.get("fire_per_1k_bars"),
        "cost": s.get("cost"),
        "gate_pass": gate,
        "protocol": s.get("protocol"),
        "note": (
            "holdout 干净窗口（检测器/前向从未碰过）· 扣 maker 0.06%"
            if kind == "holdout"
            else "⚠️ pre-holdout 发现级：检测器训练数据在此窗内，数字偏乐观，仅作筛查"
        ),
    }


def _live_active_judgment() -> dict:
    """Current models/ACTIVE freeze meta for honesty checks."""
    from src.webapp.model_hub import read_active_pointer

    ptr = read_active_pointer()
    out = {
        "artifact_id": ptr.get("artifact_id"),
        "threshold_val_q90": None,
        "dataset_path": None,
        "dataset_name": None,
    }
    aid = ptr.get("artifact_id")
    if not aid:
        return out
    meta_path = Path(__file__).resolve().parents[2] / "models" / f"{aid}.json"
    if not meta_path.is_file():
        return out
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    ds = meta.get("dataset_path")
    thr = meta.get("threshold_val_q90")
    out["dataset_path"] = ds
    out["dataset_name"] = Path(str(ds)).name if ds else None
    try:
        out["threshold_val_q90"] = float(thr) if thr is not None else None
    except (TypeError, ValueError):
        out["threshold_val_q90"] = None
    return out


def trade_rows_payload(window: str = "accept", limit: int = 1000, cost: float = BASE_COST,
                       symbol: str = "", universe: str = DEFAULT_UNIVERSE) -> list[dict]:
    rows = trades(universe)
    if window == "accept":
        rows = rows[rows["entry_time"] >= ACCEPT_START]
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
    mas = chart_ma_series(frame, ts)
    signals, threshold = scored_signals(spec.key)
    t0 = frame["open_time"].iloc[0]
    sig = signals[(signals["source"] == source) & (signals["symbol"] == symbol) & (signals["signal_time"] >= t0)]
    traded_times = set(trades(spec.key)[lambda df: (df["source"] == source) & (df["symbol"] == symbol)]["entry_time"])
    markers = [_marker_payload(row, threshold, traded_times) for row in sig.itertuples()]
    return {
        "candles": candles,
        "mas": mas,
        "emas": mas,  # alias for older frontends
        "markers": markers,
        "threshold": round(threshold, 4),
        "tp_mult": TP_ATR_MULT,
        "sl_mult": SL_ATR_MULT,
        "ma_legend": "SMA/EMA 20·60·120（展示用，与 YOLO/TG 一致）",
    }


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


def _stage_rows(spec: UniverseSpec, p0: dict, p2a: dict, p2b: dict, hold: dict, base: dict) -> list[dict]:
    pf = base.get("profit_factor", 0)
    return [
        {"id": "P0", "name": "P0 信号检验", "status": "done", "summary": _p0_summary(p0)},
        {"id": "2a", "name": "2a 检测层 YOLO", "status": "done",
         "summary": (
             "主线候选源已切到 YOLO（owner detector）；历史 mAP50 %.4f 仅作检测质量参考，"
             "主线以 2b 打分 + 阶段 3 回测/前向为准"
         ) % p2a.get("mAP50", 0)},
        {"id": "2b", "name": "2b 判断层 LightGBM", "status": "passed",
         "summary": (
             "ACTIVE：YOLO 池 + 回归 realized_ret（预测收益排序）；数据集 %s；"
             "阈值 = val 分数 90 分位。二分类 YOLO 保留为 SHADOW 对照。"
         ) % relative_path(spec.dataset_path)},
        {"id": "3", "name": "3 事件驱动回测", "status": "passed" if pf >= 1.3 else "failed",
         "summary": "%s 动态回测：PF %.2f @%.1f%% 成本，%s 笔；终审仍以前向 100 笔为准" % (
             spec.label, pf, BASE_COST * 100, base.get("n_trades", 0))},
    ]


def _fmt_threshold(threshold: float) -> str:
    # regression thresholds are small (predicted ret); binary probs near 0.7
    if abs(threshold) < 0.05:
        return f"{threshold:.4f}"
    return f"{threshold:.3f}"


def _overview_tiles(spec: UniverseSpec, base: dict, threshold: float) -> list[dict]:
    thr_sub = "val q90 · 回归 ACTIVE" if abs(threshold) < 0.2 else "val q90 · 二分类"
    net = base.get("net_return_on_capital")
    net_s = f"{100 * net:+.1f}%" if net is not None else "—"
    return [
        {"label": "宇宙", "value": spec.label, "sub": "主线 SWAP"},
        {"label": "验收 PF", "value": "%.2f" % base.get("profit_factor", 0),
         "sub": f"{BASE_COST * 100:.1f}% 成本 · 线 1.3"},
        {"label": "净收益 / 胜率", "value": net_s,
         "sub": f"胜率 {100 * base.get('win_rate', 0):.1f}% · {base.get('n_trades', 0)} 笔"},
        {"label": "阀门阈值", "value": _fmt_threshold(threshold), "sub": thr_sub},
    ]


def _coverage_tiles(spec: UniverseSpec, n_files: int, n_rows: int, signals: pd.DataFrame,
                    threshold: float, all_trades: pd.DataFrame, accept: pd.DataFrame) -> list[dict]:
    return [
        {"label": "K 线数据", "value": f"{n_rows / 1e6:.1f}M", "sub": f"{n_files} 个 {spec.label} 15m 新拉取文件"},
        {"label": "候选信号", "value": f"{len(signals):,}", "sub": "TP5/SL2 h72 数据集"},
        {"label": "合格信号", "value": f"{int((signals['score'] >= threshold).sum()):,}", "sub": "score ≥ 阈值"},
        {"label": "回测成交", "value": f"{len(all_trades):,}", "sub": f"验收窗口 {len(accept)} 笔"},
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
