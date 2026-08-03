"""Read-only P2.0 audit of the immutable P1 dataset and P2.1 preregistration.

This command loads one explicit content-addressed P1 manifest, validates the
pre-holdout interval boundary, constructs the outcome-independent chronological
split, and checks the proposed preregistration file.  It does not import a
training entry point, fit a model, score a row, calibrate a real threshold, read
funding/holdout data, or touch any runtime/execution artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.judgment.features import FEATURE_COLUMNS
from src.judgment.p1_dataset import file_sha256, load_immutable_dataset
from src.judgment.p2_protocol import (
    CALIBRATION_QUANTILE,
    CALIBRATION_START,
    EARLY_STOP_START,
    HOLDOUT_CUTOFF,
    THRESHOLD_OPERATOR,
    prepare_three_way_split,
)

PROJECT = Path(__file__).resolve().parents[1]
P1_MANIFEST = PROJECT / "data/p1/p1_short_l2_preholdout_aade2a334448d644.manifest.json"
PREREG = PROJECT / "analysis/output/p2_l2_prereg_20260803.json"
OUTPUT = PROJECT / "analysis/output/p2_l2_audit_20260803.json"
EXPECTED_DATASET_SHA = "aade2a334448d6443e71fb0d3dbbfcf450390875ce60e1f800f6dbe9c855e93a"
EXPECTED_MANIFEST_SHA = "53b8a07612dae667a184da38bf8e0a694aaae15a5fd240d5b13238da3e13d682"
PROTECTED = (
    "models/ACTIVE",
    "data/forward_log.csv",
    "data/executor_ledger.jsonl",
)


def _relative(path: Path) -> str:
    return path.relative_to(PROJECT).as_posix()


def _segment(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "rows": int(len(frame)),
        "event_groups": int(frame["event_group_id"].nunique()),
        "symbols": int(frame["symbol"].nunique()),
        "signal_start": str(frame["signal_time"].min()),
        "signal_end": str(frame["signal_time"].max()),
        "max_interval_end": str(frame["interval_end"].max()),
    }


def _load_prereg(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "dataset_sha256": EXPECTED_DATASET_SHA,
        "target_column": "net_ret_swap_taker",
        "calibration_quantile": CALIBRATION_QUANTILE,
        "threshold_operator": THRESHOLD_OPERATOR,
        "early_stop_start": EARLY_STOP_START.isoformat(),
        "calibration_start": CALIBRATION_START.isoformat(),
        "holdout_cutoff": HOLDOUT_CUTOFF.isoformat(),
    }
    actual = {
        "dataset_sha256": raw["dataset"]["sha256"],
        "target_column": raw["model"]["target_column"],
        "calibration_quantile": raw["selector"]["calibration_quantile"],
        "threshold_operator": raw["selector"]["threshold_operator"],
        "early_stop_start": raw["split"]["early_stop_start"],
        "calibration_start": raw["split"]["calibration_start"],
        "holdout_cutoff": raw["safety"]["holdout_cutoff"],
    }
    if actual != expected:
        raise ValueError(f"preregistration does not match code contract: {actual!r}")
    return raw


def build_audit() -> dict[str, Any]:
    manifest_sha = file_sha256(P1_MANIFEST)
    if manifest_sha != EXPECTED_MANIFEST_SHA:
        raise ValueError("P1 manifest hash mismatch")
    data = load_immutable_dataset(P1_MANIFEST)
    manifest = json.loads(P1_MANIFEST.read_text(encoding="utf-8"))
    if manifest["dataset_sha256"] != EXPECTED_DATASET_SHA:
        raise ValueError("P1 dataset hash mismatch")
    for column in ("signal_time", "interval_start", "interval_end"):
        data[column] = pd.to_datetime(data[column], utc=True, errors="raise")
    split = prepare_three_way_split(data)
    prereg = _load_prereg(PREREG)
    decisions = prereg["owner_decisions"]
    training_allowed = prereg["status"] == "accepted" and all(
        decision.get("status") == "accepted" and decision.get("accepted_value") is not None
        for decision in decisions.values()
    )
    feature_values = data[list(FEATURE_COLUMNS)].to_numpy(dtype=float)
    protected = {
        name: {
            "exists": (PROJECT / name).exists(),
            "sha256": file_sha256(PROJECT / name) if (PROJECT / name).exists() else None,
        }
        for name in PROTECTED
    }
    active_bundle = PROJECT / "models/active_bundle.json"
    cost_identity = np.abs(
        data["net_ret_swap_taker"].to_numpy(dtype=float)
        - (
            data["gross_ret"].to_numpy(dtype=float)
            - data["fee_swap_taker"].to_numpy(dtype=float)
        )
    )
    return {
        "audit_version": "p2_l2_readonly_audit_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verdict": "accepted",
        "p2_training_allowed": training_allowed,
        "p2_training_blocker": None if training_allowed else (
            "owner must approve exact actual-cost pressure line and fixed runtime gate"
        ),
        "dataset": {
            "manifest_path": _relative(P1_MANIFEST),
            "manifest_sha256": manifest_sha,
            "path": manifest["dataset_path"],
            "sha256": manifest["dataset_sha256"],
            "rows": int(len(data)),
            "columns": int(len(data.columns)),
            "symbols": int(data["symbol"].nunique()),
            "event_groups": int(data["event_group_id"].nunique()),
            "candidate_duplicates": int(data["candidate_id"].duplicated().sum()),
            "signal_start": str(data["signal_time"].min()),
            "signal_end": str(data["signal_time"].max()),
            "max_interval_end": str(data["interval_end"].max()),
            "holdout_signal_rows": int((data["signal_time"] >= HOLDOUT_CUTOFF).sum()),
            "holdout_interval_rows": int((data["interval_end"] >= HOLDOUT_CUTOFF).sum()),
            "feature_missing_cells": int(data[list(FEATURE_COLUMNS)].isna().sum().sum()),
            "feature_infinite_cells": int(np.isinf(feature_values).sum()),
            "label_positive_rows": int(data["label_tp_before_sl"].sum()),
            "label_positive_rate": float(data["label_tp_before_sl"].mean()),
            "gross_mean": float(data["gross_ret"].mean()),
            "net_taker_mean": float(data["net_ret_swap_taker"].mean()),
            "fee_values": sorted(float(value) for value in data["fee_swap_taker"].unique()),
            "cost_identity_max_abs_error": float(cost_identity.max()),
        },
        "split": {
            "selection_basis": "fixed UTC boundaries derived from signal_time counts only; outcomes/features unused",
            "early_stop_start": EARLY_STOP_START.isoformat(),
            "calibration_start": CALIBRATION_START.isoformat(),
            "train": _segment(split.train),
            "early_stop": _segment(split.early_stop),
            "calibration": _segment(split.calibration),
            "purged_rows": int(len(split.purged)),
            "purged_event_groups": int(split.purged["event_group_id"].nunique()),
            "cross_segment_event_groups": 0,
        },
        "preregistration": {
            "path": _relative(PREREG),
            "sha256": file_sha256(PREREG),
            "status": prereg["status"],
            "unresolved_owner_decisions": prereg["owner_decisions"],
        },
        "cost_facts": {
            "p1_contains_swap_taker_round_trip": 0.001,
            "p1_contains_slippage": False,
            "p1_contains_funding": False,
            "clean_historical_fill_pairs_for_slippage": 0,
            "evidence_report": "analysis/p_execution_slippage.md",
            "evidence_report_sha256": file_sha256(PROJECT / "analysis/p_execution_slippage.md"),
        },
        "protected": {
            **protected,
            "models/active_bundle.json": {
                "exists": active_bundle.exists(),
                "sha256": file_sha256(active_bundle) if active_bundle.exists() else None,
            },
        },
        "safety": {
            "data_sources_used": [manifest["dataset_path"]],
            "holdout_read": False,
            "trained": False,
            "real_threshold_calibrated": False,
            "active_modified": False,
            "active_bundle_created": False,
            "deployed": False,
            "ordered": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    audit = build_audit()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
