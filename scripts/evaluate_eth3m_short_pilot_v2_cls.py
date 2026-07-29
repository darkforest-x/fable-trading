#!/usr/bin/env python3
# noqa: SIZE_OK
# Frozen one-shot audit CLI; split trigger: analysis/eth3m_short_pilot_v2_cls_maintenance_plan.md.
"""Evaluate the completed ETH 3m v2 diagnostic classifier at fixed p=0.50.

This is a reporting-only evaluator for the preregistered classification pilot.
It reads only the prepared train/val manifest and the completed local run
artifacts.  Weak/review, smoke, and holdout material are explicitly rejected.
The threshold is frozen at 0.50; this script never sweeps or tunes thresholds.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from src.detection.eth3m_v2_classification import (
    EXPECTED_CLASSES,
    EXPECTED_COUNTS,
    IMAGE_SIZE,
    OUTPUT_DATASET,
    PREREG,
    sha256,
    validate_authorization,
    verify_prepared,
)

PROJECT = Path(__file__).resolve().parents[1]
RUN_DIR = PROJECT / "runs/classify/eth3m_short_pilot_v2_cls_diag_20260730"
OUT_DIR = PROJECT / "analysis/output/eth3m_short_pilot_v2_cls_diag_20260730"
THRESHOLD = 0.50
POSITIVE_CLASS = "short_start"
NEGATIVE_CLASS = "no_start"
FORBIDDEN_PARTS = {"holdout", "weak_or_review", "continuous_smoke", "smoke"}
BASELINE_FIRST_BELOW_ALL = {
    "name": "pre_holdout_first_below_all_safe_prefix",
    "threshold": "strict fixed rule, no ML probability",
    "tp": 5,
    "fp": 0,
    "tn": 34,
    "fn": 3,
    "source": "safe pre-holdout strict-prefix baseline supplied for this diagnostic; not recomputed here",
}
EXPECTED_REMOTE_WEIGHTS_SHA256 = "3ce89b668096e79eb00ae0ee8b4913024f91f46356626d22cbe11d3a98c30056"
REMOTE_LOG = OUT_DIR / "remote_train.log"
REMOTE_EXIT = OUT_DIR / "remote_exit_code.txt"
REMOTE_BEST = OUT_DIR / "remote_best.pt"


def assert_fixed_threshold(threshold: float) -> None:
    if not math.isclose(float(threshold), THRESHOLD, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("threshold is frozen at p=0.50; sweeps/tuning are forbidden")


def read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _int_or_float(value: Any) -> int | float | str | None:
    if pd.isna(value):
        return None
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value)
    return value


def select_manifest_rows(dataset: Path, splits: tuple[str, ...]) -> list[dict[str, Any]]:
    """Select only prepared train/val rows and reject any forbidden path."""
    if any(split not in {"train", "val"} for split in splits):
        raise ValueError(f"unsupported split list: {splits}")
    rows = read_manifest(dataset / "manifest.csv")
    selected: list[dict[str, Any]] = []
    counts: Counter[tuple[str, str]] = Counter()
    for row in rows:
        rel = Path(row["image_rel"])
        if FORBIDDEN_PARTS.intersection(rel.parts):
            raise ValueError(f"forbidden evaluation path in prepared manifest: {rel}")
        if row["split"] not in {"train", "val"}:
            raise ValueError(f"unexpected prepared split: {row['split']}")
        if row["class_name"] not in EXPECTED_CLASSES:
            raise ValueError(f"unexpected class: {row['class_name']}")
        if rel.parts[:2] != (row["split"], row["class_name"]):
            raise ValueError(f"path does not match split/class contract: {rel}")
        if row["target"] != str(EXPECTED_CLASSES[row["class_name"]]):
            raise ValueError(f"target mismatch for {rel}")
        path = dataset / rel
        if not path.is_file():
            raise FileNotFoundError(path)
        counts[(row["split"], row["class_name"])] += 1
        if row["split"] in splits:
            selected.append(
                {
                    "sample_id": row["sample_id"],
                    "split": row["split"],
                    "class_name": row["class_name"],
                    "target": int(row["target"]),
                    "anchor_time": row["anchor_time"],
                    "image_rel": rel.as_posix(),
                    "path": path,
                }
            )
    if counts != Counter(EXPECTED_COUNTS):
        raise ValueError(f"prepared counts changed: {dict(counts)}")
    return selected


def predict_rows(
    weights: Path,
    rows: list[dict[str, Any]],
    *,
    device: str,
    batch: int,
) -> list[dict[str, Any]]:
    """Run Ultralytics classification predict lazily so tests avoid importing it."""
    from ultralytics import YOLO

    model = YOLO(str(weights), task="classify")
    paths = [str(row["path"]) for row in rows]
    results = model.predict(
        paths,
        imgsz=IMAGE_SIZE,
        device=device,
        batch=batch,
        verbose=False,
    )
    predictions: list[dict[str, Any]] = []
    pos_idx = EXPECTED_CLASSES[POSITIVE_CLASS]
    neg_idx = EXPECTED_CLASSES[NEGATIVE_CLASS]
    if len(results) != len(rows):
        raise ValueError(f"prediction count mismatch: got {len(results)} for {len(rows)} rows")
    for row, result in zip(rows, results):
        probs = result.probs.data.detach().cpu().numpy().astype(float)
        p_short = float(probs[pos_idx])
        p_no = float(probs[neg_idx])
        pred = 1 if p_short >= THRESHOLD else 0
        predictions.append(
            {
                **{key: row[key] for key in ("sample_id", "split", "class_name", "target", "anchor_time", "image_rel")},
                "p_no_start": p_no,
                "p_short_start": p_short,
                "pred_target": pred,
                "pred_class": POSITIVE_CLASS if pred else NEGATIVE_CLASS,
                "correct": int(pred == row["target"]),
            }
        )
    return predictions


def probability_summary(values: np.ndarray) -> dict[str, float | None]:
    if len(values) == 0:
        return {key: None for key in ("min", "p25", "p50", "p75", "max", "mean", "std")}
    return {
        "min": float(np.min(values)),
        "p25": float(np.quantile(values, 0.25)),
        "p50": float(np.quantile(values, 0.50)),
        "p75": float(np.quantile(values, 0.75)),
        "max": float(np.max(values)),
        "mean": float(np.mean(values)),
        "std": float(np.std(values, ddof=0)),
    }


def evaluate_predictions(predictions: list[dict[str, Any]]) -> dict[str, Any]:
    frame = pd.DataFrame(predictions)
    summaries: dict[str, Any] = {}
    for split, group in frame.groupby("split", sort=True):
        y = group["target"].to_numpy(dtype=int)
        pred = group["pred_target"].to_numpy(dtype=int)
        prob = group["p_short_start"].to_numpy(dtype=float)
        tp = int(((y == 1) & (pred == 1)).sum())
        fp = int(((y == 0) & (pred == 1)).sum())
        tn = int(((y == 0) & (pred == 0)).sum())
        fn = int(((y == 1) & (pred == 0)).sum())
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        specificity = tn / (tn + fp) if (tn + fp) else 0.0
        accuracy = (tp + tn) / len(group) if len(group) else 0.0
        balanced_accuracy = (recall + specificity) / 2
        auc = float(roc_auc_score(y, prob)) if len(np.unique(y)) == 2 else None
        ap = float(average_precision_score(y, prob)) if len(np.unique(y)) == 2 else None
        by_class = {
            class_name: probability_summary(
                group.loc[group["class_name"] == class_name, "p_short_start"].to_numpy(dtype=float)
            )
            for class_name in (NEGATIVE_CLASS, POSITIVE_CLASS)
        }
        summaries[str(split)] = {
            "n": int(len(group)),
            "positives": int((y == 1).sum()),
            "negatives": int((y == 0).sum()),
            "threshold": THRESHOLD,
            "tp": tp,
            "fp": fp,
            "tn": tn,
            "fn": fn,
            "precision": precision,
            "recall": recall,
            "specificity": specificity,
            "balanced_accuracy": balanced_accuracy,
            "accuracy": accuracy,
            "roc_auc": auc,
            "average_precision": ap,
            "p_short_summary": probability_summary(prob),
            "p_short_by_true_class": by_class,
        }
    return summaries


def summarize_training_results(run_dir: Path, val_n: int) -> dict[str, Any]:
    results = pd.read_csv(run_dir / "results.csv")
    results.columns = [str(column).strip() for column in results.columns]
    top1_col = "metrics/accuracy_top1"
    best_top1 = float(results[top1_col].max())
    best_rows = results.loc[results[top1_col] == best_top1]
    best_epoch = int(best_rows.iloc[0]["epoch"])
    return {
        "epochs_recorded": int(len(results)),
        "configured_epochs": 100,
        "patience": 20,
        "early_stop_after_epochs": int(len(results)),
        "best_epoch_by_first_max_top1": best_epoch,
        "best_top1": best_top1,
        "best_top1_count": int(round(best_top1 * val_n)),
        "val_n_for_top1_count": int(val_n),
        "last_epoch": int(results.iloc[-1]["epoch"]),
        "first_epoch_lr_pg2": float(results.iloc[0]["lr/pg2"]),
        "warmup_bias_lr_pg2_note": "0.077023 is recorded in results.csv; treating warmup bias as a hypothesis only",
    }


def verify_remote_training_evidence(
    log_path: Path,
    exit_path: Path,
    remote_best_path: Path,
    local_best_path: Path,
) -> dict[str, Any]:
    """Pin the copied 3060 log/receipt without contacting or mutating the worker."""
    required_paths = (log_path, exit_path, remote_best_path, local_best_path)
    if missing := [str(path) for path in required_paths if not path.is_file()]:
        raise FileNotFoundError(f"missing copied remote evidence: {missing}")
    exit_text = exit_path.read_text(encoding="utf-8").strip()
    if exit_text != "0":
        raise ValueError(f"remote launcher exit receipt is not zero: {exit_text!r}")
    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    required_markers = (
        '"status": "preflight_passed"',
        "NVIDIA GeForce RTX 3060",
        "Best results observed at epoch 1",
        "[launcher] exit_code=0",
    )
    missing = [marker for marker in required_markers if marker not in log_text]
    if missing:
        raise ValueError(f"copied remote log is missing completion marker(s): {missing}")
    remote_best_sha = sha256(remote_best_path)
    local_best_sha = sha256(local_best_path)
    if remote_best_sha != EXPECTED_REMOTE_WEIGHTS_SHA256 or local_best_sha != remote_best_sha:
        raise ValueError("copied 3060 best.pt and local best.pt are not the frozen identical artifact")
    return {
        "log_path": str(log_path.resolve()),
        "log_sha256": sha256(log_path),
        "log_bytes": log_path.stat().st_size,
        "exit_receipt_path": str(exit_path.resolve()),
        "exit_receipt_sha256": sha256(exit_path),
        "exit_code": 0,
        "remote_best_path": str(remote_best_path.resolve()),
        "remote_best_sha256": remote_best_sha,
        "remote_best_bytes": remote_best_path.stat().st_size,
        "local_best_sha256": local_best_sha,
        "remote_local_best_match": True,
        "completion_markers_verified": list(required_markers),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"no rows for {path}")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_metrics_csv(path: Path, metrics: dict[str, Any]) -> None:
    rows: list[dict[str, Any]] = []
    for split, data in metrics.items():
        row = {"split": split}
        for key in (
            "n",
            "positives",
            "negatives",
            "tp",
            "fp",
            "tn",
            "fn",
            "precision",
            "recall",
            "specificity",
            "balanced_accuracy",
            "accuracy",
            "roc_auc",
            "average_precision",
        ):
            row[key] = data[key]
        rows.append(row)
    write_csv(path, rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=OUTPUT_DATASET)
    parser.add_argument("--run-dir", type=Path, default=RUN_DIR)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--threshold", type=float, default=THRESHOLD)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--remote-log", type=Path, default=REMOTE_LOG)
    parser.add_argument("--remote-exit", type=Path, default=REMOTE_EXIT)
    parser.add_argument("--remote-best", type=Path, default=REMOTE_BEST)
    parser.add_argument("--splits", nargs="+", choices=("train", "val"), default=["train", "val"])
    args = parser.parse_args()
    assert_fixed_threshold(args.threshold)
    validate_authorization(PREREG)
    meta = verify_prepared(args.dataset)
    weights = args.run_dir / "weights/best.pt"
    required = [weights, args.run_dir / "results.csv", args.run_dir / "args.yaml"]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"missing run artifact(s): {missing}")

    splits = tuple(dict.fromkeys(args.splits))
    rows = select_manifest_rows(args.dataset, splits)
    predictions = predict_rows(weights, rows, device=args.device, batch=args.batch)
    metrics = evaluate_predictions(predictions)
    val_metrics = metrics.get("val")
    if not val_metrics:
        raise ValueError("val split is required for gates")
    gates = {
        "threshold": THRESHOLD,
        "tp_min": 6,
        "fp_max": 2,
        "actual_tp": val_metrics["tp"],
        "actual_fp": val_metrics["fp"],
        "passed": bool(val_metrics["tp"] >= 6 and val_metrics["fp"] <= 2),
        "policy": "if failed, stop; do not run smoke, owner review, promote, or ACTIVE switch",
    }
    training = summarize_training_results(args.run_dir, int(val_metrics["n"]))
    weights_sha = sha256(weights)
    remote_training_evidence = verify_remote_training_evidence(
        args.remote_log, args.remote_exit, args.remote_best, weights
    )
    summary = {
        "status": "passed" if gates["passed"] else "failed_gates",
        "experiment_id": args.run_dir.name,
        "threshold_policy": "fixed p=0.50 only; no sweep",
        "dataset": str(args.dataset.resolve()),
        "run_dir": str(args.run_dir.resolve()),
        "weights_sha256": weights_sha,
        "reported_remote_weights_sha256": remote_training_evidence["remote_best_sha256"],
        "remote_local_weights_sha_match": remote_training_evidence["remote_local_best_match"],
        "remote_training_evidence": remote_training_evidence,
        "prepared_manifest_sha256": sha256(args.dataset / "manifest.csv"),
        "prepared_build_meta_sha256": sha256(args.dataset / "build_meta.json"),
        "training": training,
        "metrics": metrics,
        "gates": gates,
        "baseline_first_below_all": BASELINE_FIRST_BELOW_ALL,
        "majority_baseline": {
            "split": "val",
            "rule": "always predict no_start",
            "correct": int(val_metrics["negatives"]),
            "n": int(val_metrics["n"]),
            "accuracy": float(val_metrics["negatives"] / val_metrics["n"]),
            "note": "matches the training top1 count, so top1 alone adds no information",
        },
        "scope_guard": {
            "read_splits": list(splits),
            "forbidden_parts_rejected": sorted(FORBIDDEN_PARTS),
            "holdout_read": False,
            "weak_or_review_read": False,
            "smoke_read": False,
            "economics": "N/A for current-tip image classification; no return labels evaluated",
            "random_controls": "N/A here; compare only fixed first-below-all baseline",
        },
        "prepared_meta": {
            "total": meta["total"],
            "counts": meta["counts"],
            "diagnostic_pilot_only": meta["diagnostic_pilot_only"],
            "promotion_eligible": meta["promotion_eligible"],
            "active_eligible": meta["active_eligible"],
        },
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.out_dir / "predictions.csv", predictions)
    write_metrics_csv(args.out_dir / "metrics_by_split.csv", metrics)
    (args.out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.out_dir / "evidence.md").write_text(
        "\n".join(
            [
                "# ETH3m v2 classifier fixed-threshold evidence",
                "",
                f"- invocation: `{Path(__file__).as_posix()} --device {args.device} --batch {args.batch}`",
                f"- binary observable: exit_code=0, status={summary['status']}",
                f"- artifacts: `{args.out_dir / 'summary.json'}`, `{args.out_dir / 'predictions.csv'}`, `{args.out_dir / 'metrics_by_split.csv'}`",
                f"- val gate: TP={gates['actual_tp']} / min {gates['tp_min']}; FP={gates['actual_fp']} / max {gates['fp_max']}; passed={gates['passed']}",
                f"- remote completion: exit_code={remote_training_evidence['exit_code']}; log_sha256={remote_training_evidence['log_sha256']}; exit_receipt_sha256={remote_training_evidence['exit_receipt_sha256']}",
                f"- remote/local best.pt SHA256 match: {summary['remote_local_weights_sha_match']} ({weights_sha})",
                f"- guard: holdout_read={summary['scope_guard']['holdout_read']}, weak_or_review_read={summary['scope_guard']['weak_or_review_read']}, smoke_read={summary['scope_guard']['smoke_read']}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps({"status": summary["status"], "out_dir": str(args.out_dir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
