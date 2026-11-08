"""Aggregate actual second-entry trades with separate returns for each timeframe."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from yoyo.evaluation import monday_flow_divergence_stats as stats


def comparison(g, name):
    z = g.rename(columns={"week":"_week","net_bp":"_net"}).copy()
    # A set containing both divergence subtypes still identifies the binary arm.
    definite = z.possible.fillna("").str.split("|",regex=False).map(
        lambda x: bool(x) and set(x).issubset({"regular","absorption"}))
    z["_label"] = np.where(definite, "regular", z.label.where(z.label=="none","unknown"))
    left, right = frozenset(["regular"]), frozenset(["none"])
    yes = stats._cohort(z,left)
    no = stats._cohort(z,right)
    boot = stats._bootstrap_ci(stats._weekly_sufficient(z),left,right,name)
    return dict(divergent=yes,none=no,
                diff_pp=100*(yes["rate"]-no["rate"]) if yes["rate"] is not None and no["rate"] is not None else None,
                ci95_pp=[100*v for v in boot["win_rate_diff_95ci"]] if boot["win_rate_diff_95ci"] else None,
                weeks=boot["weeks"])


def metrics(g, name):
    out = {"all":comparison(g,name)}
    asof=g.copy()
    asof["label"]=asof["asof_label"]
    asof["possible"]=asof["asof_label"]
    out["pre_retest_subbar"]=comparison(asof,name+"/pre")
    out["pre_retest_subbar_by_label"]=breakdown(asof)
    out["by_label"]=breakdown(g)
    out["by_period"]={k:comparison(z,name+"/"+k) for k,z in g.groupby(np.where(g.week<"2025","through_2024",g.week.str[:4]))}
    out["by_side"]={str(k):comparison(z,name+"/"+str(k)) for k,z in g.groupby("side")}
    out["both_endpoints_one_minute"]=comparison(g[g.both_1m.eq(True)], name+"/one_minute")
    # Price geometry control: condition on the same close-by, held extreme for both groups.
    inward = np.where(g.side==1, g.cross_min-g.first_extreme, g.first_extreme-g.cross_max)
    far = np.where(g.side==1, g.cross_max-g.first_extreme, g.first_extreme-g.cross_min)
    near = (inward>=0) & (far<=g.tolerance)
    out["held_extreme_within_quarter_atr"]=comparison(g[near],name+"/held_extreme")
    out["pierced_extreme"]=comparison(g[far<0],name+"/pierced")
    possible=g.possible.fillna("").map(lambda s:frozenset(s.split("|")) if s else frozenset())
    frame=pd.DataFrame({"_net":g.net_bp,"_possible_tf":possible})
    out["ambiguity_bounds"]=stats._feasible_bounds(frame,"tf",frozenset(["regular","absorption"]),frozenset(["none"]))
    return out


def breakdown(g):
    out={}
    for label,rows in g.groupby("label",dropna=False):
        v=rows.net_bp.dropna()
        out[str(label)]={"events":len(rows),"closed":len(v),"wins":int(v.gt(0).sum()),
                         "win_rate":float(v.gt(0).mean()) if len(v) else None,
                         "mean_net_bp":float(v.mean()) if len(v) else None}
    return out


def write_summary(exp:Path):
    folders=sorted(p.parent for p in (exp/"symbols").glob("*/done.json"))
    expected=json.loads((exp/"config.json").read_text())["source"]
    root=exp.parents[2]
    total_sources=sum(p.name!="XAUUSD" and (p/"trades.csv.gz").is_file() for p in (root/expected).iterdir())
    if len(folders)!=total_sources:
        raise ValueError(f"Have {len(folders)}/{total_sources} symbol receipts")
    frames=[pd.read_csv(p/"events.csv.gz") for p in folders]
    rows=pd.concat(frames,ignore_index=True).sort_values(["first_ms","symbol","timeframe"])
    if rows.duplicated(["event","timeframe"]).any():
        raise ValueError("Duplicate event/timeframe")
    rows.to_csv(exp/"events.csv.gz",index=False)
    actual=rows[rows.status=="retested"].copy()
    receipts=[json.loads((p/"done.json").read_text()) for p in folders]
    if actual.trade_id.nunique()!=sum(r["trades"] for r in receipts):
        raise ValueError("Native trades and unique actual retests do not reconcile")
    source=actual.drop_duplicates("trade_id")
    settled=source.dropna(subset=["net_bp"])
    if not np.allclose(settled.gross_bp-settled.fee_bp,settled.net_bp,atol=1e-6):
        raise ValueError("Trade cost reconciliation failed")
    summary={"symbols":len(folders),"first_touches":rows.event.nunique(),"distinct_retest_fills":len(source),
             "closed_distinct_fills":len(settled),"distinct_shared_across_timeframes":len(actual)-len(source),
             "first_utc":str(pd.to_datetime(rows.first_ms.min(),unit="ms",utc=True)),
             "last_retest_utc":str(pd.to_datetime(actual.retest_bar_ms.max(),unit="ms",utc=True)),
             "setup_counts":{tf:z.status.value_counts().to_dict() for tf,z in rows.groupby("timeframe")},
             "by_timeframe":{tf:metrics(z,tf) for tf,z in actual.groupby("timeframe")}}
    for tf,z in actual.groupby("timeframe"):
        picked=z.copy()
        for old,new in [("first_ms","first_touch_utc"),("armed_close_ms","armed_close_utc"),("retest_bar_ms","retest_bar_utc")]:
            picked[new]=pd.to_datetime(picked[old],unit="ms",utc=True).dt.strftime("%Y-%m-%d %H:%M")
        picked[["symbol","week","side","first_touch_utc","armed_close_utc","retest_bar_utc","limit","first_extreme",
                "label","possible","asof_label","cvd_min","cvd_max","entry_price","exit_price","reason","fee_bp","net_bp"]].to_csv(
                    exp/f"retests_{tf}.csv.gz",index=False)
    (exp/"summary.json").write_text(json.dumps(summary,indent=2,allow_nan=False)+"\n")
