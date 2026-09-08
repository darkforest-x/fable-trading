"""Finalize completed expanded shards with explicit NumPy scalar conversion.

The frozen runner's grouped_stats produces numpy.int64 for multicolumn group
keys; Python json.dumps rejects those keys' values. This reproducible output
bug does not affect any candle, inference, proposal, decision or shard receipt.
This separate, committed finalizer preserves the original source manifest and
reads only all 216 completed hashed shards and four saved combined ledgers.
It never loads a model, reads source OHLCV, changes gate rules or reruns inference.
The same four predeclared direction permutations are computed from the saved
decisions. Their recomputation is not an additional market/model evaluation.
"""
from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from . import imacd_yolo_expanded as ex


def native_scalar(value):
    """Convert numeric NumPy scalars only; do not silently stringify objects."""
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def run():
    root, exp, data = ex.ROOT, ex.EXP, ex.DATA
    source = str(Path(__file__).relative_to(root))
    subprocess.run(["git", "ls-files", "--error-unmatch", source], cwd=root,
                   check=True, stdout=subprocess.DEVNULL)
    if subprocess.check_output(["git", "status", "--porcelain", "--", source], cwd=root, text=True).strip():
        raise ValueError("commit finalizer before writing output")
    results = exp/"results"
    summary_path = results/"summary.json"
    if summary_path.exists():
        raise ValueError("summary already exists; do not overwrite completed evidence")
    started = json.loads((results/"run_started.json").read_text())
    manifest_path = exp/"source_manifest.json"
    source_sha = ex.digest(manifest_path)
    if started["source_manifest_sha256"] != source_sha:
        raise ValueError("source manifest differs from evaluation")
    for path, expected in json.loads(manifest_path.read_text())["files"].items():
        if ex.digest(root/path) != expected:
            raise ValueError(f"changed frozen source: {path}")
    universe = json.loads((exp/"universe.json").read_text())
    progress = json.loads((results/"progress.json").read_text())
    if not progress["completed_groups"] == progress["total_groups"] == 216:
        raise ValueError("all 216 groups must have completed")
    if len(universe["sources"]) != 54:
        raise ValueError("incomplete universe")
    receipts = {}
    for source_record in universe["sources"]:
        for minutes in ex.PERIODS:
            for fold in ex.FOLDS:
                key = f"{source_record['symbol']}_{minutes}_{fold}"
                receipt_path = data/f"{key}_receipt.json"
                receipt = json.loads(receipt_path.read_text())
                identity = dict(source_manifest_sha256=source_sha,
                    raw_bounded_ohlcv_sha256=receipt["input"]["raw_bounded_ohlcv_sha256"], key=key)
                receipts[key] = ex.load_completed(key, identity)
                if receipts[key] is None or not receipts[key]["validation"]["passed"]:
                    raise ValueError(f"missing or unvalidated shard: {key}")
    for suffix, combined_name in (("candidates.csv","candidates.csv"),
            ("decisions.csv","decisions.csv"), ("proposals.csv.gz","proposals.csv.gz"),
            ("trace.csv.gz","traces.csv.gz")):
        parts = pd.concat([pd.read_csv(data/f"{key}_{suffix}") for key in receipts], ignore_index=True)
        combined = pd.read_csv(data/combined_name)
        pd.testing.assert_frame_equal(parts, combined, check_dtype=False, rtol=1e-12, atol=1e-12)
    decisions = pd.read_csv(data/"decisions.csv")
    decisions["month"] = pd.to_datetime(decisions.signal_available_at, utc=True).dt.strftime("%Y-%m")
    summary = dict(**started, model_sha256=ex.frozen.MODEL_SHA256, max_wait_bars=9,
        symbol_count=54, symbols=[s["symbol"] for s in universe["sources"]],
        inputs={k:r["input"] for k,r in receipts.items()}, table=[r["table"] for r in receipts.values()],
        by_timeframe=ex.grouped_stats(decisions,["timeframe_min"]),
        by_fold_timeframe=ex.grouped_stats(decisions,["fold","timeframe_min"]),
        by_month_timeframe=ex.grouped_stats(decisions,["month","timeframe_min"]),
        by_direction_timeframe=ex.grouped_stats(decisions,["side","timeframe_min"]),
        direction_null=ex.direction_nulls(decisions),
        validation={k:r["validation"] for k,r in receipts.items()},
        versions={p:importlib.metadata.version(p) for p in ("torch","ultralytics","numpy","pandas")},
        files={str(p.relative_to(root)):ex.digest(p) for p in sorted(data.glob("*")) if p.is_file()},
        economic_evaluation=False, training_eligible=False, production_eligible=False,
        completed_at=pd.Timestamp.now(tz="UTC").isoformat(),
        finalizer_source=source, finalizer_source_sha256=ex.digest(Path(__file__)),
        finalizer_commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True).strip(),
        output_fix="Explicit NumPy scalar conversion only; all market inference and saved shards unchanged.")
    serialized = json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False, default=native_scalar)
    with summary_path.open("x") as handle:
        handle.write(serialized+"\n")
    print(json.dumps(dict(by_timeframe=summary["by_timeframe"],direction_null=summary["direction_null"]), default=native_scalar))


if __name__ == "__main__":
    run()
