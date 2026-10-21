"""Replay frozen V2.30 wick entries through the existing Nautilus framework.

Features: completed 15m OHLC, Stoch5/3/3, pre-touch Monday levels, pre-arrow
five bodies and +/-2 touch window; ATR14/close is used only to match controls.
Exit: exact touch extreme stop, or first profitable 15m 2/2 pivot recognized
at its confirmation close. Orders fill at that close, never the old extreme.
No tuning, day-end exit, Pine modification or funded-account claim.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.btc_range_wick_fanshen import monday_levels
from yoyo.evaluation.monday_range_v2_2_reference import aggregate, atr, signals
from yoyo.evaluation.monday_range_v2_24_reference import select_matches
from yoyo.evaluation.pepe_range_exits import inference, pairs
from yoyo.evaluation.range_framework import ROOT, dump, file_stamp, run

DEFAULT = ROOT / "experiments/active/exp-range-v230-pivot-20261021-v1/config.json"
CODE = ["yoyo/evaluation/range_v230_pivot_study.py",
        "yoyo/evaluation/range_framework.py",
        "yoyo/layers/l3_backtest/nautilus_independent.py",
        "yoyo/evaluation/monday_range_v2_24_reference.py"]


def prepare_symbol(cfg, spec, work):
    """Reuse existing minute sources and reject gaps/overlap conflicts."""
    dest = work/spec["symbol"]
    if (dest/"preparation.json").exists():
        old = json.loads((dest/"preparation.json").read_text())
        if old["config"] != cfg or old["spec"] != spec:
            raise ValueError("Preserve existing preparation; use a new experiment")
        return dest/"native.json"
    start, end = pd.Timestamp(cfg["start"]), pd.Timestamp(cfg["end"])
    warm = pd.Timestamp(cfg["warmup"])
    source = json.loads((ROOT/spec["source_receipt"]).read_text())
    paths = ([Path(p["path"]) for p in source["sources"]] if "sources" in source else
             [ROOT/p for p in source["source_files"]])
    pieces = []
    for path in paths:
        for chunk in pd.read_csv(path, usecols=["ts", "open", "high", "low", "close"],
                                 float_precision="round_trip", chunksize=250_000):
            pieces.append(chunk[(chunk.ts >= warm.value//1_000_000)
                                & (chunk.ts < end.value//1_000_000)])
    minutes = pd.concat(pieces, ignore_index=True)
    duplicate = minutes[minutes.duplicated("ts", keep=False)]
    if not duplicate.empty and (duplicate.groupby("ts").nunique().max(axis=1)>1).any():
        raise ValueError("Conflicting OHLC in registered source overlap")
    minutes = minutes.drop_duplicates("ts", keep="last").sort_values("ts")
    minutes.index = pd.to_datetime(minutes.pop("ts"), unit="ms", utc=True)
    expected = pd.date_range(warm, end, freq="min", inclusive="left").tz_convert("UTC")
    if not minutes.index.equals(expected) or minutes.isna().any().any():
        raise ValueError("Missing or invalid minute data")
    five = aggregate(minutes, 5)
    q = aggregate(minutes, 15)
    grid, labels, weeks, _ = monday_levels(q, cfg["ratios"], cfg["timezone"])
    arrows = signals(q).to_numpy()
    # The wider-buffer oracle enumerates matching identities. Apply the exact
    # current Pine risk guard explicitly, with no synthetic stop buffer.
    selected = select_matches(q, arrows, grid, labels, weeks, before=5, wait_bars=2,
                              penetration=.1, buffer=.002, tick=spec["tick"],
                              timezone=cfg["timezone"], start=cfg["start"])
    real = []
    for row in selected:
        j, e, side = row["touch_i"], row["entry_i"], row["side"]
        stop = float(q.iloc[j]["low" if side == 1 else "high"])
        if start <= q.index[e] < end and side*(row["entry"]-stop) >= spec["tick"] and stop > 0:
            real.append(dict(row, stop=stop))
    if len(real) != spec["expected_signals"]:
        raise AssertionError(f"Frozen count changed: {spec['symbol']} {len(real)}")
    # A control has the same side and relative initial risk as its matched
    # signal. Its price is sampled only from the same week/half/ATR bucket.
    volatility = atr(q, 14).to_numpy()/q.close.to_numpy()
    bucket = np.digitize(volatility, cfg["volatility_edges"])
    week = (q.index-pd.to_timedelta(q.index.dayofweek, unit="D")).normalize().asi8
    segment = (q.index >= pd.Timestamp(cfg["midpoint"])).astype(int)
    weekday = q.index.tz_convert(cfg["timezone"]).dayofweek
    excluded = {r["entry_i"] for r in real} | set(np.flatnonzero(arrows))
    allowed = (q.index >= start) & (q.index < end) & (weekday >= 1) & (weekday <= 4)
    pools = {}
    for i in np.flatnonzero(allowed & np.isfinite(volatility)):
        if int(i) not in excluded:
            pools.setdefault((int(week[i]),int(segment[i]),int(bucket[i])), []).append(int(i))
    rng = np.random.default_rng(cfg["seed"] + spec["seed_offset"])
    for pool in pools.values():
        rng.shuffle(pool)
    entries = []
    for pair_id, source_row in enumerate(real):
        e, side = source_row["entry_i"], source_row["side"]
        key = (int(week[e]),int(segment[e]),int(bucket[e]))
        pool = pools.get(key, [])
        if not pool:
            raise ValueError(f"No matched random control for {pair_id}")
        random_i = pool.pop()
        risk_fraction = side*(source_row["entry"]-source_row["stop"])/source_row["entry"]
        for population, i in (("signal",e),("random",random_i)):
            entry = float(q.close.iloc[i])
            raw = entry*(1-side*risk_fraction)
            control_stop = (math.floor(raw/spec["tick"]+1e-8) if side == 1 else
                            math.ceil(raw/spec["tick"]-1e-8))*spec["tick"]
            stop = source_row["stop"] if population == "signal" else control_stop
            entries.append(dict(pair_id=pair_id, population=population,
                entry_i=int(five.index.get_indexer([q.index[i]])[0]),
                side=side, entry=entry, stop=stop, end_i=len(five)-1,
                segment=int(segment[i]), week=int(week[i]), bucket=int(bucket[i]),
                entry_time=q.index[i].isoformat()))
    dest.mkdir(parents=True, exist_ok=False)
    out = five.copy()
    out.insert(0,"ts", (five.index.as_unit("ns").asi8//1_000_000)-300000)
    out.to_csv(dest/"candles.csv.gz", index=False)
    pd.DataFrame(entries).to_csv(dest/"candidates.csv", index=False)
    pd.DataFrame(real).to_csv(dest/"signal_matches.csv", index=False)
    native = dict(schema=1,experiment_id=cfg["experiment_id"],engine="nautilus",
        symbol=spec["symbol"],tick=spec["tick"],market_tick=spec["tick"],entry_mode="provided",
        data=str((dest/"candles.csv.gz").relative_to(ROOT)),
        entry_candidates=str((dest/"candidates.csv").relative_to(ROOT)),
        stop_window_5m=1,buffer_percent=0,maximum_risk_percent=0,
        round_trip_cost=cfg["round_trip_cost"],
        policies=[dict(name="pivot",confirmed_pivot_exit=True,opposite_hour_exit=False),
                  dict(name="strict_ties",confirmed_pivot_exit=True,
                       strict_pivot_ties=True,opposite_hour_exit=False)],
        output=str((dest/"native").relative_to(ROOT)))
    dump(dest/"native.json", native)
    dump(dest/"preparation.json",dict(config=cfg,spec=spec,
        sources=[file_stamp(p) for p in paths],minute_rows=len(minutes),quarter_rows=len(q),
        signals=len(real),controls=len(real),code=[file_stamp(ROOT/p) for p in CODE],
        source_commit=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()))
    return dest/"native.json"


def analyze(cfg, work):
    summaries = []
    for spec in cfg["symbols"]:
        dest = work/spec["symbol"]
        rows = pd.read_csv(dest/"native/pivot/trades.csv", float_precision="round_trip")
        strict = pd.read_csv(dest/"native/strict_ties/trades.csv",float_precision="round_trip").set_index("trade_id")
        aligned = strict.loc[rows.trade_id]
        changed = ((rows.exit_i.to_numpy()!=aligned.exit_i.to_numpy())
                   | ~np.isclose(rows.exit_price,aligned.exit_price.to_numpy(),rtol=0,atol=spec["tick"]*1e-7))
        groups = [("all",rows),("long",rows[rows.side==1]),("short",rows[rows.side==-1]),
                  ("first_half",rows[rows.segment==0]),("second_half",rows[rows.segment==1])]
        for label, subset in groups:
            s = subset[subset.population=="signal"]
            r = subset[subset.population=="random"]
            paired = pairs(s,r)
            ordered = s.sort_values(["exit_i","entry_i"]).net_r.cumsum().to_numpy()
            drawdown = float((np.maximum.accumulate(np.r_[0,ordered])[1:]-ordered).max()) if len(ordered) else 0.
            losses = -s.loc[s.net_r<0,"net_r"].sum()
            summaries.append(dict(symbol=spec["symbol"],group=label,n=len(s),
                wins=int((s.net_r>0).sum()),net_win_rate=float((s.net_r>0).mean()),
                gross_win_rate=float((s.gross_r>0).mean()),gross_r=float(s.gross_r.sum()),
                fee_r=float(s.fee_r.sum()),net_r=float(s.net_r.sum()),
                pf=float(s.loc[s.net_r>0,"net_r"].sum()/losses) if losses else None,
                independent_r_drawdown=drawdown,
                mean_net_bp=float(s.net_notional_bp.mean()),
                median_stop_percent=float((s.risk/s.entry*100).median()),
                stopped=int((s.reason==1).sum()),pivot_exits=int((s.reason==7).sum()),
                pivot_exit_losses=int(((s.reason==7)&(s.net_r<=0)).sum()),
                boundary_n=int((s.reason==0).sum()),boundary_net_r=float(s.loc[s.reason==0,"net_r"].sum()),
                random_net_r=float(r.net_r.sum()),random_win_rate=float((r.net_r>0).mean()),
                **inference(paired,cfg["seed"])))
        signal_changed = changed & rows.population.eq("signal").to_numpy()
        summaries[-5]["tie_signal_paths_changed"] = int(signal_changed.sum())
        summaries[-5]["tie_control_paths_changed"] = int((changed & ~rows.population.eq("signal").to_numpy()).sum())
        summaries[-5]["strict_tie_signal_net_r"] = float(strict.loc[strict.population=="signal","net_r"].sum())
    dump(work/"summary.json",summaries)
    return summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",type=Path,default=DEFAULT)
    args = parser.parse_args()
    if subprocess.run(["git","diff","--quiet","HEAD","--",*CODE],cwd=ROOT).returncode:
        raise RuntimeError("Commit the builder before generating results")
    cfg = json.loads(args.config.read_text())
    for spec in cfg["symbols"]:
        native = prepare_symbol(cfg,spec,args.config.parent)
        run(native)
    summary = analyze(cfg,args.config.parent)
    print(json.dumps([s for s in summary if s["group"]=="all"],indent=2))


if __name__ == "__main__":
    main()
