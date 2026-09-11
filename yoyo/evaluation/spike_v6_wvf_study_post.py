"""Canonical post-processing for committed SPIKE V6/WVF raw ledgers.

This preserves the runner's raw files and applies the documented half-open fold
clock to signal denominators.  It does not recreate indicators or oracle state.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import hashlib

import numpy as np

import pandas as pd

from yoyo.evaluation.spike_v6_wvf_study import ExecutionSpec, _initial_position


FOLD_END = {"development": pd.Timestamp("2025-09-10T00:00:00Z"),
            "validation": pd.Timestamp("2026-09-10T00:00:00Z")}


def evaluate_single_control(cache: dict, candidate_i: int, side: int, *, tick: float,
                            fold_start: pd.Timestamp, fold_end: pd.Timestamp) -> dict | None:
    """Evaluate one candidate only; raw opposite V6 events may exit, never enter."""
    bars, signals = cache["bars"], cache["signals"]
    gap = cache.get("data_gap", pd.Series(False, index=bars.index)).to_numpy(dtype=bool)
    spec = ExecutionSpec(tick=tick)
    pos = _initial_position(bars, candidate_i, side, spec, pd.Series(gap, index=bars.index))
    if pos is None or pos["entry_time"] < fold_start or pos["entry_time"] >= fold_end:
        return None
    o, h, l, c, a = (bars[x].to_numpy(float) for x in ("open", "high", "low", "close", "atr"))
    long, short = signals.long_signal.to_numpy(bool), signals.short_signal.to_numpy(bool)
    for j in range(candidate_i + 1, len(bars)):
        if bars.index[j] >= fold_end or gap[j]:
            pos["censored"] = True; return pos
        stop = float(pos["protection"])
        if (l[j] <= stop if side == 1 else h[j] >= stop):
            price = min(o[j], stop) if side == 1 else max(o[j], stop); gross = side*(price/float(pos["entry_price"])-1)
            pos.update(exit_time=bars.index[j], exit_price=price, censored=False, net_return=gross-spec.round_trip_cost, net_r=(gross-spec.round_trip_cost)/float(pos["initial_risk_frac"])); return pos
        pos["mfe_r"] = max(float(pos["mfe_r"]), side*((h[j] if side == 1 else l[j])-float(pos["entry_price"]))/float(pos["initial_risk"]))
        current_r = side*(c[j]-float(pos["entry_price"]))/float(pos["initial_risk"])
        pos["trail_armed"] = bool(pos["trail_armed"]) or current_r >= spec.arm_r
        if bool(pos["trail_armed"]) and np.isfinite(a[j]) and a[j] > 0:
            candidate = (np.floor((c[j]-side*spec.trail_atr*a[j])/tick)*tick if side == 1 else np.ceil((c[j]-side*spec.trail_atr*a[j])/tick)*tick)
            pos["protection"] = max(stop, candidate) if side == 1 else min(stop, candidate)
        if (short[j] if side == 1 else long[j]):
            if j + 1 >= len(bars) or bars.index[j + 1] >= fold_end or gap[j + 1]:
                pos["censored"] = True; return pos
            price = o[j+1]; gross = side*(price/float(pos["entry_price"])-1)
            pos.update(exit_time=bars.index[j+1], exit_price=price, censored=False, net_return=gross-spec.round_trip_cost, net_r=(gross-spec.round_trip_cost)/float(pos["initial_risk_frac"])); return pos
    pos["censored"] = True
    return pos


def matched_random_controls(cache: dict, targets: pd.DataFrame, *, tick: float,
                            fold_start: pd.Timestamp, fold_end: pd.Timestamp,
                            seeds=range(99)) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return fixed-seed, causal matched single-event controls from one cache.

    ``targets`` supplies actual strategy trades with ``signal_bar_open`` and
    ``side``.  Controls match calendar month and a bucket built from *prior*
    120-bar ATR/close quantiles.  Each candidate is replayed once per
    (candidate, side) and cached; its path keeps original opposite-V6 exits and
    the same frozen stop/trail/cost.  Results are paired event differences, not
    a portfolio or account-return null.
    """
    bars, signals = cache["bars"], cache["signals"]
    gap = cache.get("data_gap", pd.Series(False, index=bars.index))
    if not {"signal_bar_open", "side", "net_r", "net_return"}.issubset(targets):
        raise ValueError("targets need signal_bar_open, side, net_r and net_return")
    atr_fraction = bars.atr / bars.close
    prior = atr_fraction.shift(1)
    quantiles = pd.concat([prior.rolling(120, min_periods=120).quantile(q) for q in (.25, .5, .75)], axis=1)
    qvalues = quantiles.to_numpy()
    bucket = (atr_fraction.to_numpy()[:, None] > qvalues).sum(axis=1)
    eligible = (bars.ready.astype(bool) & pd.Series(np.isfinite(qvalues).all(axis=1), index=bars.index)
                & bars.index.to_series().shift(-1).notna())
    # 5 complete structural bars and the next observed open are mandatory.
    valid = np.isfinite(bars[["open", "high", "low", "close", "atr"]].to_numpy()).all(axis=1)
    cadence = pd.Timedelta(minutes=int(bars.attrs["minutes"]))
    observed_gap = bars.index.to_series().diff().ne(cadence)
    observed_gap.iloc[0] = False
    combined_gap = pd.Series(gap, index=bars.index).astype(bool) | observed_gap
    continuous = ~combined_gap.rolling(5, min_periods=5).max().fillna(1).astype(bool)
    next_valid = pd.Series(valid, index=bars.index).shift(-1).fillna(False).astype(bool)
    next_contiguous = ~combined_gap.shift(-1).fillna(True).astype(bool)
    confirm_time = bars.index + cadence
    next_open_time = bars.index.to_series().shift(-1)
    eligible &= (pd.Series(valid, index=bars.index).rolling(5, min_periods=5).min().eq(1)
                 & continuous & next_valid & next_contiguous & bars.atr.gt(0) & bars.close.gt(0)
                 & (confirm_time >= fold_start) & (confirm_time < fold_end)
                 & next_open_time.ge(fold_start) & next_open_time.lt(fold_end))
    months = bars.index.strftime("%Y-%m")
    cache_paths: dict[tuple[int, int, pd.Timestamp], dict] = {}
    month_bucket: dict[tuple[object, int], np.ndarray] = {}

    def path(candidate_i: int, side: int) -> dict | None:
        key = candidate_i, side, fold_end
        if key not in cache_paths:
            cache_paths[key] = evaluate_single_control(cache, candidate_i, side, tick=tick, fold_start=fold_start, fold_end=fold_end)
        return cache_paths[key]

    rows = []
    targets = targets.copy()
    targets["signal_bar_open"] = pd.to_datetime(targets.signal_bar_open, utc=True)
    for seed in seeds:
        for target in targets.itertuples(index=False):
            stamp, side = target.signal_bar_open, int(target.side)
            if stamp not in bars.index:
                rows.append({"seed": seed, "target_time": stamp, "side": side, "matched": False, "reason": "target_not_in_cache"})
                continue
            i = int(bars.index.get_loc(stamp))
            if not np.isfinite(qvalues[i]).all():
                rows.append({"seed": seed, "target_time": stamp, "side": side, "matched": False, "reason": "target_bucket_unavailable"})
                continue
            key = stamp.strftime("%Y-%m"), int(bucket[i])
            if key not in month_bucket:
                month_bucket[key] = np.flatnonzero(eligible.to_numpy() & (months == key[0]) & (bucket == key[1]))
            candidates = month_bucket[key]
            candidates = candidates[candidates != i]
            if not len(candidates):
                rows.append({"seed": seed, "target_time": stamp, "side": side, "matched": False, "reason": "no_exact_causal_match"})
                continue
            chosen = candidates[int(hashlib.sha256(f"{seed}|{stamp.isoformat()}".encode()).hexdigest(), 16) % len(candidates)]
            control = path(int(chosen), side)
            if control is None or bool(control.get("censored", False)):
                rows.append({"seed": seed, "target_time": stamp, "side": side, "matched": False, "reason": "control_unresolved"})
                continue
            rows.append({"seed": seed, "target_time": stamp, "side": side, "matched": True, "reason": "matched",
                         "candidate_time": bars.index[chosen], "target_net_r": target.net_r,
                         "control_net_r": control["net_r"], "net_r_difference": target.net_r - control["net_r"],
                         "target_net_return": target.net_return, "control_net_return": control["net_return"],
                         "net_return_difference": target.net_return - control["net_return"]})
    matches = pd.DataFrame(rows)
    for column in ("net_r_difference", "net_return_difference"):
        if column not in matches:
            matches[column] = np.nan
    summary = matches.groupby("seed").agg(total=("matched", "size"), matched=("matched", "sum"),
        mean_net_r_difference=("net_r_difference", "mean"), mean_net_return_difference=("net_return_difference", "mean")).reset_index()
    summary["match_rate"] = summary.matched / summary.total
    return matches, summary


def _parts(path: Path) -> tuple[str, int, str, str]:
    symbol, minutes, fold, variant, _ = path.name.replace(".csv.gz", "").split("_")
    return symbol, int(minutes.removesuffix("m")), fold, variant


def canonicalize(raw: Path, output: Path) -> None:
    """Write strict [start,end) signals and side-separated closed-trade stats."""
    output.mkdir(parents=True, exist_ok=False)
    signals: list[pd.DataFrame] = []
    trades: list[pd.DataFrame] = []
    for path in raw.glob("*_signals.csv.gz"):
        symbol, minutes, fold, variant = _parts(path)
        table = pd.read_csv(path, parse_dates=["signal_confirm_time"])
        table = table.loc[table.signal_confirm_time < FOLD_END[fold]].copy()
        table["symbol"], table["timeframe_min"], table["fold"], table["variant"] = symbol, minutes, fold, variant
        signals.append(table)
    for path in raw.glob("*_trades.csv.gz"):
        symbol, minutes, fold, variant = _parts(path)
        table = pd.read_csv(path)
        if {"side", "censored", "net_r", "net_return"}.issubset(table):
            table = table.loc[~table.censored.astype(bool)].copy()
            table["symbol"], table["timeframe_min"], table["fold"], table["variant"] = symbol, minutes, fold, variant
            trades.append(table)
    signal = pd.concat(signals, ignore_index=True)
    trade = pd.concat(trades, ignore_index=True) if trades else pd.DataFrame()
    signal.to_csv(output / "canonical_signals.csv.gz", index=False, compression="gzip")
    trade.to_csv(output / "canonical_closed_trades.csv.gz", index=False, compression="gzip")
    signal_summary = signal.groupby(["fold", "variant", "side"], dropna=False).agg(
        raw_signals=("side", "size"), admitted=("admitted_for_entry", "sum")).reset_index()
    trade_summary = (trade.groupby(["fold", "variant", "side"], dropna=False).agg(
        executed_trades=("side", "size"), net_r=("net_r", "sum"), sum_net_trade_returns=("net_return", "sum"),
        win_rate=("net_return", lambda x: x.gt(0).mean())) .reset_index() if len(trade) else pd.DataFrame())
    signal_summary.to_csv(output / "canonical_signal_summary.csv", index=False)
    trade_summary.to_csv(output / "canonical_trade_summary_by_side.csv", index=False)
    # PF uses realized net returns; account drawdown remains the runner's
    # closed-trade measure and is not represented as intrabar MTM.
    if len(trade):
        def stats(g):
            gains, losses = g.net_return.clip(lower=0).sum(), -g.net_return.clip(upper=0).sum()
            return pd.Series({"trades": len(g), "win_rate": g.net_return.gt(0).mean(),
                              "profit_factor": gains / losses if losses else np.nan, "net_r": g.net_r.sum(),
                              "sum_gross_trade_returns": g.gross_return.sum(), "sum_net_trade_returns": g.net_return.sum(),
                              "mfe_ge_10r": g.mfe_r.ge(10).sum()})
        trade.groupby(["fold", "variant", "timeframe_min", "side"]).apply(stats).reset_index().to_csv(output / "canonical_metrics.csv", index=False)
        # A is the unfiltered denominator. B/C retention and misses are signal-
        # identity joins, never an outcome-selected subset.
        a = trade.loc[trade.variant.eq("A"), ["symbol", "timeframe_min", "fold", "side", "signal_bar_open", "net_r", "mfe_r"]]
        tails = []
        for variant in ("B12", "C24"):
            v = trade.loc[trade.variant.eq(variant), ["symbol", "timeframe_min", "fold", "side", "signal_bar_open"]]
            joined = a.merge(v.assign(retained=True), how="left", on=["symbol", "timeframe_min", "fold", "side", "signal_bar_open"])
            tails.append({"variant": variant, "baseline_net_r_ge_10": int(joined.net_r.ge(10).sum()),
                          "retained_net_r_ge_10": int((joined.net_r.ge(10) & joined.retained.fillna(False)).sum()),
                          "missed_net_r_ge_10": int((joined.net_r.ge(10) & ~joined.retained.fillna(False)).sum()),
                          "baseline_mfe_ge_10": int(joined.mfe_r.ge(10).sum()),
                          "retained_mfe_ge_10": int((joined.mfe_r.ge(10) & joined.retained.fillna(False)).sum())})
        pd.DataFrame(tails).to_csv(output / "tail_retention.csv", index=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    canonicalize(args.raw, args.output)
