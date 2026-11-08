"""Switch only the TP for the previously defined deep-wick band using saved native legs.

The known-at-entry band is side * (1.618 level - touch close) / level >= 0.015.
Targets are the original NY Monday near boundary and midpoint known before entry.
"""
import json
import numpy as np
import pandas as pd

from yoyo.evaluation.live_wick_score_october import metrics
from yoyo.evaluation.live_wick_two_closes_exit import bootstrap
from yoyo.evaluation.monday_limit_close_reclaim import committed
from yoyo.evaluation.monday_limit_return_opt import frac
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.strategy_booklet_paths import Trace


OUT = ROOT / "experiments/active/exp-live-wick-deep-midpoint-20261109-v1"


def comparison(rows, cfg):
    rows = rows.reset_index(drop=True)
    far = rows.far.to_numpy(bool)
    delta = (rows.midpoint - rows.near).to_numpy()
    strata = list(rows.groupby([rows.entry_t.dt.year, rows.side]).indices.values())
    rng = np.random.default_rng(cfg["seed"])
    diff = np.empty(cfg["bootstrap_draws"])
    for i in range(len(diff)):
        mask = far.copy()
        for group in strata:
            mask[group] = rng.permutation(far[group])
        diff[i] = delta[mask].sum() / len(rows)
    observed = (rows.new_net - rows.old_net).mean()
    return dict(baseline=metrics(rows.old_net), conditional=metrics(rows.new_net),
                deep_count=int(far.sum()), deep_old=metrics(rows.loc[far,"old_net"]),
                deep_new=metrics(rows.loc[far,"new_net"]), paired_weekly=bootstrap(rows,cfg),
                random_assignment_p=float((1+(diff>=observed-1e-9).sum())/(1+len(diff))))


def recent_account(x, root):
    start = "2025-01-01"
    ref = json.loads((root / "manifest.json").read_text())["accounts"]["near_monday_edge-2025"]
    x = x[x.entry_t >= pd.Timestamp(start, tz="UTC")].copy()
    for mode in ["near_monday_edge", "range_0.5"]:
        ledger = pd.read_csv(root / f"{mode}-2025-ledger.csv.gz").dropna(subset=["trade_id"]).set_index("trade_id")
        if len(x)!=254 or set(ledger.index)!=set(x.event):
            raise ValueError("Incomplete recent account legs")
        ordered = ledger.loc[x.event]
        if not np.allclose(ordered.net_bp, x.near if mode=="near_monday_edge" else x.midpoint, rtol=0, atol=1e-6):
            raise ValueError("Native leg net differs from archived account")
        for col in ["exit_t","mae_pct","reason"]:
            x[mode+"_"+col] = ordered[col].to_numpy()
    out = {}
    for name, choose in [("baseline", pd.Series(False,index=x.index)), ("conditional", x.far)]:
        exit_t = pd.to_datetime(x.near_monday_edge_exit_t.where(~choose, x["range_0.5_exit_t"]), utc=True)
        rows = x.assign(exit_ns=exit_t.dt.as_unit("ns").astype("int64"), entry_ns=x.entry_t.dt.as_unit("ns").astype("int64"),
                        mae_pct=x.near_monday_edge_mae_pct.where(~choose,x["range_0.5_mae_pct"]),
                        net=x.near.where(~choose,x.midpoint), reason=x.near_monday_edge_reason.where(~choose,x["range_0.5_reason"]))
        tr = Trace(pd.Timestamp(start,tz="UTC").value)
        result = frac(rows,6,3,ref["seed"],trace=tr)
        if name=="baseline" and (result["taken"]!=ref["funded"] or abs(result["final_multiple"]-ref["summary"]["end"]/1000)>1e-7):
            raise ValueError("Original account parity failed")
        out[name] = {**result, "funded":metrics(pd.DataFrame(tr.rows[1:]).net_bp)}
        pd.DataFrame(tr.rows).to_csv(OUT / (name+"-2025.csv.gz"), index=False)
    return out


def main():
    committed("yoyo/evaluation/live_wick_deep_midpoint.py", str((OUT / "config.json").relative_to(ROOT)))
    cfg = json.loads((OUT/"config.json").read_text())
    legs = pd.read_csv(ROOT/cfg["paired"],parse_dates=["entry_t"])
    cohort = pd.read_csv(ROOT/cfg["cohort"],usecols=["event","far","side","week","symbol"])
    x = legs.merge(cohort,on="event",validate="one_to_one")
    if len(x)!=398 or x.far.isna().any():
        raise ValueError("Missing deep-close classifications")
    x["old_net"] = x.near
    x["new_net"] = x.near.where(~x.far,x.midpoint)
    year = x.entry_t.dt.year
    by_period = {k:comparison(x[m],cfg) for k,m in dict(all=year>=2020,to2024=year<2025,
                 y2025=year==2025,y2026=year==2026,since2025=year>=2025).items()}
    account = recent_account(x, ROOT/cfg["accounts"])
    x.to_csv(OUT/"paired.csv.gz",index=False)
    dump(OUT/"summary.json",dict(groups=by_period,account_since2025=account))


if __name__ == "__main__":
    main()
