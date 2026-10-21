"""Isolate V2.21 post-arrow confirmation bars (0/1/2) in the Nautilus framework.

Only `after` in the V2.21 reference matcher changes; it is the Pine input
"勾叉后收盘确认（根）". Admission otherwise keeps V2.21 defaults: completed
15m OHLC, Stoch5/3/3 arrows, pre-touch Monday/Fib grid, five pre-arrow bodies,
10% wick penetration, short keys at/above Monday low, Tue-Fri NY entry closes
and the 0.2%-of-entry touch-extreme stop. Execution, the confirmed 15m 2/2
pivot exit (market at its confirmation close, initial stop first), strict-tie
sensitivity and 20bp cost reuse the scripts/backtest-range runner unchanged.
Each variant draws its own same-week/half/ATR-bucket control from the shared
seed. post2 must reproduce the prior V2.21 comparison candidates and pivot
ledger exactly; it is a parity anchor, not a new result.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.btc_range_wick_fanshen import monday_levels
from yoyo.evaluation.monday_range_v2_21_reference import select_matches
from yoyo.evaluation.monday_range_v2_2_reference import signals
from yoyo.evaluation.pepe_range_exits import aggregate
from yoyo.evaluation.range_framework import ROOT, dump, file_stamp, run
from yoyo.evaluation.range_management_study import holm
from yoyo.evaluation.range_v221_compare import CODE as COMPARE_CODE, candidate_rows, controls
from yoyo.evaluation.range_v230_pivot_study import analyze as analyze_pivots

DEFAULT=ROOT/"experiments/active/exp-range-v221-postbars-20261021-v1/config.json"
CODE=list(dict.fromkeys([*COMPARE_CODE,"yoyo/evaluation/range_v221_postbars.py"]))
LEDGER=["pair_id","population","entry_i","side","entry","stop","exit_i","exit_price",
        "reason","gross_r","fee_r","net_r"]


def check_config(cfg):
    """Pine bounds are 0/1/2; post2 is required as the frozen-default anchor."""
    bars=cfg["post_bars"]
    if len(bars)!=len(set(bars)) or not set(bars)<={0,1,2} or 2 not in bars:
        raise ValueError("post_bars must be distinct values from 0/1/2 and include 2")


def delay_cost(side, early, late, late_stop):
    """Price the later entry gives up on the same arrow: bp of early, R of late.

    Positive means the later variant entered worse in the trade direction.
    Inputs are admitted entry closes and the later entry's initial stop only.
    """
    side,early,late,late_stop=(np.asarray(x,float) for x in (side,early,late,late_stop))
    cost=side*(late-early)
    return cost/early*1e4, cost/(side*(late-late_stop))


def prepare(cfg,spec,dest):
    source=ROOT/cfg["source"]/spec["symbol"]
    native=json.loads((source/"native.json").read_text())
    data=ROOT/native["data"]
    reference=ROOT/cfg["parity_reference"]/spec["symbol"]
    prepared=dest/spec["symbol"]/"preparation.json"
    identity=dict(config=cfg,spec=spec,data=file_stamp(data),
        reference=file_stamp(reference/"candidates.csv"),code=[file_stamp(ROOT/p) for p in CODE])
    if prepared.exists():
        previous=json.loads(prepared.read_text())
        if previous["identity"]!=identity:
            raise RuntimeError("Preparation identity changed; retain previous results")
        return previous
    frame=pd.read_csv(data,float_precision="round_trip")
    frame.index=pd.DatetimeIndex(pd.to_datetime(frame.pop("ts"),unit="ms",utc=True)+pd.Timedelta(minutes=5)).as_unit("ns")
    frame=frame[["open","high","low","close"]]
    q=aggregate(frame,15)
    grid,names,weeks,_=monday_levels(q,cfg["ratios"],cfg["timezone"])
    arrows=signals(q).to_numpy()
    admitted={}
    probes_checked=0
    for after in cfg["post_bars"]:
        kwargs=dict(before=cfg["pre_bars"],after=after,penetration=cfg["penetration"],
                    buffer=cfg["buffer"],tick=spec["tick"],timezone=cfg["timezone"],start=cfg["start"])
        selected=select_matches(q,arrows,grid,names,weeks,**kwargs)
        rows=[dict(r,pair_id=i) for i,r in enumerate(selected)
              if pd.Timestamp(r["entry_time"])<pd.Timestamp(cfg["end"])]
        probes=sorted(set([r["entry_i"]+1 for r in rows[::max(1,len(rows)//12)]]+[len(q)]))
        for end in probes:
            got=select_matches(q.iloc[:end],arrows[:end],grid[:end],names,weeks[:end],**kwargs)
            assert got==[r for r in selected if r["entry_i"]<end]
        probes_checked+=len(probes)
        matched,half,week,bucket=controls(q,arrows,rows,cfg,spec)
        folder=dest/f"post{after}"/spec["symbol"]
        folder.mkdir(parents=True,exist_ok=False)
        candidate_rows(frame,q,rows,matched,(half,week,bucket),spec["tick"]).to_csv(folder/"candidates.csv",index=False)
        pd.DataFrame(rows).to_csv(folder/"signal_matches.csv",index=False)
        dump(folder/"native.json",dict(native,experiment_id=cfg["experiment_id"],
            entry_candidates=str((folder/"candidates.csv").relative_to(ROOT)),
            output=str((folder/"native").relative_to(ROOT))))
        admitted[after]=pd.DataFrame(rows)
    # post2 is the frozen V2.21 default: pairs and control times must not move.
    pd.testing.assert_frame_equal(pd.read_csv(dest/"post2"/spec["symbol"]/"candidates.csv"),
                                  pd.read_csv(reference/"candidates.csv"))
    base=admitted[2]
    admission={}
    for after,rows in admitted.items():
        shared=rows.merge(base,on=["signal_i","side"],suffixes=("","_post2"),validate="one_to_one")
        bp,r=delay_cost(shared.side,shared.entry,shared.entry_post2,shared.stop_post2)
        later=shared.entry_i_post2>shared.entry_i
        admission[f"post{after}"]=dict(n=len(rows),longs=int((rows.side==1).sum()),
            shorts=int((rows.side==-1).sum()),shared_with_post2=len(shared),
            only_this=len(rows)-len(shared),post2_only=len(base)-len(shared),
            entry_bars_after_arrow={str(k):int(v) for k,v in
                (rows.entry_i-rows.signal_i).value_counts().sort_index().items()},
            post2_later=int(later.sum()),
            post2_cost_bp_mean=float(bp[later].mean()) if later.any() else 0.,
            post2_cost_bp_median=float(np.median(bp[later])) if later.any() else 0.,
            post2_cost_r_median=float(np.median(r[later])) if later.any() else 0.,
            different_touch=int((shared.touch_i!=shared.touch_i_post2).sum()))
    metadata=dict(identity=identity,admission=admission,causal_prefixes=probes_checked,
        source_commit=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip())
    prepared.parent.mkdir(parents=True,exist_ok=True)
    dump(prepared,metadata)
    print(json.dumps(dict(symbol=spec["symbol"],**{k:v["n"] for k,v in admission.items()})),flush=True)
    return metadata


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",type=Path,default=DEFAULT)
    parser.add_argument("--prepare-only",action="store_true")
    args=parser.parse_args()
    if subprocess.run(["git","diff","--quiet","HEAD","--",*CODE,str(args.config)],cwd=ROOT).returncode:
        raise RuntimeError("Commit the post-bar builder and configuration before generation")
    cfg=json.loads(args.config.read_text());dest=args.config.parent
    check_config(cfg)
    for spec in cfg["symbols"]:
        prepare(cfg,spec,dest)
    if args.prepare_only:
        return
    results=[]
    for after in cfg["post_bars"]:
        work=dest/f"post{after}"
        for spec in cfg["symbols"]:
            run(work/spec["symbol"]/"native.json")
        results.extend(dict(r,variant=f"post{after}",win_rate=r["net_win_rate"],
            conservative_net_r=r.get("strict_tie_signal_net_r",r["net_r"])) for r in analyze_pivots(cfg,work))
    for spec in cfg["symbols"]:
        got=pd.read_csv(dest/"post2"/spec["symbol"]/"native/pivot/trades.csv",float_precision="round_trip")
        old=pd.read_csv(ROOT/cfg["parity_reference"]/spec["symbol"]/"native/pivot/trades.csv",float_precision="round_trip")
        pd.testing.assert_frame_equal(got[LEDGER],old[LEDGER],check_exact=False,rtol=0,atol=1e-9)
        # Holm covers the three confirmation variants per symbol.
        family=[r for r in results if r["symbol"]==spec["symbol"] and r["group"]=="all"]
        for row,pvalue in zip(family,holm([r["p_one_sided"] for r in family])):
            row["holm_p_postbar_family"]=float(pvalue)
    dump(dest/"summary.json",results)


if __name__=="__main__":
    main()
