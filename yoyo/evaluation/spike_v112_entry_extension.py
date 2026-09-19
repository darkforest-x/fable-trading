"""Test one causal entry-extension gate on the owner's fixed 29-symbol pool.

Source: owner 2026-09-20 authorizes research after the complete trade review.
At joint close i with parent signal s, E=(close[i]-close[s])/(close[s]-stop[s]).
Parent stop reads OHLC[s-4:s+1] and ATR[s] only, using the unchanged stop spec;
no next open, parent future MFE or exit may enter E. The only treatment is E<=1.
Q6 reads seven closes ending at i (six changes); it is descriptive, not a gate.
The first box break is consumed before filtering; each arm owns serial state.
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import math
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v10_4_study as study
from yoyo.evaluation import spike_v10_4_increment as inc
from yoyo.evaluation import spike_v11_study as v11
from yoyo.evaluation import spike_v112_support_study as support
from yoyo.evaluation.spike_v10_4 import V104Params, box_joints, joint_events, reference_long_exits
from yoyo.evaluation.spike_v112_support_report import compare, validate_control_keys
from yoyo.evaluation.spike_v112_1h_diagnostics import metrics

EXP=Path("experiments/active/exp-spike-v112-entry-extension-20260920-v1")
SOURCE=Path("experiments/active/exp-spike-v112-selected-returns-20260919-v1")
LEDGER=SOURCE/"trades_with_controls.csv"
OLD_BOX=Path("experiments/active/exp-spike-v11-box-joint-20260918-v1/results/run_v1/streams")
OLD_4H=Path("experiments/active/exp-spike-v112-selected-counts-20260919-v1/results")
PAIRS=(("15m",15,"1h",60),("1h",60,"4h",240),("4h",240,"1d",1440))
ARMS=("box_any","extension_le1")
TABLES=("trades","statuses","controls","decisions")
SEED,REPS=91509,2000


def parent_geometry(prepared,s):
    """Signal-close stop/risk geometry, independent of the next bar's open."""
    spec=prepared.spec
    if not math.isfinite(spec.tick) or spec.tick<=0: raise ValueError("tick must be positive and finite")
    start=s-spec.stop_bars+1
    if start<0 or prepared.gap[start:s+1].any(): return math.nan,math.nan
    values=np.column_stack([getattr(prepared,k)[start:s+1] for k in ("open","high","low","close")])
    atr,close=float(prepared.atr[s]),float(prepared.close[s])
    if not np.isfinite(values).all() or not math.isfinite(atr) or atr<=0: return math.nan,math.nan
    if (values[:,2]<=0).any() or (values[:,1]<np.maximum(values[:,0],values[:,3])).any() or (values[:,2]>np.minimum(values[:,0],values[:,3])).any():
        return math.nan,math.nan
    raw=min(values[:,2].min()-spec.stop_buffer_atr*atr,close-spec.risk_floor_atr*atr)
    stop=math.floor(raw/spec.tick)*spec.tick
    risk=close-stop
    if stop<=0 or risk<=spec.tick*1e-8: return math.nan,math.nan
    return stop,risk


def features_at(prepared,s,i):
    """Closed-bar extension and six-close-change efficiency; future suffix unused."""
    if not 0<=s<=i<len(prepared.frame): raise ValueError("invalid parent/event order")
    stop,risk=parent_geometry(prepared,s)
    close=float(prepared.close[i]); parent=float(prepared.close[s])
    valid=math.isfinite(risk) and math.isfinite(close) and not prepared.gap[s:i+1].any()
    extension=(close-parent)/risk if valid else math.nan
    passed=bool(math.isfinite(extension) and extension<=1.)
    q6=math.nan
    if i>=6 and not prepared.gap[i-5:i+1].any():
        changes=np.diff(prepared.close[i-6:i+1])
        if np.isfinite(changes).all():
            absolute=float(np.abs(changes).sum())
            q6=float(changes.sum()/absolute) if absolute>0 else 0.
    return {"parent_signal_close":parent,"parent_known_stop":stop,"parent_close_risk":risk,
            "signal_close_price":close,"extension":extension,"extension_pass":passed,
            "gate_reason":"pass" if passed else "extension_gt1" if math.isfinite(extension) else "invalid_geometry_or_gap",
            "efficiency6":q6}


def run_pair(symbol,base,meta,pair):
    tf,minutes,htf_name,htf_minutes=pair
    tick,asset=float(meta["tick"]),meta["asset"]
    bars=v11.bars_for(base,minutes)
    parts=support._empty_parts()
    if not len(bars) or not study.in_window(bars.index,minutes).any(): return parts
    facts=study.v9_facts(bars,minutes,asset,tick); f=facts["frame"]
    box={}
    exits=reference_long_exits(f.high,f.low,f.close,f.atr,ready=facts["ready"],gap=facts["gap"],
            raw_side=facts["side"],signal_side=np.where(facts["v9"],facts["side"],0),tick=tick,state=box)
    assert np.array_equal(exits,facts["ref_long_exit"])
    params=V104Params()
    htf,_=v11.htf_inputs(f.index,minutes,v11.bars_for(base,htf_minutes),htf_minutes,tick,params)
    chart=joint_events(f.open,f.high,f.low,f.close,f.atr,can_run=facts["can_run"],confirmed_long=facts["v9_long"],
            parent_high=facts["parent_high"],parent_low=facts["parent_low"],raw_side=facts["side"],
            long_alive=facts["long_alive"],momentum=facts["momentum"],current_gate=facts["current_gate"],
            ref_long_exit=facts["ref_long_exit"],tick=tick,params=params)
    # Consumption must happen before the extension filter, including pre-window events.
    original=box_joints(box["long_open"],box["box_entry"],htf["known"]|chart.break_event)&study.in_window(f.index,minutes)
    candidates=np.flatnonzero(original)
    key=f"binance_um:{symbol}:{tf}"
    prepared=study.prepared_arm(f,facts["gap"],facts["side"],key,
            {"venue":"binance_um","symbol":symbol,"asset":asset,"timeframe":tf,"timeframe_min":minutes},minutes,tick)
    decisions=[]
    for i in candidates:
        s=int(box["box_entry"][i])
        decisions.append({"symbol":symbol,"timeframe":tf,"signal_i":int(i),"signal_bar_open":f.index[i],
                "trade_key":f"{key}:box_any:{i}","box_entry_i":s,"bars_after_v9":int(i-s),
                "source":"both" if htf["known"][i] and chart.break_event[i] else "htf" if htf["known"][i] else "chart",
                **features_at(prepared,s,int(i))})
    if not decisions: return parts
    d=pd.DataFrame(decisions); parts["decisions"]=d
    trs,sts=[],[]
    for arm in ARMS:
        cand=candidates if arm=="box_any" else d.loc[d.extension_pass,"signal_i"].to_numpy(int)
        t,s=support.serial(prepared,cand,key,arm)
        for row in t: row.update(symbol=symbol,asset=asset,timeframe=tf)
        for row in s: row.update(symbol=symbol,asset=asset,timeframe=tf,signal_bar_open=f.index[row["signal_i"]])
        trs.extend(t);sts.extend(s)
    parts["trades"]=pd.DataFrame(trs,columns=parts["trades"].columns)
    parts["statuses"]=pd.DataFrame(sts,columns=parts["statuses"].columns)
    parts["trades"]["censored"]=parts["trades"].status.ne("closed")
    if len(parts["trades"]):
        parts["controls"]=pd.concat([study.controls(prepared,g,minutes,facts["ready"]) for _,g in parts["trades"].groupby("arm")],ignore_index=True)
    for arm in ARMS:
        status={r["signal_i"]:r["status"] for r in sts if r["arm"]==arm}
        d[arm+"_status"]=[status.get(int(i),"rejected_extension") for i in candidates]
    return parts


def worker(args):
    symbol,path,meta,expected_sha,identity_hash=args
    assert study.digest(Path(path))==expected_sha
    out=EXP/"run/streams"/symbol
    if (out/"completion.json").exists():
        r=json.loads((out/"completion.json").read_text())
        assert r["identity_hash"]==identity_hash and r["source_sha256"]==expected_sha
        for name,sha in r["outputs"].items(): assert study.digest(out/name)==sha
        return r
    base=inc.guarded_5m(Path(path),study.START-pd.Timedelta(days=study.WARMUP_BARS))
    parts={name:[] for name in TABLES}
    for pair in PAIRS:
        result=run_pair(symbol,base,meta,pair)
        for name in TABLES: parts[name].append(result[name])
    stage=out.parent/("."+symbol+".staging");stage.mkdir(parents=True,exist_ok=True)
    for name in TABLES:
        pd.concat(parts[name],ignore_index=True).to_csv(stage/f"{name}.csv.gz",index=False,compression={"method":"gzip","mtime":0})
    assert study.digest(Path(path))==expected_sha
    r={"symbol":symbol,"source_path":str(path),"source_sha256":expected_sha,"identity_hash":identity_hash,
       "outputs":{f"{name}.csv.gz":study.digest(stage/f"{name}.csv.gz") for name in TABLES}}
    (stage/"completion.json").write_text(json.dumps(r,indent=2)+"\n")
    stage.replace(out)
    return r


def add_dates(t):
    t=t.copy()
    for c in ("signal_bar_open","entry_time","exit_time","control_exit_time"):
        if c in t: t[c]=pd.to_datetime(t[c],utc=True,format="mixed")
    t["signal_close"]=t.signal_bar_open+pd.to_timedelta(t.timeframe.map({x[0]:x[1] for x in PAIRS}),unit="min")
    t["month"]=t.signal_close.dt.strftime("%Y-%m")
    t["cohort"]=np.where(t.signal_close.ge(study.SPLIT),"later",np.where(t.exit_time.lt(study.SPLIT),"earlier","cross_split"))
    t["hold_h"]=(t.exit_time-t.entry_time).dt.total_seconds()/3600
    t["net_bp"],t["gross_bp"]=t.net_return*1e4,t.gross_return*1e4
    return t


def mean_difference(a,b,field):
    """Paired UTC-month bootstrap with each arm's own event denominator."""
    months=sorted(set(a.month)|set(b.month))
    point=float(b[field].mean()-a[field].mean())
    if len(months)<2 or not len(a) or not len(b): return {"delta":point,"low":math.nan,"high":math.nan,"blocks":len(months)}
    agg=[g.groupby("month")[field].agg(["sum","size"]).reindex(months,fill_value=0) for g in (a,b)]
    ix=np.random.default_rng(SEED).integers(0,len(months),(REPS,len(months)))
    values=[]
    for g in agg:
        den=g["size"].to_numpy()[ix].sum(1)
        with np.errstate(divide="ignore",invalid="ignore"): values.append(g["sum"].to_numpy()[ix].sum(1)/den)
    diff=values[1]-values[0];diff=diff[np.isfinite(diff)]
    low,high=np.quantile(diff,[.025,.975]) if len(diff) else (math.nan,math.nan)
    return {"delta":point,"low":float(low),"high":float(high),"blocks":len(months)}


def analyze(symbols,old):
    raw={name:pd.concat([pd.read_csv(EXP/"run/streams"/s/f"{name}.csv.gz") for s in symbols],ignore_index=True) for name in TABLES}
    t,c,d,status=raw["trades"],raw["controls"],raw["decisions"],raw["statuses"]
    validate_control_keys(t,c)
    assert c.matched.isin([True,False]).all()
    c["matched"]=c.matched.astype(bool)
    t=t.merge(c,on=["arm","trade_key"],validate="one_to_one")
    columns=["trade_key","box_entry_i","bars_after_v9","source","parent_signal_close","parent_known_stop","parent_close_risk",
             "signal_close_price","extension","extension_pass","gate_reason","efficiency6"]
    t=t.merge(d[columns],on="trade_key",validate="many_to_one")
    t=add_dates(t);old=add_dates(old)
    a=t[t.arm.eq(ARMS[0])];b=t[t.arm.eq(ARMS[1])]
    fields=["signal_i","entry_i","entry_time","entry_price","initial_stop","initial_risk","exit_i","exit_time",
            "exit_price","exit_reason","mfe_r","net_r","status","matched","control_net_r","control_net_return"]
    parity=[compare(old,a,fields,"published_baseline"),compare(a,b,fields,"shared_entries",same_keys=False)]
    assert all(r["passed"] for r in parity),parity
    assert len(a)==570 and a.status.eq("closed").sum()==568
    assert b.extension_pass.all()
    # Validate the complete candidate/status sequence, not only the 570 executions.
    old_s=pd.concat([pd.read_csv(OLD_BOX/s/"statuses.csv.gz") for s in symbols],ignore_index=True)
    old_s=old_s[old_s.arm.eq("box_any")&old_s.timeframe.isin(["15m","1h"])]
    old_h=pd.concat([pd.read_csv(OLD_4H/s/"statuses.csv") for s in symbols],ignore_index=True)
    old_h=old_h[old_h.arm.eq("box_any")]
    orig_s=pd.concat([old_s,old_h],ignore_index=True)
    skeys=["symbol","timeframe","signal_i","status"]
    assert set(map(tuple,orig_s[skeys].values))==set(map(tuple,status.loc[status.arm.eq(ARMS[0]),skeys].values))
    closed=t[t.status.eq("closed")]
    rows,deltas,attribution,changes,groups,tails=[],[],[],[],[],[]
    for tf in [p[0] for p in PAIRS]:
        for period in ("full","earlier","later","cross_split"):
            q=closed[closed.timeframe.eq(tf)]
            if period!="full": q=q[q.cohort.eq(period)]
            for arm in ARMS:
                g=q[q.arm.eq(arm)]
                if len(g): rows.append({"timeframe":tf,"period":period,"arm":arm,**metrics(g,period=="earlier")})
            aa,bb=[q[q.arm.eq(arm)] for arm in ARMS]
            delta={"timeframe":tf,"period":period}
            for field in ("net_r","net_bp"):
                delta.update({field+"_"+k:v for k,v in mean_difference(aa,bb,field).items()})
            deltas.append(delta)
            removed=aa[~aa.trade_key.isin(bb.trade_key)];added=bb[~bb.trade_key.isin(aa.trade_key)]
            for field in ("net_r","net_bp"):
                np.testing.assert_allclose(bb[field].sum()-aa[field].sum(),added[field].sum()-removed[field].sum(),atol=1e-8)
            attribution.append({"timeframe":tf,"period":period,"removed":len(removed),"direct_reject":int((~removed.extension_pass).sum()),
                    "removed_occupancy":int(removed.extension_pass.sum()),"removed_losses":int(removed.net_r.lt(0).sum()),
                    "removed_wins":int(removed.net_r.gt(0).sum()),"saved_loss_r":-removed.loc[removed.net_r.lt(0),"net_r"].sum(),
                    "lost_profit_r":removed.loc[removed.net_r.gt(0),"net_r"].sum(),"added":len(added),"added_r":added.net_r.sum(),
                    "delta_total_r":bb.net_r.sum()-aa.net_r.sum(),"delta_total_bp":bb.net_bp.sum()-aa.net_bp.sum()})
            if period=="full":
                for label,gg in (("removed",removed),("added",added)):
                    changes.extend([{**r,"change":label} for r in gg.to_dict("records")])
                base=q[q.arm.eq(ARMS[0])].copy()
                base["extension_bin"]=pd.cut(base.extension,[-np.inf,0,.5,1,2,np.inf],labels=["<=0","0-.5",".5-1","1-2",">2"])
                base["efficiency_bin"]=pd.cut(base.efficiency6,[-np.inf,0,.5,np.inf],labels=["<=0","0-.5",">.5"])
                for col in ("extension_bin","efficiency_bin"):
                    for cohort in ("full","earlier","later"):
                        sample=base if cohort=="full" else base[base.cohort.eq(cohort)]
                        for bucket,gg in sample.groupby(col,observed=True):
                            groups.append({"timeframe":tf,"period":cohort,"feature":col,"bucket":str(bucket),**metrics(gg,cohort=="earlier")})
            if period in ("full","later"):
                for arm in ARMS:
                    gg=q[q.arm.eq(arm)].sort_values("net_r",ascending=False)
                    for k in (1,2):
                        if len(gg)>k: tails.append({"timeframe":tf,"period":period,"arm":arm,"removed_top":k,**metrics(gg.iloc[k:])})
    summary=pd.DataFrame(rows);differences=pd.DataFrame(deltas)
    verdict=[]
    for tf in [p[0] for p in PAIRS]:
        def row(period):
            found=summary[(summary.timeframe.eq(tf))&summary.arm.eq(ARMS[1])&summary.period.eq(period)]
            return found.iloc[0] if len(found) else pd.Series({k:math.nan for k in ("mean_r","mean_bp","excess_r","excess_p_month_signflip")})
        f,l=row("full"),row("later")
        low=differences[(differences.timeframe.eq(tf))&differences.period.isin(["full","later"])].net_bp_low
        gates={"full_r_bp_positive":bool(f.mean_r>0 and f.mean_bp>0),"later_r_bp_positive":bool(l.mean_r>0 and l.mean_bp>0),
               "both_bp_delta_ci_positive":bool(len(low)==2 and low.gt(0).all()),
               "random_excess_positive_and_adjusted_p":bool(f.excess_r>0 and f.excess_p_month_signflip*3<.01),
               "later_random_excess_positive":bool(l.excess_r>0)}
        verdict.append({"timeframe":tf,**gates,"passed":all(gates.values()),"adjusted_p":min(1.,f.excess_p_month_signflip*3)})
    d["extension_bucket"]=pd.cut(d.extension,[-np.inf,0,1,np.inf],labels=["<=0","0-1",">1"]).astype(object).fillna("invalid")
    candidate_counts=d.groupby(["timeframe","source","extension_bucket","extension_le1_status"]).size().rename("n").reset_index()
    out=EXP/"statistics";out.mkdir(exist_ok=True)
    tables={"summary":summary,"differences":differences,"attribution":pd.DataFrame(attribution),"changed_trades":pd.DataFrame(changes),
            "feature_groups":pd.DataFrame(groups),"tail_sensitivity":pd.DataFrame(tails),"verdict":pd.DataFrame(verdict),"parity":pd.DataFrame(parity),
            "candidate_counts":candidate_counts}
    for name,frame in tables.items(): frame.to_csv(out/f"{name}.csv",index=False)
    t.to_csv(out/"trades.csv.gz",index=False,compression={"method":"gzip","mtime":0})
    d.to_csv(out/"decisions.csv.gz",index=False,compression={"method":"gzip","mtime":0})
    status.groupby(["timeframe","arm","status"]).size().rename("n").to_csv(out/"status_counts.csv")
    print(summary[["timeframe","period","arm","n","win_pct","sum_r","mean_r","mean_bp","excess_r"]].to_string(index=False))
    return {"baseline_entries":len(a),"baseline_closed":int(a.status.eq("closed").sum()),"candidate_rows":len(d),
            "original_status_parity":len(orig_s),"gate_invalid":int(d.gate_reason.eq("invalid_geometry_or_gap").sum()),"verdict":verdict}


def main():
    declared=(*support._local_transitive_python((Path(__file__),)),EXP/"PROJECT_PLAN.md",Path("tests/evaluation/test_spike_v112_entry_extension.py"))
    assert support._committed(declared),"Commit code, plan and tests before replay"
    source_receipt=json.loads((SOURCE/"receipt.json").read_text())
    assert study.digest(LEDGER)==source_receipt["files"][str(LEDGER)]
    old=pd.read_csv(LEDGER);symbols=sorted(old.symbol.unique());assert len(symbols)==29
    files,meta=study.series_files(),study.symbol_meta()
    sources=[LEDGER,study.EXCHANGE_INFO]+[p for s in symbols for p in (OLD_BOX/s/"statuses.csv.gz",OLD_4H/s/"statuses.csv")]
    identity={"source_commit":subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
       "code":{str(p):study.digest(p) for p in declared},"input_sha":{s:study.digest(files[s]) for s in symbols},
       "source_ledgers":{str(p):study.digest(p) for p in sources},"pairs":PAIRS,"gate":"causal_extension <= 1.0","cost":.002}
    hash_payload={k:v for k,v in identity.items() if k!="source_commit"}
    identity_hash=hashlib.sha256(json.dumps(hash_payload,sort_keys=True).encode()).hexdigest()
    ip=EXP/"run/identity.json";ip.parent.mkdir(exist_ok=True)
    if ip.exists():
        prior=json.loads(ip.read_text());assert prior["identity_hash"]==identity_hash,"Different run identity; use separate run directory"
    else: ip.write_text(json.dumps({**identity,"identity_hash":identity_hash},indent=2)+"\n")
    receipts=[]
    with ProcessPoolExecutor(max_workers=4) as pool:
        tasks=[pool.submit(worker,(s,str(files[s]),meta[s],identity["input_sha"][s],identity_hash)) for s in symbols]
        for task in as_completed(tasks):
            receipts.append(task.result());print(json.dumps({"completed":len(receipts),"total":29,"symbol":receipts[-1]["symbol"]}),flush=True)
    result=analyze(symbols,old)
    completion={**result,"identity_hash":identity_hash,"streams":receipts,"training_eligible":False,"production_eligible":False,
                "files":{str(p):study.digest(p) for p in sorted((EXP/"statistics").glob("*"))}}
    (EXP/"completion.json").write_text(json.dumps(completion,ensure_ascii=False,indent=2)+"\n")


if __name__=="__main__": main()
