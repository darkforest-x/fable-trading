"""V28 source-first VWMA288 fixed-clock directional persistence research.

Freeze one random set of triples before raw OPEN reading, retain all288 cases,
and reuse V24's unchanged source/label/sampling contracts. New statistics is a
static specialization of V24 ONLY for288/284/260/fold counts, not a runtime
monkeypatch or exit change. 4h primary,1/12/24 descriptive; subtract20bp once.
Future OPENs are labels, never reference features. No2025+ price materialization.
Assumption diagnostics precede the same monthly bootstrap/signflip inference;
normality never chooses another test or deletes observations.
https://numpy.org/doc/2.0/reference/random/bit_generators/pcg64.html
https://numpy.org/doc/2.0/reference/random/generated/numpy.random.Generator.permutation.html
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd
import scipy

from yoyo.evaluation import hourly_impulse_fixed_clock_research as fixed
from yoyo.evaluation import hourly_impulse_fixed_clock_analysis as diagnostic
from yoyo.evaluation import hourly_impulse_vwma_background_research as background
from yoyo.evaluation import hourly_impulse_vwma_clock_statistics as statistics

ROOT=Path(__file__).resolve().parents[2]
EXPERIMENT_ID="exp-btcusdtp-1h-vwma-fixed-clock-preholdout-20260907-v28"
E=Path("experiments/active")/EXPERIMENT_ID
V27=background.E
MOTHERS=background.V26/"results/vwma_entries.csv.gz"
V27_NAMES=["config.json","results/started.json","results/support_frozen.json","results/reference_rebuilt.json",
           "results/summary.json","audit.json"]
V27_NAMES += ["results/"+n+".csv.gz" for n in ("original_mothers","matching_frame","mother_support","stage_counts","eligible_edges",
               "allocation","assignments","controls","component_capacity","fold_coverage")]
SOURCES=list(dict.fromkeys(background.SOURCES+[
    "yoyo/evaluation/hourly_impulse_vwma_clock_research.py",
    "yoyo/evaluation/hourly_impulse_vwma_clock_statistics.py",
    "tests/test_hourly_impulse_vwma_clock.py",
    "yoyo/evaluation/hourly_impulse_fixed_clock_research.py",
    "yoyo/evaluation/hourly_impulse_fixed_clock.py",
    "yoyo/evaluation/hourly_impulse_fixed_clock_statistics.py",
    "yoyo/evaluation/hourly_impulse_fixed_clock_analysis.py",diagnostic.HELPER,
    str(V27/"audit_saved.py"),str(E/"audit_saved.py"),str(E/"config.json"),str(E/"PROJECT_PLAN.md")]))
sha,write_json,write_tables=fixed.digest,fixed.write_json,fixed.write_tables


def configuration(inputs):
    config=fixed.frozen_config()
    config.update(experiment_id=EXPERIMENT_ID,inputs=inputs,
                  population={"mothers":288,"matched_mothers":284,"controls":852,"unmatched_mothers":4},
                  entry_reference="VWMA40_HL2",inference_executed_by_runner=True,
                  cohort_source=str(MOTHERS),support_source=str(V27),
                  primary_question="4h own cost threshold and own matched background excess both positive",
                  prior_data_reuse="2023/24 development exploratory,not independent validation")
    config["statistics_preregistration"].update(minimum_known_cases=260,minimum_complete_pairs=260)
    config["runtime_versions"]={"numpy":"2.0.2","pandas":"2.3.3","scipy":"1.13.1"}
    return config


def committed_sources(root):
    commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True).strip()
    sources=[]
    for path in SOURCES:
        payload=subprocess.check_output(["git","show",commit+":"+path],cwd=root)
        expected=hashlib.sha256(payload).hexdigest()
        if sha(root/path)!=expected:raise ValueError("Commit V28 source first: "+path)
        sources.append({"path":path,"sha256":expected})
    return commit,sources


def verify_inputs(root,config):
    names={str(V27/n) for n in V27_NAMES}|{str(MOTHERS),str(fixed.BASE),str(fixed.AUDIT)}
    if set(config["inputs"])!=names:raise ValueError("V28 input allowlist changed")
    for path,expected in config["inputs"].items():
        if sha(root/path)!=expected:raise ValueError("V28 saved input changed: "+path)
    def read(n):return json.loads((root/V27/n).read_text())
    started,freeze,summary,audit=[read(n) for n in ("results/started.json","results/support_frozen.json","results/summary.json","audit.json")]
    if summary["status"]!="background_support_passed" or summary["mothers"]!=288 or summary["maximum_matched"]!=284 or summary["controls"]!=852:
        raise ValueError("V27 frozen population changed")
    if summary["outcomes_read_or_computed"] or freeze["outcomes_read_or_computed"] or freeze["capacity_attempted"]:
        raise ValueError("V27 support scope changed")
    if audit["status"]!="passed" or audit["summary_sha256"]!=sha(root/V27/"results/summary.json"):
        raise ValueError("V27 independent audit has not passed")
    if summary["support_frozen_sha256"]!=sha(root/V27/"results/support_frozen.json"):
        raise ValueError("V27 freeze linkage changed")
    if not started["sources"]==freeze["sources"]==summary["sources"]:raise ValueError("V27 source receipts disagree")
    if not started["builder_commit"]==freeze["builder_commit"]==summary["builder_commit"]:raise ValueError("V27 builder identity mismatch")
    if not fixed.stamp(started["at"])<fixed.stamp(freeze["generated_at"])<=fixed.stamp(summary["generated_at"]):
        raise ValueError("V27 source chronology invalid")
    for item in started["sources"]:
        blob=subprocess.check_output(["git","show",started["builder_commit"]+":"+item["path"]],cwd=root)
        if hashlib.sha256(blob).hexdigest()!=item["sha256"]:raise ValueError("V27 historical source mismatch")
    for name,expected in summary["output_hashes"].items():
        if config["inputs"].get(str(V27/"results"/name))!=expected:raise ValueError("V27 output manifest mismatch")
    for name,expected in freeze["output_hashes"].items():
        if summary["output_hashes"].get(name)!=expected:raise ValueError("V27 graph changed after freeze")
    parent_config=read("config.json")
    lineage=background.verify_inputs(root,parent_config)
    if lineage!=summary["source_receipt"] or lineage!=freeze["input_receipt"]:raise ValueError("V27 lineage drift")
    if json.loads((root/fixed.BASE).read_text())["source"]!=fixed.SOURCE:raise ValueError("Raw source changed")
    return {"inputs":config["inputs"],"support_summary_sha256":sha(root/V27/"results/summary.json"),
            "support_audit_sha256":sha(root/V27/"audit.json"),"parent_builder_commit":started["builder_commit"],
            "parent_sources":len(started["sources"]),"parent_support_only":True}


def run(root=ROOT):
    root=Path(root);config=json.loads((root/E/"config.json").read_text())
    if config!=configuration(config["inputs"]):raise ValueError("Frozen V28 config changed")
    if {"numpy":np.__version__,"pandas":pd.__version__,"scipy":scipy.__version__}!=config["runtime_versions"]:
        raise ValueError("Pinned numeric runtime changed")
    commit,sources=committed_sources(root)
    directory=root/E/"results";directory.mkdir(exist_ok=False)
    write_json(directory/"started.json",dict(at=pd.Timestamp.now(tz="UTC"),builder_commit=commit,sources=sources,
                  inputs=config["inputs"],config_sha256=sha(root/E/"config.json")))
    try:
        receipt=verify_inputs(root,config)
        mothers=pd.read_csv(root/V27/"results/original_mothers.csv.gz")
        original=pd.read_csv(root/MOTHERS)
        pd.testing.assert_frame_equal(mothers,original,rtol=1e-12,atol=1e-12)
        if len(mothers)!=288 or mothers.groupby("fold").size().to_dict()!=statistics._FOLD_COUNTS:raise ValueError("VWMA membership changed")
        edges=pd.read_csv(root/V27/"results/eligible_edges.csv.gz")
        support=pd.read_csv(root/V27/"results/mother_support.csv.gz")
        tables,sampling=fixed.sample_controls(mothers,edges,support)
        if sampling["matched_mothers"]!=284 or sampling["controls"]!=852 or len(edges)!=15493:
            raise ValueError("New sampling support differs from freeze")
        hashes=write_tables(directory,tables)
        write_json(directory/"sampling_frozen.json",dict(at=pd.Timestamp.now(tz="UTC"),sampling=sampling,
                   input_receipt=receipt,output_hashes=hashes.copy(),before_any_raw_read=True,before_any_label=True,sources=sources))
        raw,source_receipt=fixed.load_open_source(root,fixed.SOURCE,config["inputs"][str(fixed.AUDIT)])
        write_json(directory/"source_receipt.json",source_receipt)
        hashes.update(write_tables(directory,{"open_prefix":raw}))
        cases=fixed.label_requests(raw,tables["case_requests"])
        controls=fixed.label_requests(raw,tables["control_requests"])
        pairs=fixed.paired_labels(cases,controls)
        if len(cases)!=1152 or len(controls)!=3408 or len(pairs)!=1152:raise ValueError("Label denominator changed")
        statistics._validate(cases,pairs,statistics.FOLD_BOUNDS)
        hashes.update(write_tables(directory,{"case_labels":cases,"control_labels":controls,"paired_labels":pairs}))
        # Pin and call the actual skill utility. Failure is recorded, no fallback.
        path=root/diagnostic.HELPER
        if sha(path)!=diagnostic.HELPER_SHA:raise ValueError("Assumption helper drift")
        spec=importlib.util.spec_from_file_location("v28_assumption",path)
        helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
        utility=helper.load_pinned_utility()
        c=cases.loc[cases.horizon_hours.eq(4)];p=pairs.loc[pairs.horizon_hours.eq(4)]
        distributions={"all_case":diagnostic.diagnose(c.cost_threshold_markout,c.event_id,utility.check_normality),
                       "paired_excess":diagnostic.diagnose(p.cost_threshold_excess_markout,p.event_id,utility.check_normality),
                       "helper_sha256":diagnostic.HELPER_SHA,"utility_sha256":helper.UTILITY_SHA256,
                       "at":pd.Timestamp.now(tz="UTC"),"normality_does_not_establish_independence":True}
        write_json(directory/"diagnostics_before_inference.json",distributions)
        inference=statistics.analyze_fixed_clock_statistics(cases,pairs,statistics.FOLD_BOUNDS)
        inference.update(generated_at=pd.Timestamp.now(tz="UTC"),source_commit=commit,
                         diagnostics_sha256=sha(directory/"diagnostics_before_inference.json"))
        write_json(directory/"statistics.json",inference)
        if verify_inputs(root,config)!=receipt or committed_sources(root)[1]!=sources:raise ValueError("Inputs/sources changed during run")
        for name,expected in hashes.items():
            if sha(directory/name)!=expected:raise ValueError("Saved label output drift")
        summary=dict(experiment_id=EXPERIMENT_ID,status=inference["decision"]["status"],mothers=288,matched_mothers=284,
                     controls=852,unmatched_mothers=4,labels={"case":len(cases),"control":len(controls),"paired":len(pairs)},
                     descriptive=fixed.descriptive_summary(cases,controls,pairs),decision=inference["decision"],
                     sampling=sampling,output_hashes=hashes,input_receipt=receipt,source_receipt=source_receipt,
                     sources=sources,builder_commit=commit,generated_at=pd.Timestamp.now(tz="UTC"),inference_executed=True,
                     statistics_sha256=sha(directory/"statistics.json"),sampling_frozen_sha256=sha(directory/"sampling_frozen.json"),
                     config_sha256=sha(root/E/"config.json"),executable_pnl=False,holdout_consumed=False,
                     production_eligible=False,training_eligible=False,independent_validation=False,
                     limitation="Exploratory2023/24 OPEN markouts,not stop-managed executable PnL;random controls are not randomized treatment.")
        write_json(directory/"summary.json",summary)
        return {k:summary[k] for k in ("experiment_id","status","mothers","matched_mothers","controls","labels","descriptive","decision")}
    except Exception as error:
        write_json(directory/"failure.json",dict(at=pd.Timestamp.now(tz="UTC"),status="failed_not_economic_evidence",
                   error_type=type(error).__name__,message=str(error),sampling_frozen=(directory/"sampling_frozen.json").exists()))
        raise


if __name__=="__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(fixed.clean(run()),ensure_ascii=False,indent=2,allow_nan=False))
