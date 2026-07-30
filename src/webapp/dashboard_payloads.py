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

TIP_REPLAY_HOLDOUT = OUTPUT_DIR / "v16_holdout_verdict.json"
TIP_REPLAY_DISCOVERY = OUTPUT_DIR / "v16_discovery_preholdout.json"

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
    # Look-ahead scored_signals retired — empty by design; tip-replay is the reference.
    signals, threshold = scored_signals(spec.key)
    all_trades = trades(spec.key)
    accept = all_trades[all_trades["entry_time"] >= ACCEPT_START] if not all_trades.empty else all_trades
    base = window_metrics(accept, BASE_COST) if not accept.empty else {
        "n_trades": 0, "profit_factor": 0, "win_rate": 0,
        "mean_net_per_trade": 0, "net_return_on_capital": 0, "max_drawdown_pct": 0,
        "net_total_units": 0,
    }
    n_files, n_rows = _fetched_coverage(spec)
    tip = tip_replay_payload()
    # Overview no longer ships look-ahead equity (PF 6.x / +245% era). Keep
    # sparkline empty so any old frontend chart dies; tip-replay detail lives on #backtest.
    pf = base.get("profit_factor", 0) or 0
    verdict = _verdict_line(spec, base, pf)
    # Drop the parenthetical "旧 PF … 前视" from the headline once and for all.
    if "（旧" in verdict:
        verdict = verdict.split("（旧")[0].rstrip()
    return {
        "universe": spec.key,
        "universe_label": spec.label,
        # Keep stages/coverage in payload for API consumers; overview UI no longer renders them.
        "verdict": verdict,
        "stages": _stage_rows(spec, p0, p2a, p2b, hold, base),
        "tiles": _overview_tiles(spec, base, threshold),
        "coverage": _coverage_tiles(spec, n_files, n_rows, signals, threshold, all_trades, accept),
        "sparkline": [],
        "acceptance": _acceptance(base),
        "next": "前向 maker-filled closed 满 100 笔再看 PF",
        "sparkline_source": "none",
        "sparkline_retired": True,
        "sparkline_note": "前视验收净值图已下线；tip-replay 曲线见 #backtest",
    }


def backtest_payload(cost: float = BASE_COST, universe: str = DEFAULT_UNIVERSE) -> dict:
    """Deprecated look-ahead stage-3 payload — no longer used by the UI.

    Historical JSON/CSV products were archived under
    ``analysis/archive/backtest_legacy_*``. The backtest page now loads
    ``tip_replay_payload`` only. This stub remains so old clients get an
    explicit empty response instead of PF 6.61 green numbers.
    """
    honest = _honest_verdict()
    empty_metrics = {
        "n_trades": 0,
        "profit_factor": None,
        "win_rate": None,
        "mean_net_per_trade": None,
        "net_return_on_capital": None,
        "max_drawdown_pct": None,
        "equity": [],
        "drawdown": [],
        "monthly": [],
        "per_symbol": {"best": [], "worst": []},
        "decile": [],
    }
    return {
        "cost": cost,
        "universe": universe,
        "universe_label": universe_spec(universe).label,
        "deprecated": True,
        "lookahead_warning": (
            "旧前视回测已下线（产物已归档 analysis/archive/backtest_legacy_*）。"
            "请使用本页 tip-replay 终审。"
            + (f" tip-replay: PF {honest.get('profit_factor', 0):.3f} · "
               f"{honest.get('n_trades', 0)} 笔 · "
               f"每笔净 {100 * honest.get('mean_net_per_trade', 0):+.3f}%。"
               if honest else "")
        ),
        "honest_verdict": honest,
        "score_threshold": None,
        "score_semantics": None,
        "score_range": {"min": 0.0, "max": 1.0},
        "pf_curve": [],
        "accept": dict(empty_metrics),
        "full": dict(empty_metrics),
    }


def backtest_compare_payload(cost: float = BASE_COST) -> dict:
    """ACTIVE vs shadow portfolio table from precomputed JSON.

    Historical compare tables were archived (owner 2026-07-30 clean-up).
    Always report unavailable so the UI does not resurrect PF 6.61-era rows.
    """
    if not COMPARE_JSON.exists():
        return {
            "available": False,
            "reason": "历史对照表已归档（analysis/archive/backtest_legacy_*）；回测页仅展示 tip-replay",
            "archived": True,
        }
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

    This is the **only** backtest the dashboard backtest page should headline.
    Stage-3 look-ahead tables (PF 6.61 era) were archived under
    analysis/archive/backtest_legacy_* and are no longer served here.

    Prefers holdout verdict, falls back to pre-holdout discovery; reports
    pending while a run is in flight. Includes trade rows + equity for the UI.
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
            "trades": [],
            "equity": [],
            "drawdown": [],
            "monthly": [],
            "outcomes": {},
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
    raw_trades = data.get("trades") or []
    trades_out: list[dict] = []
    for row in raw_trades:
        trades_out.append({
            "source": "okx",
            "symbol": str(row.get("symbol") or ""),
            "signal_time": str(row.get("signal_time") or ""),
            "entry_time": str(row.get("entry_time") or ""),
            "exit_time": str(row.get("exit_time") or ""),
            "score": None,
            "outcome": str(row.get("outcome") or ""),
            "gross_ret": float(row["gross_ret"]) if row.get("gross_ret") is not None else None,
            "net_ret": float(row["net_ret"]) if row.get("net_ret") is not None else None,
        })
    equity, drawdown, monthly, outcomes = _tip_replay_series(trades_out)
    return {
        "available": True,
        "state": "done",
        "kind": kind,  # holdout = clean verdict; discovery = in-sample, optimistic
        "clean": kind == "holdout",
        "window": s.get("window"),
        "weights": s.get("weights"),
        "n_symbols": s.get("n_symbols"),
        "n_trades": s.get("n_trades") or len(trades_out),
        "win_rate": s.get("win_rate"),
        "profit_factor": pf,
        "mean_net_per_trade": s.get("mean_net_per_trade"),
        "total_net_units": net,
        "fire_per_1k_bars": s.get("fire_per_1k_bars"),
        "cost": s.get("cost"),
        "gate_pass": gate,
        "protocol": s.get("protocol"),
        "source_file": relative_path(src),
        "note": (
            "holdout 干净窗口（检测器/前向从未碰过）· 扣 maker 0.06%"
            if kind == "holdout"
            else "⚠️ pre-holdout 发现级：检测器训练数据在此窗内，数字偏乐观，仅作筛查"
        ),
        "trades": trades_out,
        "equity": equity,
        "drawdown": drawdown,
        "monthly": monthly,
        "outcomes": outcomes,
    }


def _tip_replay_series(trades: list[dict]) -> tuple[list[dict], list[dict], list[dict], dict]:
    """Build equity / drawdown / monthly / outcome counts from tip-replay rows."""
    if not trades:
        return [], [], [], {}
    rows = sorted(
        [t for t in trades if t.get("entry_time") and t.get("net_ret") is not None],
        key=lambda t: str(t["entry_time"]),
    )
    equity: list[dict] = []
    drawdown: list[dict] = []
    cum = 0.0
    peak = 0.0
    month_net: dict[str, float] = {}
    outcomes: dict[str, int] = {}
    for t in rows:
        net = float(t["net_ret"])
        cum += net
        peak = max(peak, cum)
        ts = pd.Timestamp(t["entry_time"])
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        unix = int(ts.timestamp())
        equity.append({"time": unix, "value": round(100 * cum, 4)})
        drawdown.append({"time": unix, "value": round(100 * (cum - peak), 4)})
        month_key = ts.strftime("%Y-%m")
        month_net[month_key] = month_net.get(month_key, 0.0) + net
        oc = str(t.get("outcome") or "")
        outcomes[oc] = outcomes.get(oc, 0) + 1
    monthly = [
        {"month": k, "value": round(100 * v, 4)}
        for k, v in sorted(month_net.items())
    ]
    return equity, drawdown, monthly, outcomes


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


def tip_replay_trades_frame() -> pd.DataFrame:
    """Load tip-replay trades as a DataFrame (honest signal source for #signals)."""
    src = TIP_REPLAY_HOLDOUT if TIP_REPLAY_HOLDOUT.exists() else (
        TIP_REPLAY_DISCOVERY if TIP_REPLAY_DISCOVERY.exists() else None
    )
    if src is None:
        return pd.DataFrame(
            columns=[
                "source", "symbol", "signal_time", "entry_time", "exit_time",
                "outcome", "gross_ret", "net_ret", "score",
            ]
        )
    data = json.loads(src.read_text(encoding="utf-8"))
    rows = []
    for t in data.get("trades") or []:
        rows.append({
            "source": "okx",
            "symbol": str(t.get("symbol") or ""),
            "signal_time": pd.Timestamp(t.get("signal_time") or t.get("entry_time")),
            "entry_time": pd.Timestamp(t.get("entry_time") or t.get("signal_time")),
            "exit_time": pd.Timestamp(t["exit_time"]) if t.get("exit_time") else pd.NaT,
            "outcome": str(t.get("outcome") or ""),
            "gross_ret": float(t["gross_ret"]) if t.get("gross_ret") is not None else float("nan"),
            "net_ret": float(t["net_ret"]) if t.get("net_ret") is not None else float("nan"),
            "score": float(t["score"]) if t.get("score") is not None else float("nan"),
        })
    if not rows:
        return pd.DataFrame(
            columns=[
                "source", "symbol", "signal_time", "entry_time", "exit_time",
                "outcome", "gross_ret", "net_ret", "score",
            ]
        )
    frame = pd.DataFrame(rows)
    for col in ("signal_time", "entry_time", "exit_time"):
        frame[col] = pd.to_datetime(frame[col], utc=True, errors="coerce")
    return frame


def trade_rows_payload(window: str = "accept", limit: int = 1000, cost: float = BASE_COST,
                       symbol: str = "", universe: str = DEFAULT_UNIVERSE) -> list[dict]:
    """Trade list for APIs — tip-replay only (look-ahead portfolio retired)."""
    del window, cost, universe  # tip-replay is a fixed holdout book
    rows = tip_replay_trades_frame()
    if symbol:
        rows = rows[rows["symbol"] == symbol]
    if rows.empty:
        return []
    rows = rows.sort_values("entry_time", ascending=False).head(limit).copy()
    out = []
    for r in rows.itertuples():
        out.append({
            "source": r.source,
            "symbol": r.symbol,
            "entry_time": str(r.entry_time) if pd.notna(r.entry_time) else "",
            "exit_time": str(r.exit_time) if pd.notna(r.exit_time) else "",
            "score": None if pd.isna(r.score) else float(r.score),
            "outcome": r.outcome,
            "gross_ret": None if pd.isna(r.gross_ret) else float(r.gross_ret),
            "net_ret": None if pd.isna(r.net_ret) else float(r.net_ret),
        })
    return out


def symbols_payload(universe: str = DEFAULT_UNIVERSE) -> list[dict]:
    """Symbol picker for #signals — tip-replay trade counts only."""
    del universe
    frame = tip_replay_trades_frame()
    if frame.empty:
        return []
    rows = []
    for (source, symbol), group in frame.groupby(["source", "symbol"]):
        rows.append({
            "source": source,
            "symbol": symbol,
            "n_signals": int(len(group)),
            "n_eligible": int(len(group)),
            "n_trades": int(len(group)),
            "last_signal": str(group["signal_time"].max()),
            "source_kind": "tip_replay",
        })
    rows.sort(key=lambda r: (-r["n_trades"], r["symbol"]))
    return rows


# tip-replay protocol (v16 holdout): short TP5/SL2 — not labeling.py long defaults (TP4).
TIP_REPLAY_TP_MULT = 5.0
TIP_REPLAY_SL_MULT = 2.0


def chart_payload(source: str, symbol: str, bars: int = 3000, universe: str = DEFAULT_UNIVERSE) -> dict:
    """K-line + tip-replay markers only (no look-ahead scored_signals markers).

    Enriches each marker with entry/ATR/TP/SL from OHLC so the frontend can
    draw Claude-style short barriers (not bare arrows).
    """
    from src.judgment.candidates import add_indicators

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
    t0 = frame["open_time"].iloc[0]
    tip = tip_replay_trades_frame()
    if not tip.empty:
        tip = tip[(tip["source"] == source) & (tip["symbol"] == symbol) & (tip["signal_time"] >= t0)]
    enriched = add_indicators(frame) if not tip.empty else frame
    markers = (
        [_tip_replay_marker_enriched(row, enriched) for row in tip.itertuples()]
        if not tip.empty
        else []
    )
    honest = _honest_verdict() or {}
    return {
        "candles": candles,
        "mas": mas,
        "emas": mas,  # alias for older frontends
        "markers": markers,
        "threshold": None,
        "side": "short",
        "marker_source": "tip_replay",
        "marker_note": (
            f"tip-replay holdout · 做空 TP{TIP_REPLAY_TP_MULT:g}/SL{TIP_REPLAY_SL_MULT:g} · "
            f"{honest.get('window', '')} · PF {honest.get('profit_factor', 0):.3f}"
        ).strip(" ·"),
        "tp_mult": TIP_REPLAY_TP_MULT,
        "sl_mult": TIP_REPLAY_SL_MULT,
        "ma_legend": "SMA/EMA 20·60·120（展示用，与 YOLO/TG 一致）",
    }


def _tip_replay_marker_enriched(row, enriched: pd.DataFrame) -> dict:
    """Build a chart marker with entry/ATR/TP/SL for short tip-replay trades."""
    sig = pd.Timestamp(row.signal_time)
    if sig.tzinfo is None:
        sig = sig.tz_localize("UTC")
    else:
        sig = sig.tz_convert("UTC")
    ent_ts = row.entry_time if pd.notna(row.entry_time) else sig
    ent_ts = pd.Timestamp(ent_ts)
    if ent_ts.tzinfo is None:
        ent_ts = ent_ts.tz_localize("UTC")
    else:
        ent_ts = ent_ts.tz_convert("UTC")
    ext = row.exit_time if pd.notna(getattr(row, "exit_time", pd.NaT)) else pd.NaT
    ret = row.net_ret if pd.notna(row.net_ret) else row.gross_ret
    score = None if pd.isna(row.score) else float(row.score)

    times = pd.to_datetime(enriched["open_time"], utc=True)
    # nearest signal bar
    hits = np.flatnonzero(times == sig)
    if len(hits) == 0:
        # tolerate second-level mismatch
        diffs = (times - sig).abs()
        si = int(diffs.argmin()) if len(diffs) else -1
        if si < 0 or diffs.iloc[si] > pd.Timedelta(minutes=20):
            si = -1
    else:
        si = int(hits[0])

    entry_price = None
    atr = None
    atr_pct = None
    tp_px = None
    sl_px = None
    exit_price = None
    if si >= 0:
        atr_raw = enriched["atr14"].iloc[si] if "atr14" in enriched.columns else np.nan
        if pd.notna(atr_raw) and float(atr_raw) > 0:
            atr = float(atr_raw)
        # entry = next bar open (tip-replay protocol); fallback signal close
        ei = si + 1
        if ei < len(enriched):
            entry_price = float(enriched["open"].iloc[ei])
        else:
            entry_price = float(enriched["close"].iloc[si])
        if entry_price and entry_price > 0 and atr is not None:
            atr_pct = atr / entry_price
            # SHORT barriers (Claude paper-chart style)
            tp_px = entry_price - TIP_REPLAY_TP_MULT * atr
            sl_px = entry_price + TIP_REPLAY_SL_MULT * atr
        if entry_price and entry_price > 0 and pd.notna(ret):
            # short ret ≈ (entry - exit) / entry  →  exit = entry * (1 - ret)
            exit_price = entry_price * (1.0 - float(ret))

    exit_unix = (
        int(pd.Timestamp(ext).timestamp())
        if pd.notna(ext)
        else int(pd.Timestamp(ent_ts).timestamp())
    )
    return {
        "time": int(sig.timestamp()),
        "entry_time": int(ent_ts.timestamp()),
        "exit_time": exit_unix,
        "eligible": True,
        "traded": True,
        "score": score,
        "outcome": str(row.outcome or ""),
        "ret": None if pd.isna(ret) else round(float(ret), 5),
        "gross_ret": None if pd.isna(row.gross_ret) else round(float(row.gross_ret), 5),
        "entry_price": None if entry_price is None else round(float(entry_price), 8),
        "exit_price": None if exit_price is None else round(float(exit_price), 8),
        "atr": None if atr is None else round(float(atr), 8),
        "atr_pct": None if atr_pct is None else round(float(atr_pct), 6),
        "tp_price": None if tp_px is None else round(float(tp_px), 8),
        "sl_price": None if sl_px is None else round(float(sl_px), 8),
        "tp_mult": TIP_REPLAY_TP_MULT,
        "sl_mult": TIP_REPLAY_SL_MULT,
        "side": "short",
        "dense_len": 0,
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


def _honest_verdict() -> dict | None:
    """The tip-replay holdout result, which superseded the PF 6.61 backtest.

    The stage-3 backtest behind `base` was measured with the detector able to see
    bars to the right of its own box, so its PF 6.61 / 77.1% win rate describes a
    model conditioned on the printed future. Replaying tip-only put the same
    chain at PF 0.784. The overview must lead with the number that survived,
    because a headline of 6.61 under four green ticks reads as "accepted".
    """
    src = OUTPUT_DIR / "v16_holdout_verdict.json"
    if not src.exists():
        return None
    try:
        return json.loads(src.read_text()).get("summary")
    except Exception:  # noqa: BLE001 -- a broken file must not blank the dashboard
        return None


def _verdict_line(spec: UniverseSpec, base: dict, pf: float) -> str:
    honest = _honest_verdict()
    if honest:
        return (f"{spec.label} · tip-replay 终审 PF {honest.get('profit_factor', 0):.3f} · "
                f"{honest.get('n_trades', 0)} 笔 · "
                f"每笔净 {100 * honest.get('mean_net_per_trade', 0):+.3f}%")
    return (f"{spec.label} · ⚠️ 尚无 tip-replay 终审 · 前视净值图已下线")


def _overview_tiles(spec: UniverseSpec, base: dict, threshold: float) -> list[dict]:
    thr_sub = "val q90 · 回归 ACTIVE" if abs(threshold) < 0.2 else "val q90 · 二分类"
    honest = _honest_verdict()
    if honest:
        pf_tile = {
            "label": "PF（tip-replay 终审）",
            "value": "%.2f" % honest.get("profit_factor", 0),
            "sub": f"{honest.get('n_trades', 0)} 笔 · {honest.get('window', '')} · 线 1.3",
        }
        perf_tile = {
            "label": "每笔净 / 胜率",
            "value": f"{100 * honest.get('mean_net_per_trade', 0):+.3f}%",
            "sub": f"胜率 {100 * honest.get('win_rate', 0):.1f}% · tip-replay holdout",
        }
    else:
        pf_tile = {"label": "验收 PF", "value": "%.2f" % base.get("profit_factor", 0),
                   "sub": f"{BASE_COST * 100:.1f}% 成本 · 线 1.3 · ⚠️ 前视回测"}
        net = base.get("net_return_on_capital")
        perf_tile = {"label": "净收益 / 胜率",
                     "value": f"{100 * net:+.1f}%" if net is not None else "—",
                     "sub": f"胜率 {100 * base.get('win_rate', 0):.1f}% · ⚠️ 未经 tip-replay"}
    return [
        {"label": "宇宙", "value": spec.label, "sub": "主线 SWAP"},
        pf_tile,
        perf_tile,
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
    """Acceptance ticks, judged on the tip-replay result when one exists.

    Judging them on the look-ahead backtest printed four green ticks beside a PF
    of 6.61, which is the single most misleading thing this dashboard could
    show. The same four criteria against the honest replay fail, which is the
    truth of the matter.
    """
    honest = _honest_verdict()
    if honest:
        return {
            "net_positive": honest.get("total_net_units", 0) > 0,
            "profit_factor_ge_1.3": honest.get("profit_factor", 0) >= 1.3,
            "max_drawdown_le_20pct": False,      # not measured by the replay
            "n_trades_ge_100": honest.get("n_trades", 0) >= 100,
        }
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
