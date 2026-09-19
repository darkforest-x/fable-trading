"""Receipt-bound non-directional V9 six-line morphology decomposition.

Computes only contemporaneous/past geometry; selection never reads outcome
returns. Source and candidate V9 facts reuse the prior29-asset study. Per-bar
label permutations provide a width-preserving crossing null, not market data.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor,as_completed
import hashlib
import json
from pathlib import Path
import subprocess
import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v9_htf_sma_study as prior
from yoyo.evaluation.spike_ma_density import MAS,ARM_NAMES,diagnostics,phenotypes

EXP=Path("experiments/active/exp-spike-ma-density-20260920-v1")


def config():return json.loads((EXP/"config.json").read_text())
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def one(args):
    symbol,item,outpath,identity_hash=args;cfg=config()
    path=Path(item["path"]);assert digest(path)==item["sha256"]
    base=prior.inc.guarded_5m(path,prior.source.START-pd.Timedelta(days=prior.source.WARMUP_BARS))
    records,flags,signals,nulls,cover,cases,windows,distributions=[],[],[],[],[],[],[],[]
    for tf,minutes in cfg["timeframes"]:
        bars=prior.v11.bars_for(base,minutes)
        if not len(bars):continue
        facts=prior.source.v9_facts(bars,minutes,item["meta"]["asset"],float(item["meta"]["tick"]))
        f=facts["frame"];d=diagnostics(f,minutes);ph=phenotypes(d)
        clock=f.index+pd.Timedelta(minutes=minutes)
        active=(clock>=pd.Timestamp(cfg["start"]))&(clock<pd.Timestamp(cfg["end"]))
        known=d.known.to_numpy(bool)&active
        # Exact released formula outside reset windows, including all15 pairs.
        np.testing.assert_allclose(d.loc[d.known,"mean_atr12"],f.loc[d.known,"pastWidth"],rtol=1e-8,atol=1e-8)
        np.testing.assert_allclose(d.loc[d.known,"cross_count12"],f.loc[d.known,"pastCrosses"],rtol=0,atol=0)
        v9=facts["v9"]&active
        sig=d.loc[v9].copy();sig["side"]=facts["side"][v9];sig["symbol"]=symbol;sig["timeframe"]=tf
        sig["signal_bar_open"]=sig.index;sig["period"]=np.where(clock[v9]<pd.Timestamp(cfg["split"]),"earlier","later")
        signals.append(sig.reset_index(drop=True))
        _,partial=prior.source.aggregate(base.loc[base.index>=bars.index.min()],minutes)
        cover.append({"symbol":symbol,"timeframe":tf,"chart_bars":len(f),"in_window_ready":int((active&f.ready).sum()),
                      "shared_known":int(known.sum()),"excluded_ready":int((active&f.ready&~d.known).sum()),
                      "v9_candidates":int(v9.sum()),"v9_known":int((v9&d.known).sum()),"partial_buckets":partial})
        rng=np.random.default_rng(int(hashlib.sha256(f"{cfg['seed']}|{symbol}|{tf}".encode()).hexdigest()[:16],16))
        null=f.copy();ma=f[list(MAS)].to_numpy();order=np.argsort(rng.random(ma.shape),axis=1)
        null.loc[:,list(MAS)]=np.take_along_axis(ma,order,axis=1)
        nd=diagnostics(null,minutes)
        np.testing.assert_allclose(d.mean_atr12,nd.mean_atr12,equal_nan=True)
        np.testing.assert_allclose(d.stable_max12,nd.stable_max12,equal_nan=True)
        for period,mask in (("full",known),("earlier",known&(clock<pd.Timestamp(cfg["split"]))),("later",known&(clock>=pd.Timestamp(cfg["split"])))):
            n=int(mask.sum());b=d.legacy.to_numpy(bool)&mask
            for arm in ARM_NAMES:
                q=d[arm].to_numpy(bool)&mask
                start=d[arm]&~d[arm].shift(1,fill_value=False)
                records.append({"symbol":symbol,"timeframe":tf,"period":period,"arm":arm,"known_bars":n,
                                "bars":int(q.sum()),"episodes":int((start&mask).sum()),"overlap":int((q&b).sum()),
                                "removed":int((b&~q).sum()),"added":int((q&~b).sum())})
            for name in ph:
                flags.append({"symbol":symbol,"timeframe":tf,"period":period,"flag":name,"known_bars":n,
                              "legacy_bars":int(b.sum()),"bars":int((ph[name]&mask).sum())})
            for memory in (3,6,12):
                rm=d[f"legacy_recent{memory}"]&mask
                records.append({"symbol":symbol,"timeframe":tf,"period":period,"arm":f"memory_{memory}","known_bars":n,
                                "bars":int(rm.sum()),"episodes":int((rm&~rm.shift(1,fill_value=False)).sum()),
                                "overlap":int((rm&d.legacy_recent12&mask).sum()),"removed":int((~rm&d.legacy_recent12&mask).sum()),"added":0})
            for arm in ("legacy","width_1","distinct_pairs_2","group_edges_2","max_width_3"):
                nulls.append({"symbol":symbol,"timeframe":tf,"period":period,"arm":arm,"known_bars":n,
                              "real":int((d[arm]&mask).sum()),"identity_permuted":int((nd[arm]&mask).sum())})
            for name in ("mean_atr12","stable_mean12","max_atr12","cross_count12","distinct_pairs12","group_edges12","contraction_ratio"):
                for cohort,cm in (("all",mask),("legacy",b),("v9",mask&v9)):
                    values=d.loc[cm,name].replace([np.inf,-np.inf],np.nan).dropna()
                    distributions.append({"symbol":symbol,"timeframe":tf,"period":period,"cohort":cohort,"field":name,"n":len(values),
                                          **{f"p{int(q*100)}":float(values.quantile(q)) if len(values) else np.nan for q in (.1,.5,.9)}})
        # Purposeful, deterministic geometry cases; no outcome, future bar or rank.
        for kind in ph:
            eligible=np.flatnonzero(ph[kind].to_numpy()&known)
            if not len(eligible):continue
            hashes=pd.util.hash_pandas_object(pd.Series(f.index[eligible]),index=False).to_numpy(np.uint64)
            i=int(eligible[np.argmin(hashes)])
            cid=f"{symbol}_{tf}_{kind}"
            row={"case_id":cid,"symbol":symbol,"timeframe":tf,"minutes":minutes,"kind":kind,"time":str(f.index[i]),"signal_i":i}
            row.update(d.iloc[i].to_dict());cases.append(row)
            z=f.iloc[max(0,i-80):i+1][["open","high","low","close","atr",*MAS]].copy()
            z["time"]=z.index;z["case_id"]=cid;z["relative_i"]=np.arange(-len(z)+1,1);windows.append(z.reset_index(drop=True))
    folder=Path(outpath)/"streams"/symbol;folder.mkdir(parents=True)
    tables={"counts":pd.DataFrame(records),"flags":pd.DataFrame(flags),"v9":pd.concat(signals,ignore_index=True),
            "null":pd.DataFrame(nulls),"coverage":pd.DataFrame(cover),"case_candidates":pd.DataFrame(cases),
            "case_windows":pd.concat(windows,ignore_index=True) if windows else pd.DataFrame(),"distributions":pd.DataFrame(distributions)}
    files={}
    for name,table in tables.items():
        file=folder/(name+".csv.gz");table.to_csv(file,index=False,compression={"method":"gzip","mtime":0});files[file.name]=digest(file)
    assert digest(path)==item["sha256"]
    receipt={"symbol":symbol,"identity_hash":identity_hash,"source_sha256":item["sha256"],"files":files,"streams":len(cover),"legacy_feature_parity":True}
    (folder/"completion.json").write_text(json.dumps(receipt,indent=2)+"\n");return receipt


def run(out,workers=3):
    cfg=config();original=json.loads(Path(cfg["source_identity"]).read_text())
    declared=(*prior._local_transitive_python((Path(__file__),)),EXP/"config.json",EXP/"PROJECT_PLAN.md",Path("tests/evaluation/test_spike_ma_density.py"))
    if not prior.engine._committed(tuple(declared)):raise ValueError("Commit study/config/dependencies/tests first")
    identity={"config":cfg,"source_identity_hash":original["identity_hash"],"inputs":original["inputs"],
              "code":{str(p):digest(p) for p in declared},"source_commit":subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()}
    identity["identity_hash"]=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    out=Path(out);out.mkdir(parents=True)
    (out/"identity.json").write_text(json.dumps(identity,indent=2)+"\n")
    args=[(s,item,str(out),identity["identity_hash"]) for s,item in identity["inputs"].items()]
    done=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(one,x) for x in args]):
            receipt=future.result();done.append(receipt);print(json.dumps({"done":len(done),"total":len(args),"symbol":receipt["symbol"]}),flush=True)
    assert len(done)==29 and sum(r["streams"] for r in done)==87
    (out/"completion.json").write_text(json.dumps({"identity_hash":identity["identity_hash"],"symbols":sorted(r["symbol"] for r in done),"streams":87},indent=2)+"\n")


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--output",type=Path,default=EXP/"run_v1");p.add_argument("--workers",type=int,default=3)
    args=p.parse_args();run(args.output,args.workers)
