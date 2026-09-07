"""V27 source-first background capacity for frozen288 VWMA entry mothers.

Uses only V10 saved entry-OPEN/causal support and V26 frozen reference features;
no raw archive, returns, exits or label readers. New reference-dependent gates
are rebuilt by the pure adapter. Exact V23 graph/MILP implementation is reused,
but its historical251/226 summary gate is explicitly discarded for288/260.
Maximum-capacity witnesses are not random economic samples.
Sources: pandas2.3.3 merge/null contract and SciPy1.13.1 MILP certificates:
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.DataFrame.merge.html
https://docs.scipy.org/doc/scipy-1.13.1/reference/generated/scipy.optimize.milp.html
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import pandas as pd

from yoyo.evaluation import hourly_impulse_background_support as old
from yoyo.evaluation.hourly_impulse_vwma_background import rebuild_matching

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ID = "exp-btcusdtp-1h-vwma-background-support-preholdout-20260907-v27"
E = Path("experiments/active")/EXPERIMENT_ID
V26 = Path("experiments/active/exp-btcusdtp-1h-vwma-reference-support-preholdout-20260907-v26")
SOURCES = ["yoyo/evaluation/hourly_impulse_vwma_background_research.py",
           "yoyo/evaluation/hourly_impulse_vwma_background.py",
           "tests/test_hourly_impulse_vwma_background.py",
           "tests/test_hourly_impulse_vwma_background_research.py",
           "yoyo/evaluation/hourly_impulse_background_support.py",
           "yoyo/evaluation/hourly_impulse_matching_capacity.py",
           "yoyo/evaluation/hourly_impulse_k2_matching.py",
           "yoyo/evaluation/hourly_impulse_vwma.py",
           "yoyo/evaluation/hourly_impulse_vwma_support.py",
           "yoyo/data/hourly_impulse.py",str(E/"config.json"),str(E/"PROJECT_PLAN.md")]
V26_NAMES = ["config.json","results/started.json","results/baseline_reproduced.json",
             "results/support_frozen.json","results/summary.json","audit.json"]
V26_TABLES = ["opportunities","shape_opportunities","counts","sma_entries","vwma_entries","hourly_sma","hourly_vwma"]
V26_NAMES += ["results/"+n+".csv.gz" for n in V26_TABLES]
sha,write_json,write_tables = old._sha,old._write_json,old._write_tables


def configuration(inputs, frozen_helpers):
    return {"experiment_id":EXPERIMENT_ID,"inputs":inputs,"frozen_helpers":frozen_helpers,
            "mothers":288,"fold_counts":{"2023H1":68,"2023H2":74,"2024H1":66,"2024H2":80},
            "required_complete_mothers":260,"count_per_mother":3,"matching_keys":old.KEYS,
            "folds":[list(f) for f in old.FOLDS],"embargo_hours":72,"component_time_limit_seconds":30,
            "entry_reference":"VWMA40_HL2","management_reference":"unchanged_SMA",
            "new_hourly_support":"inherited_SMA_known_AND_VWMA_side_slope_MA_known",
            "raw_support_preserved":True,"control_time_reuse":False,"fallback":False,
            "allocation_role":"capacity_witness_not_random_sample","seed":None,
            "outcomes_read_or_computed":False,"raw_price_io":False,"holdout_consumed":False,
            "training_eligible":False,"production_eligible":False}


def committed_sources(root):
    commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True).strip()
    receipt=[]
    for path in SOURCES:
        blob=subprocess.check_output(["git","show",commit+":"+path],cwd=root)
        expected=hashlib.sha256(blob).hexdigest()
        if sha(root/path)!=expected:raise ValueError("Uncommitted V27 source: "+path)
        receipt.append({"path":path,"sha256":expected})
    return commit,receipt


def verify_inputs(root,config):
    expected_names=set(old.INPUTS)|{str(V26/n) for n in V26_NAMES}
    if set(config["inputs"])!=expected_names:raise ValueError("Input allowlist changed")
    if any(config["inputs"][p]!=s for p,s in old.INPUTS.items()):raise ValueError("V10 input contract changed")
    for path,expected in {**config["inputs"],**config["frozen_helpers"]}.items():
        if sha(root/path)!=expected:raise ValueError("Frozen input/helper changed: "+path)
    receipt=old.verify_saved_inputs(root)
    def read(name):return json.loads((root/V26/name).read_text())
    started,baseline,freeze,summary,audit=[read(n) for n in
       ("results/started.json","results/baseline_reproduced.json","results/support_frozen.json","results/summary.json","audit.json")]
    if summary["status"]!="support_pass_requires_new_background" or summary["support"]["vwma"]["values"]["events"]!=288:
        raise ValueError("V26 new cohort support not passed")
    if summary["outcomes_read_or_computed"] or summary["economic_acceptance"] or audit["status"]!="passed":
        raise ValueError("V26 evidence scope changed")
    if (summary["support_frozen_sha256"]!=config["inputs"][str(V26/"results/support_frozen.json")]
        or audit["summary_sha256"]!=config["inputs"][str(V26/"results/summary.json")]
        or freeze["baseline_checkpoint_sha256"]!=config["inputs"][str(V26/"results/baseline_reproduced.json")]):
        raise ValueError("V26 audit/freeze linkage changed")
    if started["sources"]!=freeze["sources"]:raise ValueError("V26 source receipts disagree")
    if started["config_sha256"]!=config["inputs"][str(V26/"config.json")]:raise ValueError("V26 original config linkage changed")
    if not started["builder_commit"]==summary["builder_commit"]==freeze["builder_commit"]:raise ValueError("V26 builder identity changed")
    for item in started["sources"]:
        blob=old._git_bytes(root,started["builder_commit"],item["path"])
        if hashlib.sha256(blob).hexdigest()!=item["sha256"]:raise ValueError("V26 historical source changed")
    if not (old._utc_stamp(started["at"])<old._utc_stamp(baseline["at"])<old._utc_stamp(freeze["at"])<=old._utc_stamp(summary["generated_at"])):
        raise ValueError("V26 stage ordering changed")
    for name,expected in summary["output_hashes"].items():
        if freeze["output_hashes"].get(name)!=expected or config["inputs"].get(str(V26/"results"/name))!=expected:
            raise ValueError("V26 output receipt changed")
    return {"v10":receipt,"v26_builder_commit":started["builder_commit"],"v26_source_count":len(started["sources"]),
            "v26_independent_audit_sha256":config["inputs"][str(V26/"audit.json")],"saved_only":True}


def rebase_capacity_summary(summary):
    result=dict(summary)
    if result["mothers"]!=288:raise ValueError("Not the frozen288 cohort")
    if isinstance(result["maximum_matched"],bool) or not isinstance(result["maximum_matched"],int) or not 0<=result["maximum_matched"]<=288:
        raise ValueError("Invalid complete mother count")
    passed=result["maximum_matched"]>=260
    result.update(experiment_id=EXPERIMENT_ID,required_complete_mothers=260,coverage_gate_passed=passed,
                  status="background_support_passed" if passed else "background_support_insufficient",
                  legacy_251_226_gate_discarded=True,profitability_test=False,
                  limitation="New VWMA cohort offline support; no random sample, treatment causal effect or profitability evidence.")
    return result


def run(root=ROOT):
    root=Path(root);config=json.loads((root/E/"config.json").read_text())
    if config!=configuration(config["inputs"],config["frozen_helpers"]):raise ValueError("Frozen V27 config changed")
    commit,sources=committed_sources(root)
    directory=root/E/"results";directory.mkdir(exist_ok=False)
    write_json(directory/"started.json",dict(at=pd.Timestamp.now(tz="UTC"),builder_commit=commit,sources=sources,
                  config_sha256=sha(root/E/"config.json"),inputs=config["inputs"]))
    try:
        receipt=verify_inputs(root,config)
        # Timestamp-only preflight before any full saved feature materialization.
        files=[root/old.V10/"matching_frame.csv.gz",root/V26/"results/hourly_sma.csv.gz",root/V26/"results/hourly_vwma.csv.gz"]
        clocks=[]
        for path in files:
            time=old._times(pd.read_csv(path,usecols=["open_time"]).open_time,"open_time")
            if not time.is_unique or not time.is_monotonic_increasing or not time.lt(pd.Timestamp("2025-01-01T00:00:00Z")).all():
                raise ValueError("Saved time preflight failed")
            clocks.append(time)
        if not clocks[1].equals(clocks[2]):raise ValueError("Reference time identity differs")
        matching,sma,vwma=[pd.read_csv(p,low_memory=False) for p in files]
        mothers=pd.read_csv(root/V26/"results/vwma_entries.csv.gz")
        if len(mothers)!=288 or mothers.groupby("fold").size().to_dict()!=config["fold_counts"]:raise ValueError("New mother identity changed")
        rebuilt,diagnostics=rebuild_matching(mothers,matching,sma,vwma)
        graph=old.build_support_graph(mothers,rebuilt)
        hashes=write_tables(directory,graph)
        write_json(directory/"reference_rebuilt.json",diagnostics)
        write_json(directory/"support_frozen.json",dict(generated_at=pd.Timestamp.now(tz="UTC"),builder_commit=commit,
                   mothers=288,matching_edges=len(graph["eligible_edges"]),capacity_attempted=False,outcomes_read_or_computed=False,
                   input_receipt=receipt,output_hashes=hashes.copy(),sources=sources,
                   reference_rebuilt_sha256=sha(directory/"reference_rebuilt.json")))
        tables,legacy=old.allocate_support(graph)
        summary=rebase_capacity_summary(legacy)
        if verify_inputs(root,config)!=receipt or committed_sources(root)[1]!=sources:raise ValueError("Input/source drift during computation")
        hashes.update(write_tables(directory,{k:v for k,v in tables.items() if k not in graph}))
        for name,expected in hashes.items():
            if sha(directory/name)!=expected:raise ValueError("Saved output drift")
        summary.update(generated_at=str(pd.Timestamp.now(tz="UTC")),builder_commit=commit,sources=sources,
                       source_receipt=receipt,output_hashes=hashes,config_sha256=sha(root/E/"config.json"),
                       support_frozen_sha256=sha(directory/"support_frozen.json"),reference_rebuild=diagnostics)
        write_json(directory/"summary.json",summary)
        return {k:v for k,v in summary.items() if k not in ("components","sources","source_receipt","output_hashes")}
    except Exception as error:
        write_json(directory/"failure.json",dict(at=pd.Timestamp.now(tz="UTC"),status="failed_not_support_evidence",
                  error_type=type(error).__name__,message=str(error),diagnostics=getattr(error,"diagnostics",None)))
        raise


if __name__=="__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(old._json_safe(run()),ensure_ascii=False,indent=2,allow_nan=False))
