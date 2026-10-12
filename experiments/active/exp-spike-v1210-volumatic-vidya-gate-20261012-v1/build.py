"""Single-variable V12.10 Volumatic VIDYA admission study (owner task #47, 2026-10-12).

The only signal change is a same-timeframe direction gate using BigBeluga's
open-source Volumatic VIDYA defaults (length 10, momentum 20, SMA 15, ATR 200,
band distance 2). It is evaluated on the confirmed close. A long is admitted
only in the latched up state; a short only in the down state. The volume-delta
and pivot annotations do not add conditions.

This runner replays V12.9 raw facts through the unchanged serial V12.9 exit
engine. It does not post-filter completed trades: rejecting an earlier entry
must release occupancy so a later aligned signal can enter. Baseline trades
must match the saved ETH/alt V12.9 ledgers exactly before results are written.
The gate's random control is additionally restricted to bars in the same
VIDYA trend state, besides the existing same symbol/side/week/fold/ATR-close
strata. All entries fill next bar open and retain the 20bp round-trip cost.

Run after committing this builder, config, and V12.10 Pine source:
  PYTHONPATH=. .venv/bin/python experiments/active/exp-spike-v1210-volumatic-vidya-gate-20261012-v1/build.py
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from datetime import timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation import market_sync_shock as market
from yoyo.evaluation import spike_lowtf_v1_v126 as low
from yoyo.evaluation import spike_v128_recent as replay
from yoyo.evaluation import spike_v129_alts_confirm as prior_alts
from yoyo.evaluation import spike_v129_eth15m as eth15
from yoyo.evaluation import spike_v129_eth5m as eth5


ROOT = Path.cwd()
HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.json"
PINE_V129 = ROOT / "yoyo/evaluation/pine/spike_burst_v12_9.pine"
PINE_V1210 = ROOT / "yoyo/evaluation/pine/spike_burst_v12_10.pine"
OUT = HERE / "results_v1"
PRIOR_ALT_TRADES = Path("experiments/active/exp-spike-v129-alts-confirm-20261011-v1/results_v1/trades.csv.gz")
BASELINE_FIELDS = ("side", "entry_time", "entry_price", "initial_stop", "exit_time", "exit_price", "exit_reason", "net_r")
SOURCE_MODULES = (market, low, replay, prior_alts, eth15, eth5)
BOOTSTRAPS = 10_000


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def load_config() -> dict:
    cfg = json.loads(CONFIG.read_text())
    if cfg["experiment_id"] != HERE.name or cfg["execution"]["round_trip_cost"] != 0.002:
        raise ValueError("experiment identity or 20bp cost changed")
    if cfg["altcoins_binance"]["symbols"] != prior_alts.config()["expected_universe"]:
        raise ValueError("frozen 20-alt universe changed")
    if cfg["pine_source"] != str(PINE_V1210.relative_to(ROOT)):
        raise ValueError("V12.10 Pine path mismatch")
    return cfg


def committed_sources() -> dict[str, str]:
    paths = [CONFIG, Path(__file__).resolve(), PINE_V129, PINE_V1210]
    paths.extend(Path(module.__file__).resolve() for module in SOURCE_MODULES)
    paths.extend(Path(path).resolve() for path in replay._local_transitive_python((Path(__file__).resolve(),)))
    paths = list(dict.fromkeys(paths))
    for path in paths:
        rel = path.relative_to(ROOT)
        status = subprocess.run(["git", "status", "--porcelain", "--", str(rel)], text=True, capture_output=True, check=True)
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", str(rel)], text=True, capture_output=True)
        if status.stdout.strip() or tracked.returncode:
            raise ValueError(f"Commit every source before generating results: {rel}")
    return {str(path.relative_to(ROOT)): digest(path) for path in paths}


def _rma(values: np.ndarray, length: int) -> np.ndarray:
    """TradingView ta.rma seed: SMA of the first complete true-range window."""
    x = np.asarray(values, dtype=float)
    out = np.full(len(x), np.nan)
    if len(x) < length:
        return out
    out[length - 1] = float(np.mean(x[:length]))
    alpha = 1.0 / length
    for i in range(length, len(x)):
        out[i] = alpha * x[i] + (1.0 - alpha) * out[i - 1]
    return out


def volumatic_trend(frame: pd.DataFrame) -> dict[str, np.ndarray]:
    """Causal Python port of the documented BigBeluga default trend-state path."""
    close = frame.close.to_numpy(float)
    high = frame.high.to_numpy(float)
    low_ = frame.low.to_numpy(float)
    n = len(close)
    momentum = np.diff(close, prepend=np.nan)
    positive = pd.Series(np.where(momentum >= 0, momentum, 0.0)).rolling(20).sum().to_numpy(float)
    negative = pd.Series(np.where(momentum >= 0, 0.0, -momentum)).rolling(20).sum().to_numpy(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        cmo = np.abs(100.0 * (positive - negative) / (positive + negative))
    alpha = 2.0 / 11.0
    vidya_raw = np.full(n, np.nan)
    previous = 0.0
    for i in range(n):
        if not np.isfinite(cmo[i]):
            previous = 0.0
            continue
        k = alpha * cmo[i] / 100.0
        previous = k * close[i] + (1.0 - k) * previous
        vidya_raw[i] = previous
    vidya = pd.Series(vidya_raw).rolling(15).mean().to_numpy(float)

    prior_close = np.roll(close, 1)
    true_range = np.maximum(high - low_, np.maximum(np.abs(high - prior_close), np.abs(low_ - prior_close)))
    if n:
        true_range[0] = high[0] - low_[0]
    atr = _rma(true_range, 200)
    upper, lower = vidya + 2.0 * atr, vidya - 2.0 * atr
    up = np.zeros(n, dtype=bool)
    state = False
    for i in range(1, n):
        cross_up = close[i] > upper[i] and close[i - 1] <= upper[i - 1]
        cross_down = close[i] < lower[i] and close[i - 1] >= lower[i - 1]
        if cross_up:
            state = True
        if cross_down:
            state = False
        up[i] = state
    ready = np.isfinite(vidya) & np.isfinite(atr) & np.isfinite(upper) & np.isfinite(lower)
    return {"up": up, "ready": ready, "vidya": vidya, "atr200": atr, "upper": upper, "lower": lower}


def _make_events(facts: dict, frame: pd.DataFrame, identity: dict, key: str,
                 minutes: int, start: pd.Timestamp, end: pd.Timestamp) -> list[dict]:
    events = []
    for i in np.flatnonzero(facts["v9"]):
        close_time = frame.index[i] + pd.Timedelta(minutes=minutes)
        if close_time >= end:
            continue
        events.append({**identity, "arm": "ordinary", "signal_i": int(i), "side": int(facts["side"][i]),
                       "signal_bar_open": frame.index[i], "signal_close": close_time,
                       "in_window": bool(close_time >= start), "signal_price": float(frame.close.iloc[i]),
                       "signal_atr_pct": float(frame.atr.iloc[i] / frame.close.iloc[i]),
                       "trade_key": f"{key}:ordinary:{close_time.isoformat()}"})
    return events


def _condition(events: list[dict], trend: dict[str, np.ndarray]) -> tuple[list[dict], dict]:
    admitted, counts = [], {"raw_candidates": 0, "admitted_candidates": 0, "rejected_opposite": 0, "rejected_unready": 0,
                           "raw_long": 0, "raw_short": 0, "admitted_long": 0, "admitted_short": 0}
    for event in events:
        i, side = int(event["signal_i"]), int(event["side"])
        allowed = bool(trend["ready"][i] and (trend["up"][i] == (side == 1)))
        if event["in_window"]:
            counts["raw_candidates"] += 1
            counts["raw_long" if side == 1 else "raw_short"] += 1
            if allowed:
                counts["admitted_candidates"] += 1
                counts["admitted_long" if side == 1 else "admitted_short"] += 1
            elif not trend["ready"][i]:
                counts["rejected_unready"] += 1
            else:
                counts["rejected_opposite"] += 1
        if allowed:
            admitted.append({**event, "vidya_up": bool(trend["up"][i]), "vidya_ready": True})
    return admitted, counts


def _same_regime_controls(prepared, trades: list[dict], cfg: dict, trend: dict[str, np.ndarray]) -> pd.DataFrame:
    """Same-symbol V12.9 control, additionally conditioned on the trade's VIDYA state."""
    if not trades:
        return pd.DataFrame()
    frame, minutes = prepared.frame, int(prepared.context.minutes)
    close_time = frame.index + pd.Timedelta(minutes=minutes)
    week = np.asarray(close_time.strftime("%G-W%V"))
    case_cfg = cfg["eth_okx"] if trades[0].get("venue") == "okx" else cfg["altcoins_binance"]
    split = pd.Timestamp(case_cfg["split"])
    fold = np.where(close_time < split, "earlier", "later")
    bins = np.searchsorted(np.asarray(cfg["execution"]["volatility_bins"], float), prepared.atr / prepared.close, side="left")
    ready = frame.get("ready", pd.Series(False, index=frame.index)).fillna(False).to_numpy(bool)
    valid = (ready & trend["ready"] & np.isfinite(prepared.atr) & (prepared.atr > 0)
             & np.isfinite(prepared.close) & (prepared.close > 0) & ~prepared.gap
             & (close_time >= pd.Timestamp(case_cfg["start"])) & (close_time < pd.Timestamp(case_cfg["end"])))
    pools: dict[tuple, np.ndarray] = {}
    cache: dict[tuple[int, int], tuple[str, dict | None]] = {}
    rows = []
    for trade in trades:
        i, side = int(trade["signal_i"]), int(trade["side"])
        stratum = (side, str(week[i]), str(fold[i]), int(bins[i]))
        pool_key = (*stratum,)
        if pool_key not in pools:
            regime = trend["up"] if side == 1 else ~trend["up"]
            pools[pool_key] = np.flatnonzero(valid & regime & (week == stratum[1]) & (fold == stratum[2]) & (bins == stratum[3]))
        choices = pools[pool_key][pools[pool_key] != i]
        chosen = None
        if len(choices):
            token = f"{cfg['execution']['control_seed']}|{trade['trade_key']}"
            chosen = int(choices[int(hashlib.sha256(token.encode()).hexdigest(), 16) % len(choices)])
        status, control = "empty_stratum", None
        if chosen is not None:
            cache_key = (chosen, side)
            if cache_key not in cache:
                cache[cache_key] = replay.attempt(prepared, chosen, side)
            status, control = cache[cache_key]
        matched = control is not None and status == "closed" and not bool(trade.get("censored", False))
        rows.append({"trade_key": trade["trade_key"], "venue": trade["venue"], "symbol": trade["symbol"],
                     "timeframe_min": minutes, "side": side, "utc_week": stratum[1], "fold": stratum[2],
                     "vol_bin": stratum[3], "vidya_up": bool(trend["up"][i]), "matched": bool(matched),
                     "reason": "matched" if matched else ("target_censored" if bool(trade.get("censored", False)) else status),
                     "control_sig": chosen,
                     "control_signal_close": None if chosen is None else close_time[chosen],
                     "control_exit_time": None if control is None else control.get("exit_time"),
                     "control_exit_reason": None if control is None else control.get("exit_reason"),
                     "control_net_r": math.nan if not matched else float(control["net_r"]),
                     "control_net_return": math.nan if not matched else float(control["net_return"])})
    return pd.DataFrame(rows)


def _baseline_controls(prepared, trades: list[dict], cfg: dict) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame()
    if trades[0].get("venue") == "okx":
        control_cfg = eth5.config() if int(trades[0]["timeframe_min"]) == 5 else eth15.config()
    else:
        control_cfg = prior_alts.config()
    return replay.matched_controls(prepared, trades, control_cfg)


def _case(base: pd.DataFrame, *, symbol: str, venue: str, asset: str, tick: float, minutes: int,
          start: pd.Timestamp, split: pd.Timestamp, end: pd.Timestamp, cfg: dict,
          source_path: Path | None = None) -> tuple[dict, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    bars, partial = low.complete_bars(base, minutes)
    if partial:
        raise ValueError(f"partial {minutes}m bars for {symbol}: {partial}")
    if venue == "okx" and minutes == 5:
        facts = low.pine_facts(bars, bars, asset, tick, 5)
    elif venue == "okx":
        facts = replay.facts_for(bars, base, asset, tick, minutes)
    else:
        facts = prior_alts.facts_for(base, asset, tick, minutes)
    frame = facts["frame"]
    identity = {"venue": venue, "symbol": symbol, "asset": asset, "timeframe_min": minutes}
    key = f"{venue}:{symbol}:{minutes}m"
    prepared = replay.source.prepared_arm(frame, facts["gap"], facts["side"], key, identity, minutes, tick)
    events = _make_events(facts, frame, identity, key, minutes, start, end)
    trend = volumatic_trend(frame)
    gated_events, gate_counts = _condition(events, trend)
    baseline_trades, baseline_statuses = replay.serial(prepared, events, "ordinary", key)
    gated_trades, gated_statuses = replay.serial(prepared, gated_events, "ordinary", key)
    for variant, trades in (("baseline", baseline_trades), ("vidya_gate", gated_trades)):
        closed = [t for t in trades if not bool(t["censored"])]
        if closed:
            np.testing.assert_allclose([t["gross_return"] - t["net_return"] for t in closed], 0.002, atol=1e-12)
            np.testing.assert_allclose([t["net_r"] for t in closed],
                                       [t["net_return"] / t["initial_risk_frac"] for t in closed], atol=1e-10)
        for t in trades:
            t["variant"] = variant
            t["vidya_up_at_signal"] = bool(trend["up"][int(t["signal_i"])])
            t["vidya_ready_at_signal"] = bool(trend["ready"][int(t["signal_i"])])
    baseline_controls = _baseline_controls(prepared, baseline_trades, cfg)
    baseline_controls["variant"] = "baseline"
    gated_controls = _same_regime_controls(prepared, gated_trades, cfg, trend)
    gated_controls["variant"] = "vidya_gate"
    diagnostics = {"venue": venue, "symbol": symbol, "timeframe_min": minutes, "first_bar": frame.index[0],
                   "last_bar": frame.index[-1], "bars": len(frame), "data_gaps": int(np.asarray(facts["gap"]).sum()),
                   **gate_counts,
                   "baseline_trades_opened": len(baseline_trades), "gated_trades_opened": len(gated_trades),
                   "baseline_trades_closed": sum(not bool(t["censored"]) for t in baseline_trades),
                   "gated_trades_closed": sum(not bool(t["censored"]) for t in gated_trades),
                   "baseline_skipped_in_position": sum(s["status"] == "skipped_in_position" for s in baseline_statuses),
                   "gated_skipped_in_position": sum(s["status"] == "skipped_in_position" for s in gated_statuses),
                   "data_path": None if source_path is None else str(source_path),
                   "data_sha256": None if source_path is None else digest(source_path)}
    return diagnostics, pd.DataFrame(baseline_trades), pd.DataFrame(gated_trades), baseline_controls, gated_controls


def _assert_parent_parity(actual: pd.DataFrame, parent: pd.DataFrame, label: str) -> dict:
    if "arm" in parent:
        parent = parent.loc[parent.arm.eq("ordinary")]
    parent = parent.loc[~parent.censored.astype(bool)].copy() if "censored" in parent else parent.copy()
    actual = actual.loc[~actual.censored.astype(bool)].copy()
    parent["signal_i"] = parent.signal_i.astype(int)
    actual["signal_i"] = actual.signal_i.astype(int)
    left = parent.set_index("signal_i").sort_index()
    right = actual.set_index("signal_i").sort_index()
    if set(left.index) != set(right.index):
        missing = sorted(set(left.index) - set(right.index))[:5]
        extra = sorted(set(right.index) - set(left.index))[:5]
        raise ValueError(f"{label} saved-ledger parity failed; missing={missing}, extra={extra}")
    l, r = left[list(BASELINE_FIELDS)].copy(), right[list(BASELINE_FIELDS)].copy()
    for col in ("entry_time", "exit_time"):
        l[col] = pd.to_datetime(l[col], utc=True)
        r[col] = pd.to_datetime(r[col], utc=True)
    for col in ("entry_price", "initial_stop", "exit_price", "net_r"):
        np.testing.assert_allclose(l[col].to_numpy(float), r[col].to_numpy(float), rtol=1e-10, atol=1e-10,
                                   err_msg=f"{label} {col} drift")
    if not l[["side", "entry_time", "exit_time", "exit_reason"]].equals(r[["side", "entry_time", "exit_time", "exit_reason"]]):
        raise ValueError(f"{label} saved-ledger identity fields differ")
    return {"label": label, "closed_trades_checked": len(left), "exact": True}


def _metric(trades: pd.DataFrame, controls: pd.DataFrame, start: pd.Timestamp, split: pd.Timestamp,
            end: pd.Timestamp, seed: int) -> dict:
    if trades.empty:
        return {"closed": 0, "periods": {}}
    t = trades.loc[~trades.censored.astype(bool)].copy()
    if t.empty:
        return {"closed": 0, "periods": {}}
    t["signal_close"] = pd.to_datetime(t.signal_close, utc=True)
    t["exit_time"] = pd.to_datetime(t.exit_time, utc=True)
    control_map = controls.set_index("trade_key").control_net_r if not controls.empty else pd.Series(dtype=float)
    t["control_net_r"] = t.trade_key.map(control_map)
    t["excess_r"] = t.net_r - t.control_net_r
    t["month"] = t.signal_close.dt.strftime("%Y-%m")
    masks = {"full": (t.signal_close >= start) & (t.signal_close < end),
             "before_split": (t.signal_close >= start) & (t.signal_close < split),
             "from_split": (t.signal_close >= split) & (t.signal_close < end)}
    periods = {}
    for label, mask in masks.items():
        g = t.loc[mask].sort_values(["exit_time", "trade_key"])
        if g.empty:
            periods[label] = {"closed": 0}
            continue
        r = g.net_r.to_numpy(float)
        gross = g.gross_r.to_numpy(float)
        positive, negative = r[r > 0].sum(), -r[r < 0].sum()
        cumulative = np.cumsum(r)
        peak = np.maximum.accumulate(np.concatenate([[0.0], cumulative]))[1:]
        matched = g.loc[np.isfinite(g.control_net_r.to_numpy(float))]
        diff = matched.excess_r.to_numpy(float)
        months = matched.month.to_numpy(str)
        periods[label] = {
            "closed": int(len(g)), "wins": int((r > 0).sum()), "win_rate": float((r > 0).mean()),
            "mean_net_r": float(np.mean(r)), "sum_net_r": float(np.sum(r)), "mean_gross_r": float(np.mean(gross)),
            "mean_net_bp": float(g.net_return.mean() * 10_000),
            "mean_cost_r": float((g.gross_r - g.net_r).mean()),
            "profit_factor_r": None if negative == 0 else float(positive / negative),
            "max_drawdown_r": float(np.min(cumulative - peak)),
            "median_risk_pct": float(g.initial_risk_frac.median() * 100),
            "max_consecutive_losses": _max_streak(r < 0),
            "matched_controls": int(len(matched)),
            "control_mean_net_r": None if not len(matched) else float(matched.control_net_r.mean()),
            "excess_mean_r": None if not len(matched) else float(np.mean(diff)),
            "excess_month_block_p": None if not len(matched) else float(market.month_block_sign_flip(diff, months, seed, BOOTSTRAPS)),
            "excess_month_block_ci95": _block_ci(diff, months, seed + 1) if len(matched) else None,
        }
    return {"closed": int(len(t)), "periods": periods}


def _max_streak(loss: np.ndarray) -> int:
    best = run = 0
    for item in loss:
        run = run + 1 if item else 0
        best = max(best, run)
    return best


def _block_ci(values: np.ndarray, months: np.ndarray, seed: int) -> list[float] | None:
    values = np.asarray(values, dtype=float)
    months = np.asarray(months, dtype=str)
    keep = np.isfinite(values)
    values, months = values[keep], months[keep]
    keys = np.unique(months)
    if not len(values) or not len(keys):
        return None
    sums = np.array([values[months == key].sum() for key in keys])
    counts = np.array([(months == key).sum() for key in keys])
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(keys), size=(BOOTSTRAPS, len(keys)))
    means = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    return [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))]


def _add_summaries(case_rows, baseline_all, gated_all, baseline_controls_all, gated_controls_all, cfg):
    case_summary, per_coin = {}, []
    for row in case_rows:
        key = f"{row['venue']}:{row['symbol']}:{row['timeframe_min']}m"
        start = pd.Timestamp(cfg["eth_okx"]["start"] if row["venue"] == "okx" else cfg["altcoins_binance"]["start"])
        split = pd.Timestamp(cfg["eth_okx"]["split"] if row["venue"] == "okx" else cfg["altcoins_binance"]["split"])
        end = pd.Timestamp(cfg["eth_okx"]["end"] if row["venue"] == "okx" else cfg["altcoins_binance"]["end"])
        case_summary[key] = {"candidate_gate": {k: row[k] for k in (
            "raw_candidates", "admitted_candidates", "rejected_opposite", "rejected_unready", "raw_long", "raw_short",
            "admitted_long", "admitted_short", "baseline_trades_opened", "gated_trades_opened",
            "baseline_trades_closed", "gated_trades_closed", "baseline_skipped_in_position", "gated_skipped_in_position")},
            "baseline": _metric(baseline_all.loc[(baseline_all.venue == row["venue"]) & (baseline_all.symbol == row["symbol"])
                                                  & (baseline_all.timeframe_min == row["timeframe_min"])],
                                 baseline_controls_all.loc[(baseline_controls_all.venue == row["venue"])
                                                           & (baseline_controls_all.symbol == row["symbol"])
                                                           & (baseline_controls_all.timeframe_min == row["timeframe_min"])],
                                 start, split, end, cfg["execution"]["stat_seed"]),
            "vidya_gate": _metric(gated_all.loc[(gated_all.venue == row["venue"]) & (gated_all.symbol == row["symbol"])
                                                & (gated_all.timeframe_min == row["timeframe_min"])],
                                   gated_controls_all.loc[(gated_controls_all.venue == row["venue"])
                                                         & (gated_controls_all.symbol == row["symbol"])
                                                         & (gated_controls_all.timeframe_min == row["timeframe_min"])],
                                   start, split, end, cfg["execution"]["stat_seed"] + 1)}
        for variant in ("baseline", "vidya_gate"):
            metrics = case_summary[key][variant]["periods"]
            for period in ("before_split", "from_split", "full"):
                m = metrics.get(period, {"closed": 0})
                per_coin.append({"venue": row["venue"], "symbol": row["symbol"], "timeframe_min": row["timeframe_min"],
                                 "variant": variant, "period": period, "closed": m.get("closed", 0),
                                 "win_rate": m.get("win_rate"), "mean_net_r": m.get("mean_net_r"),
                                 "sum_net_r": m.get("sum_net_r"), "profit_factor_r": m.get("profit_factor_r"),
                                 "control_mean_net_r": m.get("control_mean_net_r"), "excess_mean_r": m.get("excess_mean_r"),
                                 "excess_month_block_p": m.get("excess_month_block_p")})

    pooled = {}
    alts = [r for r in case_rows if r["venue"] == "binance"]
    for minutes in (5, 15):
        for variant, all_trades, all_controls in (("baseline", baseline_all, baseline_controls_all),
                                                   ("vidya_gate", gated_all, gated_controls_all)):
            select = all_trades.loc[(all_trades.venue == "binance") & (all_trades.timeframe_min == minutes)]
            controls = all_controls.loc[(all_controls.venue == "binance") & (all_controls.timeframe_min == minutes)]
            start, split, end = (pd.Timestamp(cfg["altcoins_binance"][k]) for k in ("start", "split", "end"))
            summary = _metric(select, controls, start, split, end, cfg["execution"]["stat_seed"] + (0 if variant == "baseline" else 1))
            # Per-trade pooled R is descriptive. With overlapping symbols and no
            # common capital allocation, a stitched multi-coin path has no valid
            # portfolio drawdown or consecutive-loss interpretation.
            summary.pop("max_drawdown_r", None)
            summary.pop("max_consecutive_losses", None)
            summary["aggregation_note"] = (
                "Trade-weighted pool across symbols; no portfolio drawdown or loss streak is reported "
                "because positions overlap across coins without a frozen capital allocation."
            )
            summary["coins_positive_by_period"] = {}
            for period, boundary in (("before_split", (start, split)), ("from_split", (split, end)), ("full", (start, end))):
                lo, hi = boundary
                subset = select.loc[~select.censored.astype(bool)].copy()
                subset["signal_close"] = pd.to_datetime(subset.signal_close, utc=True)
                subset = subset.loc[(subset.signal_close >= lo) & (subset.signal_close < hi)]
                coinmeans = subset.groupby("symbol").net_r.mean()
                summary["coins_positive_by_period"][period] = int((coinmeans > 0).sum())
            pooled[f"{minutes}m_{variant}"] = summary
    primary = {}
    for minutes in (5, 15):
        periods = pooled[f"{minutes}m_vidya_gate"].get("periods", {})
        checks = {}
        for period in ("before_split", "from_split"):
            metrics = periods.get(period, {})
            checks[period] = {
                "closed": metrics.get("closed", 0),
                "mean_net_r_positive": bool(metrics.get("mean_net_r", 0) is not None and metrics.get("mean_net_r", 0) > 0),
                "excess_mean_r_positive": bool(metrics.get("excess_mean_r", 0) is not None and metrics.get("excess_mean_r", 0) > 0),
                "month_block_p_below_0_01": bool(metrics.get("excess_month_block_p") is not None and metrics["excess_month_block_p"] < 0.01),
            }
            checks[period]["passes"] = all((checks[period]["closed"] > 0,
                                               checks[period]["mean_net_r_positive"],
                                               checks[period]["excess_mean_r_positive"],
                                               checks[period]["month_block_p_below_0_01"]))
        primary[f"{minutes}m"] = {"periods": checks, "passes_both_periods": all(x["passes"] for x in checks.values())}
    return {"cases": case_summary, "alt_pool": pooled, "primary_assessment": primary}, pd.DataFrame(per_coin)


def _eth_base(cfg: dict) -> tuple[pd.DataFrame, dict]:
    base_cfg = eth5.config()
    receipt = eth5.prepare_data(base_cfg)
    inputs = {str(Path(path)): expected for path, expected in receipt["inputs"].items()}
    for path, expected in inputs.items():
        if digest(Path(path)) != expected:
            raise ValueError(f"OKX ETH input hash drift: {path}")
    base = low.load_base([Path(path) for path in inputs], pd.Timestamp(cfg["eth_okx"]["end"]))
    return base, {"receipt": str(Path(base_cfg["data_dir"]) / "receipt.json"), "inputs": inputs,
                  "rows_1m": len(base), "first": str(base.index[0]), "last": str(base.index[-1])}


def _run_alts(cfg: dict, workers: int) -> list:
    jobs = [(symbol, cfg) for symbol in cfg["altcoins_binance"]["symbols"]]
    rows = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_scan_alt, job) for job in jobs]
        for index, future in enumerate(futures, 1):
            partial = future.result()
            rows.extend(partial)
            print(json.dumps({"alts_done": index, "alts_total": len(jobs), "symbol": jobs[index - 1][0],
                              "cases": len(partial)}), flush=True)
    return rows


def _scan_alt(job) -> list:
    symbol, cfg = job
    acfg = cfg["altcoins_binance"]
    base = prior_alts.base5m(symbol, prior_alts.config())
    meta = replay.source.symbol_meta()[symbol]
    series_path = Path(market.SERIES) / f"{symbol}.csv.gz"
    rows = []
    for minutes in acfg["timeframes"]:
        row, baseline, gated, bcontrol, gcontrol = _case(
            base, symbol=symbol, venue="binance", asset=str(meta["asset"]), tick=float(meta["tick"]),
            minutes=int(minutes), start=pd.Timestamp(acfg["start"]), split=pd.Timestamp(acfg["split"]),
            end=pd.Timestamp(acfg["end"]), cfg=cfg, source_path=series_path)
        # Return case data through deterministic task-local serialized tables to avoid large IPC copies.
        rows.append({"row": row, "baseline": baseline, "gated": gated,
                     "baseline_controls": bcontrol, "gated_controls": gcontrol})
    return rows


def _run_eth(cfg: dict) -> tuple[list, dict]:
    base, data_info = _eth_base(cfg)
    ecfg = cfg["eth_okx"]
    meta = replay.source.symbol_meta()["ETHUSDT"]
    cases = []
    for minutes in ecfg["timeframes"]:
        row, baseline, gated, bcontrol, gcontrol = _case(
            base, symbol=ecfg["symbol"], venue="okx", asset=ecfg["asset"], tick=float(ecfg["tick"]),
            minutes=int(minutes), start=pd.Timestamp(ecfg["start"]), split=pd.Timestamp(ecfg["split"]),
            end=pd.Timestamp(ecfg["end"]), cfg=cfg)
        if int(minutes) == 5:
            parent_path = ROOT / cfg["eth_okx"]["baseline_experiments"]["5"]
        else:
            parent_path = ROOT / cfg["eth_okx"]["baseline_experiments"]["15"]
        parent = pd.read_csv(parent_path)
        parity = _assert_parent_parity(baseline, parent, f"OKX ETH {minutes}m")
        row["saved_v129_parity"] = parity
        row["saved_v129_receipt"] = str(parent_path)
        cases.append({"row": row, "baseline": baseline, "gated": gated,
                      "baseline_controls": bcontrol, "gated_controls": gcontrol})
    return cases, data_info


def _assert_alt_parity(rows: list) -> list[dict]:
    parent = pd.read_csv(PRIOR_ALT_TRADES)
    evidence = []
    for item in rows:
        row = item["row"]
        expected = parent.loc[parent.symbol.eq(row["symbol"]) & parent.timeframe_min.eq(row["timeframe_min"])]
        evidence.append(_assert_parent_parity(item["baseline"], expected,
                                              f"Binance {row['symbol']} {row['timeframe_min']}m"))
    return evidence


def _write(cfg: dict, eth_cases: list, alt_cases: list, data_info: dict, source_hashes: dict,
           alt_parity: list[dict], elapsed: float) -> None:
    cases = eth_cases + alt_cases
    rows = [x["row"] for x in cases]
    baseline = pd.concat([x["baseline"] for x in cases if not x["baseline"].empty], ignore_index=True)
    gated = pd.concat([x["gated"] for x in cases if not x["gated"].empty], ignore_index=True)
    base_controls = pd.concat([x["baseline_controls"] for x in cases if not x["baseline_controls"].empty], ignore_index=True)
    gate_controls = pd.concat([x["gated_controls"] for x in cases if not x["gated_controls"].empty], ignore_index=True)
    OUT.mkdir(parents=True, exist_ok=True)
    for name, frame in (("baseline_trades.csv.gz", baseline), ("vidya_gate_trades.csv.gz", gated),
                        ("baseline_controls.csv.gz", base_controls), ("vidya_gate_controls.csv.gz", gate_controls)):
        frame.to_csv(OUT / name, index=False, compression={"method": "gzip", "mtime": 0})
    pd.DataFrame(rows).to_csv(OUT / "candidate_diagnostics.csv", index=False)
    summary, per_coin = _add_summaries(rows, baseline, gated, base_controls, gate_controls, cfg)
    per_coin.to_csv(OUT / "per_coin.csv", index=False)
    _dump(OUT / "summary.json", summary)
    artifacts = ["baseline_trades.csv.gz", "vidya_gate_trades.csv.gz", "baseline_controls.csv.gz",
                 "vidya_gate_controls.csv.gz", "candidate_diagnostics.csv", "per_coin.csv", "summary.json"]
    parity_eth = [x["row"]["saved_v129_parity"] for x in eth_cases]
    receipt = {"experiment_id": cfg["experiment_id"], "run": "v1", "source_commit": _git_head(),
               "config_sha256": digest(CONFIG), "source_sha256": source_hashes, "data": data_info,
               "saved_baseline_parity": {"eth": parity_eth,
                                            "alts": {"checked": len(alt_parity), "all_exact": all(x["exact"] for x in alt_parity),
                                                     "closed_trades_checked": sum(x["closed_trades_checked"] for x in alt_parity)}},
               "cases": len(rows), "baseline_rows": len(baseline), "vidya_gate_rows": len(gated),
               "generated_at": pd.Timestamp.now(tz="UTC").isoformat(), "elapsed_s": round(elapsed, 1),
               "artifacts_sha256": {name: digest(OUT / name) for name in artifacts}}
    _dump(OUT / "receipt.json", receipt)
    print(json.dumps({"saved_baseline_parity": receipt["saved_baseline_parity"], "cases": len(rows),
                      "baseline_rows": len(baseline), "vidya_gate_rows": len(gated),
                      "alt_pool": summary["alt_pool"]}, ensure_ascii=False, indent=2), flush=True)


def _dump(path: Path, value) -> None:
    path.write_text(json.dumps(clean(value), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n")


def _git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def run(workers: int) -> None:
    began = time.perf_counter()
    cfg = load_config()
    source_hashes = committed_sources()
    eth_cases, data_info = _run_eth(cfg)
    alt_cases = _run_alts(cfg, workers)
    alt_parity = _assert_alt_parity(alt_cases)
    _write(cfg, eth_cases, alt_cases, data_info, source_hashes, alt_parity, time.perf_counter() - began)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Run frozen V12.10 Volumatic VIDYA gate replay")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    if args.workers < 1 or args.workers > 20:
        raise ValueError("workers must be between 1 and 20")
    run(args.workers)


if __name__ == "__main__":
    main()
