"""Single-factor entry-candle stop comparison using existing native engines.

Source: owner's Oct20 request, frozen ETH 15m-watch/5m-entry timed-BE study.
Stop features use completed 5m high/low and entry close only, with the same
entry-price percentage buffer and outward tick rounding as the existing code.
No future data enters admission. New outcomes are computed by Nautilus 1.231.0
and checked against the existing C++ runner. Unchanged five-bar results are reused.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation.range_rsi_scaleout import context, aggregate
from yoyo.evaluation.pepe_range_exits import stop_events, pairs, inference
from yoyo.evaluation.pepe_range_breakeven import replay_search, replay_timed
from yoyo.evaluation.range_parameter_search import decorate
from yoyo.evaluation.range_framework import ROOT, dump, compare_golden, file_stamp
from yoyo.evaluation.range_partial_study import cash_events
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native

DEFAULT = ROOT / "experiments/active/exp-eth-entrybar-stop-20261020-v1/config.json"


def replace_stops(five, base, window=1, buffer_percent=.05, tick=.01):
    """Read only completed 5m OHLC ending at each frozen entry_i_5m.

    The native runner uses 1m indices, which are preserved after invoking the
    shared 5m stop builder. Unchanged inputs include entry time, side and bounds.
    """
    source = base.copy()
    source["entry_i"] = base.entry_i_5m
    source["end_i"] = base.end_i_5m
    out, _ = stop_events(
        dict(frame=five, bars=five[["open", "high", "low", "close"]].to_numpy()),
        source, window, buffer_percent, tick)
    if not out.index.equals(base.index) or not out.trade_id.equals(base.trade_id):
        raise ValueError("Stop replacement unexpectedly changed entry membership")
    out["entry_i"], out["end_i"] = base.entry_i, base.end_i
    return out


def run(config):
    cfg = json.loads(config.read_text())
    folder = config.parent
    if (folder / "native_receipt.json").exists():
        print("Completed native results retained; use analyze.", flush=True)
        return
    tracked = ["yoyo/evaluation/range_entry_bar_stop.py", str(config),
               "tests/evaluation/test_range_entry_bar_stop.py"]
    if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *tracked], cwd=ROOT).returncode:
        raise RuntimeError("Commit builder and config before running")
    started = time.perf_counter()
    _, old, ctx, base, info = context(ROOT / cfg["source_config"])
    five = aggregate(ctx["frame"], 5)
    restored = replace_stops(five, base, 5, cfg["buffer_percent"], cfg["tick"])
    for col in ("entry", "stop", "risk"):
        np.testing.assert_allclose(restored[col], base[col], atol=1e-8, rtol=0)
    new = replace_stops(five, base, 1, cfg["buffer_percent"], cfg["tick"])
    assert (new.risk <= base.risk + 1e-8).all()
    # Prefix causality check on several actual entries, without recomputing outcomes.
    for pos in np.linspace(0, len(base)-1, 5, dtype=int):
        one = base.iloc[[pos]]
        prefix = five.iloc[:int(one.entry_i_5m.iloc[0])+1]
        short = replace_stops(prefix, one, 1, cfg["buffer_percent"], cfg["tick"])
        np.testing.assert_allclose(short[["entry", "stop", "risk"]],
                                   new.iloc[[pos]][["entry", "stop", "risk"]], atol=0, rtol=0)
    new.to_csv(folder / "entries.csv", index=False)
    matrix = new[["entry_i", "side", "entry", "stop", "end_i"]].to_numpy()
    audits = {}
    for name in cfg["policies"]:
        destination = folder / name
        destination.mkdir(exist_ok=True)
        if (destination / "receipt.json").exists():
            audits[name] = json.loads((destination / "receipt.json").read_text())
            continue
        t0 = time.perf_counter()
        raw = (replay_timed(ctx, matrix, delay=25, cost=cfg["round_trip_cost"], tick=cfg["tick"])
               if name == "time25" else replay_search(ctx, matrix, cost=cfg["round_trip_cost"], tick=cfg["tick"]))
        expected = decorate(new, raw, cfg, ctx, cfg["tick"])
        ii = expected.exit_i.to_numpy(int)
        gap = expected.reason.isin([1, 2, 5]).to_numpy() & (
            expected.side.to_numpy() * (ctx["bars"][ii, 0] - expected.protection.to_numpy()) < -1e-9)
        expected["exit_time"] = ctx["frame"].index[ii] - pd.to_timedelta(gap.astype(int), unit="min")
        expected.to_csv(destination / "screen.csv", index=False)
        rows, fills, audit = run_native(
            ctx["frame"], ctx["atr"], ctx["hour"], new,
            ExitPolicy(time_breakeven_minutes=25 if name == "time25" else None,
                       round_trip_cost=cfg["round_trip_cost"]),
            cfg["tick"], "ETH", cfg["tick"], execution_minutes=1)
        rows.to_csv(destination / "trades.csv", index=False)
        fills.to_csv(destination / "fills.csv", index=False)
        audit["parity"] = compare_golden(rows, destination / "screen.csv", cfg["tick"])
        cash = cash_events(rows, fills)
        np.testing.assert_allclose(cash.groupby("trade_id").cash_r.sum().sort_index(),
                                   rows.set_index("trade_id").net_r.sort_index(), atol=1e-7, rtol=0)
        np.testing.assert_allclose(rows.framework_commission,
                                   rows.quantity * rows.entry * cfg["round_trip_cost"], atol=1e-7, rtol=0)
        audit["elapsed_seconds"] = time.perf_counter() - t0
        dump(destination / "receipt.json", audit)
        audits[name] = audit
        print(json.dumps(dict(completed=name, native_positions=len(rows),
                              seconds=audit["elapsed_seconds"])), flush=True)
    dump(folder / "native_receipt.json", dict(
        config=cfg, reused_context=info, source_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True).strip(),
        old_stop_reconstruction_rows=len(base), prefix_checks=5, audits=audits,
        elapsed_seconds=time.perf_counter()-started))


def describe(rows, signal, control, cfg):
    matched = pairs(signal, control)
    seq = signal.sort_values(["exit_i", "entry_i"]).net_r.to_numpy()
    curve = np.r_[0, np.cumsum(seq)]
    return dict(
        n=len(signal), net_r=float(signal.net_r.sum()), gross_r=float(signal.gross_r.sum()),
        fee_r=float(signal.fee_r.sum()), mean_bp=float(signal.net_notional_bp.mean()),
        net_positive=int((signal.net_r > 1e-8).sum()),
        net_above_0p1r=int((signal.net_r > .1).sum()),
        initial_stop=int(signal.reason.eq(1).sum()), be_exits=int(signal.reason.eq(5).sum()),
        be_effective=int(signal.be_effective_i.ge(0).sum()),
        gross10=int(signal.gross_r.ge(10).sum()), gross20=int(signal.gross_r.ge(20).sum()),
        net10=int(signal.net_r.ge(10).sum()), net20=int(signal.net_r.ge(20).sum()),
        peak10=int(signal.peak_r.ge(10).sum()), peak20=int(signal.peak_r.ge(20).sum()),
        max_peak=float(signal.peak_r.max()), risk_percent_median=float((signal.risk/signal.entry*100).median()),
        risk_points_median=float(signal.risk.median()), median_fee_r=float(signal.fee_r.median()),
        boundary_n=int(signal.reason.eq(0).sum()),
        boundary_net_r=float(signal.loc[signal.reason.eq(0), "net_r"].sum()),
        realized_r_drawdown=float((np.maximum.accumulate(curve)-curve).max()),
        matched_n=len(matched), random_net_r=float(control.net_r.sum()),
        paired_excess=inference(matched, cfg["seed"]))


def analyze(config):
    cfg = json.loads(config.read_text())
    folder = config.parent
    assert (folder / "native_receipt.json").exists()
    oldfolder = (ROOT / cfg["source_config"]).parent / "runs"
    all_rows = {}
    result, comparisons = [], []
    for policy in cfg["policies"]:
        for window in (5, 1):
            source = oldfolder / policy if window == 5 else folder / policy
            rows = pd.read_csv(source / "trades.csv", float_precision="round_trip")
            all_rows[policy, window] = rows
            for period in ("full", "2024", "2025", "2026", "review_month"):
                signals = rows[rows.population.eq("signal")]
                if period == "review_month":
                    entry = pd.to_datetime(signals.entry_time, utc=True).dt.tz_convert("Asia/Shanghai")
                    signals = signals[entry.ge(cfg["review_start_bjt"]) & entry.lt(cfg["review_end_exclusive_bjt"])]
                elif period != "full":
                    signals = signals[signals.segment.eq(int(period)-2024)]
                controls = rows[rows.population.eq("random") & rows.pair_id.isin(signals.pair_id)]
                result.append(dict(policy=policy, window=window, period=period,
                                   **describe(rows, signals, controls, cfg)))
        a, b = [all_rows[policy, w].set_index("trade_id").sort_index() for w in (5, 1)]
        pd.testing.assert_index_equal(a.index, b.index)
        for field in ("entry_i", "entry_time", "side", "entry", "end_i", "pair_id"):
            np.testing.assert_array_equal(a[field], b[field], err_msg=field)
        signal = a.population.eq("signal")
        diff = pd.DataFrame(dict(
            pair_id=a.pair_id, population=a.population, entry_time=a.entry_time, side=a.side,
            old_stop=a.stop, new_stop=b.stop, old_risk=a.risk, new_risk=b.risk,
            old_net_r=a.net_r, new_net_r=b.net_r, delta_net_r=b.net_r-a.net_r,
            old_gross_r=a.gross_r, new_gross_r=b.gross_r,
            old_net_bp=a.net_notional_bp, new_net_bp=b.net_notional_bp,
            old_peak_r=a.peak_r, new_peak_r=b.peak_r,
            old_reason=a.reason, new_reason=b.reason))
        diff.to_csv(folder / f"{policy}_paired.csv")
        for period in ("full", "review_month"):
            mask = signal.copy()
            if period == "review_month":
                times = pd.to_datetime(a.entry_time, utc=True).dt.tz_convert("Asia/Shanghai")
                mask &= times.ge(cfg["review_start_bjt"]) & times.lt(cfg["review_end_exclusive_bjt"])
            old_tail = mask & a.net_r.ge(20)
            d = diff[mask]
            comparisons.append(dict(
                policy=policy, period=period, n=len(d), delta_net_r=float(d.delta_net_r.sum()),
                improved=int(d.delta_net_r.gt(1e-8).sum()), worsened=int(d.delta_net_r.lt(-1e-8).sum()),
                same_stop=int(np.isclose(d.old_stop, d.new_stop, atol=1e-8, rtol=0).sum()),
                old_net20=int(old_tail.sum()), old_net20_still_net20=int((old_tail & b.net_r.ge(20)).sum()),
                old_net20_new_net_positive=int((old_tail & b.net_r.gt(0)).sum()),
                old_net20_old_netbp_sum=float(a.loc[old_tail, "net_notional_bp"].sum()),
                old_net20_new_netbp_sum=float(b.loc[old_tail, "net_notional_bp"].sum())))
    pd.DataFrame(result).drop(columns="paired_excess").to_csv(folder / "summary.csv", index=False)
    dump(folder / "summary.json", result)
    dump(folder / "comparisons.json", comparisons)
    receipt = json.loads((folder / "native_receipt.json").read_text())
    receipt.update(summary=result, comparisons=comparisons,
        limitations=["Known-history research; no fresh forward validation.",
                     "Independent original-risk R, not account return; 0.2% cost, no funding/extra slippage.",
                     "Narrower risk changes R units; equal-notional basis points included.",
                     "Review-month controls retain original pair IDs and can enter outside exact month edges."],
        local_artifacts=[file_stamp(p) for p in folder.glob("*/trades.csv")] +
                        [file_stamp(p) for p in folder.glob("*/fills.csv")],
        training_eligible=False, production_eligible=False)
    dump(folder / "delivery_receipt.json", receipt)
    print(json.dumps([r for r in result if r["period"] in ("full", "review_month")], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT)
    parser.add_argument("--phase", choices=["run", "analyze"], required=True)
    args = parser.parse_args()
    (run if args.phase == "run" else analyze)(args.config)
