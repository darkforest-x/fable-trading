#!/usr/bin/env python3
"""Leakage-resistant v2 of the 128-bar L1.5 + L2 pipeline experiment.

The v1 attempt proved that post-core confirmation candles reconstruct the
existing Grade-A launch/no-launch weak label almost perfectly.  V2 therefore
changes one L1.5 input boundary: all global-shape features end at the mapped
core, before every confirmation candle.  L1 signal time, candidate pool,
LONG/SHORT split, L2 features, outcome, cost, controls and economic splits stay
unchanged.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import scripts.research_15m_ma_launch_l15_l2_pipeline as base
from yoyo.layers.l2_judgment.global_shape import (
    GLOBAL_CONTEXT_BARS,
    GLOBAL_PRECORE_FEATURE_COLUMNS,
    add_global_shape_indicators,
    extract_precore_global_shape_features,
)


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-l15-precore-global-shape-l2-side-split-v2"
EXPERIMENT_DIR = ROOT / "experiments" / "active" / EXPERIMENT_ID
PREREG_PATH = EXPERIMENT_DIR / "preregistration.json"
RESULTS_DIR = EXPERIMENT_DIR / "results"
OUTPUT_DIR = ROOT / "analysis" / "output" / "ma_launch_l15_precore_l2_pipeline_v2"


def _configure_base() -> None:
    base.EXPERIMENT_ID = EXPERIMENT_ID
    base.EXPERIMENT_DIR = EXPERIMENT_DIR
    base.PREREG_PATH = PREREG_PATH
    base.RESULTS_DIR = RESULTS_DIR
    base.OUTPUT_DIR = OUTPUT_DIR
    base.GLOBAL_SHAPE_FEATURE_COLUMNS = GLOBAL_PRECORE_FEATURE_COLUMNS


def load_preregistration() -> dict[str, Any]:
    prereg = base.read_json(PREREG_PATH)
    if prereg.get("experiment_id") != EXPERIMENT_ID:
        raise base.PipelineError("v2 experiment id drift")
    if int(prereg["l15"]["context_bars"]) != GLOBAL_CONTEXT_BARS:
        raise base.PipelineError("v2 L1.5 context drift")
    if tuple(prereg["l15"]["feature_columns"]) != GLOBAL_PRECORE_FEATURE_COLUMNS:
        raise base.PipelineError("v2 L1.5 feature contract drift")
    if tuple(prereg["l2"]["feature_columns"]) != base.L2_REDUCED_FEATURES:
        raise base.PipelineError("v2 L2 feature contract drift")
    if prereg["safety"]["holdout_read"]:
        raise base.PipelineError("holdout read must remain false")
    for key in ("source_manifest", "candidate_dataset", "matched_controls"):
        spec = prereg["inputs"][key]
        path = base.repo_path(spec["path"])
        if not path.is_file() or base.sha256_file(path) != spec["sha256"]:
            raise base.PipelineError(f"immutable input mismatch: {key}")
    return prereg


def _split_for_core(row: Mapping[str, Any], prereg: Mapping[str, Any]) -> str:
    if str(row["split"]) == "val":
        return "final_validation"
    core_end = base.utc(row["core_end_time"])
    cutoff = base.utc(prereg["l15"]["train_tune_cutoff"])
    purge = pd.Timedelta(hours=float(prereg["l15"]["purge_hours_each_side"]))
    if core_end < cutoff - purge:
        return "train"
    if core_end >= cutoff + purge:
        return "tune"
    return "purge"


def _canonical_events(manifest_path: Path) -> list[dict[str, Any]]:
    events: dict[tuple[str, str], dict[str, Any]] = {}
    with manifest_path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row["sample_kind"] == "negative" and row["negative_kind"] != "hard":
                continue
            event_id = str(row["event_id"] if row["sample_kind"] == "positive" else row["negative_event_id"])
            key = (str(row["sample_kind"]), event_id)
            current = events.get(key)
            if current is None or int(row["post_bars"]) < int(current["post_bars"]):
                events[key] = row
    return list(events.values())


def build_l15_dataset(prereg: Mapping[str, Any]) -> dict[str, Any]:
    terminal = RESULTS_DIR / "l15_dataset_receipt.json"
    if terminal.exists():
        return base.read_json(terminal)
    manifest_path = base.repo_path(prereg["inputs"]["source_manifest"]["path"])
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _canonical_events(manifest_path):
        grouped[str(row["source_path"])].append(row)
    output_rows: list[dict[str, Any]] = []
    for source_name in sorted(grouped):
        enriched = add_global_shape_indicators(base.normalize_ohlcv(base.repo_path(source_name)))
        for row in grouped[source_name]:
            positive = row["sample_kind"] == "positive"
            side = str(row["direction"] if positive else row["paired_direction"]).lower()
            event_id = str(row["event_id"] if positive else row["negative_event_id"])
            core_end_i = int(row["source_core_end_i"] if positive else row["core_end_i"])
            output_rows.append(
                {
                    "event_id": event_id,
                    "paired_positive_event_id": str(row.get("paired_positive_event_id") or event_id),
                    "sample_kind": "positive" if positive else "hard_negative",
                    "label": int(positive),
                    "side": side,
                    "symbol": str(row["symbol"]),
                    "source_path": source_name,
                    "core_end_i": core_end_i,
                    "decision_time": base.utc(row["core_end_time"]).isoformat(),
                    "core_end_time": base.utc(row["core_end_time"]).isoformat(),
                    "manifest_split": str(row["split"]),
                    "split": _split_for_core(row, prereg),
                    **extract_precore_global_shape_features(enriched, core_end_i=core_end_i, side=side),
                }
            )
    data = pd.DataFrame(output_rows)
    if len(data) != int(prereg["l15"]["expected_event_rows"]):
        raise base.PipelineError(f"unexpected v2 L1.5 row count {len(data)}")
    if base.utc(data["decision_time"].max()) >= base.HOLDOUT_START:
        raise base.PipelineError("v2 L1.5 dataset reaches holdout")
    if int((data.groupby("event_id")["split"].nunique() > 1).sum()) != 0:
        raise base.PipelineError("v2 event crosses splits")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    dataset_path = OUTPUT_DIR / "l15_precore_dataset.csv"
    data.to_csv(dataset_path, index=False)
    payload = {
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "builder_commit": base.git_head(),
        "dataset_path": base.repo_relative(dataset_path),
        "dataset_sha256": base.sha256_file(dataset_path),
        "rows": len(data),
        "events": int(data["event_id"].nunique()),
        "counts": data.groupby(["split", "side", "sample_kind"]).size().rename("rows").reset_index().to_dict("records"),
        "feature_columns": list(GLOBAL_PRECORE_FEATURE_COLUMNS),
        "post_core_bars_visible_to_l15": 0,
        "event_cross_split_failures": 0,
        "max_core_end_time": str(data["decision_time"].max()),
        "holdout_consumed": False,
    }
    base.write_json(terminal, payload)
    return payload


def apply_l15_to_candidates(prereg: Mapping[str, Any]) -> dict[str, Any]:
    terminal = RESULTS_DIR / "candidate_l15_receipt.json"
    if terminal.exists():
        return base.read_json(terminal)
    import lightgbm as lgb

    data = pd.read_csv(base.repo_path(prereg["inputs"]["candidate_dataset"]["path"]))
    data["dependency_representative"] = data["dependency_representative"].astype(str).str.lower() == "true"
    feature_rows: list[dict[str, float]] = []
    for symbol, indices in data.groupby("symbol", sort=True).groups.items():
        snapshot = base.repo_path(prereg["inputs"]["snapshot_dir"]) / f"{symbol}.csv"
        enriched = add_global_shape_indicators(base.normalize_ohlcv(snapshot))
        for index in indices:
            row = data.loc[index]
            feature_rows.append(
                {
                    "_index": int(index),
                    **extract_precore_global_shape_features(
                        enriched,
                        core_end_i=int(row["core_end_i"]),
                        side=str(row["side"]),
                    ),
                }
            )
    features = pd.DataFrame(feature_rows).set_index("_index").sort_index()
    if not features.index.equals(data.index):
        raise base.PipelineError("v2 candidate feature alignment failed")
    receipt = base.read_json(RESULTS_DIR / "l15_training_receipt.json")
    data["l15_score"] = np.nan
    data["l15_threshold"] = np.nan
    for side in base.SIDES:
        mask = data["side"] == side
        arm = receipt["arms"][side]
        model_path = base.repo_path(arm["model_path"])
        if base.sha256_file(model_path) != arm["model_sha256"]:
            raise base.PipelineError(f"v2 L1.5 model hash mismatch: {side}")
        model = lgb.Booster(model_file=str(model_path))
        data.loc[mask, "l15_score"] = model.predict(features.loc[mask, list(GLOBAL_PRECORE_FEATURE_COLUMNS)])
        data.loc[mask, "l15_threshold"] = float(arm["threshold_selection"]["threshold"])
    data["l15_keep"] = data["l15_score"] >= data["l15_threshold"]
    for column in GLOBAL_PRECORE_FEATURE_COLUMNS:
        data[f"l15_{column}"] = features[column].to_numpy()
    if base.utc(data["exposure_end_exclusive"].max()) > base.HOLDOUT_START:
        raise base.PipelineError("v2 candidate exposure reaches holdout")
    output_path = OUTPUT_DIR / "candidate_l15_precore_scored.csv"
    data.to_csv(output_path, index=False)
    payload = {
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "path": base.repo_relative(output_path),
        "sha256": base.sha256_file(output_path),
        "rows": len(data),
        "counts": data.groupby(["split", "side", "dependency_representative", "l15_keep"]).size().rename("rows").reset_index().to_dict("records"),
        "post_core_bars_visible_to_l15": 0,
        "max_exposure_end_exclusive": str(data["exposure_end_exclusive"].max()),
        "holdout_consumed": False,
    }
    base.write_json(terminal, payload)
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-l15-dataset", action="store_true")
    parser.add_argument("--train-l15", action="store_true")
    parser.add_argument("--apply-l15", action="store_true")
    parser.add_argument("--train-l2", action="store_true")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--all", action="store_true")
    return parser.parse_args()


def main() -> None:
    _configure_base()
    args = parse_args()
    prereg = load_preregistration()
    if args.all or args.build_l15_dataset:
        build_l15_dataset(prereg)
    if args.all or args.train_l15:
        base.train_l15(prereg)
    if args.all or args.apply_l15:
        apply_l15_to_candidates(prereg)
    if args.all or args.train_l2:
        base.train_l2_factorial(prereg)
    if args.all or args.render:
        base.render_review(prereg)
    if args.all or args.verify:
        base.verify_outputs(prereg)


if __name__ == "__main__":
    main()

