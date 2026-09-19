"""Frozen three-year fifth-LARGE-diamond / quarter-exit BTC study.

Owner rules are in exp-btc-rsi-fifth-color-20260920-v1/PROJECT_PLAN.md.
All outcomes are fixed-entry-notional returns, not account returns or R.
Random entries match BTC, side, month, phase and the causal five-minute
volatility quintile (TR14/close14, cut points from strictly prior 30 days).
Only prior closed five-minute volatility is used at an entry. Exit outcomes
may use later confirmed strong diamonds; those are labels, never features.
"""
from __future__ import annotations

import argparse
import itertools
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.btc_rsi_fifth import prepare_signals
from yoyo.evaluation.btc_rsi_diamond_scaleout import replay, signals_with_streak, trace_position
from yoyo.evaluation.btc_rsi_sixma import _volatility
from yoyo.evaluation.btc_rsi_sixma_study import START, SPLIT, END, SEED, dump, sha

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-btc-rsi-fifth-color-20260920-v1"
PARENT = ROOT / "experiments/active/exp-btc-rsi1h-sixma5m-20260920-v1"
BAR = pd.Timedelta(minutes=5)


def fast_outcomes(frame, signals, entries, side, end):
    """Vectorized exact four-event labels for externally selected entries.

    Each row enters after its contemporaneous signal is already consumed;
    only opposite strong events strictly later than entry can reduce it.
    A boundary is marked at the final available close, never force-closed.
    """
    entries = pd.DatetimeIndex(entries)
    price = frame.loc[entries, "open"].to_numpy(float)
    exits = signals.index[(signals.strong_side == -side) & (signals.index < end)]
    positions = np.searchsorted(exits.asi8, entries.asi8, side="right")
    gross = np.zeros(len(entries))
    count = np.zeros(len(entries), dtype=int)
    for k in range(4):
        idx = positions + k
        valid = idx < len(exits)
        if valid.any():
            ep = frame.loc[exits[idx[valid]], "open"].to_numpy(float)
            gross[valid] += .25 * side * (ep / price[valid] - 1)
            count[valid] += 1
    remaining = 1 - .25 * count
    terminal = float(frame.loc[frame.index < end, "close"].iloc[-1])
    gross += remaining * side * (terminal / price - 1)
    return gross - .002, remaining, count


def path_metrics(frame, result):
    """5m close marked PnL and intrabar excursions with shrinking quantity.

    Signals fill at bar open, so a final exit excludes that bar's later high
    and low. Raw price MAE is also reported, distinct from remaining-exposure
    net PnL. The marked curve reserves remaining exit costs at every point.
    """
    index = frame.index[(frame.index >= START) & (frame.index < END)]
    curve = np.zeros(len(index), dtype=float)
    positions = result["positions"].copy()
    fields = {k: [] for k in ("holding_days", "raw_price_mae", "raw_price_mfe", "minimum_position_marked_return", "maximum_position_marked_return", "partial_exits")}
    for p in positions.itertuples():
        fills = result["fills"].loc[result["fills"].position_id == p.position_id].sort_values("time")
        gross = costs = 0.0
        raw_min = raw_max = 0.0
        net_min, net_max = -.002, -.002
        for j, fill in enumerate(fills.itertuples()):
            gross += float(fill.gross_return)
            costs += float(fill.cost)
            remaining = float(fill.remaining_fraction_after)
            left = index.searchsorted(fill.time)
            next_time = fills.iloc[j+1].time if j+1 < len(fills) else END
            right = index.searchsorted(next_time)
            # The observed exit open is part of the pre-reduction path.
            at_open = p.side * (fill.price / p.entry_price - 1)
            raw_min, raw_max = min(raw_min, at_open), max(raw_max, at_open)
            if remaining == 0:
                curve[left:] += gross - costs
                net_min, net_max = min(net_min, gross-costs), max(net_max, gross-costs)
                continue
            segment = frame.loc[(frame.index >= fill.time) & (frame.index < next_time)]
            fixed = gross - costs - .001 * remaining
            close_return = fixed + remaining * p.side * (segment.close.to_numpy(float) / p.entry_price - 1)
            curve[left:right] += close_return
            adverse = segment.low.to_numpy(float) if p.side == 1 else segment.high.to_numpy(float)
            favorable = segment.high.to_numpy(float) if p.side == 1 else segment.low.to_numpy(float)
            if len(segment):
                low = p.side * (adverse / p.entry_price - 1)
                high = p.side * (favorable / p.entry_price - 1)
                raw_min, raw_max = min(raw_min, float(low.min())), max(raw_max, float(high.max()))
                net_min = min(net_min, float((fixed + remaining * low).min()))
                net_max = max(net_max, float((fixed + remaining * high).max()))
        stop_time = p.finalexit_time if p.status == "closed" else END
        fields["holding_days"].append((stop_time - p.entry_time).total_seconds() / 86400)
        fields["raw_price_mae"].append(raw_min)
        fields["raw_price_mfe"].append(raw_max)
        fields["minimum_position_marked_return"].append(net_min)
        fields["maximum_position_marked_return"].append(net_max)
        fields["partial_exits"].append(int((fills.fill_type == "partial_exit").sum()))
    for name, values in fields.items():
        positions[name] = values
    peak = np.maximum.accumulate(np.r_[0, curve])[1:]
    marked = pd.DataFrame(dict(time=index+BAR, net_pnl=curve, drawdown=peak-curve))
    np.testing.assert_allclose(curve[-1], positions.terminal_marked_net_return.sum(), atol=1e-12)
    return positions, marked


def paired_summary(rows):
    if rows.empty:
        return {"matched_entries": 0}
    pairs = rows.groupby("position_id").agg(month=("month", "first"), actual=("actual_net", "first"), control=("control_net", "mean"), n=("control_net", "size"))
    pairs["delta"] = pairs.actual - pairs.control
    blocks = pairs.groupby("month").agg(delta=("delta", "sum"), n=("delta", "size")).to_numpy(float)
    rng = np.random.default_rng(SEED)
    draws = blocks[rng.integers(0, len(blocks), (10000, len(blocks)))].sum(axis=1)
    ci = np.quantile(draws[:, 0] / draws[:, 1], [.025, .975])
    observed = float(pairs.delta.mean())
    if len(blocks) <= 16:
        signs = np.array(list(itertools.product((-1, 1), repeat=len(blocks))))
        null = (signs * blocks[None, :, 0]).sum(axis=1) / blocks[:, 1].sum()
        p_value = float((null >= observed - 1e-15).mean())
        method = "exact month-sign permutation"
    else:
        signs = rng.choice([-1, 1], (20000, len(blocks)))
        null = (signs * blocks[None, :, 0]).sum(axis=1) / blocks[:, 1].sum()
        p_value = float((1 + (null >= observed).sum()) / (len(null) + 1))
        method = "20000 month-sign permutations"
    return dict(matched_entries=len(pairs), controls=len(rows), minimum_matches=int(pairs.n.min()),
        months=len(blocks), actual_mean=float(pairs.actual.mean()), random_mean=float(pairs.control.mean()),
        excess_mean=observed, excess_month_bootstrap_95=ci, p_greater=p_value, method=method,
        censored_controls=int(rows.control_remaining.gt(0).sum()))


def describe(positions, controls):
    if positions.empty:
        return dict(entries=0, closed=0, open=0, control=paired_summary(controls))
    net = positions.terminal_marked_net_return.to_numpy(float)
    closed = positions.loc[positions.status == "closed"]
    cn = closed.terminal_marked_net_return.to_numpy(float)
    months = positions.assign(month=positions.entry_time.dt.strftime("%Y-%m")).groupby("month").terminal_marked_net_return.agg(["sum", "count"]).to_numpy(float)
    rng = np.random.default_rng(SEED)
    sampled = months[rng.integers(0, len(months), (10000, len(months)))].sum(axis=1)
    return dict(entries=len(positions), closed=len(closed), open=len(positions)-len(closed),
        closed_wins=int((cn > 0).sum()), closed_win_rate=float((cn > 0).mean()) if len(cn) else None,
        closed_net_sum=float(cn.sum()), marked_net_sum=float(net.sum()), marked_net_mean=float(net.mean()),
        marked_gross_sum=float(net.sum() + .002 * len(net)), mean_net_month_bootstrap_95=np.quantile(sampled[:,0]/sampled[:,1],[.025,.975]),
        closed_profit_factor=float(cn[cn>0].sum() / -cn[cn<0].sum()) if (cn<0).any() else None,
        max_holding_days=float(positions.holding_days.max()), median_holding_days=float(positions.holding_days.median()),
        worst_raw_price_mae=float(positions.raw_price_mae.min()),
        worst_position_marked_return=float(positions.minimum_position_marked_return.min()),
        control=paired_summary(controls))


def make_controls(frame, signals, positions, vol_bucket):
    """Pre-draw matched entries without selecting by their future outcome."""
    rng = np.random.default_rng(SEED)
    times = signals.index[(signals.index >= START) & (signals.index < END)]
    prior = frame.index.get_indexer(times) - 1
    pool = pd.DataFrame(dict(time=times, bucket=vol_bucket[prior], month=times.strftime("%Y-%m"), early=times < SPLIT))
    pool = pool.loc[pool.bucket >= 0]
    rows = []
    for p in positions.itertuples():
        entry_i = frame.index.get_loc(p.entry_time)
        bucket = int(vol_bucket[entry_i-1])
        available = pool.loc[(pool.bucket == bucket) & (pool.month == p.entry_time.strftime("%Y-%m")) & (pool.early == (p.entry_time < SPLIT)) & (pool.time != p.entry_time), "time"]
        if available.empty:
            raise ValueError(f"unmatched entry {p.position_id}")
        selected = pd.DatetimeIndex(available.iloc[rng.choice(len(available), 100, replace=len(available)<100)])
        values, remaining, _ = fast_outcomes(frame, signals, selected, p.side, END)
        for when, value, rem in zip(selected, values, remaining):
            rows.append(dict(position_id=p.position_id, side=p.side, month=p.entry_time.strftime("%Y-%m"), bucket=bucket,
                actual_entry_time=p.entry_time, control_entry_time=when, actual_net=p.terminal_marked_net_return,
                control_net=float(value), control_remaining=float(rem), horizon=END))
    return pd.DataFrame(rows, columns=["position_id", "side", "month", "bucket", "actual_entry_time", "control_entry_time", "actual_net", "control_net", "control_remaining", "horizon"])


def run(out):
    if out.exists():
        raise ValueError("immutable output exists")
    builders = ["yoyo/evaluation/" + n + ".py" for n in ["btc_rsi_diamond_study", "btc_rsi_diamond_scaleout", "btc_rsi_fifth", "btc_rsi_sixma", "btc_rsi_sixma_study", "parabolic_rsi_sar", "spike_v6_bb_squeeze"]]
    for rel in builders:
        if subprocess.check_output(["git", "show", "HEAD:"+rel], cwd=ROOT) != (ROOT/rel).read_bytes():
            raise ValueError("commit builder before scoring: "+rel)
    data = EXP / "data/okx_btc_usdt_swap_5m_90d.csv.gz"
    frame = pd.read_csv(data, index_col="open_time", parse_dates=True)
    frame.index = pd.to_datetime(frame.index, utc=True)
    old = pd.read_csv(PARENT / "data/okx_btc_usdt_swap_5m.csv.gz", index_col="open_time", parse_dates=True)
    old.index = pd.to_datetime(old.index, utc=True)
    pd.testing.assert_frame_equal(frame.loc[old.index], old, check_names=False, check_freq=False)
    _, buckets = _volatility(frame)
    out.mkdir(parents=True)
    summary = dict(start=START, split=SPLIT, end_exclusive=END, data_sha256=sha(data), source_rows=len(frame), arms={})
    parity = {}
    for hours in (1, 4):
        print(f"running {hours}h", flush=True)
        signals = prepare_signals(frame, hours)
        counted = signals_with_streak(signals)
        old_signals = signals_with_streak(prepare_signals(old, hours))
        eval_index = old_signals.index[(old_signals.index >= START) & (old_signals.index < END)]
        a, b = old_signals.loc[eval_index], counted.loc[eval_index]
        parity[f"{hours}h"] = dict(evaluation_close_labels=len(eval_index), total_complete_90d_bars=len(signals),
            rsi_max_abs_difference=float((a.rsi-b.rsi).abs().max()), sar_max_abs_difference=float((a.sar-b.sar).abs().max()),
            state_difference=int((a.side != b.side).sum()), strong_difference=int((a.strong_side != b.strong_side).sum()),
            fifth_mask_difference=int(((a.strong_streak == 5) != (b.strong_streak == 5)).sum()),
            strong_streak_difference=int((a.strong_streak != b.strong_streak).sum()))
        result = replay(frame, signals, START, END)
        positions, curve = path_metrics(frame, result)
        result["positions"] = positions
        controls = make_controls(frame, signals, positions, buckets)
        # Validate the vectorized label against independent event-loop tracer.
        checks = []
        for p in positions.itertuples():
            value, rem, _ = fast_outcomes(frame, signals, [p.entry_time], p.side, END)
            np.testing.assert_allclose([value[0],rem[0]], [p.terminal_marked_net_return,p.remaining_fraction], atol=1e-12)
        if not controls.empty:
            for idx in sorted(set([0, len(controls)//2, len(controls)-1])):
                c = controls.iloc[idx]
                expected = trace_position(frame, signals, c.control_entry_time, int(c.side), END)["positions"].iloc[0]
                np.testing.assert_allclose([c.control_net,c.control_remaining], [expected.terminal_marked_net_return,expected.remaining_fraction], atol=1e-12)
                checks.append(int(idx))
        stats = {"all": describe(positions, controls)}
        for label, mask in (("long", positions.side == 1), ("short", positions.side == -1), ("late_entries", positions.entry_time >= SPLIT)):
            sub = positions.loc[mask]
            stats[label] = describe(sub, controls.loc[controls.position_id.isin(sub.position_id)])
        # Early cohort is valued at the temporal split, even if it later closes.
        early_result = replay(frame, signals, START, SPLIT)
        early = early_result["positions"].copy()
        early_control = controls.loc[controls.actual_entry_time < SPLIT].copy()
        for side in (-1, 1):
            mask = early_control.side == side
            values, remaining, _ = fast_outcomes(frame, signals, pd.DatetimeIndex(early_control.loc[mask,"control_entry_time"]), side, SPLIT)
            early_control.loc[mask,"control_net"] = values
            early_control.loc[mask,"control_remaining"] = remaining
        early_control["actual_net"] = early_control.position_id.map(early.set_index("position_id").terminal_marked_net_return)
        early_control["horizon"] = SPLIT
        stats["early_entries_asof_split"] = dict(entries=len(early), closed=int(early.status.eq("closed").sum()),
            open=int(early.status.eq("open").sum()), marked_net_sum=float(early.terminal_marked_net_return.sum()),
            marked_net_mean=float(early.terminal_marked_net_return.mean()) if len(early) else None,
            control=paired_summary(early_control))
        split_pnl = float(curve.loc[curve.time <= SPLIT, "net_pnl"].iloc[-1])
        np.testing.assert_allclose(split_pnl, early.terminal_marked_net_return.sum(), atol=1e-12)
        evaluated = counted.loc[(counted.index >= START) & (counted.index < END)]
        summary["arms"][f"{hours}h"] = dict(stats=stats, strong_diamonds=int(evaluated.strong_side.ne(0).sum()),
            fifth_candidates=int(evaluated.strong_streak.eq(5).sum()),
            fifth_long=int(((evaluated.strong_streak==5)&(evaluated.strong_side==1)).sum()),
            fifth_short=int(((evaluated.strong_streak==5)&(evaluated.strong_side==-1)).sum()),
            max_strong_run=int(evaluated.strong_streak.max()),
            marked_drawdown=float(curve.drawdown.max()), early_calendar_marked_pnl=split_pnl,
            late_calendar_marked_pnl=float(curve.net_pnl.iloc[-1]-split_pnl),
            vector_tracer_checks=checks, empty_control_pools=0)
        for name, table in result.items():
            table.to_csv(out/f"{hours}h_{name}.csv",index=False)
        controls.to_csv(out/f"{hours}h_controls.csv.gz",index=False,compression="gzip")
        early.to_csv(out/f"{hours}h_early_positions.csv",index=False)
        early_control.to_csv(out/f"{hours}h_early_controls.csv.gz",index=False,compression="gzip")
        curve.to_csv(out/f"{hours}h_curve.csv.gz",index=False,compression="gzip")
        counted.loc[counted.strong_side.ne(0)].to_csv(out/f"{hours}h_diamonds.csv")
        groups = []
        for period in ("year", "month"):
            if positions.empty:
                continue
            keys = positions.entry_time.dt.strftime("%Y" if period == "year" else "%Y-%m")
            for key, sub in positions.groupby(keys):
                s = describe(sub, controls.loc[controls.position_id.isin(sub.position_id)])
                groups.append(dict(period=period, value=key, entries=s["entries"], closed=s["closed"], open=s["open"],
                    closed_win_rate=s["closed_win_rate"], marked_net_sum=s["marked_net_sum"], marked_net_mean=s["marked_net_mean"],
                    random_mean=s["control"].get("random_mean"), excess_mean=s["control"].get("excess_mean"), p_greater=s["control"].get("p_greater")))
        pd.DataFrame(groups).to_csv(out/f"{hours}h_grouped.csv",index=False)
    dump(out/"warmup_qa.json", parity)
    summary["builder_commit"] = subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
    dump(out/"summary.json", summary)
    dump(out/"manifest.json", dict(generated_at=pd.Timestamp.now(tz="UTC"), builder_commit=summary["builder_commit"],
        builders={p:sha(ROOT/p) for p in builders}, data_sha256=sha(data),
        files={p.name:sha(p) for p in sorted(out.iterdir()) if p.is_file()}))
    print("complete", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    run(parser.parse_args().output)
