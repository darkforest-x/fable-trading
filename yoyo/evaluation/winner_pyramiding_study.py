"""Frozen-input comparison of zero, two and unlimited profitable additions.

See exp-winner-pyramiding-20260921-v1/PROJECT_PLAN.md. V9 features use published
past/current OHLCV windows; initial stops use the preceding five closed bars
including the signal and its ATR. Every add reads only a completed structural
event and the following open. No screenshot fitting, fetch, model or orders.
Calendar-month blocked policy comparisons do not imply a compound portfolio.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import itertools
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v9_htf_sma_study as source
from yoyo.evaluation.spike_v9 import v9_admissions
from yoyo.evaluation.winner_pyramiding import replay_path

EXP = Path("experiments/active/exp-winner-pyramiding-20260921-v1")
PRIOR = source.EXP / "run_v1"
VOL_BINS = np.array([.005, .01, .02, .05, .1])


def digest(path):
    return source.source.digest(Path(path))


def read_config():
    return json.loads((EXP / "config.json").read_text())


def choose_control(eligible, months, bins, i, event_key, seed, *, excluded=None):
    """One deterministic month/volatility match; never inspect its outcome."""
    if excluded is not None:
        eligible=eligible & ~np.asarray(excluded,bool)
    options = np.flatnonzero(eligible & (months == months[i]) & (bins == bins[i]))
    options = options[options != i]
    if not len(options):
        return None
    token = int(hashlib.sha256(f"{seed}|{event_key}".encode()).hexdigest(), 16)
    return int(options[token % len(options)])


def period_bounds(index, lo, hi):
    """Membership follows next-open entry; a close exactly at the cut is later."""
    scheduled=index+pd.Timedelta(hours=1)
    return (scheduled>=lo)&(scheduled<hi),int(index.searchsorted(hi,side="left"))


def initial(p, i):
    """Preserve the published original entry/stop, cancel a missing next bar."""
    if i+1 >= len(p.frame) or p.gap[i+1]:
        return None
    return source.source._initial_position_fast(p.frame.index, p.open, p.high, p.low,
        p.close, p.atr, p.gap, i, 1, p.spec)


def one(args):
    symbol, item, cfg, output, identity_hash = args
    began = time.monotonic()
    path = Path(item["path"])
    if digest(path) != item["sha256"]:
        raise ValueError(f"raw source changed: {symbol}")
    raw = source.inc.guarded_5m(path, source.source.START-pd.Timedelta(days=source.source.WARMUP_BARS))
    bars = source.v11.bars_for(raw, 60)
    facts = source.source.v9_facts(bars, 60, item["meta"]["asset"], float(item["meta"]["tick"]))
    p = source.build_prepared(facts, symbol, item["meta"], "1h", 60)
    check = v9_admissions(p.context)
    np.testing.assert_array_equal(check.v9.to_numpy(bool) & source.source.in_window(p.frame.index,60),p.allowed)
    # Current-hour incomplete source buckets are not permitted in this research.
    counts = raw.resample("1h").size().reindex(p.frame.index).fillna(0).to_numpy()
    partial = counts != 12
    p.gap[:] = p.gap | partial
    f = p.frame
    scheduled = f.index + pd.Timedelta(hours=1)
    start, split, end = map(pd.Timestamp,(cfg["start"],cfg["split"],cfg["end"]))
    months = np.asarray(scheduled.strftime("%Y-%m"))
    bins = np.searchsorted(VOL_BINS,p.atr/p.close,side="left")
    ready = f.ready.fillna(False).to_numpy(bool)
    rv = f.rv.to_numpy(float)
    finite = np.isfinite(f[["open","high","low","close","atr"]].to_numpy(float)).all(axis=1)
    eligible = ready & finite & (p.atr>0) & (p.close>0) & ~p.gap & ~partial & (scheduled.dayofweek!=6) & np.isfinite(rv) & (rv>=0) & (rv<=50)
    rows, events, matched, coverage = [],[],[],[]
    actual_targets=p.allowed & (p.raw_side==1)
    for period, lo, hi in (("earlier",start,split),("later",split,end)):
        window,last = period_bounds(f.index,lo,hi)
        available = eligible & window
        cut_frame = f.iloc[:last]
        short, gap = (p.raw_side[:last]==-1),p.gap[:last]
        candidates = np.flatnonzero(p.allowed & (p.raw_side==1) & window & ~partial)
        next_signal_i, entered, invalid, occupied = -1,0,0,0
        cache = {}

        def evaluate(signal_i, event_key, scope):
            made = initial(p,signal_i)
            if made is None or int(made["entry_i"])>=last:
                return None
            answers=[]; add_prefixes={}
            for arm,limit in cfg["arms"].items():
                key=(signal_i,arm)
                if scope=="random" and key in cache:
                    answer=cache[key].copy(); detail=[]
                else:
                    answer,detail=replay_path(cut_frame,short,gap,
                        entry_i=int(made["entry_i"]),initial_stop=float(made["initial_stop"]),
                        tick=float(p.spec.tick),max_adds=limit,capital=cfg["capital"],
                        gross_risk_budget=cfg["gross_risk_budget"],leverage_cap=cfg["leverage_cap"],
                        events=(scope=="actual"))
                    if scope=="random":cache[key]=answer.copy()
                answer={**answer,"symbol":symbol,"period":period,"arm":arm,"scope":scope,
                    "event_key":event_key,"signal_i":signal_i,"signal_time":f.index[signal_i],
                    "entry_time":f.index[int(made["entry_i"])],"month":months[signal_i],
                    "vol_bin":int(bins[signal_i]),"signal_rv":float(rv[signal_i])}
                for event in detail:
                    events.append({**event,"symbol":symbol,"period":period,"arm":arm,"event_key":event_key})
                if scope=="actual":
                    add_prefixes[arm]=[(e["bar"],e["price"],e["quantity"]) for e in detail if e["kind"]=="add"][:2]
                answers.append(answer)
            if scope=="actual" and add_prefixes["two"]!=add_prefixes["continuous"]:
                raise AssertionError(f"first two additions changed: {symbol} {signal_i}")
            for a in answers[1:]:
                for key in ("entry_i","exit_i","exit_price","initial_stop","initial_quantity","initial_risk_usd","censored"):
                    if a[key]!=answers[0][key]:
                        raise AssertionError(f"quantity changed common path {symbol} {signal_i} {key}")
            return answers

        for i in candidates:
            i=int(i)
            if i<next_signal_i:
                occupied+=1;continue
            event_key=f"{symbol}|{f.index[i].isoformat()}"
            actual=evaluate(i,event_key,"actual")
            if actual is None:invalid+=1;continue
            entered+=1;rows.extend(actual)
            # An intrabar exit leaves the stream flat at this same bar's close.
            next_signal_i=int(actual[0]["exit_i"])
            j=choose_control(available,months,bins,i,event_key,cfg["seed"],excluded=actual_targets)
            control=evaluate(j,event_key,"random") if j is not None else None
            if control is not None:rows.extend(control)
            for a in actual:
                c=next((r for r in control or [] if r["arm"]==a["arm"]),None)
                valid=c is not None and not c["censored"] and not a["censored"]
                matched.append({"symbol":symbol,"period":period,"arm":a["arm"],"event_key":event_key,
                    "month":months[i],"vol_bin":int(bins[i]),"target_signal_i":i,
                    "control_signal_i":j,"matched":valid,"target_censored":a["censored"],
                    "control_censored":None if c is None else c["censored"],
                    "reason":"matched" if valid else "missing_or_censored",
                    "target_net_r":a["net_r"],"control_net_r":None if c is None else c["net_r"],
                    "delta_r":a["net_r"]-c["net_r"] if valid else None})
        coverage.append({"symbol":symbol,"period":period,"bars":int(window.sum()),
            "candidates":len(candidates),"entries":entered,"invalid_initial":invalid,"occupied":occupied,
            "partial_buckets":int((partial & window).sum()),"gaps":int((p.gap & window).sum())})
    if digest(path)!=item["sha256"]:raise ValueError("source changed during run")
    folder=Path(output)/"streams"/symbol;folder.mkdir(parents=True,exist_ok=False)
    for name,records in (("trades",rows),("events",events),("controls",matched)):
        pd.DataFrame(records).to_csv(folder/f"{name}.csv.gz",index=False,compression={"method":"gzip","mtime":0})
    result={"symbol":symbol,"identity_hash":identity_hash,"source_sha256":item["sha256"],
        "v9_mask_parity":True,"coverage":coverage,"first":str(f.index.min()),"last":str(f.index.max()),
        "seconds":time.monotonic()-began,"files":{x.name:digest(x) for x in folder.iterdir()}}
    (folder/"completion.json").write_text(json.dumps(result,indent=2)+"\n")
    return result


def block_inference(table, *, seed=921261, reps=4000):
    """Resample entire shared calendar-month blocks, not individual trades."""
    if table.empty:
        return {"n":0,"months":0,"mean_delta":None,"ci_low":None,"ci_high":None,"p_one_sided":None}
    blocks=table.groupby("month").delta_r.agg(["sum","count"])
    sums=blocks["sum"].to_numpy(float);counts=blocks["count"].to_numpy(float)
    rng=np.random.default_rng(seed)
    draws=rng.integers(0,len(sums),size=(reps,len(sums)))
    values=sums[draws].sum(axis=1)/counts[draws].sum(axis=1)
    nonzero=sums[np.abs(sums)>1e-12];observed=sums.sum()
    if len(nonzero)<=18:
        null=np.array([np.dot(sign,nonzero) for sign in itertools.product((-1,1),repeat=len(nonzero))])
        p=float(np.mean(null>=observed-1e-12))
    else:
        null=(rng.choice([-1,1],size=(20000,len(nonzero)))*nonzero).sum(axis=1)
        p=float((1+np.count_nonzero(null>=observed-1e-12))/(len(null)+1))
    return {"n":len(table),"months":len(sums),"nonzero_months":len(nonzero),
        "mean_delta":float(table.delta_r.mean()),"total_delta":float(observed),
        "ci_low":float(np.quantile(values,.025)),"ci_high":float(np.quantile(values,.975)),"p_one_sided":p}


def summarize(output, cfg):
    output=Path(output)
    tables={}
    for name in ("trades","controls"):
        parts=[]
        for path in sorted((output/"streams").glob(f"*/{name}.csv.gz")):
            try:
                parts.append(pd.read_csv(path))
            except pd.errors.EmptyDataError:
                # A newly listed symbol may legitimately have no admitted long.
                # Its zero-entry completion receipt remains part of coverage.
                continue
        if not parts:
            raise ValueError(f"No {name} in any completed stream")
        tables[name]=pd.concat(parts,ignore_index=True)
    t,c=tables["trades"],tables["controls"]
    summaries=[]
    for subset,mask in (("all",np.ones(len(t),bool)),("WIF",t.symbol.eq("WIFUSDT"))):
        for (period,arm,scope),part in t.loc[mask].groupby(["period","arm","scope"]):
            closed=part.loc[~part.censored.astype(bool)]
            profits=closed.net_r
            pair=c.loc[c.period.eq(period)&c.arm.eq(arm)&c.matched.astype(bool)]
            if subset=="WIF":pair=pair.loc[pair.symbol.eq("WIFUSDT")]
            summaries.append({"subset":subset,"period":period,"arm":arm,"scope":scope,
                "entries":len(part),"closed":len(closed),"censored":int(part.censored.sum()),
                "gross_r":float(closed.gross_r.sum()),"net_r":float(profits.sum()),
                "mean_net_r":float(profits.mean()) if len(closed) else None,
                "win_rate":float(profits.gt(0).mean()) if len(closed) else None,
                "pf":float(profits[profits>0].sum()/-profits[profits<0].sum()) if profits.lt(0).any() else None,
                "max_adds":int(part.adds_count.max()),"total_adds":int(part.adds_count.sum()),
                "trades_ge3_adds":int(part.adds_count.ge(3).sum()),
                "positive_class_count":int(profits.gt(0).sum()),
                "mean_gross_initial_notional_return":float(closed.gross_return.mean()) if len(closed) else None,
                "mean_net_initial_notional_return":float(closed.net_return.mean()) if len(closed) else None,
                "initial_risk_clipped_count":int(part.initial_risk_usd.lt(cfg["gross_risk_budget"]-1e-9).sum()),
                "mean_initial_risk_usd":float(part.initial_risk_usd.mean()),
                "mean_peak_close_to_exit_giveback_usd":float((closed.peak_close_equity-cfg["capital"]-closed.net_usd).clip(lower=0).mean()) if len(closed) else None,
                "mean_intratrade_close_drawdown_usd":float(closed.max_close_drawdown_usd.mean()) if len(closed) else None,
                "matched_pairs":len(pair),"matched_random_net_r":float(pair.control_net_r.sum()),
                "matched_excess_r":float(pair.delta_r.sum())})
    pd.DataFrame(summaries).to_csv(output/"summary.csv",index=False)
    actual=t.loc[t.scope.eq("actual") & ~t.censored.astype(bool)].copy()
    wide=actual.pivot(index=["event_key","symbol","period","month"],columns="arm",values="net_r").reset_index()
    stats=[]
    for period in ("earlier","later"):
        part=wide.loc[wide.period.eq(period)]
        for treatment,baseline in (("continuous","two"),("continuous","none"),("two","none")):
            pair=part.copy();pair["delta_r"]=pair[treatment]-pair[baseline]
            stats.append({"period":period,"comparison":f"{treatment}-{baseline}",**block_inference(pair,reps=cfg["bootstrap_reps"]),
                "improved_events":int(pair.delta_r.gt(1e-10).sum()),"worsened_events":int(pair.delta_r.lt(-1e-10).sum())})
        pair=c.loc[c.period.eq(period)&c.arm.eq("continuous")&c.matched.astype(bool)]
        stats.append({"period":period,"comparison":"continuous-random",**block_inference(pair,reps=cfg["bootstrap_reps"])})
    primary=[x for x in stats if x["period"]=="later" and x["comparison"] in ("continuous-two","continuous-random") and x["p_one_sided"] is not None]
    running=0.0
    for rank,row in enumerate(sorted(primary,key=lambda x:x["p_one_sided"])):
        running=max(running,min(1.0,(len(primary)-rank)*row["p_one_sided"]))
        row["holm_p_two_primary_claims"]=running
    actual.loc[actual.arm.eq("continuous")].sort_values(["adds_count","net_r"],ascending=False).to_csv(output/"continuous_examples.csv",index=False)
    wide["continuous_minus_two"]=wide.continuous-wide.two
    wide.sort_values("continuous_minus_two").to_csv(output/"paired_policy_deltas.csv",index=False)
    (output/"statistics.json").write_text(json.dumps({"comparisons":stats,
        "unique_control_event_count":int(c.loc[c.arm.eq("none"),["symbol","period","control_signal_i"]].dropna().drop_duplicates().shape[0]),
        "control_draw_count":int(c.arm.eq("none").sum()),"notes":"Monthly blocked, nonblind historical inference; independent event risk units, not account compounding."},indent=2)+"\n")


def run(output,workers=3,symbols=None):
    cfg=read_config();output=Path(output)
    declared=(*source._local_transitive_python((Path(__file__),)),EXP/"config.json",EXP/"PROJECT_PLAN.md",
        Path("tests/evaluation/test_winner_pyramiding.py"),Path("tests/evaluation/test_winner_pyramiding_study.py"))
    if not source.engine._committed(tuple(declared)):
        raise ValueError("Commit unchanged research builder/dependencies/tests/config before replay")
    prior=json.loads((PRIOR/"identity.json").read_text())
    prior_completion=json.loads((PRIOR/"completion.json").read_text())
    assert prior_completion["identity_hash"]==prior["identity_hash"]
    expected=sorted(("1000PEPE" if n=="PEPE" else n)+"USDT" for n in source.NAMES)
    assert sorted(prior["inputs"])==expected and sorted(prior_completion["symbols"])==expected
    assert digest(source.source.EXCHANGE_INFO)==prior["metadata_sha256"]
    wanted=expected if symbols is None else sorted(symbols)
    assert len(wanted)==len(set(wanted)) and set(wanted)<=set(expected)
    inputs={s:prior["inputs"][s] for s in wanted}
    identity={"config":cfg,"inputs":inputs,"expected_symbols":expected,"full_pool":wanted==expected,
        "prior_identity_sha256":digest(PRIOR/"identity.json"),"metadata_sha256":prior["metadata_sha256"],
        "declared":{str(p):digest(p) for p in declared},
        "source_commit":subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()}
    identity["identity_hash"]=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    output.mkdir(parents=True,exist_ok=False)
    (output/"identity.json").write_text(json.dumps(identity,indent=2)+"\n")
    results=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        jobs=[pool.submit(one,(s,item,cfg,str(output),identity["identity_hash"])) for s,item in inputs.items()]
        for future in as_completed(jobs):
            r=future.result();results.append(r)
            print(json.dumps({"done":len(results),"total":len(inputs),"symbol":r["symbol"],"seconds":round(r["seconds"],2)}),flush=True)
    assert sorted(r["symbol"] for r in results)==wanted
    coverage=[row for receipt in results for row in receipt["coverage"]]
    pd.DataFrame(coverage).to_csv(output/"coverage.csv",index=False)
    summarize(output,cfg)
    manifest={"source_commit":identity["source_commit"],"identity_hash":identity["identity_hash"],
        "full_pool":wanted==expected,"symbols":wanted,"generated_at":pd.Timestamp.now(tz="UTC").isoformat(),
        "files":{str(p.relative_to(output)):digest(p) for p in output.rglob("*") if p.is_file()}}
    (output/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,default=EXP/"run_v1")
    parser.add_argument("--workers",type=int,default=3)
    parser.add_argument("--symbols",nargs="+")
    args=parser.parse_args();run(args.output,args.workers,args.symbols)
