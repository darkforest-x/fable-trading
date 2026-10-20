"""Bounded ETH exit optimization reusing the existing C++ and Nautilus adapters.

Inputs: frozen causal 15m-watch/5m entries and matching controls; last N completed
5m extrema, sparse completed 15m ATR14 and completed H1 arrows. No admission or
cost changes. Outcomes read future prices only within original year boundaries.
The owner authorizes joint stop/exit parameters and fixes total original-risk
net R as primary. Fixed-TP selection uses stop-first ambiguous-bar sensitivity;
native O-H-L-C outcomes remain separately named. This is known historical data.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import itertools
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

import numpy as np
import pandas as pd

from yoyo.evaluation.pepe_range_breakeven import native, replay_search
from yoyo.evaluation.pepe_range_exits import stop_events, pairs, inference
from yoyo.evaluation.range_framework import ROOT, prepared, dump, file_stamp, compare_golden
from yoyo.evaluation.range_parameter_search import decorate
from yoyo.evaluation.range_partial_study import cash_events, drawdown

DEFAULT = ROOT/"experiments/active/exp-eth-exit-search-20261020-v1/config.json"
CODE = ["yoyo/evaluation/range_exit_search.py",
        "yoyo/evaluation/pepe_range_breakeven.py",
        "yoyo/evaluation/pepe_range_be_native.cpp",
        "yoyo/layers/l3_backtest/nautilus_independent.py"]


def all_arms(cfg):
    exits = []
    for arm, multiple, be, reverse in itertools.product(
            cfg["trail_arm_r"], cfg["trail_atr_multiple"], cfg["breakeven_r"],
            cfg["opposite_hour_exit"]):
        exits.append(dict(family="trail", arm_r=arm, atr_multiple=multiple,
                          be_r=be, reverse_exit=reverse, target_r=None))
    for target, be in itertools.product(cfg["fixed_target_r"], cfg["breakeven_r"]):
        if be is None or be < target:
            exits.append(dict(family="fixed", arm_r=5, atr_multiple=4,
                              be_r=be, reverse_exit=False, target_r=target))
    result = []
    for window, buffer, policy in itertools.product(
            cfg["stop_window_5m"], cfg["buffer_percent"], exits):
        p = dict(window=window, buffer=buffer, **policy)
        p["name"] = (f"w{window}_b{buffer}_{policy['family']}"
                     f"_r{policy['target_r'] or policy['arm_r']}"
                     f"_a{policy['atr_multiple']}_be{policy['be_r'] or 0}"
                     f"_rev{int(policy['reverse_exit'])}").replace(".", "p")
        result.append(p)
    assert len({p["name"] for p in result}) == len(result)
    return result


def replay_args(p):
    return {k:p[k] for k in ("arm_r","atr_multiple","be_r","reverse_exit","target_r")}


def choose(table, segment, family=None):
    """Select only the specified elapsed segment, never later rows."""
    part = table[table.segment.eq(segment)]
    if family:
        part = part[part.family.eq(family)]
    return str(part.sort_values(["selection_net_r","realized_r_drawdown","name"],
                               ascending=[False,True,True]).iloc[0]["name"])


def compact_summary(base, output, conservative, policy, cost):
    entry, risk, side = (base[k].to_numpy() for k in ("entry","risk","side"))
    gross = side*(output[:,1]-entry)/risk
    fee = cost*entry/risk
    net = gross-fee
    net[np.abs(net)<1e-9] = 0.
    lower = side*(conservative[:,1]-entry)/risk-fee
    lower[np.abs(lower)<1e-9] = 0.
    bp = (gross*risk/entry-cost)*10000
    segment = base.segment.to_numpy()
    signal = base.population.eq("signal").to_numpy()
    rows = []
    for seg in (-1,0,1,2,-2,-3):
        mask = np.ones(len(base),bool) if seg==-1 else (
            segment>0 if seg==-2 else segment<2 if seg==-3 else segment==seg)
        ss, rr = mask&signal, mask&~signal
        v = net[ss]
        ordered = v[np.lexsort((base.entry_i.to_numpy()[ss],output[ss,0]))]
        neg = -v[v<0].sum()
        rows.append(dict(**policy, segment=seg, n=int(ss.sum()),
            net_r=float(v.sum()), selection_net_r=float(lower[ss].sum()),
            random_net_r=float(net[rr].sum()), random_selection_net_r=float(lower[rr].sum()),
            gross_r=float(gross[ss].sum()), fee_r=float(fee[ss].sum()),
            win_rate=float((v>1e-9).mean()), pf=float(v[v>0].sum()/neg) if neg else None,
            mean_net_bp=float(bp[ss].mean()), random_mean_net_bp=float(bp[rr].mean()),
            realized_r_drawdown=drawdown(ordered),
            gross_10r=int((gross[ss]>=10-1e-7).sum()),
            gross_20r=int((gross[ss]>=20-1e-7).sum()),
            initial_stops=int((output[ss,2]==1).sum()),
            boundary_n=int((output[ss,2]==0).sum()),
            natural_net_r=float(net[ss&(output[:,2]!=0)].sum()),
            collisions=int(output[ss,6].sum()),
            mean_risk_pct=float((100*risk[ss]/entry[ss]).mean())))
    return rows


def context(config):
    cfg = json.loads(config.read_text())
    original = json.loads((ROOT/cfg["source_config"]).read_text())
    cache = config.parent/".cache"
    source_cache = (ROOT/cfg["source_config"]).parent/".cache"
    if not cache.exists() and source_cache.exists():
        shutil.copytree(source_cache,cache)
    ctx,base,hit = prepared(original,cache)
    assert np.isnan(ctx["atr"][ctx["frame"].index.minute%15!=0]).all()
    return cfg,original,ctx,base,hit


def screen(config):
    cfg,original,ctx,original_base,hit = context(config)
    work=config.parent
    if (work/"screen_receipt.json").exists():
        raise RuntimeError("Completed screen exists; reuse its results")
    if subprocess.run(["git","diff","--quiet","HEAD","--",*CODE,str(config)],cwd=ROOT).returncode:
        raise RuntimeError("Commit the builder and frozen search plan before execution")
    start=time.perf_counter()
    lib=native()
    policies=all_arms(cfg)
    summary=[]
    baseline_parity=None
    stop_groups=itertools.groupby(policies,key=lambda p:(p["window"],p["buffer"]))
    for (window,buffer),group in stop_groups:
        base,_=stop_events(ctx,original_base,window,buffer,original["tick"])
        base["trade_id"]=base.population+"-"+base.pair_id.astype(str)
        assert len(base)==len(original_base)
        events=base[["entry_i","side","entry","stop","end_i"]].to_numpy()
        for p in group:
            result=replay_search(ctx,events,tick=original["tick"],lib=lib,**replay_args(p))
            lower=(replay_search(ctx,events,tick=original["tick"],lib=lib,
                    stop_first=True,**replay_args(p)) if p["family"]=="fixed" else result)
            summary.extend(compact_summary(base,result,lower,p,cfg["round_trip_cost"]))
            if (window,buffer,p["arm_r"],p["atr_multiple"],p["be_r"],
                    p["reverse_exit"],p["target_r"])==(5,.05,5,4,None,True,None):
                golden=work/"baseline_screen.csv"
                rows=decorate(base,result,cfg,ctx,original["tick"])
                rows.to_csv(golden,index=False)
                baseline_parity=compare_golden(
                    pd.read_csv(ROOT/original["output"]/"baseline/trades.csv",
                                float_precision="round_trip"),golden,original["tick"])
                baseline_name=p["name"]
        print(json.dumps(dict(stop_window=window,buffer=buffer,
            screened=len(summary)//6,elapsed_seconds=round(time.perf_counter()-start,2))),flush=True)
    assert baseline_parity
    table=pd.DataFrame(summary)
    table.to_csv(work/"summary.csv",index=False)
    selected=dict(development_2024=choose(table,0),
                  rolling_through_2025=choose(table,-3),
                  descriptive_full=choose(table,-1),
                  descriptive_fixed=choose(table,-1,"fixed"),
                  descriptive_trail=choose(table,-1,"trail"),
                  baseline=baseline_name)
    dump(work/"selection.json",selected)
    chosen=set(selected.values())
    records={}
    for name in sorted(chosen):
        p=next(p for p in policies if p["name"]==name)
        base,_=stop_events(ctx,original_base,p["window"],p["buffer"],original["tick"])
        base["trade_id"]=base.population+"-"+base.pair_id.astype(str)
        output=replay_search(ctx,base[["entry_i","side","entry","stop","end_i"]].to_numpy(),
                            tick=original["tick"],lib=lib,**replay_args(p))
        folder=work/"selected"/name
        folder.mkdir(parents=True)
        ledger=folder/"screen.csv"
        rows=decorate(base,output,cfg,ctx,original["tick"])
        rows.to_csv(ledger,index=False)
        native_cfg=dict(original,experiment_id=cfg["experiment_id"],
            stop_window_5m=p["window"],buffer_percent=p["buffer"],
            policies=[dict(name="candidate",trail_arm_r=p["arm_r"],
                trail_atr_multiple=p["atr_multiple"],breakeven_r=p["be_r"],
                opposite_hour_exit=p["reverse_exit"],fixed_target_r=p["target_r"],
                golden_ledger=str(ledger.relative_to(ROOT)))],
            output=str((folder/"native").relative_to(ROOT)))
        dump(folder/"config.json",native_cfg)
        records[name]=p
    dump(work/"selected_policies.json",records)
    dump(work/"screen_receipt.json",dict(
        source_commit=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
        feature_cache_hit=hit,arms=len(policies),paths=len(policies)*len(original_base),
        baseline_parity=baseline_parity,selection=selected,
        inputs=[file_stamp(ROOT/original[k]) for k in ("data","entry_candidates")],
        elapsed_seconds=time.perf_counter()-start,
        phase="screening;selected policies require native verification",
        selection_score="Total net R; fixed targets use conservative stop-first collision sensitivity",
        historical_previously_seen=True,training_eligible=False,production_eligible=False))


def native_verify(config):
    work=config.parent
    selection=json.loads((work/"selection.json").read_text())
    names=sorted(set(selection.values())-{selection["baseline"]})
    def one(name):
        folder=work/"selected"/name
        if not (folder/".cache").exists():
            shutil.copytree(work/".cache",folder/".cache")
        with (folder/"native.log").open("w") as log:
            subprocess.run([sys.executable,"-m","yoyo.evaluation.range_framework",
                "--config",str(folder/"config.json")],cwd=ROOT,stdout=log,
                stderr=subprocess.STDOUT,check=True)
        receipt=json.loads((folder/"native/receipt.json").read_text())
        audit=receipt["results"][0]["audit"]
        assert audit["parity"]["status"]=="passed"
        print(json.dumps(dict(native_completed=name,rows=audit["positions"])),flush=True)
        return name,dict(elapsed_seconds=receipt["elapsed_seconds"],audit=audit)
    start=time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=dict(pool.map(one,names))
    dump(work/"native_validation.json",dict(results=results,
        elapsed_seconds=time.perf_counter()-start,baseline="Reused original cached native ledger"))


def analyze(config):
    cfg=json.loads(config.read_text())
    work=config.parent
    selection=json.loads((work/"selection.json").read_text())
    policies=json.loads((work/"selected_policies.json").read_text())
    validation=json.loads((work/"native_validation.json").read_text())
    table=pd.read_csv(work/"summary.csv")
    original=json.loads((ROOT/cfg["source_config"]).read_text())
    base=pd.read_csv(ROOT/original["output"]/"baseline/trades.csv",float_precision="round_trip")
    results=[]
    for name,p in policies.items():
        if name==selection["baseline"]:
            rows=base
            fills=pd.read_csv(ROOT/original["output"]/"baseline/fills.csv",float_precision="round_trip")
        else:
            folder=work/"selected"/name/"native/candidate"
            rows=pd.read_csv(folder/"trades.csv",float_precision="round_trip")
            fills=pd.read_csv(folder/"fills.csv",float_precision="round_trip")
            assert name in validation["results"]
            prior=pd.read_csv(work/"selected"/name/"screen.csv",float_precision="round_trip")
            assert rows.set_index("trade_id").ambiguous.sort_index().equals(
                prior.set_index("trade_id").ambiguous.astype(int).sort_index())
        cash=cash_events(rows,fills)
        for seg in (-1,0,1,2,-2,-3):
            subset=rows if seg==-1 else rows[rows.segment>0] if seg==-2 else (
                rows[rows.segment<2] if seg==-3 else rows[rows.segment==seg])
            signal=subset[subset.population=="signal"]
            random=subset[subset.population=="random"]
            paired=pairs(signal,random)
            reference=base[base.trade_id.isin(signal.trade_id)]
            gain=signal[["trade_id","pair_id","week","net_r"]].merge(
                reference[["trade_id","net_r"]],on="trade_id",suffixes=("","_random"))
            c=cash[cash.trade_id.isin(signal.trade_id)]
            cash_dd=drawdown(c.groupby("ts_ns").cash_r.sum().sort_index().to_numpy())
            winner_ids=set(reference.loc[reference.gross_r>=20-1e-7,"trade_id"])
            winners=signal[signal.trade_id.isin(winner_ids)]
            result=dict(name=name,segment=seg,paired_n=len(paired),
                signal_vs_random=inference(paired,cfg["seed"]),
                gain_vs_baseline=inference(gain,cfg["seed"]),
                realized_cash_r_drawdown=cash_dd,
                original_20r_n=len(winner_ids),still_20r_n=int((winners.gross_r>=20-1e-7).sum()),
                original_20r_profit=float(reference.loc[reference.trade_id.isin(winner_ids),"gross_r"].sum()),
                new_original_20r_profit=float(winners.gross_r.sum()))
            if seg==-1:
                result["signal_vs_random"]["bonferroni_search_p"]=min(
                    1.,result["signal_vs_random"]["p_one_sided"]*len(table[table.segment==-1]))
            results.append(result)
    dump(work/"comparisons.json",results)
    full=table[table.segment==-1].sort_values("selection_net_r",ascending=False)
    full.head(20).to_csv(work/"top20.csv",index=False)
    # OAT slices attribute each parameter without claiming interactions are main effects.
    baseline=policies[selection["baseline"]]
    axes=["window","buffer","family","arm_r","atr_multiple","be_r","reverse_exit","target_r"]
    def changes(row):
        return sum(not ((pd.isna(row[k]) and baseline[k] is None) or row[k]==baseline[k])
                   for k in axes)
    full.loc[full.apply(changes,axis=1)<=1].to_csv(work/"single_factor.csv",index=False)
    neighbors={}
    for label,name in selection.items():
        target=full[full.name==name].iloc[0]
        group=full[full.family==target.family]
        near=group[group.apply(lambda r:sum(
            not ((pd.isna(r[k]) and pd.isna(target[k])) or r[k]==target[k])
            for k in axes)<=1,axis=1)]
        neighbors[label]=dict(n=len(near),minimum_net_r=float(near.selection_net_r.min()),
            median_net_r=float(near.selection_net_r.median()),maximum_net_r=float(near.selection_net_r.max()),
            positive_n=int((near.selection_net_r>0).sum()))
    dump(work/"neighborhoods.json",neighbors)
    dump(work/"analysis_receipt.json",dict(
        arms=len(full),positive_full_arms=int((full.selection_net_r>0).sum()),
        positive_all_three_years=int(table[table.segment.isin([0,1,2])].groupby("name")
            .selection_net_r.apply(lambda x:(x>0).all()).sum()),
        selected=selection,comparisons=len(results),not_account_returns=True,
        no_pine_or_default_change=True,training_eligible=False,production_eligible=False))
    print(json.dumps(dict(arms=len(full),positive=int((full.selection_net_r>0).sum()),
                          top=full.head(3).to_dict("records")),default=str),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",type=Path,default=DEFAULT)
    parser.add_argument("--phase",choices=["screen","native","analyze"],required=True)
    args=parser.parse_args()
    {"screen":screen,"native":native_verify,"analyze":analyze}[args.phase](args.config.resolve())


if __name__=="__main__":
    main()
