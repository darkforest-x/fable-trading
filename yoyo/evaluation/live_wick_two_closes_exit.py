"""Single fixed exit on the archived live-wick entry population.

The exit sees only the entry bar's low/high and two subsequent completed 5m closes.
Run the alternative on the original Nautilus brackets, keeping the frozen baseline
and allocation order. All source times are UTC; candle indexes in raw denote opens.
"""
import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.live_wick_confirmed_swing import fills
from yoyo.evaluation.live_wick_score_october import metrics
from yoyo.evaluation.monday_limit_backstop import CODEX
from yoyo.evaluation.monday_limit_close_reclaim import committed
from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
from yoyo.evaluation.monday_limit_return_opt import frac
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.strategy_booklet_paths import Trace


OUT = ROOT / "experiments/active/exp-live-wick-two-closes-exit-20261109-v1"
OHLC = ["open", "high", "low", "close"]
STEP = pd.Timedelta(minutes=5)


def replay_symbol(task):
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native

    symbol, events, job, native_dir = task
    raw, _ = load_job(job)
    decimals = max(2, min(9, int(np.ceil(-np.log10(raw[OHLC].to_numpy().min()))) + 7))
    tick = 10.**-decimals
    result = []
    for path in ("ohlc", "olhc"):
        archived = fills(Path(native_dir), symbol, path)
        policy = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0,
                            target_from_entry=True, intrabar_path=path.upper(), reclaim_next_bar=True,
                            breakeven_includes_cost=False, fill_maker_fee=.0002, fill_taker_fee=.0005,
                            monotone_segments=True, reclaim_backstop=True, wick_failure_next_open=True)
        for r in events.itertuples():
            ref = archived.loc[r.event]
            a, b = int(ref.entry_i), int(ref.exit_i)
            if raw.index[a] + STEP != r.entry_t or int(ref.side) != r.side:
                raise ValueError(f"{r.event}: archived entry clock or side differs")
            # One extra actual bar leaves the old exit free of artificial end-of-data handling.
            frame = raw.iloc[a:min(b+2, len(raw))][OHLC].round(decimals).copy()
            frame.index = frame.index + STEP
            if abs(frame.close.iloc[0] - ref.entry) > tick * .51:
                raise ValueError(f"{r.event}: archived entry price differs")
            monday = pd.Timestamp(r.week, tz="America/New_York")
            day = raw[(raw.index >= monday) & (raw.index < monday + pd.DateOffset(days=1))]
            edge = day.low.min() if r.side == 1 else day.high.max()
            level = edge - r.side * .618 * (day.high.max() - day.low.min())
            if len(day) != 288 or abs(edge - ref.target_price) > tick * .51:
                raise ValueError(f"{r.event}: original Monday range differs")
            level_ticks = np.floor(level / tick + 1e-7) if r.side == 1 else np.ceil(level / tick - 1e-7)
            order = pd.DataFrame([dict(trade_id=r.event, side=r.side, entry_i=0, end_i=len(frame)-1,
                                       entry=ref.entry, risk=ref.entry, stop=ref.stop, target_price=ref.target_price,
                                       reclaim_ref=level_ticks*tick, reclaim_minutes=60)])
            rows, _, audit = run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)),
                                        order, policy, tick=tick, symbol=symbol.removesuffix("USDT"))
            alt = rows.iloc[0]
            net = float(alt.net_notional_bp - 10 - (0 if alt.reason == 4 else 10))
            old_time, new_time = pd.Timestamp(ref.exit_time), pd.Timestamp(alt.exit_time)
            if audit["version"] != "1.231.0" or audit["positions"] != 1 or audit["fills"] != 2:
                raise ValueError(f"{r.event}: invalid native fill audit")
            if alt.reason != 15 and (ref.reason != alt.reason or old_time != new_time or abs(net - ref.net) > 1e-6):
                raise ValueError(f"{r.event}: existing bracket diverged on {path}")
            i = int(alt.exit_i)
            extreme = frame.low.iloc[0] if r.side == 1 else frame.high.iloc[0]
            if alt.reason == 15 and (i < 3 or alt.exit_path_phase != 0 or new_time != frame.index[i]-STEP
                    or new_time > old_time or abs(alt.exit_price - frame.open.iloc[i]) > tick*.51
                    or not (r.side*(frame.close.iloc[i-2:i] - extreme) < 0).all()):
                raise ValueError(f"{r.event}: unconfirmed or wrongly timed exit")
            # Archived allocator excludes the original exit bar (retains one bar for immediate exits).
            adverse = raw.iloc[a+1:max(a+2, b)]
            worst = adverse.low.min() if r.side == 1 else adverse.high.max()
            old_mae = max(0., 100*r.side*(ref.entry-worst)/ref.entry)
            mae = old_mae
            if alt.reason == 15:
                bars = raw.iloc[a+1:a+i]
                worst = min(bars.low.min(), alt.exit_price) if r.side == 1 else max(bars.high.max(), alt.exit_price)
                mae = max(0., 100*r.side*(ref.entry-worst)/ref.entry)
            result.append(dict(event=r.event, path=path, entry_t=r.entry_t, symbol=symbol, side=r.side, week=r.week,
                               entry=ref.entry, wick=extreme, old_net=ref.net, old_exit=old_time, old_reason=int(ref.reason),
                               old_mae=old_mae, new_net=net, new_exit=new_time, new_reason=int(alt.reason), new_mae=mae,
                               old_px=ref.exit_price, new_px=alt.exit_price,
                               first_close=frame.close.iloc[i-2] if alt.reason == 15 else None,
                               second_close=frame.close.iloc[i-1] if alt.reason == 15 else None))
    return result


def run_fills(cfg, workers):
    cohort = pd.read_csv(ROOT / cfg["cohort"], parse_dates=["entry_t"])
    base = json.loads((ROOT / cfg["base"] / "config.json").read_text())
    ext_jobs = json.loads((ROOT / base["extension"] / "jobs.json").read_text())
    tasks = []
    for (symbol, source), group in cohort.groupby(["symbol", "source"]):
        if source == "extension":
            job, native_dir = ext_jobs[symbol], base["extension"]
        else:
            job = json.loads((CODEX / "symbols" / symbol / "identity.json").read_text())["job"]
            native_dir = base["fills"]
        tasks.append((symbol, group, job, str(ROOT / native_dir)))
    with ProcessPoolExecutor(workers) as pool:
        both = pd.DataFrame([r for block in pool.map(replay_symbol, tasks) for r in block])
    if len(both) != 832 or both.duplicated(["event", "path"]).any() or set(both.event) != set(cohort.event):
        raise ValueError("Incomplete frozen population")
    both.to_csv(OUT / "native_paths.csv.gz", index=False)


def paired_paths(cfg):
    both = pd.read_csv(OUT / "native_paths.csv.gz", parse_dates=["entry_t", "old_exit", "new_exit"])
    # Ties go to OHLC for each policy independently.
    old = both.sort_values(["event", "old_net", "path"]).drop_duplicates("event")
    new = both.sort_values(["event", "new_net", "path"]).drop_duplicates("event")
    old_fields = ["event", "symbol", "side", "entry_t", "week", "entry", "wick", "path", "old_net", "old_exit", "old_reason", "old_mae", "old_px"]
    new_fields = ["event", "path", "new_net", "new_exit", "new_reason", "new_mae", "new_px", "first_close", "second_close"]
    pairs = old[old_fields].merge(new[new_fields], on="event", validate="one_to_one", suffixes=("_old", "_new"))
    ref = pd.read_csv(ROOT / cfg["cohort"]).set_index("event").loc[pairs.event]
    if not np.allclose(pairs.old_net, ref.net_bp, atol=1e-6, rtol=0) or not np.array_equal(pairs.old_reason, ref.reason):
        raise ValueError("Archive conservative path parity failed")
    return pairs


def bootstrap(rows, cfg):
    """Resample paired original NY signal weeks; shared shocks stay in the same draw."""
    pieces = {}
    for name in ["old", "new"]:
        net = rows[name + "_net"]
        pieces[name] = pd.DataFrame(dict(w=net.clip(lower=0), l=-net.clip(upper=0),
                                         nw=(net>0).astype(int), nl=(net<0).astype(int), n=1)).groupby(rows.week).sum().to_numpy()
    rng = np.random.default_rng(cfg["seed"])
    weeks = len(pieces["old"])
    pick = rng.integers(0, weeks, (cfg["bootstrap_draws"], weeks))
    totals = {}
    for name in pieces:
        w, loss, nw, nl, n = pieces[name][pick].sum(axis=1).T
        totals[name] = np.column_stack([w*nl/(loss*nw), (w-loss)/n])
    ci = np.percentile(totals["new"]-totals["old"], [2.5,97.5], axis=0)
    return dict(weeks=weeks, payoff_delta_ci95=ci[:,0].tolist(), mean_bp_delta_ci95=ci[:,1].tolist())


def compare(rows, cfg):
    early = rows[rows.new_reason == 15]
    saved = early[early.old_reason == 1]
    return dict(baseline=metrics(rows.old_net), alternative=metrics(rows.new_net),
                triggered=len(early), changed_net=int(((rows.new_net-rows.old_net).abs()>1e-6).sum()),
                old_backstops=int((rows.old_reason==1).sum()), backstops_avoided=len(saved),
                saved_backstop_avg_bp=float((saved.new_net-saved.old_net).mean()) if len(saved) else None,
                old_winners_cut=int((early.old_net>0).sum()), wins_to_nonwins=int(((rows.old_net>0)&(rows.new_net<=0)).sum()),
                old_targets_cut=int((early.old_reason==4).sum()), paired_weekly=bootstrap(rows,cfg))


def summarize(cfg):
    pairs = paired_paths(cfg)
    years = pairs.entry_t.dt.year
    groups = {k:compare(pairs[m],cfg) for k,m in dict(all=years>=2020, to2024=years<2025,
              y2025=years==2025, y2026=years==2026, since2025=years>=2025).items()}
    order = pd.read_csv(ROOT / cfg["order"], usecols=["account","start","event","net"])
    root = ROOT / cfg["base"]
    manifest = json.loads((root / "manifest.json").read_text())
    accounts, traces = {}, []
    for key, ref in manifest["accounts"].items():
        start = ref["start"]
        population = order[(order.account=="live-wick6x3") & (order.start==start)].merge(pairs, on="event", validate="one_to_one")
        if len(population) != ref["candidates"] or not np.allclose(population.net, population.old_net, atol=1e-6,rtol=0):
            raise ValueError("Candidate order or baseline net changed")
        period = {}
        for name in ["old","new"]:
            x = population.assign(entry_ns=population.entry_t.dt.as_unit("ns").astype("int64"),
                                  exit_ns=population[name+"_exit"].dt.as_unit("ns").astype("int64"),
                                  net=population[name+"_net"], mae_pct=population[name+"_mae"], reason=population[name+"_reason"])
            tr = Trace(pd.Timestamp(start, tz="UTC").value)
            alloc = frac(x, ref["allocator"]["slots"], ref["allocator"]["leverage"], ref["seed"], trace=tr)
            led = pd.DataFrame(tr.rows[1:])
            if name == "old":
                archived = pd.read_csv(root / (key+"-ledger.csv.gz")).dropna(subset=["trade_id"])
                if (list(archived.trade_id)!=list(led.trade_id) or
                        not np.allclose(archived.equity, led.equity, atol=1e-6,rtol=0)):
                    raise ValueError(f"{start}: original funded ledger mismatch")
                period["original_funded_pairs"] = compare(population[population.event.isin(led.trade_id)], cfg)
            period[name] = dict(**alloc, funded=metrics(led.net_bp))
            traces.append(led.assign(start=start, policy=name))
        accounts[start] = period
    pairs.sort_values("entry_t").to_csv(OUT / "paired.csv.gz", index=False)
    pd.concat(traces).to_csv(OUT / "funded.csv.gz", index=False)
    dump(OUT / "summary.json", dict(groups=groups, accounts=accounts))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["fills","summarize"])
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    committed("yoyo/evaluation/live_wick_two_closes_exit.py", "yoyo/layers/l3_backtest/nautilus_independent.py",
              str((OUT / "config.json").relative_to(ROOT)))
    configuration = json.loads((OUT / "config.json").read_text())
    if args.stage == "fills":
        run_fills(configuration, args.workers)
    else:
        summarize(configuration)
