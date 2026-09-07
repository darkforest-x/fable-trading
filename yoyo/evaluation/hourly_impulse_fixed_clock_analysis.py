"""Saved V24 label diagnostics before the frozen monthly-cluster inference.

Never reads raw quotes or old exit paths. Checks the complete saved-run hash
manifest, current committed source identities, sampling chronology and label
accounting before calling the pinned statistical-analysis assumption utility.
Normality/IQR diagnostics never remove tails or choose another outcome/test.
The monthly inference remains exploratory and cannot accept profitability.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import subprocess
import warnings

import numpy as np
import pandas as pd

from yoyo.evaluation import hourly_impulse_fixed_clock_statistics as statistics

ROOT = Path(__file__).resolve().parents[2]
REL = "experiments/active/exp-btcusdtp-1h-fixed-clock-preholdout-20260907-v24"
HELPER = "scripts/diagnose_hourly_impulse_failed_confirm_v18.py"
HELPER_SHA = "5bdb0ad504ade6ba42b3fcf602587f794ecbac4a1f08f3e9211167cd9c4e6e44"
SOURCES = ["yoyo/evaluation/hourly_impulse_fixed_clock_analysis.py",
           "tests/test_hourly_impulse_fixed_clock_analysis.py",
           "yoyo/evaluation/hourly_impulse_fixed_clock_statistics.py",
           "tests/test_hourly_impulse_fixed_clock_statistics.py", HELPER]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path, value):
    with path.open("x") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n")


def diagnose(values, event_ids, normality_check):
    """All finite supplied observations in bp; NaN unknown, infinity invalid."""
    values = np.asarray(values, dtype=float)
    if len(values) != len(event_ids) or np.isinf(values).any():
        raise ValueError("Invalid distribution identities or infinite markout")
    mask = np.isfinite(values)
    x = values[mask]*10000
    if not np.isfinite(x).all():
        raise ValueError("Basis-point overflow")
    q = np.quantile(x, [0,.05,.25,.5,.75,.95,1], method="linear") if len(x) else None
    outliers = np.zeros(len(x), dtype=bool)
    if len(x):
        iqr = q[4]-q[2]
        outliers = (x < q[2]-1.5*iqr) | (x > q[4]+1.5*iqr)
    normality = {"status":"insufficient_observations","n":len(x)}
    if len(x) >= 3:
        with warnings.catch_warnings(record=True) as messages:
            warnings.simplefilter("always")
            result = normality_check(x.copy(), name="V24 fixed-clock markout (bp)", plot=False)
        if result["test"] != "Shapiro-Wilk" or result["n"] != len(x):
            raise ValueError("Assumption utility returned another population")
        w, p = float(result["statistic"]), float(result["p_value"])
        if not np.isfinite([w,p]).all() or not 0 <= p <= 1 or not 0 <= w <= 1:
            raise ValueError("Invalid Shapiro diagnostic")
        normality.update(status="constant" if np.ptp(x)==0 else "computed", W=w, p=p,
                         warnings=[str(item.message) for item in messages])
    result = {"total":len(values),"known":len(x),"unknown":int((~mask).sum()),
              "quantiles_bp":q.tolist() if q is not None else None,
              "quantile_levels":[0,.05,.25,.5,.75,.95,1],
              "mean_bp":float(x.mean()) if len(x) else None,
              "sd_bp":float(x.std(ddof=1)) if len(x)>1 else None,
              "outlier_event_ids":np.asarray(event_ids)[mask][outliers].tolist(),
              "outliers_removed":0,"normality":normality,"test_selection_changed":False}
    json.dumps(result,allow_nan=False)
    return result


def run(root=ROOT):
    e = root/REL
    directory = e/"analysis_results"
    if directory.exists():
        raise FileExistsError("Preserve prior analysis and failures")
    commit = subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True).strip()
    sources=[]
    for path in SOURCES:
        expected=hashlib.sha256(subprocess.check_output(["git","show",commit+":"+path],cwd=root)).hexdigest()
        if sha(root/path)!=expected:
            raise ValueError("Commit analysis source before running: "+path)
        sources.append({"path":path,"sha256":expected})
    directory.mkdir(exist_ok=False)
    dump(directory/"started.json",{"at":pd.Timestamp.now(tz="UTC").isoformat(),"commit":commit,"sources":sources})
    try:
        results=e/"results"
        if (results/"failure.json").exists():
            raise ValueError("A failed label run is not evidence")
        summary=json.loads((results/"summary.json").read_text())
        if summary["status"]!="labels_materialized_not_economic_acceptance" or summary["holdout_consumed"] or summary["executable_pnl"]:
            raise ValueError("Wrong label status/scope")
        for name,expected in summary["output_hashes"].items():
            if Path(name).name!=name or sha(results/name)!=expected:
                raise ValueError("Saved label run hash changed: "+name)
        for source in summary["sources"]:
            if hashlib.sha256(subprocess.check_output(["git","show",summary["builder_commit"]+":"+source["path"]],cwd=root)).hexdigest()!=source["sha256"]:
                raise ValueError("Label builder provenance changed")
        frozen=json.loads((results/"sampling_frozen.json").read_text())
        if not frozen["before_any_raw_read"] or not frozen["before_any_label"] or pd.Timestamp(frozen["at"])>pd.Timestamp(summary["generated_at"]):
            raise ValueError("Sampling checkpoint chronology failed")
        cases=pd.read_csv(results/"case_labels.csv.gz")
        pairs=pd.read_csv(results/"paired_labels.csv.gz")
        statistics._validate(cases,pairs,statistics.FOLD_BOUNDS)
        helper_path=root/HELPER
        if sha(helper_path)!=HELPER_SHA:
            raise ValueError("Pinned diagnostic helper changed")
        spec=importlib.util.spec_from_file_location("v24_assumption_helper",helper_path)
        helper=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        utility=helper.load_pinned_utility()
        c=cases.loc[cases.horizon_hours.eq(4)]
        p=pairs.loc[pairs.horizon_hours.eq(4)]
        diagnostics={"all_case":diagnose(c.cost_threshold_markout,c.event_id,utility.check_normality),
                     "paired_excess":diagnose(p.cost_threshold_excess_markout,p.event_id,utility.check_normality),
                     "utility_sha256":helper.UTILITY_SHA256,"helper_sha256":HELPER_SHA,
                     "normality_does_not_establish_independence":True}
        dump(directory/"diagnostics_before_inference.json",diagnostics)
        inference=statistics.analyze_fixed_clock_statistics(cases,pairs,statistics.FOLD_BOUNDS)
        inference.update(generated_at=pd.Timestamp.now(tz="UTC").isoformat(),source_commit=commit,
                         label_summary_sha256=sha(results/"summary.json"),sources=sources,
                         diagnostics_sha256=sha(directory/"diagnostics_before_inference.json"),raw_prices_read=False)
        dump(directory/"statistics.json",inference)
        return inference
    except Exception as exc:
        dump(directory/"failure.json",{"type":type(exc).__name__,"message":str(exc),"status":"failed_not_inference_evidence"})
        raise


if __name__=="__main__":
    result=run()
    print(json.dumps({"decision":result["decision"],"primary":result["horizons"]["4"]},allow_nan=False))
