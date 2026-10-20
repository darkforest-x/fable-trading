"""Owner's timed-BE / ChartPrime strong-diamond exit experiment.

Source: Notion3ff8856479af8017a948f6445144c9c6, owner clarifications2026-10-20.
Features use only completed3m/5m closes: shared WilderRSI14/SAR.02/.02/.2.
Strong events test SAR30/70. Original5m entry and structural risk are frozen.
All six arms use the SAME original-source1m data and sparse15mATR/H1 events;
the3m indicator is never reconstructed from5m OHLC. Outcome overlays read
future events only within the frozen runner exit and are audit data, never
inputs to native admission. No new execution engine or dependency is used.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

import numpy as np
import pandas as pd

from yoyo.evaluation.parabolic_rsi_sar import pine_sar,diamonds
from yoyo.evaluation.spike_v6_bb_squeeze import _rsi_wilder
from yoyo.evaluation.pepe_range_breakeven import native,replay_timed,replay_search
from yoyo.evaluation.range_framework import ROOT,dump,file_stamp,prepared,compare_golden
from yoyo.evaluation.range_parameter_search import decorate
from yoyo.evaluation.range_partial_study import cash_events,drawdown
from yoyo.evaluation.range_management_study import summary,subset,holm
from yoyo.evaluation.pepe_range_exits import pairs,inference
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy,run_native

DEFAULT=ROOT/"experiments/active/exp-eth-timebe-rsi-20261020-v1/config.json"
CODE=["yoyo/evaluation/range_rsi_scaleout.py",
      "yoyo/evaluation/pepe_range_be_native.cpp",
      "yoyo/evaluation/pepe_range_breakeven.py",
      "yoyo/layers/l3_backtest/nautilus_independent.py"]
OHLC=["open","high","low","close"]


def aggregate(frame,minutes):
    """Input UTC minute CLOSE timestamps; retain only fully completed buckets."""
    grouped=frame.resample(f"{minutes}min",closed="right",label="right",origin="epoch")
    out=grouped.agg(dict(open="first",high="max",low="min",close="last"))
    return out[grouped.close.count().eq(minutes)]


def strong_features(frame,minutes):
    """Current and prior completed closes only; no ordinary diamonds counted."""
    grouped=aggregate(frame,minutes)
    rsi=_rsi_wilder(grouped.close,14).to_numpy(float)
    sar,below=pine_sar(rsi)
    events=diamonds(sar,below)
    sides=np.where(events["strong_up"],1,np.where(events["strong_dn"],-1,0))
    return pd.DataFrame(dict(rsi=rsi,sar=sar,strong_side=sides),index=grouped.index)


def context(config):
    cfg=json.loads(config.read_text())
    old=json.loads((ROOT/cfg["source_config"]).read_text())
    folder=config.parent
    cache=ROOT/cfg["feature_cache"] if cfg.get("feature_cache") else folder/".cache"
    cache.mkdir(exist_ok=True)
    identity=dict(schema=1,inputs=[file_stamp(ROOT/p) for p in cfg["minute_sources"]],
        original_inputs=[file_stamp(ROOT/old[k]) for k in ("data","entry_candidates")],
        indicator_code=[file_stamp(ROOT/p) for p in (
            "yoyo/evaluation/parabolic_rsi_sar.py","yoyo/evaluation/spike_v6_bb_squeeze.py")])
    meta=cache/"features.json"
    if meta.exists():
        prior=json.loads(meta.read_text())
        if prior["identity"]!=identity:
            raise RuntimeError("Cached feature inputs changed; use a new experiment")
        with np.load(cache/"features.npz",allow_pickle=False) as z:
            ctx={k:z[k] for k in ("bars","atr","hour","rsi3","rsi5","be_clock")}
            ctx["frame"]=pd.DataFrame(ctx["bars"],columns=OHLC,
                index=pd.to_datetime(z["times"],utc=True))
        base=pd.read_csv(cache/"entries.csv",float_precision="round_trip")
        return cfg,old,ctx,base,prior
    oldcache=cache/"original"
    if not oldcache.exists():
        shutil.copytree((ROOT/cfg["source_config"]).parent/".cache",oldcache)
    orig,base,hit=prepared(old,oldcache)
    start=orig["frame"].index[0]-pd.Timedelta(minutes=5)
    end=orig["frame"].index[-1]
    lo,hi=int(start.timestamp()*1000),int(end.timestamp()*1000)
    chunks=[]
    for path in cfg["minute_sources"]:
        for chunk in pd.read_csv(ROOT/path,usecols=["ts",*OHLC],
                                 chunksize=250000,float_precision="round_trip"):
            part=chunk[chunk.ts.ge(lo)&chunk.ts.lt(hi)]
            if len(part):
                chunks.append(part)
    data=pd.concat(chunks,ignore_index=True).sort_values("ts")
    duplicates=data[data.ts.duplicated(keep=False)]
    if len(duplicates):
        assert not duplicates.groupby("ts")[OHLC].nunique().gt(1).any().any()
    data=data.drop_duplicates("ts")
    index=pd.to_datetime(data.pop("ts"),unit="ms",utc=True)+pd.Timedelta(minutes=1)
    frame=data[OHLC].set_axis(pd.DatetimeIndex(index).as_unit("ns"))
    assert frame.index[0]==start+pd.Timedelta(minutes=1) and frame.index[-1]==end
    assert (frame.index.to_series().diff().dropna()==pd.Timedelta(minutes=1)).all()
    five=aggregate(frame,5)
    pd.testing.assert_index_equal(five.index,orig["frame"].index,check_names=False)
    np.testing.assert_allclose(five.to_numpy(),orig["bars"],rtol=0,atol=1e-8)
    ctx=dict(frame=frame,bars=np.ascontiguousarray(frame.to_numpy(float)),
        atr=np.full(len(frame),np.nan),hour=np.zeros(len(frame)),
        be_clock=np.ascontiguousarray(frame.index.minute%5==0,dtype=float))
    positions=frame.index.get_indexer(orig["frame"].index)
    assert (positions>=0).all()
    ctx["atr"][positions]=orig["atr"]
    ctx["hour"][positions]=orig["hour"]
    feature_counts={}
    for minutes in (3,5):
        feature=strong_features(frame,minutes)
        aligned=np.zeros(len(frame))
        aligned[frame.index.get_indexer(feature.index)]=feature.strong_side.to_numpy()
        ctx[f"rsi{minutes}"]=aligned
        feature_counts[str(minutes)]=dict(strong_up=int((aligned==1).sum()),
                                         strong_down=int((aligned==-1).sum()))
    base["entry_i_5m"],base["end_i_5m"]=base.entry_i,base.end_i
    base["entry_i"]=positions[base.entry_i.to_numpy(int)]
    base["end_i"]=positions[base.end_i.to_numpy(int)]
    np.testing.assert_allclose(ctx["bars"][base.entry_i,3],base.entry,rtol=0,atol=1e-8)
    base.to_csv(cache/"entries.csv",index=False)
    np.savez_compressed(cache/"features.npz",times=frame.index.asi8,
                        **{k:v for k,v in ctx.items() if k!="frame"})
    info=dict(identity=identity,minute_rows=len(frame),original_5m_rows=len(five),
        original_ohlc_parity=True,original_feature_cache_hit=hit,entries=len(base),
        first_close=str(frame.index[0]),last_close=str(end),rsi_counts=feature_counts,
        source_commit=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip())
    dump(meta,info)
    return cfg,old,ctx,base,info


def scaleout_overlay(runner,ctx,minutes,cost=.002,tick=.01,skip_events=0):
    """Size-only audit overlay; stops/management remain independent of quantity.

    Native terminal exits have precedence at their execution-bar close. The
    firstfive qualifying later opposite events reduce ORIGINAL fifths. None
    of the runner's future state is exposed as actual state after a fifth exit.
    """
    events={side:np.flatnonzero(ctx[f"rsi{minutes}"]==-side) for side in (1,-1)}
    out=[]
    for r in runner.itertuples():
        available=events[r.side]
        a,b=np.searchsorted(available,[r.entry_i,r.exit_i],side="left")
        available=available[a:b]
        available=available[available>r.entry_i]
        # Count confirmed opposite events before the profit gate. Even a
        # losing first event is the first diamond, not the first reduction.
        available=available[skip_events:]
        pnl=r.side*(ctx["bars"][available,3]-r.entry)
        hits=available[pnl-cost*r.entry>tick*1e-7][:5]
        count=len(hits)
        final_i=int(hits[-1]) if count==5 else int(r.exit_i)
        final_price=ctx["bars"][final_i,3] if count==5 else r.exit_price
        gross=(.2*r.side*(ctx["bars"][hits,3]-r.entry).sum()/r.risk
               +(1-count/5)*r.gross_r)
        fee=cost*r.entry/r.risk
        row={k:getattr(r,k) for k in (
            "trade_id","pair_id","population","segment","week","bucket","entry_i",
            "end_i","side","entry","stop","risk","entry_time")}
        row.update(exit_i=final_i,exit_price=final_price,reason=6 if count==5 else int(r.reason),
            gross_r=gross,fee_r=fee,net_r=gross-fee,
            net_notional_bp=(gross*r.risk/r.entry-cost)*10000,
            rsi_partial_count=count,rsi_first_i=int(hits[0]) if count else -1,
            rsi_realized_weight=count/5,ambiguous=0,
            exit_time=str(ctx["frame"].index[final_i]) if count==5 else r.exit_time)
        out.append(row)
    result=pd.DataFrame(out)
    result["net_r"]=result.net_r.mask(result.net_r.abs()<1e-9,0.)
    return result


def screen(config):
    if (config.parent/"screen_receipt.json").exists():
        raise RuntimeError("Completed screen exists; reuse it")
    if subprocess.run(["git","diff","--quiet","HEAD","--",*CODE,str(config)],cwd=ROOT).returncode:
        raise RuntimeError("Commit code and config before building")
    started=time.perf_counter()
    cfg,old,ctx,base,info=context(config)
    lib=native()
    orig,original_entries,_=prepared(old,config.parent/".cache"/"original")
    original_output=replay_search(orig,original_entries[
        ["entry_i","side","entry","stop","end_i"]].to_numpy(),lib=lib)
    original_screen=decorate(original_entries,original_output,cfg,orig,.01)
    original_screen.to_csv(config.parent/"baseline_5m_probe.csv",index=False)
    prior=pd.read_csv(ROOT/old["output"]/"baseline/trades.csv",float_precision="round_trip")
    original_parity=compare_golden(prior,config.parent/"baseline_5m_probe.csv",.01)
    events=base[["entry_i","side","entry","stop","end_i"]].to_numpy()
    runners={}
    for key,delay in (("baseline",0),("time25",25)):
        outputs=(replay_search(ctx,events,lib=lib) if not delay else
                 replay_timed(ctx,events,delay=delay,lib=lib))
        rows=decorate(base,outputs,cfg,ctx,.01)
        ii=rows.exit_i.to_numpy(int)
        gap=rows.reason.isin([1,2,5]).to_numpy()&(
            rows.side.to_numpy()*(ctx["bars"][ii,0]-rows.protection.to_numpy())<-.01e-7)
        rows["exit_time"]=ctx["frame"].index[ii]-pd.to_timedelta(gap.astype(int),unit="min")
        runners[key]=rows
    for arm in cfg["arms"]:
        folder=config.parent/"runs"/arm["name"]
        folder.mkdir(parents=True,exist_ok=True)
        runner=runners["time25" if arm["time_be"] else "baseline"]
        rows=scaleout_overlay(runner,ctx,arm["rsi_minutes"]) if arm["rsi_minutes"] else runner
        rows.to_csv(folder/"screen.csv",index=False)
    # Old execution resolution is a separate reference, never the primary comparator.
    merged=runners["baseline"][["trade_id","net_r","exit_price"]].merge(
        prior[["trade_id","net_r","exit_price"]],on="trade_id",suffixes=("_1m","_5m"))
    signal=merged[merged.trade_id.str.startswith("signal-")]
    dump(config.parent/"screen_receipt.json",dict(
        context=info,elapsed_seconds=time.perf_counter()-started,
        arms=len(cfg["arms"]),paths=len(base)*len(cfg["arms"]),
        cached_native_5m_baseline_parity=original_parity,
        old_execution_comparison=dict(
            signal_net_r_5m=float(signal.net_r_5m.sum()),signal_net_r_1m=float(signal.net_r_1m.sum()),
            different_exit_prices=int((abs(merged.exit_price_1m-merged.exit_price_5m)>1e-8).sum())),
        source_commit=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()))
    print(json.dumps(dict(screened=len(cfg["arms"]),elapsed=time.perf_counter()-started)),flush=True)


def verify_overlay(actual,expected,tick=.01):
    a=actual.set_index("trade_id").sort_index()
    e=expected.set_index("trade_id").sort_index()
    assert a.index.equals(e.index)
    exact=["entry_i","side","end_i","exit_i","reason","rsi_partial_count","rsi_first_i"]
    numeric=["entry","stop","risk","exit_price","gross_r","fee_r","net_r","net_notional_bp"]
    for col in exact:
        np.testing.assert_array_equal(a[col],e[col],err_msg=col)
    for col in numeric:
        np.testing.assert_allclose(a[col],e[col],rtol=0,atol=1e-7,err_msg=col)
    np.testing.assert_array_equal(pd.to_datetime(a.exit_time,utc=True),
                                  pd.to_datetime(e.exit_time,utc=True))
    return dict(status="passed",rows=len(a),compared_fields=exact+numeric+["exit_time"])


def native_arm(config,name):
    cfg,old,ctx,base,info=context(config)
    arm=next(p for p in cfg["arms"] if p["name"]==name)
    folder=config.parent/"runs"/name
    receipt=folder/"receipt.json"
    if receipt.exists():
        prior=json.loads(receipt.read_text())
        assert prior["arm"]==arm and prior["inputs"]==info["identity"]
        print(json.dumps(dict(cached=name)),flush=True)
        return
    started=time.perf_counter()
    rsi=ctx[f"rsi{arm['rsi_minutes']}"] if arm["rsi_minutes"] else None
    rows,fills,audit=run_native(ctx["frame"],ctx["atr"],ctx["hour"],base,
        ExitPolicy(time_breakeven_minutes=25 if arm["time_be"] else None,
                   rsi_scaleout=bool(arm["rsi_minutes"]),
                   rsi_skip_events=arm.get("rsi_skip_events",0)),.01,"ETH",.01,
        execution_minutes=1,rsi_side=rsi)
    # Preserve failed native paths as evidence too; do not discard them on mismatch.
    rows.to_csv(folder/"trades.csv",index=False)
    fills.to_csv(folder/"fills.csv",index=False)
    audit["parity"]=(verify_overlay(rows,pd.read_csv(folder/"screen.csv",float_precision="round_trip"))
        if rsi is not None else compare_golden(rows,folder/"screen.csv",.01))
    cash=cash_events(rows,fills)
    np.testing.assert_allclose(cash.groupby("trade_id").cash_r.sum().sort_index(),
        rows.set_index("trade_id").net_r.sort_index(),rtol=0,atol=1e-7)
    np.testing.assert_allclose(rows.framework_commission,rows.quantity*rows.entry*.002,
                               rtol=0,atol=1e-7)
    audit["cash_and_fee_rows"]=len(rows)
    dump(receipt,dict(arm=arm,audit=audit,inputs=info["identity"],
        source_commit=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
        elapsed_seconds=time.perf_counter()-started))
    print(json.dumps(dict(completed=name,net_r=float(rows.loc[rows.population=="signal","net_r"].sum()),
                          seconds=time.perf_counter()-started)),flush=True)


def run(config):
    cfg=json.loads(config.read_text())
    def one(arm):
        name=arm["name"]
        reused=(config.parent/"runs"/name/"receipt.json").exists()
        with (config.parent/(name+".log")).open("w") as log:
            subprocess.run([sys.executable,"-m","yoyo.evaluation.range_rsi_scaleout",
                "--config",str(config),"--phase","native-arm","--arm",name],
                cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
        r=json.loads((config.parent/"runs"/name/"receipt.json").read_text())
        r=dict(r,reused=reused)
        print(json.dumps(dict(completed=name,reused=reused,
                              seconds=0 if reused else r["elapsed_seconds"])),flush=True)
        return name,r
    started=time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as pool:
        result=dict(pool.map(one,cfg["arms"]))
    dump(config.parent/"native_validation.json",dict(
        arms={k:v["audit"] for k,v in result.items()},
        reused_arms=[k for k,v in result.items() if v["reused"]],
        new_arms=[k for k,v in result.items() if not v["reused"]],
        elapsed_seconds=time.perf_counter()-started))


def analyze(config):
    cfg=json.loads(config.read_text());folder=config.parent
    frames={p["name"]:pd.read_csv(folder/"runs"/p["name"]/"trades.csv",
             float_precision="round_trip") for p in cfg["arms"]}
    baseline=frames["baseline"].set_index("trade_id")
    tables=[];comparisons=[];attribution=[]
    for name,rows in frames.items():
        assert json.loads((folder/"runs"/name/"receipt.json").read_text())["audit"]["parity"]["status"]=="passed"
        fills=pd.read_csv(folder/"runs"/name/"fills.csv",float_precision="round_trip")
        cash=cash_events(rows,fills)
        for seg in (-1,0,1,2,-2):
            part=subset(rows,seg)
            signal,control=part[part.population=="signal"],part[part.population=="random"]
            for pop,p in (("signal",signal),("random",control)):
                money=subset(cash,seg);money=money[money.population==pop]
                metric=summary(p)
                metric.update(mean_net_bp=float(p.net_notional_bp.mean()),
                    cash_r_drawdown=drawdown(money.groupby("ts_ns").cash_r.sum().sort_index()),
                    near_zero_n=int(p.net_r.abs().le(.01).sum()),
                    tiny_positive_n=int((p.net_r.gt(1e-9)&p.net_r.le(.01)).sum()),
                    net_above_0p1r_rate=float(p.net_r.gt(.1).mean()),
                    rsi_reduced_n=int(p.rsi_partial_count.gt(0).sum()) if "rsi_partial_count" in p else 0,
                    rsi_fully_exited_n=int(p.reason.eq(6).sum()),
                    time_be_eligible_n=int(p.be_trigger_i.ge(0).sum()),
                    time_be_effective_n=int(p.be_effective_i.ge(0).sum()))
                tables.append(dict(arm=name,segment=seg,population=pop,**metric))
            gain=signal[["trade_id","pair_id","week","net_r"]].merge(
                baseline[["net_r"]],left_on="trade_id",right_index=True,suffixes=("","_random"))
            comparisons.append(dict(arm=name,segment=seg,
                signal_vs_random=inference(pairs(signal,control),cfg["seed"]),
                gain_vs_baseline=inference(gain,cfg["seed"])))
        signal=rows[rows.population=="signal"].set_index("trade_id")
        reference=baseline.loc[signal.index]
        delta=signal.net_r-reference.net_r
        tails=reference.gross_r>=20-1e-7
        attribution.append(dict(arm=name,
            rescued_losers=int(((reference.net_r<0)&(signal.net_r>=-1e-9)).sum()),
            damaged_winners=int(((reference.net_r>1e-9)&(delta<-1e-8)).sum()),
            improved_trade_n=int((delta>1e-8).sum()),worsened_trade_n=int((delta<-1e-8).sum()),
            improvement_r=float(delta[delta>1e-8].sum()),damage_r=float(delta[delta<-1e-8].sum()),
            original_20r_n=int(tails.sum()),still_20r_n=int((signal.loc[tails,"gross_r"]>=20-1e-7).sum()),
            original_tail_gross_r=float(reference.loc[tails,"gross_r"].sum()),
            retained_tail_gross_r=float(signal.loc[tails,"gross_r"].sum()),
            original_tail_mean_bp=float(reference.loc[tails,"net_notional_bp"].mean()),
            retained_tail_mean_bp=float(signal.loc[tails,"net_notional_bp"].mean())))
    table=pd.DataFrame(tables)
    table.to_csv(folder/"summary.csv",index=False)
    full=table[(table.segment==-1)&table.population.eq("signal")]
    dev=table[(table.segment==0)&table.population.eq("signal")]
    selected=str(dev.sort_values(["net_r","arm"],ascending=[False,True]).iloc[0].arm)
    best=str(full.sort_values(["net_r","arm"],ascending=[False,True]).iloc[0].arm)
    for segment in (-1,0,1,2,-2):
        compared=[r for r in comparisons if r["segment"]==segment and r["arm"]!="baseline"]
        for r,p in zip(compared,holm([r["gain_vs_baseline"]["p_one_sided"] for r in compared])):
            r["gain_vs_baseline"]["holm_p"]=p
    dump(folder/"comparisons.json",comparisons)
    dump(folder/"attribution.json",attribution)
    dump(folder/"analysis_receipt.json",dict(development_2024=selected,
        descriptive_full=best,arms=len(cfg["arms"]),no_new_holdout_claim=True,
        diagnostic_only_thresholds=dict(near_zero_r=.01,material_positive_r=.1),
        source_commit=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
        production_eligible=False,training_eligible=False,no_pine_change=True))
    print(full[["arm","net_r","win_rate","gross_20r"]].to_json(orient="records"),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config",type=Path,default=DEFAULT)
    p.add_argument("--phase",choices=["screen","run","native-arm","analyze"],required=True)
    p.add_argument("--arm")
    a=p.parse_args();config=a.config.resolve()
    if a.phase=="native-arm":
        native_arm(config,a.arm)
    else:
        {"screen":screen,"run":run,"analyze":analyze}[a.phase](config)


if __name__=="__main__":
    main()
