"""Allocate original E20 trades with the midpoint target for shallow entry closes only.

The frozen far classification uses the signal side, level and touch candle's close.
All legs are read from existing Nautilus fills, including the separate October run.
"""
import json
import numpy as np
import pandas as pd

from yoyo.evaluation.live_wick_score_october import metrics
from yoyo.evaluation.live_wick_two_closes_exit import bootstrap
from yoyo.evaluation.monday_limit_close_reclaim import committed
from yoyo.evaluation.monday_limit_close_survivor_paths import symbol_rows
from yoyo.evaluation.monday_limit_return_opt import frac
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.strategy_booklet_paths import Trace


A = ROOT / "experiments/active"
OUT = A / "exp-live-wick-shallow-midpoint-20261109-v1"
BASE = A / "exp-monday-limit-close-survivor-paths-20261101-v10"
V5 = A / "exp-monday-limit-close-survivor-paths-20261101-v5"


def assemble():
    source = pd.read_csv(A/"exp-live-wick-two-closes-exit-20261109-v1/paired.csv.gz")
    near = source[["event","entry_t","side","symbol","week","old_net","old_mae","old_exit","old_reason"]].copy()
    classification = pd.read_csv(A/"exp-live-wick-confirmed-swing-20261107-v1/events.csv.gz",usecols=["event","far","source"])
    x = near.merge(classification,on="event",validate="one_to_one").set_index("event")
    old = pd.read_csv(V5/"range_0.5-2020-ledger.csv.gz").dropna(subset=["trade_id"]).rename(columns={"trade_id":"event","net_bp":"net"})
    october = pd.read_csv(OUT/"october_midpoint_legs.csv.gz")
    mids = [old, october]
    shallow = x.index[~x.far]
    missing = set(shallow)-set(old.event)-set(october.event)
    for event in sorted(missing):
        legs, issues = symbol_rows((str(A/"exp-monday-limit-close-stop-grid-20261101-v1"),event.split(":")[0],10,10,"E20"))
        leg = legs[(legs.event==event) & (legs["mode"]=="range_0.5")]
        if issues or len(leg)!=1:
            raise ValueError(f"Missing archived midpoint for {event}")
        mids.append(leg)
    mid = pd.concat(mids,ignore_index=True).set_index("event").loc[shallow]
    if mid.index.has_duplicates or len(x)!=416 or len(mid)!=202 or len(october)!=10:
        raise ValueError("Incomplete E20 opportunity population")
    for dest, original, alternative in [("net","old_net","net"),("exit_t","old_exit","exit_t"),
                                        ("mae_pct","old_mae","mae_pct"),("reason","old_reason","reason")]:
        x[dest] = x[original].copy()
        x.loc[shallow,dest] = mid[alternative].astype(str).to_numpy() if dest=="exit_t" else mid[alternative].to_numpy()
    x["entry_t"] = pd.to_datetime(x.entry_t,utc=True)
    x["new_net"] = x.net
    return x


def accounts(x):
    order = pd.read_csv(A/"exp-live-ema-cloud-filter-20261103-v1/candidates.csv.gz",usecols=["account","start","event"])
    manifest = json.loads((BASE/"manifest.json").read_text())
    results, logs = {}, []
    for start in ["2020-01-01","2025-01-01","2026-10-01"]:
        ordering = order[(order.account=="live-wick6x3") & (order.start==("2025-01-01" if start=="2026-10-01" else start))]
        pop = x.loc[ordering.event]
        pop = pop[pop.entry_t>=pd.Timestamp(start,tz="UTC")]
        pair = {}
        for label, net, exit_col, mae, reason in [("near","old_net","old_exit","old_mae","old_reason"),
                                                  ("shallow_midpoint","net","exit_t","mae_pct","reason")]:
            rows = pop.assign(event=pop.index, net=pop[net], mae_pct=pop[mae], reason=pop[reason],
                              entry_ns=pop.entry_t.dt.as_unit("ns").astype("int64"),
                              exit_ns=pd.to_datetime(pop[exit_col],utc=True).dt.as_unit("ns").astype("int64"))
            tr = Trace(pd.Timestamp(start,tz="UTC").value)
            result = frac(rows,6,3,manifest["seed"],trace=tr)
            ledger = pd.DataFrame(tr.rows[1:])
            pair[label] = dict(**result, funded=metrics(ledger.net_bp))
            if label=="near" and start!="2026-10-01":
                ref = pd.read_csv(BASE/f"near_monday_edge-{start[:4]}-ledger.csv.gz").dropna(subset=["trade_id"])
                if list(ref.trade_id)!=list(ledger.trade_id) or not np.allclose(ref.equity,ledger.equity,atol=1e-6,rtol=0):
                    raise ValueError("Original funded equity path differs")
            if label=="near" and start=="2026-10-01":
                ref = json.loads((A/"exp-monday-limit-live-october-20261101-v1/summary.json").read_text())["results"]["live-wick6x3"]
                if abs(result["final_multiple"]*1000-ref["final"])>1e-6 or result["taken"]!=ref["funded"]:
                    raise ValueError("Original October account differs")
            logs.append(ledger.assign(start=start,policy=label))
        results[start] = pair
    pd.concat(logs).to_csv(OUT/"funded.csv.gz",index=False)
    return results


def main():
    committed("yoyo/evaluation/live_wick_shallow_midpoint.py", str((OUT/"config.json").relative_to(ROOT)))
    x = assemble()
    cfg = json.loads((OUT/"config.json").read_text())
    groups = {}
    for name, group in [("all",x),("discovery",x[x.source=="base"]),
            ("discovery_since2025",x[(x.source=="base")&(x.entry_t.dt.year>=2025)]),
            ("since2025",x[x.entry_t.dt.year>=2025]),("october",x[x.entry_t.dt.month.eq(10)&x.entry_t.dt.year.eq(2026)])]:
        groups[name] = dict(shallow=int((~group.far).sum()), near=metrics(group.old_net), conditional=metrics(group.new_net))
        if name!="october":
            groups[name]["paired_weekly"] = bootstrap(group,cfg)
    results = accounts(x)
    x.sort_values("entry_t").to_csv(OUT/"paired.csv.gz")
    dump(OUT/"summary.json",dict(groups=groups, accounts=results))


if __name__=="__main__":
    main()
