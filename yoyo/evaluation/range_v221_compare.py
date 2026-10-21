"""Compare frozen V2.21 defaults with V2.30, reusing annual OHLC and exits.

Admission reads completed 15m OHLC, Stoch5/3/3, pre-touch Monday/Fib levels,
five pre-arrow bodies and two post-arrow bodies. V2.21 waits for the second
post-arrow close; no outcome selects entries. Its original 0.2%-of-entry
stop buffer is retained, with an exact-wick bridge for attribution only.
All native exits and controls use the prior approved comparison framework.
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
from yoyo.evaluation.monday_range_v2_21_reference import select_matches
from yoyo.evaluation.monday_range_v2_2_reference import atr, signals
from yoyo.evaluation.pepe_range_exits import aggregate
from yoyo.evaluation.range_framework import ROOT, dump, file_stamp, run
from yoyo.evaluation.range_management_study import holm
from yoyo.evaluation.range_v230_exit_compare import CODE as EXIT_CODE, run_symbol
from yoyo.evaluation.range_v230_pivot_study import analyze as analyze_pivots

DEFAULT=ROOT/"experiments/active/exp-range-v221-compare-20261021-v1/config.json"
CODE=list(dict.fromkeys([*EXIT_CODE,"yoyo/evaluation/range_v221_compare.py",
    "yoyo/evaluation/monday_range_v2_21_reference.py",
    "yoyo/evaluation/range_v230_pivot_study.py",
    "yoyo/evaluation/pine/monday_range_v2_21.pine"]))


def wick_bridge(quarter, records, tick):
    """Keep the same admitted pairing; reject nonpositive exact-wick risk."""
    output=[]
    for row in records:
        stop=float(quarter.iloc[row["touch_i"]]["low" if row["side"]==1 else "high"])
        if stop>0 and row["side"]*(row["entry"]-stop)>=tick:
            output.append(dict(row,stop=stop))
    return output


def controls(quarter, arrows, records, cfg, spec):
    """Assign one matched time per original signal, shared by the stop bridge.

    Volatility uses completed 15m ATR14/close; bucket, week, half and weekday
    depend only on each candidate's known close. No future prices select it.
    """
    vol=atr(quarter,14).to_numpy()/quarter.close.to_numpy()
    bucket=np.digitize(vol,cfg["volatility_edges"])
    week=(quarter.index-pd.to_timedelta(quarter.index.dayofweek,unit="D")).normalize().asi8
    half=(quarter.index>=pd.Timestamp(cfg["midpoint"])).astype(int)
    weekday=quarter.index.tz_convert(cfg["timezone"]).dayofweek
    valid=(quarter.index>=pd.Timestamp(cfg["start"]))&(quarter.index<pd.Timestamp(cfg["end"]))
    valid &= (weekday>=1)&(weekday<=4)&np.isfinite(vol)
    excluded={r["entry_i"] for r in records}|set(np.flatnonzero(arrows))
    pools={}
    for i in np.flatnonzero(valid):
        if i not in excluded:
            pools.setdefault((int(week[i]),int(half[i]),int(bucket[i])),[]).append(int(i))
    rng=np.random.default_rng(cfg["seed"]+spec["seed_offset"])
    for pool in pools.values():
        rng.shuffle(pool)
    matched={}
    for row in records:
        i=row["entry_i"]
        key=(int(week[i]),int(half[i]),int(bucket[i]))
        if not pools.get(key):
            raise ValueError(f"No matched control for {spec['symbol']} {row['signal_time']}")
        matched[row["pair_id"]]=pools[key].pop()
    return matched,half,week,bucket


def candidate_rows(five,quarter,records,matched,features,tick):
    half,week,bucket=features
    rows=[]
    for record in records:
        side=record["side"]
        risk_fraction=side*(record["entry"]-record["stop"])/record["entry"]
        for population,i in (("signal",record["entry_i"]),("random",matched[record["pair_id"]])):
            entry=float(quarter.close.iloc[i])
            raw=entry*(1-side*risk_fraction)/tick
            stop=(math.floor(raw+1e-8) if side==1 else math.ceil(raw-1e-8))*tick
            if population=="signal":
                stop=record["stop"]
            five_i=int(five.index.get_indexer([quarter.index[i]])[0])
            if five_i<0:
                raise AssertionError("Missing entry close")
            rows.append(dict(pair_id=record["pair_id"],population=population,entry_i=five_i,
                side=side,entry=entry,stop=stop,end_i=len(five)-1,segment=int(half[i]),
                week=int(week[i]),bucket=int(bucket[i]),entry_time=quarter.index[i].isoformat()))
    return pd.DataFrame(rows)


def prepare(cfg,spec,dest):
    source=ROOT/cfg["source"]/spec["symbol"]
    native=json.loads((source/"native.json").read_text())
    data=ROOT/native["data"]
    prepared=dest/spec["symbol"]/"preparation.json"
    identity=dict(config=cfg,spec=spec,data=file_stamp(data),
        prior_matches=file_stamp(source/"signal_matches.csv"),code=[file_stamp(ROOT/p) for p in CODE])
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
    kwargs=dict(before=5,after=2,penetration=.1,buffer=.002,tick=spec["tick"],
                timezone=cfg["timezone"],start=cfg["start"])
    selected=select_matches(q,arrows,grid,names,weeks,**kwargs)
    original=[dict(r,pair_id=i) for i,r in enumerate(selected)
              if pd.Timestamp(r["entry_time"])<pd.Timestamp(cfg["end"])]
    bridge=wick_bridge(q,original,spec["tick"])
    # Validate prefixes at representative admitted decisions and study tail.
    probes=sorted(set([r["entry_i"]+1 for r in original[::max(1,len(original)//12)]]+[len(q)]))
    for end in probes:
        got=select_matches(q.iloc[:end],arrows[:end],grid[:end],names,weeks[:end],**kwargs)
        assert got==[r for r in selected if r["entry_i"]<end]
    matched,half,week,bucket=controls(q,arrows,original,cfg,spec)
    variants={"v221_original":original,"v221_wick":bridge}
    for name,records in variants.items():
        folder=dest/name/spec["symbol"]
        folder.mkdir(parents=True,exist_ok=False)
        candidates=candidate_rows(frame,q,records,matched,(half,week,bucket),spec["tick"])
        candidates.to_csv(folder/"candidates.csv",index=False)
        pd.DataFrame(records).to_csv(folder/"signal_matches.csv",index=False)
        config=dict(native,experiment_id=cfg["experiment_id"],data=native["data"],
            entry_candidates=str((folder/"candidates.csv").relative_to(ROOT)),
            output=str((folder/"native").relative_to(ROOT)))
        # Both pivot conventions are numerical sensitivity, never tuned exits.
        dump(folder/"native.json",config)
    prior=pd.read_csv(source/"signal_matches.csv")
    old=pd.DataFrame(original)
    shared=old.merge(prior,on=["signal_time","side"],suffixes=("_old","_current"),validate="one_to_one")
    delay=(pd.to_datetime(shared.entry_time_old,utc=True)-pd.to_datetime(shared.entry_time_current,utc=True)).dt.total_seconds()/60
    counts={name:dict(n=len(rows),longs=sum(r["side"]==1 for r in rows),
                    shorts=sum(r["side"]==-1 for r in rows)) for name,rows in variants.items()}
    counts["v230"]=dict(n=len(prior),longs=int((prior.side==1).sum()),shorts=int((prior.side==-1).sum()))
    comparison=dict(shared_arrows=len(shared),v221_only=len(old)-len(shared),v230_only=len(prior)-len(shared),
        same_entry=int((delay==0).sum()),current_earlier=int((delay>0).sum()),
        current_later=int((delay<0).sum()),median_delay_minutes=float(delay.median()),
        different_touch=int((shared.touch_time_old!=shared.touch_time_current).sum()),
        different_key=int((shared.key_old!=shared.key_current).sum()),
        exact_wick_invalid=len(original)-len(bridge))
    metadata=dict(identity=identity,counts=counts,admission=comparison,causal_prefixes=len(probes),
        source_commit=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip())
    prepared.parent.mkdir(parents=True,exist_ok=True)
    shared.to_csv(prepared.parent/"common_signals.csv",index=False)
    dump(prepared,metadata)
    print(json.dumps(dict(symbol=spec["symbol"],**counts,admission=comparison)),flush=True)
    return metadata


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",type=Path,default=DEFAULT)
    parser.add_argument("--prepare-only",action="store_true")
    args=parser.parse_args()
    if subprocess.run(["git","diff","--quiet","HEAD","--",*CODE,str(args.config)],cwd=ROOT).returncode:
        raise RuntimeError("Commit comparison builder and configuration before generation")
    cfg=json.loads(args.config.read_text());dest=args.config.parent
    metadata={spec["symbol"]:prepare(cfg,spec,dest) for spec in cfg["symbols"]}
    if args.prepare_only:
        return
    approved=json.loads((ROOT/cfg["exit_reference"]/"config.json").read_text())
    results=[]
    for variant in ("v221_original","v221_wick"):
        exit_cfg=dict(approved,experiment_id=cfg["experiment_id"],
            source=str((dest/variant).relative_to(ROOT)),variant=variant,
            admission_code=[file_stamp(ROOT/p) for p in CODE])
        for spec in cfg["symbols"]:
            selected=dict(spec,expected_signals=metadata[spec["symbol"]]["counts"][variant]["n"])
            results.extend(dict(r,variant=variant) for r in run_symbol(exit_cfg,selected,dest/variant/"exits"))
            run(dest/variant/spec["symbol"]/"native.json")
        pivots=analyze_pivots(cfg,dest/variant)
        results.extend(dict(r,variant=variant,arm="pivot",win_rate=r["net_win_rate"],
            conservative_net_r=r.get("strict_tie_signal_net_r",r["net_r"])) for r in pivots)
    old=json.loads((ROOT/cfg["exit_reference"]/"summary.json").read_text())
    results.extend(dict(r,variant="v230") for r in old)
    pivots=json.loads((ROOT/cfg["source"]/"summary.json").read_text())
    results.extend(dict(r,variant="v230",arm="pivot",win_rate=r["net_win_rate"],
        conservative_net_r=r.get("strict_tie_signal_net_r",r["net_r"])) for r in pivots)
    # Correct the entire requested version/exit comparison family per symbol.
    for symbol in (s["symbol"] for s in cfg["symbols"]):
        family=[r for r in results if r["symbol"]==symbol and r["group"]=="all"]
        for row,pvalue in zip(family,holm([r["p_one_sided"] for r in family])):
            row["holm_p_version_exit_family"]=float(pvalue)
    dump(dest/"summary.json",results)


if __name__=="__main__":
    main()
