"""Owner-approved fixed, staged, key-level and breakeven exits on V2.30.

Reuse frozen BTC131/ETH99 entries, controls, candles and the Nautilus adapter.
Each half is a separately protected native HEDGING position, then normalized
back to its ORIGINAL parent's risk and notional. No aggregate win-rate from
counting half positions. Key snapshots use completed Monday OHLC at entry;
no later week or favorable-price outcome can move an existing target.
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
from yoyo.evaluation.monday_range_v2_2_reference import atr
from yoyo.evaluation.pepe_range_exits import aggregate, inference, pairs
from yoyo.evaluation.pepe_range_breakeven import native, replay_search
from yoyo.evaluation.range_framework import ROOT, dump, file_stamp
from yoyo.evaluation.range_management_study import holm
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native

DEFAULT = ROOT/"experiments/active/exp-range-v230-exits-20261021-v1/config.json"
CODE = ["yoyo/evaluation/range_v230_exit_compare.py",
        "yoyo/layers/l3_backtest/nautilus_independent.py",
        "yoyo/evaluation/pepe_range_breakeven.py", "yoyo/evaluation/pepe_range_be_native.cpp",
        "yoyo/evaluation/btc_range_wick_fanshen.py",
        "yoyo/evaluation/monday_range_v2_2_reference.py",
        "yoyo/evaluation/pepe_range_exits.py", "yoyo/evaluation/range_management_study.py",
        "yoyo/evaluation/range_framework.py"]


def rounded(price, tick, upward):
    return (math.ceil(price/tick-1e-8) if upward else math.floor(price/tick+1e-8))*tick


def entry_keys(frame, base, ratios, timezone, bar_minutes=15):
    """Freeze levels known at each entry CLOSE, including final Monday bar.

    monday_levels exposes the range before a candle opens. The next candle's
    pre-open state therefore contains exactly entry-close knowledge. A dummy
    final candle lets prefix inputs expose that state without any future OHLC.
    """
    q = aggregate(frame, bar_minutes)
    dummy = q.iloc[-1:].copy()
    dummy.index += pd.Timedelta(minutes=bar_minutes)
    prices, _, _, _ = monday_levels(pd.concat([q,dummy]),ratios,timezone,bar_minutes=bar_minutes)
    indices = q.index.get_indexer(frame.index[base.entry_i.to_numpy(int)])
    if (indices < 0).any():
        raise ValueError("Entries must be completed signal-timeframe candles")
    return {r.trade_id: prices[i+1] for r,i in zip(base.itertuples(),indices)}


def make_legs(base, arm, keys, tick):
    """Build causal static brackets; all fractions refer to original shares."""
    result = []
    split = arm.get("split_r") is not None or arm.get("key_tp",False) or arm.get("stop_mode") is not None
    for source in base.to_dict("records"):
        entry, side, initial = source["entry"], source["side"], source["stop"]
        original_risk = source["risk"]
        levels = sorted({rounded(float(p),tick,side==1) for p in keys[source["trade_id"]]
                         if np.isfinite(p) and side*(p-entry) > 0},reverse=side==-1)
        levels = [p for p in levels if side*(p-entry) >= tick-tick*1e-7 and p > 0]
        adverse = sorted({rounded(float(p),tick,side==-1) for p in keys[source["trade_id"]]
                          if np.isfinite(p) and 0 < side*(entry-p) < original_risk},
                         reverse=side==1)
        adverse = [p for p in adverse if tick-tick*1e-7 <= side*(entry-p) < original_risk-tick*1e-7]
        for leg in range(2 if split else 1):
            stop = initial
            if leg == 0 and arm.get("stop_mode") == "half_r":
                stop = rounded(entry-side*.5*original_risk,tick,side==-1)
            if leg == 0 and arm.get("stop_mode") == "key" and adverse:
                stop = adverse[0]
            if arm.get("key_tp"):
                target = levels[leg] if len(levels)>leg else float("nan")
            else:
                multiple = arm["split_r"][leg] if arm.get("split_r") else arm["target_r"]
                target = rounded(entry+side*multiple*original_risk,tick,side==1)
                if target <= 0:
                    target = float("nan")
            result.append(dict(source, trade_id=source["trade_id"]+f"_leg{leg}",
                parent_id=source["trade_id"],parent_risk=original_risk,
                parent_stop=initial,weight=.5 if split else 1.,
                stop=stop,risk=side*(entry-stop),target_price=target,
                missing_key_target=bool(arm.get("key_tp") and not np.isfinite(target)),
                key_stop_available=bool(adverse),leg=leg))
    return pd.DataFrame(result)


def aggregate_legs(rows, base, cost):
    """Normalize fill outcomes to original risk, never the tighter leg risk."""
    output = []
    metadata = base.set_index("trade_id")
    for parent, legs in rows.groupby("parent_id",sort=False):
        original = metadata.loc[parent]
        if not math.isclose(legs.weight.sum(),1.,abs_tol=1e-10):
            raise AssertionError("Tranche fractions do not conserve original shares")
        gross = float((legs.weight*legs.side*(legs.exit_price-legs.entry)/original.risk).sum())
        fees = float((legs.weight*legs.framework_commission/legs.quantity/original.risk).sum())
        if not math.isclose(fees,cost*original.entry/original.risk,rel_tol=0,abs_tol=1e-8):
            raise AssertionError("Native partial fees do not conserve original notional")
        boundary = legs.reason==0
        boundary_r = float((legs.loc[boundary,"weight"]*(legs.loc[boundary,"side"]*
            (legs.loc[boundary,"exit_price"]-original.entry)-cost*original.entry)/original.risk).sum())
        values = dict(original)
        values.update(trade_id=parent,gross_r=gross,fee_r=fees,net_r=gross-fees,
            net_notional_bp=(gross*original.risk/original.entry-cost)*10000,
            exit_i=int(legs.exit_i.max()),boundary_weight=float(legs.loc[boundary,"weight"].sum()),
            boundary_net_r=boundary_r,ambiguous_legs=int(legs.ambiguous.sum()),
            be_effective=int((legs.be_effective_i>=0).any()),
            be_stop_weight=float(legs.loc[legs.reason==5,"weight"].sum()),
            missing_target_weight=float(legs.loc[legs.missing_key_target,"weight"].sum()))
        output.append(values)
    return pd.DataFrame(output)


def oracle(ctx, legs, arm, tick, cost, library, actual):
    """Reuse existing C++ oracle for native parity and stop-first sensitivity."""
    normal, lower = [], []
    actual = actual.set_index("trade_id")
    for row in legs.itertuples():
        target_r = row.side*(row.target_price-row.entry)/row.risk if np.isfinite(row.target_price) else None
        args=dict(cost=cost if arm.get("be_mode","cost")=="cost" else 0.,tick=tick,
                  be_r=arm.get("be_r"),target_r=target_r,arm_r=1e100,
                  reverse_exit=False,lib=library)
        event=[[row.entry_i,row.side,row.entry,row.stop,row.end_i]]
        a=replay_search(ctx,event,**args)[0]
        b=replay_search(ctx,event,stop_first=True,**args)[0]
        r=actual.loc[row.trade_id]
        np.testing.assert_array_equal([r.exit_i,r.reason],[a[0],a[2]],err_msg=row.trade_id)
        np.testing.assert_allclose(r.exit_price,a[1],rtol=0,atol=tick*1e-6,err_msg=row.trade_id)
        normal.append(dict(trade_id=row.trade_id,exit_i=int(a[0]),exit_price=float(a[1]),reason=int(a[2])))
        lower.append(dict(trade_id=row.trade_id,exit_i=int(b[0]),exit_price=float(b[1]),reason=int(b[2])))
    conservative=actual.reset_index()
    override=pd.DataFrame(lower).set_index("trade_id").loc[conservative.trade_id]
    for col in ("exit_i","exit_price","reason"):
        conservative[col]=override[col].to_numpy()
    return conservative,dict(checked=len(legs),native_price_time_parity=True,
        conservative_changed=sum(a!=b for a,b in zip(normal,lower)))


def summaries(rows,lower,symbol,name,seed):
    result=[]
    for group, mask in [("all",np.ones(len(rows),bool)),("long",rows.side==1),
        ("short",rows.side==-1),("first_half",rows.segment==0),("second_half",rows.segment==1)]:
        part=rows[mask];s=part[part.population=="signal"];r=part[part.population=="random"]
        ss=lower.set_index("trade_id").loc[s.trade_id]
        rr=lower.set_index("trade_id").loc[r.trade_id]
        loss=-s.loc[s.net_r<0,"net_r"].sum()
        result.append(dict(symbol=symbol,arm=name,group=group,n=len(s),
            wins=int((s.net_r>1e-9).sum()),win_rate=float((s.net_r>1e-9).mean()),
            gross_r=float(s.gross_r.sum()),fee_r=float(s.fee_r.sum()),net_r=float(s.net_r.sum()),
            pf=float(s.loc[s.net_r>0,"net_r"].sum()/loss) if loss else None,
            mean_net_bp=float(s.net_notional_bp.mean()),random_net_r=float(r.net_r.sum()),
            conservative_net_r=float(ss.net_r.sum()),random_conservative_net_r=float(rr.net_r.sum()),
            boundary_n=int((s.boundary_weight>0).sum()),boundary_net_r=float(s.boundary_net_r.sum()),
            natural_net_r=float((s.net_r-s.boundary_net_r).sum()),
            missing_target_n=int((s.missing_target_weight>0).sum()),
            ambiguous_legs=int(s.ambiguous_legs.sum()),be_effective_n=int(s.be_effective.sum()),
            **inference(pairs(s,r),seed)))
    return result


def run_symbol(cfg,spec,dest):
    source=ROOT/cfg["source"]/spec["symbol"]
    native_cfg=json.loads((source/"native.json").read_text())
    frame=pd.read_csv(ROOT/native_cfg["data"],float_precision="round_trip")
    frame.index=pd.DatetimeIndex(pd.to_datetime(frame.pop("ts"),unit="ms",utc=True)+pd.Timedelta(minutes=5)).as_unit("ns")
    frame=frame[["open","high","low","close"]]
    base=pd.read_csv(ROOT/native_cfg["entry_candidates"],float_precision="round_trip")
    base["trade_id"]=base.population+"-"+base.pair_id.astype(str)
    base["risk"]=base.side*(base.entry-base.stop)
    keys=entry_keys(frame,base,cfg["ratios"],cfg["timezone"],cfg.get("key_minutes",15))
    ctx=dict(bars=np.ascontiguousarray(frame.to_numpy(float)),
             atr=np.ascontiguousarray(atr(frame,14).to_numpy()),hour=np.zeros(len(frame)))
    library=native()
    identity=dict(inputs=[file_stamp(ROOT/native_cfg[k]) for k in ("data","entry_candidates")],
                  code=[file_stamp(ROOT/p) for p in CODE],config=cfg)
    results=[]
    for arm in cfg["arms"]:
        folder=dest/spec["symbol"]/arm["name"]
        receipt=folder/"receipt.json"
        if receipt.exists():
            saved=json.loads(receipt.read_text())
            if saved["identity"]!=identity:
                raise RuntimeError("Run identity changed; preserve previous results")
            results.extend(saved["summary"])
            continue
        if folder.exists():
            raise RuntimeError(f"Incomplete run exists: {folder}")
        legs=make_legs(base,arm,keys,spec["tick"])
        policy=ExitPolicy(target_from_entry=True,opposite_hour_exit=False,
            breakeven_r=arm.get("be_r"),breakeven_includes_cost=arm.get("be_mode","cost")=="cost",
            round_trip_cost=cfg["round_trip_cost"])
        rows,fills,audit=run_native(frame,ctx["atr"],ctx["hour"],legs,policy,
                                   tick=spec["tick"],symbol=spec["symbol"])
        conservative,check=oracle(ctx,legs,arm,spec["tick"],cfg["round_trip_cost"],library,rows)
        parents=aggregate_legs(rows,base,cfg["round_trip_cost"])
        lower=aggregate_legs(conservative,base,cfg["round_trip_cost"])
        if len(parents[parents.population=="signal"])!=spec["expected_signals"]:
            raise AssertionError("Parent admission set changed")
        summary=summaries(parents,lower,spec["symbol"],arm["name"],cfg["seed"])
        folder.mkdir(parents=True)
        legs.to_csv(folder/"legs.csv",index=False)
        rows.to_csv(folder/"native_trades.csv",index=False)
        fills.to_csv(folder/"fills.csv",index=False)
        parents.to_csv(folder/"trades.csv",index=False)
        lower.to_csv(folder/"stop_first.csv",index=False)
        dump(receipt,dict(identity=identity,audit=audit,oracle=check,summary=summary,
             source_commit=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()))
        results.extend(summary)
        print(json.dumps({k:summary[0][k] for k in ("symbol","arm","n","net_r","conservative_net_r","random_net_r","boundary_n")}),flush=True)
    return results


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",type=Path,default=DEFAULT)
    args=parser.parse_args()
    if subprocess.run(["git","diff","--quiet","HEAD","--",*CODE,str(args.config)],cwd=ROOT).returncode:
        raise RuntimeError("Commit approved configuration and builder first")
    cfg=json.loads(args.config.read_text())
    results=[]
    for spec in cfg["symbols"]:
        results.extend(run_symbol(cfg,spec,args.config.parent))
    for symbol in (s["symbol"] for s in cfg["symbols"]):
        family=[r for r in results if r["symbol"]==symbol and r["group"]=="all"]
        for row,pvalue in zip(family,holm([r["p_one_sided"] for r in family])):
            row["holm_p_over_arms"]=float(pvalue)
    dump(args.config.parent/"summary.json",results)


if __name__ == "__main__":
    main()
