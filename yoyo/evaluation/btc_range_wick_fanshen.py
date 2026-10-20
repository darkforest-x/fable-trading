"""Notion65 BTC Fib-wick plus confirmed 15m Fanshen research.

Owner confirmed both directions and the latest three completed 15m stop bars.
Reuse range_framework and NautilusTrader; no new execution engine. Features read
OHLC through the decision close, Stoch5/3/3, trailing ATR14, and Monday extrema
known before the touch candle opens. A later arrow/touch delays entry. Targets
and outcomes alone inspect subsequent bars, bounded by chronological segments.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.pepe_range_exits import aggregate, signals, inference, pairs
from yoyo.evaluation.monday_range_v2_2_reference import atr
from yoyo.evaluation.range_framework import ROOT, dump, file_stamp, prepared, run
from yoyo.evaluation.range_multicoin import aggregate_minutes

DEFAULT = ROOT / "experiments/active/exp-btc-range-wick-fanshen-20261021-v1/config.json"
CODE = ["yoyo/evaluation/btc_range_wick_fanshen.py",
        "tests/evaluation/test_btc_range_wick_fanshen.py"]


def monday_levels(quarter, ratios, timezone):
    """Read open timestamps and earlier OHLC only; reset before each Monday."""
    opened = quarter.index - pd.Timedelta(minutes=15)
    local = opened.tz_convert(timezone)
    hi = lo = np.nan
    monday_key = None
    upper, lower, keys, developing = [], [], [], []
    for i, stamp in enumerate(local):
        is_monday = stamp.dayofweek == 0
        key = str(stamp.date())
        if is_monday and key != monday_key:
            hi = lo = np.nan
            monday_key = key
        upper.append(hi)
        lower.append(lo)
        keys.append(monday_key)
        developing.append(is_monday)
        if is_monday:
            hi = max(hi, quarter.high.iloc[i]) if np.isfinite(hi) else quarter.high.iloc[i]
            lo = min(lo, quarter.low.iloc[i]) if np.isfinite(lo) else quarter.low.iloc[i]
    hi, lo = np.array(upper), np.array(lower)
    columns, prices = [], []
    for ratio in ratios:
        columns.append(f"{ratio}:up")
        prices.append(lo + ratio * (hi - lo))
        if ratio != .5:
            columns.append(f"{ratio}:down")
            prices.append(hi - ratio * (hi - lo))
    values = np.column_stack(prices)
    values[~(hi > lo)] = np.nan
    return values, columns, np.array(keys, dtype=object), np.array(developing)


def touch_masks(quarter, prices):
    """A directional wick contains the level; no proximity tolerance."""
    o, h, l, c = (quarter[k].to_numpy()[:, None] for k in ("open", "high", "low", "close"))
    bull = (l < np.minimum(o, c)) & (l <= prices) & (prices <= np.minimum(o, c))
    bear = (np.maximum(o, c) < h) & (np.maximum(o, c) <= prices) & (prices <= h)
    return {1: bull, -1: bear}


def pair_arrows(quarter, arrows, prices, labels, weeks, developing, distance=2):
    """Return only the first causal match for each arrow, including prefix ends.

    Each arrow can inspect nearby touch OHLC, but entry is max(arrow,touch).
    Adding future bars cannot revise a pair whose entry was already available.
    Same-week identity prevents Friday/Sunday events pairing into a new range.
    """
    touches = touch_masks(quarter, prices)
    rows = []
    for i in np.flatnonzero(np.asarray(arrows)):
        side = int(arrows.iloc[i])
        available = []
        for j in range(max(0, i-distance), min(len(quarter), i+distance+1)):
            if weeks[i] is not None and weeks[j] == weeks[i] and touches[side][j].any():
                available.append(j)
        if not available:
            continue
        j = min(available, key=lambda x: (max(i, x), abs(i-x), x))
        chosen = np.flatnonzero(touches[side][j])
        rows.append(dict(family="combined", signal_q=int(i), touch_q=int(j),
                         entry_q=max(int(i), int(j)), side=side,
                         touch_lag=int(i-j), levels="|".join(labels[k] for k in chosen),
                         level_prices="|".join(format(prices[j, k], ".12g") for k in chosen),
                         monday_key=weeks[j], developing=bool(developing[j])))
    columns = ["family", "signal_q", "touch_q", "entry_q", "side", "touch_lag",
               "levels", "level_prices", "monday_key", "developing"]
    result = pd.DataFrame(rows, columns=columns)
    for key in ("signal_q", "touch_q", "entry_q", "side", "touch_lag"):
        result[key] = result[key].astype(int)
    result["developing"] = result["developing"].astype(bool)
    return result


def candidates(quarter, cfg):
    """Build causal real events and same-clock, time/volatility matched controls."""
    arrows = signals(quarter)
    price, labels, weeks, developing = monday_levels(quarter, cfg["ratios"], cfg["timezone"])
    combined = pair_arrows(quarter, arrows, price, labels, weeks, developing,
                           cfg["signal_distance_bars"])
    ids = np.flatnonzero(arrows.to_numpy())
    baseline = pd.DataFrame(dict(family="arrow_only", signal_q=ids, touch_q=-1,
                                 entry_q=ids, side=arrows.iloc[ids].to_numpy(),
                                 touch_lag=0, levels="", level_prices="",
                                 monday_key=weeks[ids], developing=developing[ids]))
    real = pd.concat([combined, baseline], ignore_index=True)
    index = quarter.index.as_unit("ns")
    bounds = pd.to_datetime([cfg["start"], cfg["split"], cfg["end"]], utc=True).as_unit("ns")
    vol = atr(quarter, 14).to_numpy() / quarter.close.to_numpy()
    bucket = np.digitize(vol, cfg["volatility_edges"])
    week = (index-pd.to_timedelta(index.dayofweek, unit="D")).normalize().asi8
    segment = np.searchsorted(bounds.asi8, index.asi8, side="right")-1
    valid = (segment >= 0) & (segment < 2) & np.isfinite(vol)
    real = real[valid[real.entry_q.to_numpy(int)]].copy()
    if real.empty or not real.family.eq("combined").any():
        raise ValueError("No eligible combined events; check clocks and source semantics")
    idx = real.entry_q.to_numpy(int)
    real["segment"], real["week"], real["bucket"] = segment[idx], week[idx], bucket[idx]
    real = real.sort_values(["family", "entry_q", "signal_q"]).reset_index(drop=True)
    real["pair_id"] = np.arange(len(real))
    real["population"] = "signal"
    excluded = set(real.entry_q) | set(np.flatnonzero(arrows.to_numpy()))
    rng = np.random.default_rng(cfg["seed"])
    random, missing = [], []
    for key, group in real.groupby(["family", "side", "segment", "week", "bucket"], sort=True):
        family, side, seg, wk, buck = key
        pool = np.flatnonzero(valid & (segment == seg) & (week == wk) & (bucket == buck))
        pool = np.array([i for i in pool if i not in excluded], dtype=int)
        rng.shuffle(pool)
        for k, row in enumerate(group.to_dict("records")):
            if k >= len(pool):
                missing.append(int(row["pair_id"]))
                continue
            row.update(entry_q=int(pool[k]), population="random")
            # Do not let the signal's event context masquerade as random features.
            row.update(signal_q=-1, touch_q=-1, touch_lag=0, levels="", level_prices="",
                       monday_key=weeks[pool[k]], developing=bool(developing[pool[k]]))
            random.append(row)
    both = pd.concat([real, pd.DataFrame(random)], ignore_index=True)
    both["entry_time"] = index[both.entry_q.to_numpy(int)].astype(str)
    both["trade_id"] = both.population + "-" + both.pair_id.astype(str)
    return both, missing


def prepare(config):
    """Reuse registered minutes, validate actual continuity, aggregate once."""
    cfg = json.loads(config.read_text())
    work = config.parent
    receipt = work / "input_receipt.json"
    if receipt.exists():
        existing = json.loads(receipt.read_text())
        if existing["config"] != cfg or not existing["counts"].get("combined/signal"):
            raise ValueError("Preserve mismatched/empty preparation before rebuilding")
        return existing
    recent = list(work.glob(cfg["recent_glob"]))
    if len(recent) != 1:
        raise ValueError("Need exactly one completed targeted BTC minute extension")
    paths = [ROOT/p for p in cfg["inputs"]] + recent
    start, end = (int(pd.Timestamp(cfg[k]).timestamp()*1000) for k in ("warmup", "end"))
    parts = []
    for path in paths:
        for chunk in pd.read_csv(path, usecols=["ts", "open", "high", "low", "close"],
                                 float_precision="round_trip", chunksize=250_000):
            parts.append(chunk[(chunk.ts >= start) & (chunk.ts < end)])
    five = aggregate_minutes(pd.concat(parts, ignore_index=True), cfg["warmup"], cfg["end"])
    five.to_csv(work / "candles.csv.gz", index=False)
    close_frame = five.set_index(pd.to_datetime(five.ts, unit="ms", utc=True)
                                 + pd.Timedelta(minutes=5))[["open", "high", "low", "close"]]
    close_frame.index = close_frame.index.as_unit("ns")
    quarter = aggregate(close_frame, 15)
    events, missing = candidates(quarter, cfg)
    events["entry_i"] = close_frame.index.get_indexer(
        quarter.index[events.entry_q.to_numpy(int)])
    boundaries = pd.to_datetime([cfg["split"], cfg["end"]], utc=True)
    events["end_i"] = [close_frame.index.searchsorted(boundaries[int(s)], side="right")-1
                       for s in events.segment]
    assert (events.entry_i >= 0).all() and (events.entry_i < events.end_i).all()
    events.to_csv(work / "candidates.csv", index=False)
    quarter.assign(arrow=signals(quarter)).to_csv(work / "quarter.csv.gz", index_label="close_time")
    for buffer in cfg["buffer_percent"]:
        tag = str(buffer).replace(".", "p")
        native_config = dict(
            schema=1, experiment_id=cfg["experiment_id"], engine="nautilus", symbol="BTC",
            tick=cfg["tick"], market_tick=cfg["tick"],
            data=str((work/"candles.csv.gz").relative_to(ROOT)),
            entry_candidates=str((work/"candidates.csv").relative_to(ROOT)),
            stop_window_5m=3*cfg["stop_bars_15m"], buffer_percent=buffer,
            maximum_risk_percent=0, round_trip_cost=cfg["round_trip_cost"],
            policies=[dict(name=f"tp{target}", fixed_target_r=target,
                           opposite_hour_exit=False) for target in cfg["target_r"]],
            output=str((work/f"native_b{tag}").relative_to(ROOT)),
        )
        dump(work/f"native_b{tag}.json", native_config)
    record = dict(
        source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        config=cfg, sources=[file_stamp(p) for p in paths], rows_5m=len(five),
        rows_15m=len(quarter), first_close=str(close_frame.index[0]),
        last_close=str(close_frame.index[-1]), unmatched_ids=missing,
        counts=events.groupby(["family", "population"]).size().to_dict(),
    )
    record["counts"] = {"/".join(k): int(v) for k, v in record["counts"].items()}
    dump(receipt, record)
    return record


def prefix_audit(config):
    """Compare event identities at bounded prefixes, including pending matches."""
    cfg = json.loads(config.read_text())
    q = pd.read_csv(config.parent/"quarter.csv.gz", index_col="close_time", parse_dates=True)
    q.index = pd.DatetimeIndex(q.index).as_unit("ns")
    prices, labels, weeks, developing = monday_levels(q, cfg["ratios"], cfg["timezone"])
    full = pair_arrows(q, signals(q), prices, labels, weeks, developing)
    checks = []
    cutoffs = sorted(set([100, 500, 1000, len(q)//2, len(q)-1] +
                         [int(i)+1 for i in full.entry_q.iloc[::max(1, len(full)//8)]]))
    for n in cutoffs:
        part = q.iloc[:n]
        pp, ll, ww, dd = monday_levels(part, cfg["ratios"], cfg["timezone"])
        actual = pair_arrows(part, signals(part), pp, ll, ww, dd)
        expected = full[full.entry_q < n].reset_index(drop=True)
        pd.testing.assert_frame_equal(actual.reset_index(drop=True), expected)
        np.testing.assert_allclose(pp, prices[:n], equal_nan=True)
        checks.append(dict(bars=n, matched_events=len(actual)))
    return checks


def analyze(config):
    """Join native outcomes to source metadata; compare conserved-price oracle."""
    from yoyo.evaluation.pepe_range_breakeven import replay_search, native
    from yoyo.evaluation.range_partial_study import drawdown
    cfg = json.loads(config.read_text())
    work = config.parent
    meta = pd.read_csv(work/"candidates.csv").set_index("trade_id")
    lib = native()
    summary, inference_rows, audits = [], [], []
    for buffer in cfg["buffer_percent"]:
        tag = str(buffer).replace(".", "p")
        native_cfg = json.loads((work/f"native_b{tag}.json").read_text())
        ctx, base, hit = prepared(native_cfg, work/".cache")
        matrix = base[["entry_i", "side", "entry", "stop", "end_i"]].to_numpy(float)
        for target in cfg["target_r"]:
            rows = pd.read_csv(work/f"native_b{tag}"/f"tp{target}"/"trades.csv",
                               float_precision="round_trip").set_index("trade_id")
            rows = rows.loc[base.trade_id].copy()
            expected = replay_search(ctx, matrix, tick=cfg["tick"],
                                     target_r=target, reverse_exit=False, lib=lib)
            conservative = replay_search(ctx, matrix, tick=cfg["tick"],
                                         target_r=target, reverse_exit=False,
                                         stop_first=True, lib=lib)
            np.testing.assert_array_equal(rows.exit_i, expected[:, 0])
            np.testing.assert_array_equal(rows.reason, expected[:, 2])
            np.testing.assert_allclose(rows.exit_price, expected[:, 1],
                                       rtol=0, atol=cfg["tick"]*1e-7)
            economic = rows.side*(rows.exit_price-rows.entry)/rows.risk
            np.testing.assert_allclose(rows.net_r, economic-cfg["round_trip_cost"]*rows.entry/rows.risk,
                                       rtol=0, atol=1e-8)
            for col in ("family", "levels", "touch_lag", "developing", "signal_q", "touch_q"):
                rows[col] = meta.loc[rows.index, col]
            rows["selection_net_r"] = (rows.side*(conservative[:, 1]-rows.entry)
                                       - cfg["round_trip_cost"]*rows.entry)/rows.risk
            rows["collisions"] = conservative[:, 6].astype(int)
            arm = f"b{tag}_tp{target}"
            rows.to_csv(work/f"annotated_{arm}.csv.gz", index=True)
            audits.append(dict(arm=arm, paths=len(rows), native_oracle="passed",
                               conservative_changed=int((rows.exit_i != conservative[:, 0]).sum()
                                                        + (abs(rows.exit_price-conservative[:, 1])>1e-7).sum())))
            for family in ("combined", "arrow_only"):
                for segment in (-1, 0, 1):
                    group = rows[rows.family == family]
                    if segment != -1:
                        group = group[group.segment == segment]
                    signal = group[group.population == "signal"]
                    random = group[group.population == "random"]
                    paired = pairs(signal.reset_index(), random.reset_index())
                    losses = -signal.loc[signal.net_r < 0, "net_r"].sum()
                    ordered = signal.sort_values(["exit_i", "entry_i"]).net_r.to_numpy()
                    summary.append(dict(
                        arm=arm, buffer_percent=buffer, target_r=target, family=family,
                        segment=segment, n=len(signal), matched_n=len(paired),
                        net_r=float(signal.net_r.sum()), gross_r=float(signal.gross_r.sum()),
                        fee_r=float(signal.fee_r.sum()),
                        selection_net_r=float(signal.selection_net_r.sum()),
                        win_rate=float((signal.net_r>1e-9).mean()),
                        target_rate=float((signal.reason==4).mean()),
                        pf=float(signal.loc[signal.net_r>0,"net_r"].sum()/losses) if losses else None,
                        mean_net_bp=float(signal.net_notional_bp.mean()),
                        realized_r_drawdown=drawdown(ordered),
                        paired_signal_net_r=float(paired.net_r.sum()),
                        random_net_r=float(paired.net_r_random.sum()),
                        paired_excess_r=float((paired.net_r-paired.net_r_random).sum()),
                        boundary_n=int((signal.reason==0).sum()),
                        boundary_net_r=float(signal.loc[signal.reason==0,"net_r"].sum()),
                        collisions=int(signal.collisions.sum()),
                        conservative_random_r=float(random.selection_net_r.sum()),
                    ))
                    inference_rows.append(dict(arm=arm, family=family, segment=segment,
                                               **inference(paired, cfg["seed"])))
    table = pd.DataFrame(summary)
    table.to_csv(work/"summary.csv", index=False)
    dump(work/"inference.json", inference_rows)
    selections = {}
    for family in ("combined", "arrow_only"):
        def best(segment):
            return table[(table.family==family)&(table.segment==segment)].sort_values(
                ["selection_net_r", "realized_r_drawdown", "arm"],
                ascending=[False, True, True]).iloc[0].arm
        selections[family] = dict(first_year_selected=best(0), full_descriptive=best(-1))
    record = dict(selections=selections, native_audits=audits,
                  prefix_checks=prefix_audit(config), training_eligible=False,
                  production_eligible=False,
                  limitations="Known historical research; fixed initial-risk units, not account returns. "
                  "No funding/spread/capacity/latency. Five-minute OHLC order sensitivity retained.")
    dump(work/"delivery_receipt.json", record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "run", "analyze"])
    parser.add_argument("--config", type=Path, default=DEFAULT)
    args = parser.parse_args()
    config = args.config.resolve()
    if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *CODE, str(config)],
                      cwd=ROOT).returncode:
        raise RuntimeError("Commit builder/config before generating results")
    if args.action == "prepare":
        result = prepare(config)
    elif args.action == "run":
        result = []
        for buffer in json.loads(config.read_text())["buffer_percent"]:
            tag = str(buffer).replace(".", "p")
            result.append(run(config.parent/f"native_b{tag}.json"))
        result = dict(completed_buffers=len(result))
    else:
        result = analyze(config)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
