#!/usr/bin/env python3
"""Verify and summarize the completed 15m t-3 weak-label YOLO run.

Inputs are the fetched ``best.pt``, ``args.yaml``, ``results.csv``, full remote
training log and hash receipt.  The script reads no market data.  It verifies
the frozen optimizer/augmentation contract, matches fetched bytes to the
remote hashes, loads the weight metadata, writes one JSON receipt and renders
training curves.  All output remains research-only and production-ineligible.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping

import matplotlib.pyplot as plt
import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[1]
RUN_NAME = "ma_launch_t3_10000_v1_y11s_ft"
DEFAULT_RUN = ROOT / "analysis" / "output" / "ma_launch_t3_10000_v1" / RUN_NAME
DEFAULT_RESULTS = (
    ROOT
    / "experiments"
    / "active"
    / "exp-15m-ma-launch-t3-yolo10000-v1"
    / "results"
)
METRIC_COLUMNS = (
    "metrics/precision(B)",
    "metrics/recall(B)",
    "metrics/mAP50(B)",
    "metrics/mAP50-95(B)",
)
EXPECTED_ARGS: dict[str, Any] = {
    "task": "detect",
    "mode": "train",
    "epochs": 40,
    "patience": 10,
    "batch": 8,
    "imgsz": 960,
    "workers": 2,
    "optimizer": "AdamW",
    "lr0": 0.0001,
    "lrf": 0.01,
    "warmup_epochs": 0.5,
    "seed": 0,
    "deterministic": True,
    "rect": True,
    "cache": False,
    "hsv_h": 0.0,
    "hsv_s": 0.0,
    "hsv_v": 0.0,
    "degrees": 0.0,
    "translate": 0.02,
    "scale": 0.1,
    "shear": 0.0,
    "perspective": 0.0,
    "flipud": 0.0,
    "fliplr": 0.0,
    "mosaic": 0.0,
    "mixup": 0.0,
    "cutmix": 0.0,
    "copy_paste": 0.0,
    "erasing": 0.0,
}


class TrainingSummaryError(ValueError):
    """Fail-closed fetched-training verification error."""


def committed_generator() -> str:
    """Bind the receipt to the committed summarizer actually executing it."""

    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=ROOT, text=True
    ).strip()
    if branch != "main":
        raise TrainingSummaryError("training summarizer must run on main")
    relative = str(Path(__file__).resolve().relative_to(ROOT))
    dirty = subprocess.check_output(
        ["git", "status", "--short", "--", relative], cwd=ROOT, text=True
    ).strip()
    if dirty:
        raise TrainingSummaryError(f"training summarizer is not committed: {dirty}")
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise TrainingSummaryError("could not resolve summarizer commit")
    return commit


def sha256_file(path: Path) -> str:
    """Return the SHA-256 of one fetched artifact."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def strip_ansi(value: str) -> str:
    """Remove terminal color/control sequences from a remote log."""

    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value).replace("\r", "\n")


def validate_args(args: Mapping[str, Any]) -> None:
    """Require the exact preregistered training and safe-augmentation recipe."""

    drift = {
        key: {"expected": expected, "actual": args.get(key)}
        for key, expected in EXPECTED_ARGS.items()
        if args.get(key) != expected
    }
    if drift:
        raise TrainingSummaryError(f"remote args drifted: {drift}")
    if Path(str(args.get("name", ""))).name != RUN_NAME:
        raise TrainingSummaryError("unexpected remote run name")
    normalized_data = str(args.get("data", "")).replace("\\", "/")
    if not normalized_data.endswith("/datasets/ma_launch_t3_10000_v1/data.yaml"):
        raise TrainingSummaryError("remote data path is not the frozen t-3 dataset")


def read_remote_hashes(path: Path) -> dict[str, dict[str, Any]]:
    """Parse PowerShell ``path|size|sha|mtime`` lines from the fetch receipt."""

    rows: dict[str, dict[str, Any]] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = raw.strip().split("|")
        if len(parts) != 4 or not re.fullmatch(r"[0-9a-f]{64}", parts[2]):
            continue
        name = Path(parts[0].replace("\\", "/")).name
        rows[name] = {
            "remote_path": parts[0],
            "size_bytes": int(parts[1]),
            "sha256": parts[2],
            "remote_mtime_utc": parts[3],
        }
    required = {"best.pt", "args.yaml", "results.csv", f"{RUN_NAME}.log", f"{RUN_NAME}.exit_code"}
    if not required.issubset(rows):
        raise TrainingSummaryError(
            f"remote hash receipt is incomplete: missing {sorted(required - set(rows))}"
        )
    return rows


def parse_per_class(log_text: str) -> dict[str, dict[str, Any]]:
    """Take the last Ultralytics validation-table row for each frozen class."""

    output: dict[str, dict[str, Any]] = {}
    for line in strip_ansi(log_text).splitlines():
        fields = line.split()
        if len(fields) != 7 or fields[0] not in {"dense_long", "dense_short"}:
            continue
        try:
            output[fields[0]] = {
                "images": int(fields[1]),
                "instances": int(fields[2]),
                "precision": float(fields[3]),
                "recall": float(fields[4]),
                "map50": float(fields[5]),
                "map50_95": float(fields[6]),
            }
        except ValueError:
            continue
    return output


def parse_final_results_dict(log_text: str) -> dict[str, float]:
    """Parse the final reloaded-best ``results_dict`` from the remote log."""

    matches = re.findall(r"results_dict:\s*(\{[^\n]+\})", strip_ansi(log_text))
    if not matches:
        raise TrainingSummaryError("remote log has no final results_dict")
    try:
        raw = ast.literal_eval(matches[-1])
    except (SyntaxError, ValueError) as exc:
        raise TrainingSummaryError("final results_dict is not a Python literal") from exc
    required = {
        "metrics/precision(B)": "precision",
        "metrics/recall(B)": "recall",
        "metrics/mAP50(B)": "map50",
        "metrics/mAP50-95(B)": "map50_95",
        "fitness": "fitness",
    }
    if not set(required).issubset(raw):
        raise TrainingSummaryError("final results_dict is missing detection metrics")
    return {target: float(raw[source]) for source, target in required.items()}


def metric_row(row: pd.Series) -> dict[str, float | int]:
    """Project one results.csv row into report metrics."""

    return {
        "epoch": int(row["epoch"]),
        "precision": float(row["metrics/precision(B)"]),
        "recall": float(row["metrics/recall(B)"]),
        "map50": float(row["metrics/mAP50(B)"]),
        "map50_95": float(row["metrics/mAP50-95(B)"]),
        "train_box_loss": float(row["train/box_loss"]),
        "train_cls_loss": float(row["train/cls_loss"]),
        "train_dfl_loss": float(row["train/dfl_loss"]),
        "val_box_loss": float(row["val/box_loss"]),
        "val_cls_loss": float(row["val/cls_loss"]),
        "val_dfl_loss": float(row["val/dfl_loss"]),
    }


def best_metric_row(frame: pd.DataFrame) -> pd.Series:
    """Return the best fitness row; detection fitness is mAP50-95 here."""

    if frame.empty or any(column not in frame for column in METRIC_COLUMNS):
        raise TrainingSummaryError("results.csv is empty or missing detection metrics")
    if frame["epoch"].duplicated().any() or not frame["epoch"].is_monotonic_increasing:
        raise TrainingSummaryError("results.csv epoch sequence is duplicated or non-monotonic")
    return frame.loc[frame["metrics/mAP50-95(B)"].idxmax()]


def render_curves(frame: pd.DataFrame, out: Path) -> None:
    """Render compact metric/loss curves from the exact fetched CSV."""

    epochs = frame["epoch"]
    figure, axes = plt.subplots(2, 2, figsize=(13, 8), constrained_layout=True)
    axes[0, 0].plot(epochs, frame["metrics/precision(B)"], label="precision")
    axes[0, 0].plot(epochs, frame["metrics/recall(B)"], label="recall")
    axes[0, 0].set_title("Validation precision / recall")
    axes[0, 1].plot(epochs, frame["metrics/mAP50(B)"], label="mAP50")
    axes[0, 1].plot(epochs, frame["metrics/mAP50-95(B)"], label="mAP50-95")
    axes[0, 1].set_title("Validation mAP")
    for column, label in (
        ("train/box_loss", "box"),
        ("train/cls_loss", "cls"),
        ("train/dfl_loss", "dfl"),
    ):
        axes[1, 0].plot(epochs, frame[column], label=label)
    axes[1, 0].set_title("Training losses")
    for column, label in (
        ("val/box_loss", "box"),
        ("val/cls_loss", "cls"),
        ("val/dfl_loss", "dfl"),
    ):
        axes[1, 1].plot(epochs, frame[column], label=label)
    axes[1, 1].set_title("Validation losses")
    for axis in axes.flat:
        axis.set_xlabel("epoch")
        axis.grid(alpha=0.25)
        axis.legend()
    out.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(out, dpi=150)
    plt.close(figure)


def summarize(run: Path, out: Path, curve: Path) -> dict[str, Any]:
    """Verify fetched identity, parse metrics, load weights and write evidence."""

    summarizer_commit = committed_generator()
    paths = {
        "best.pt": run / "weights" / "best.pt",
        "args.yaml": run / "args.yaml",
        "results.csv": run / "results.csv",
        f"{RUN_NAME}.log": run / "train.log",
        "remote_training_receipt.txt": run / "remote_training_receipt.txt",
    }
    for name, path in paths.items():
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(f"missing fetched artifact {name}: {path}")

    args = yaml.safe_load(paths["args.yaml"].read_text(encoding="utf-8"))
    validate_args(args)
    frame = pd.read_csv(paths["results.csv"])
    best = best_metric_row(frame)
    remote = read_remote_hashes(paths["remote_training_receipt.txt"])
    local_remote_names = {
        "best.pt": "best.pt",
        "args.yaml": "args.yaml",
        "results.csv": "results.csv",
        f"{RUN_NAME}.log": f"{RUN_NAME}.log",
    }
    file_receipts: dict[str, dict[str, Any]] = {}
    for local_name, remote_name in local_remote_names.items():
        path = paths[local_name]
        actual = {"size_bytes": path.stat().st_size, "sha256": sha256_file(path)}
        if actual["size_bytes"] != remote[remote_name]["size_bytes"] or actual["sha256"] != remote[remote_name]["sha256"]:
            raise TrainingSummaryError(f"fetched bytes differ from remote receipt: {local_name}")
        file_receipts[local_name] = {**actual, **remote[remote_name]}

    from ultralytics import YOLO

    model = YOLO(str(paths["best.pt"]))
    names = {int(key): str(value) for key, value in model.names.items()}
    if names != {0: "dense_long", 1: "dense_short"}:
        raise TrainingSummaryError(f"unexpected model classes: {names}")

    log_text = paths[f"{RUN_NAME}.log"].read_text(encoding="utf-8", errors="replace")
    if "[launcher] exit_code=0" not in log_text:
        raise TrainingSummaryError("remote log has no successful launcher exit")
    per_class = parse_per_class(log_text)
    expected_instances = {"dense_long": 822, "dense_short": 648}
    if set(per_class) != set(expected_instances):
        raise TrainingSummaryError(f"final per-class validation rows missing: {per_class}")
    for name, expected in expected_instances.items():
        if int(per_class[name]["instances"]) != expected:
            raise TrainingSummaryError(f"per-class instance count drifted for {name}")
    best_model_final = parse_final_results_dict(log_text)
    class_mean_map = sum(float(row["map50_95"]) for row in per_class.values()) / len(per_class)
    if abs(best_model_final["map50_95"] - class_mean_map) > 0.001:
        raise TrainingSummaryError("overall and per-class final mAP50-95 disagree")

    render_curves(frame, curve)
    payload: dict[str, Any] = {
        "experiment_id": "exp-15m-ma-launch-t3-yolo10000-v1",
        "summarizer_commit": summarizer_commit,
        "run_name": RUN_NAME,
        "remote_host": "Administrator@192.168.1.5",
        "gpu": "NVIDIA GeForce RTX 3060 12GB",
        "epochs_requested": 40,
        "epochs_completed": len(frame),
        "early_stopped": len(frame) < 40,
        "first": metric_row(frame.iloc[0]),
        "best": metric_row(best),
        "final": metric_row(frame.iloc[-1]),
        "best_model_final_validation": best_model_final,
        "per_class_best_model_final_validation": per_class,
        "class_names": names,
        "fetched_files": file_receipts,
        "remote_exit": remote[f"{RUN_NAME}.exit_code"],
        "curve_sha256": sha256_file(curve),
        "curve_size_bytes": curve.stat().st_size,
        "args_contract_passed": True,
        "remote_hash_match": True,
        "holdout_consumed": False,
        "active_or_frozen_changed": False,
        "promoted": False,
        "deployed": False,
        "production_eligible": False,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--out", type=Path, default=DEFAULT_RESULTS / "training_receipt.json")
    parser.add_argument("--curve", type=Path, default=DEFAULT_RESULTS / "training_curves.png")
    args = parser.parse_args()
    payload = summarize(args.run.resolve(), args.out.resolve(), args.curve.resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
