"""Source-first V26 SMA40 versus VWMA40 saved-hourly entry support only.

Only10 SHA-frozen input artifacts are permitted, no outcomes/raw5/2025+.
Timestamp-only preflight precedes saved OHLCV materialization. Old SMA feature
and all251-entry parity must pass and freeze BEFORE VWMA is calculated.
The full admissible decision grid is independent of previously accepted cases.
This CLI always stops after support; it cannot assign controls or read labels.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from yoyo.data.hourly_impulse import BAR_COLUMNS, add_features
from yoyo.evaluation.hourly_impulse_vwma import add_reference_features
from yoyo.evaluation.hourly_impulse_vwma_support import (
    PARAMS, DEFAULT_FOLDS, _time, opportunity_grid, accepted_entries, assert_original_parity, build_support,
)
from yoyo.evaluation import hourly_impulse_structure_event_research as prior

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ID = "exp-btcusdtp-1h-vwma-reference-support-preholdout-20260907-v26"
EXPERIMENT = Path("experiments/active") / EXPERIMENT_ID
V20, MOTHERS = prior.V20, prior.MOTHERS
V4 = MOTHERS.parent.parent
BASE = Path("experiments/active/exp-btcusdtp-1h-impulse-ltf-exit-preholdout-20260906-v1/config.json")
INPUTS = {**{p:s for p,s in prior.INPUTS.items() if p.startswith(str(V20)+"/")},
          str(MOTHERS):prior.INPUTS[str(MOTHERS)],
          str(V4/"results/started.json"):"e5e061bf6b388252d931a5c78bac3c20aa74db0af2507758a1dfd8bd784740e0",
          str(V4/"config.json"):"a5ab44feb152afc8a89dd659d7ef4d4af198e9120cb7e012597207df3495c3c7",
          str(BASE):"95e82bd2c57d1c2aa5c8c972a07635d1d9960de4a47aa6197bd6d3cf8473733a"}
SOURCES = ["yoyo/evaluation/hourly_impulse_vwma_research.py", "yoyo/evaluation/hourly_impulse_vwma_support.py",
           "yoyo/evaluation/hourly_impulse_vwma.py", "tests/test_hourly_impulse_vwma.py",
           "tests/test_hourly_impulse_vwma_support.py", "tests/test_hourly_impulse_vwma_research.py",
           "yoyo/evaluation/hourly_impulse_structure_event_research.py",
           "yoyo/evaluation/hourly_impulse_structure_event_support.py", *prior.FEATURES,
           str(EXPERIMENT/"config.json"),str(EXPERIMENT/"PROJECT_PLAN.md")]
digest, write_json = prior.digest, prior.write_json


def frozen_config():
    return {"experiment_id":EXPERIMENT_ID,"inputs":INPUTS,"frozen_feature_sources":prior.FEATURES,
            "baseline":{"ma_kind":"SMA","ma_length":40,**PARAMS},
            "candidate":{"ma_kind":"VWMA","ma_length":40,"source":"HL2","current_closed_volume_included":True},
            "folds":[list(f) for f in DEFAULT_FOLDS],"embargo_hours":72,
            "trace_rows":18222,"trace_first_open":"2022-11-30T16:00:00Z","trace_last_open":"2024-12-28T22:00:00Z",
            "phase_end_exclusive":"2025-01-01T00:00:00Z","expected_original_events":251,
            "support":{"events":80,"minimum_per_fold":12,"active_months":12,"minimum_months_per_fold":3},
            "unchanged_execution":{"cost_fraction":.002,"max_hours":72,"stop":"K1_extreme","exit_reference":"SMA"},
            "outcomes_read_or_computed":False,"random_controls_assigned":False,"holdout_consumed":False,
            "training_eligible":False,"production_eligible":False}


def sources_committed(root):
    commit = prior._git(root,"rev-parse","HEAD").decode().strip()
    receipt = []
    for path in SOURCES:
        sha = hashlib.sha256(prior._git(root,"show",commit+":"+path)).hexdigest()
        if digest(root/path) != sha:
            raise ValueError("Uncommitted V26 source: "+path)
        receipt.append({"path":path,"sha256":sha})
    return commit,receipt


def verify_inputs(root):
    for path,sha in {**INPUTS,**prior.FEATURES}.items():
        if digest(root/path) != sha:
            raise ValueError("Frozen input/source changed: "+path)
    def read(path):
        return json.loads((root/path).read_text())
    base, config4 = read(BASE),read(V4/"config.json")
    if base["baseline"] != frozen_config()["baseline"] or config4["base_config_sha256"] != INPUTS[str(BASE)]:
        raise ValueError("Original baseline parameter identity failed")
    if base["execution"]["cost_fraction"] != .002 or base["execution"]["max_hours"] != 72:
        raise ValueError("Original cost/deadline changed")
    if base["development_folds"] != [[f,s[:10],e[:10]] for f,s,e in DEFAULT_FOLDS]:
        raise ValueError("Original development folds changed")
    old, freeze, first, failure, resumed = [read(V20/n) for n in
                ("started.json","context_frozen.json","outcomes_started.json","failure.json","outcomes_resumed_1.json")]
    times = [prior._stamp(m["at"]) for m in (old,freeze,first,failure,resumed)]
    if not all(a < b for a,b in zip(times,times[1:])) or freeze["outcomes_read"] is not False:
        raise ValueError("V20 feature/failure chronology failed")
    if failure["status"] != "failed_not_evidence" or failure["error_type"] != "AssertionError":
        raise ValueError("V20 failure identity changed")
    if any(m["context_frozen_sha256"] != INPUTS[str(V20/"context_frozen.json")] for m in (first,resumed)):
        raise ValueError("V20 recovered feature identity changed")
    if freeze["output_hashes"]["hourly_trace.csv.gz"] != INPUTS[str(V20/"hourly_trace.csv.gz")]:
        raise ValueError("V20 trace freeze changed")
    receipt = freeze["source_receipt"]
    if receipt["holdout_price_rows"] != 0 or prior._stamp(receipt["phase_price_last_open"]) >= _time(frozen_config()["phase_end_exclusive"]):
        raise ValueError("Saved trace provenance reaches forbidden phase")
    old4 = read(V4/"results/started.json")
    return {"inputs":INPUTS,"parent_commits":[old["builder_commit"],old4["builder_commit"]],
            "parent_sources_verified":[prior._source_receipt(root,old),prior._source_receipt(root,old4)],
            "same_feature_recovery":True,"raw5_read":False,"outcomes_read":False}


def timestamp_preflight(frame, config):
    times = pd.DatetimeIndex([_time(v) for v in frame.open_time])
    if len(times) != config["trace_rows"] or not times.is_unique or not times.is_monotonic_increasing:
        raise ValueError("Trace timestamp count/order/uniqueness failed")
    if len(times)==0 or times[0] != _time(config["trace_first_open"]) or times[-1] != _time(config["trace_last_open"]) or times[-1] >= _time(config["phase_end_exclusive"]):
        raise ValueError("Trace phase failed")
    grid = opportunity_grid()
    if times[0] > grid.signal_time.min() or times[-1] < grid.signal_time.max():
        raise ValueError("Saved trace outer range cannot cover NEW opportunity universe")
    return times


def old_arm(hourly, original):
    sma = add_reference_features(hourly,"SMA")
    legacy = add_features(hourly.assign(segment_id=sma.segment_id.to_numpy()),"SMA",40)
    pd.testing.assert_frame_equal(sma[legacy.columns],legacy,check_dtype=False,rtol=1e-12,atol=1e-12)
    entries = accepted_entries(sma)
    assert_original_parity(entries,original)
    if len(entries)!=251 or entries.groupby("fold").size().to_dict()!={"2023H1":55,"2023H2":66,"2024H1":55,"2024H2":75} or entries.groupby("direction").size().to_dict()!={-1:121,1:130}:
        raise ValueError("Original251 population failed")
    return sma,entries


def run(root=ROOT):
    root=Path(root)
    config=json.loads((root/EXPERIMENT/"config.json").read_text())
    if config != frozen_config():
        raise ValueError("Frozen V26 config changed")
    commit,sources=sources_committed(root)
    directory=root/EXPERIMENT/"results"
    directory.mkdir(exist_ok=False)
    write_json(directory/"started.json",{"at":pd.Timestamp.now(tz="UTC"),"builder_commit":commit,
               "sources":sources,"inputs":INPUTS,"config_sha256":digest(root/EXPERIMENT/"config.json")})
    try:
        receipt=verify_inputs(root)
        path=root/V20/"hourly_trace.csv.gz"
        times=timestamp_preflight(pd.read_csv(path,usecols=["open_time"]),config)
        hourly=pd.read_csv(path,usecols=BAR_COLUMNS+["segment_id"])
        if not pd.DatetimeIndex([_time(v) for v in hourly.open_time]).equals(times):
            raise ValueError("Trace clocks changed after preflight")
        original=pd.read_csv(root/MOTHERS)
        sma,entries=old_arm(hourly,original)
        write_json(directory/"baseline_reproduced.json",{"at":pd.Timestamp.now(tz="UTC"),"events":251,
                   "all_entry_columns_parity":True,"all_feature_columns_parity":True,
                   "before_vwma_computation":True,"inputs":INPUTS})
        vwma=add_reference_features(hourly,"VWMA")
        new_entries=accepted_entries(vwma)
        tables,summary=build_support(sma,vwma,entries,new_entries)
        tables.update(hourly_sma=sma,hourly_vwma=vwma)
        if verify_inputs(root)!=receipt or sources_committed(root)[1]!=sources:
            raise ValueError("Source/input receipts changed during run")
        hashes={}
        for name,frame in tables.items():
            target=directory/(name+".csv.gz")
            frame.to_csv(target,index=False,compression={"method":"gzip","mtime":0})
            hashes[target.name]=digest(target)
        write_json(directory/"support_frozen.json",{"at":pd.Timestamp.now(tz="UTC"),"builder_commit":commit,
                   "sources":sources,"input_receipt":receipt,"output_hashes":hashes,
                   "baseline_checkpoint_sha256":digest(directory/"baseline_reproduced.json"),
                   "timestamp_preflight_before_prices":True,"outcomes_read_or_computed":False})
        summary.update(generated_at=str(pd.Timestamp.now(tz="UTC")),experiment_id=EXPERIMENT_ID,
                       builder_commit=commit,output_hashes=hashes,
                       support_frozen_sha256=digest(directory/"support_frozen.json"))
        write_json(directory/"summary.json",summary)
        return summary
    except Exception as error:
        write_json(directory/"failure.json",{"at":pd.Timestamp.now(tz="UTC"),"status":"failed_not_evidence",
                   "error_type":type(error).__name__,"message":str(error)})
        raise


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(run(),ensure_ascii=False,indent=2))
