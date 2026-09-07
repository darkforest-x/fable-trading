"""Source-first V29 support study: frozen ChartPrime default numeric state.

V25 lineage/identity/phase helpers are reused with pinned source hashes.
Read only original identities, frozen V24 sampling metadata and V20 saved OHLC.
No outcome files, raw prices, new samples,2025+ prices or parameter search.
Timestamp-only preflight precedes saved OHLC materialization, and input/source
hashes are checked before and after the calculation. Output directory is new.
Official pandas usecols contract:
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.read_csv.html
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import hourly_impulse_structure_event_research as parent
from yoyo.evaluation.hourly_impulse_classifier_support import OHLC, build_support

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ID = "exp-btcusdtp-1h-classifier-support-preholdout-20260907-v29"
EXPERIMENT = Path("experiments/active") / EXPERIMENT_ID
PINE = "experiments/active/exp-chartprime-public-confluence-audit-20260906-v1/sources/AtJtdaDe.pine"
PINS = {PINE: "0e425fb43caeda0638bb671ee8e944f61844205833fb52f0dcdd8f8728ebbddd",
        "yoyo/evaluation/hourly_impulse_structure_event_support.py": "2d62af2f4b837bf714754d5dce13242b9a1e8a53a31014d23818417d11796024",
        "yoyo/evaluation/hourly_impulse_structure_event_research.py": "d605300c116af22778e3d6a6f1402d16b0249701f18baf64fc7324ef9f765f64"}
SOURCES = ["yoyo/evaluation/hourly_impulse_classifier_support.py",
           "yoyo/evaluation/hourly_impulse_classifier_research.py",
           "tests/test_hourly_impulse_classifier_support.py", *PINS, *parent.FEATURES,
           str(EXPERIMENT / "config.json"), str(EXPERIMENT / "PROJECT_PLAN.md")]


def frozen_config():
    base = parent.frozen_config()
    return {**base, "experiment_id": EXPERIMENT_ID,
            "gate": "completed_K1_Trend_Classifier_label_equals_direction",
            "source_pins": PINS, "parameters": {"center_length": 10, "range_length": 100,
            "band_multiple": 1, "ema_adjust": False, "ema_seed": "first_value_per_hourly_segment",
            "short_slope": "less_than_or_equal", "band_boundary": "strict",
            "visual_offset_used": False, "warmup_unknown": True},
            "new_allocation": False, "reference_is_original_SMA251": True}


def committed_sources(root):
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    sources = []
    for path in SOURCES:
        sha = hashlib.sha256(subprocess.check_output(["git", "show", commit+":"+path], cwd=root)).hexdigest()
        if parent.digest(root/path) != sha:
            raise ValueError("Commit source first: "+path)
        sources.append(dict(path=path, sha256=sha))
    return commit, sources


def verify_inputs(root):
    for path, sha in PINS.items():
        if parent.digest(root/path) != sha:
            raise ValueError("Pinned source drift: "+path)
    return {**parent.verify_inputs(root), "classifier_sources": PINS}


def run(root=ROOT):
    root = Path(root)
    config = json.loads((root/EXPERIMENT/"config.json").read_text())
    if config != frozen_config():
        raise ValueError("V29 frozen config changed")
    if np.__version__ != "2.0.2" or pd.__version__ != "2.3.3":
        raise ValueError("Numeric version contract differs")
    commit, sources = committed_sources(root)
    directory = root/EXPERIMENT/"results"
    directory.mkdir(exist_ok=False)
    parent.write_json(directory/"started.json", dict(at=pd.Timestamp.now(tz="UTC"), builder_commit=commit,
                      sources=sources, inputs=parent.INPUTS, source_pins=PINS,
                      config_sha256=parent.digest(root/EXPERIMENT/"config.json")))
    try:
        receipt = verify_inputs(root)
        path = root/parent.V20/"hourly_trace.csv.gz"
        times = parent.preflight_trace(pd.read_csv(path, usecols=["open_time"]), config)
        hourly = pd.read_csv(path, usecols=OHLC)
        if not pd.DatetimeIndex(hourly.open_time.map(parent._time)).equals(times):
            raise ValueError("Clock changed after preflight")
        cases, controls, assignments, allocation = [pd.read_csv(root/parent.V24/name) for name in
            ("case_requests.csv.gz", "control_requests.csv.gz", "random_assignments.csv.gz", "random_allocation.csv.gz")]
        original = pd.read_csv(root/parent.MOTHERS, usecols=parent.IDENTITY)
        parent.validate_requests(cases, controls, original)
        tables, summary = build_support(cases, controls, hourly, assignments, allocation)
        if verify_inputs(root) != receipt or committed_sources(root)[1] != sources:
            raise ValueError("Inputs/sources changed during support run")
        hashes = {}
        for name, table in tables.items():
            path = directory/(name+".csv.gz")
            table.to_csv(path, index=False, compression={"method":"gzip", "mtime":0})
            hashes[path.name] = parent.digest(path)
        parent.write_json(directory/"support_frozen.json", dict(at=pd.Timestamp.now(tz="UTC"), builder_commit=commit,
            sources=sources, input_receipt=receipt, output_hashes=hashes, timestamp_preflight_before_prices=True,
            outcomes_read_or_computed=False, raw5_read=False, holdout_consumed=False))
        summary.update(experiment_id=EXPERIMENT_ID, generated_at=str(pd.Timestamp.now(tz="UTC")),
                       builder_commit=commit, output_hashes=hashes,
                       support_frozen_sha256=parent.digest(directory/"support_frozen.json"))
        parent.write_json(directory/"summary.json", summary)
        return summary
    except Exception as error:
        parent.write_json(directory/"failure.json", dict(at=pd.Timestamp.now(tz="UTC"),
            status="failed_not_evidence", error_type=type(error).__name__, message=str(error)))
        raise


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(run(), ensure_ascii=False, indent=2))
