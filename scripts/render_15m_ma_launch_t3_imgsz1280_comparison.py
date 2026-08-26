#!/usr/bin/env python3
"""Render the preregistered 960-versus-1280 t-3 YOLO comparison.

Inputs are evaluation receipts produced from the immutable pre-holdout
validation images.  This presentation helper reads no OHLCV and changes no
model, threshold, split, or evaluation result.  It binds the rendered PNG to
the exact JSON inputs with SHA-256 hashes so the figure cannot silently drift
away from the report tables.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-t3-yolo10000-imgsz1280-v1"
BASELINE_WEIGHT_SHA256 = (
    "8b2e393ffa887b8284a5580f68df290963fccc08fb94cdc4e0fec0c2b1e40e10"
)
DEFAULT_RESULTS = (
    ROOT / "experiments" / "active" / EXPERIMENT_ID / "results"
)
DEFAULT_BASELINE_HARD = (
    ROOT
    / "experiments"
    / "active"
    / "exp-15m-ma-launch-t3-hardval-v1"
    / "results"
    / "hard_val_evaluation.json"
)


class ComparisonRenderError(ValueError):
    """Fail-closed comparison-figure contract error."""


def sha256_file(path: Path) -> str:
    """Hash one immutable input or rendered artifact."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    """Load one non-empty JSON object."""

    if not path.is_file() or path.stat().st_size == 0:
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ComparisonRenderError(f"JSON root must be an object: {path}")
    return payload


def _require_research_only(payload: Mapping[str, Any], name: str) -> None:
    """Reject a receipt that claims holdout or production state changes."""

    if payload.get("holdout_consumed") is not False:
        raise ComparisonRenderError(f"{name} is not explicitly pre-holdout")
    for field in ("production_eligible", "promoted", "active_or_frozen_changed"):
        if field in payload and payload[field] is not False:
            raise ComparisonRenderError(f"{name} has unsafe {field}={payload[field]!r}")


def validate_payloads(
    grid: Mapping[str, Any],
    baseline_hard: Mapping[str, Any],
    treatment_hard: Mapping[str, Any],
) -> dict[tuple[int, int], Mapping[str, Any]]:
    """Validate identity and return the exact 2x2 grid keyed by resolution."""

    _require_research_only(grid, "resolution grid")
    _require_research_only(baseline_hard, "baseline negative receipt")
    _require_research_only(treatment_hard, "treatment negative receipt")
    if grid.get("experiment_id") != EXPERIMENT_ID:
        raise ComparisonRenderError("resolution-grid experiment identity drifted")
    if treatment_hard.get("experiment_id") != EXPERIMENT_ID:
        raise ComparisonRenderError("treatment negative experiment identity drifted")
    if baseline_hard.get("weights_sha256") != BASELINE_WEIGHT_SHA256:
        raise ComparisonRenderError("baseline negative weight identity drifted")
    if int(baseline_hard.get("imgsz", -1)) != 960:
        raise ComparisonRenderError("baseline negative evaluation is not imgsz=960")
    if int(treatment_hard.get("imgsz", -1)) != 1280:
        raise ComparisonRenderError("treatment negative evaluation is not imgsz=1280")
    for name, payload in (
        ("baseline", baseline_hard),
        ("treatment", treatment_hard),
    ):
        if float(payload.get("confidence_threshold", -1.0)) != 0.25:
            raise ComparisonRenderError(f"{name} negative threshold is not 0.25")
        if payload.get("threshold_tuned") is not False:
            raise ComparisonRenderError(f"{name} negative threshold was tuned")
        for surface, expected in (("easy_val", 1470), ("hard_val", 1469)):
            row = payload.get(surface)
            if not isinstance(row, Mapping) or int(row.get("images", -1)) != expected:
                raise ComparisonRenderError(
                    f"{name} {surface} image count is not frozen at {expected}"
                )

    cells = grid.get("cells")
    if not isinstance(cells, list):
        raise ComparisonRenderError("resolution grid has no cells list")
    by_key: dict[tuple[int, int], Mapping[str, Any]] = {}
    for cell in cells:
        if not isinstance(cell, Mapping):
            raise ComparisonRenderError("resolution grid cell is not an object")
        key = (int(cell["training_imgsz"]), int(cell["inference_imgsz"]))
        if key in by_key:
            raise ComparisonRenderError(f"duplicate resolution-grid cell: {key}")
        overall = cell.get("overall")
        if not isinstance(overall, Mapping):
            raise ComparisonRenderError(f"resolution-grid cell has no metrics: {key}")
        for metric in ("precision", "recall", "map50", "map50_95"):
            value = float(overall.get(metric, -1.0))
            if not 0.0 <= value <= 1.0:
                raise ComparisonRenderError(f"invalid {metric} in cell {key}: {value}")
        by_key[key] = cell
    expected_keys = {(960, 960), (960, 1280), (1280, 960), (1280, 1280)}
    if set(by_key) != expected_keys:
        raise ComparisonRenderError(
            f"resolution grid keys drifted: {sorted(by_key)} != {sorted(expected_keys)}"
        )
    if by_key[(960, 960)].get("weight_sha256") != BASELINE_WEIGHT_SHA256:
        raise ComparisonRenderError("grid baseline weight identity drifted")
    treatment_sha = treatment_hard.get("weights_sha256")
    if not isinstance(treatment_sha, str) or len(treatment_sha) != 64:
        raise ComparisonRenderError("treatment weight hash is invalid")
    if by_key[(1280, 1280)].get("weight_sha256") != treatment_sha:
        raise ComparisonRenderError("grid and negative receipt use different treatment weights")
    return by_key


def _annotate(axis: Any, bars: Any, *, digits: int = 3) -> None:
    """Write compact values above a Matplotlib bar container."""

    for bar in bars:
        value = float(bar.get_height())
        axis.annotate(
            f"{value:.{digits}f}",
            xy=(bar.get_x() + bar.get_width() / 2.0, value),
            xytext=(0, 4),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=8,
        )


def render(
    *,
    grid_path: Path,
    baseline_hard_path: Path,
    treatment_hard_path: Path,
    out: Path,
    receipt: Path,
) -> dict[str, Any]:
    """Render one three-panel comparison and a hash-bound receipt."""

    for target in (out, receipt):
        if target.exists():
            raise FileExistsError(f"refusing to overwrite comparison artifact: {target}")
    grid = load_json(grid_path)
    baseline_hard = load_json(baseline_hard_path)
    treatment_hard = load_json(treatment_hard_path)
    by_key = validate_payloads(grid, baseline_hard, treatment_hard)

    figure, axes = plt.subplots(1, 3, figsize=(17, 5.3), constrained_layout=True)
    colors = {960: "#4C78A8", 1280: "#F58518"}

    metrics = ("precision", "recall", "map50", "map50_95")
    labels = ("Precision", "Recall", "mAP50", "mAP50-95")
    x = np.arange(len(metrics))
    width = 0.36
    for offset, resolution in ((-width / 2.0, 960), (width / 2.0, 1280)):
        values = [float(by_key[(resolution, resolution)]["overall"][key]) for key in metrics]
        bars = axes[0].bar(
            x + offset,
            values,
            width,
            label=f"train/eval {resolution}",
            color=colors[resolution],
        )
        _annotate(axes[0], bars)
    axes[0].set_xticks(x, labels, rotation=18)
    axes[0].set_ylim(0.0, 0.72)
    axes[0].set_title("Native validation metrics")
    axes[0].set_ylabel("score")
    axes[0].legend(fontsize=8)

    x = np.arange(2)
    for offset, training_resolution in ((-width / 2.0, 960), (width / 2.0, 1280)):
        values = [
            float(by_key[(training_resolution, inference_resolution)]["overall"]["map50_95"])
            for inference_resolution in (960, 1280)
        ]
        bars = axes[1].bar(
            x + offset,
            values,
            width,
            label=f"trained {training_resolution}",
            color=colors[training_resolution],
        )
        _annotate(axes[1], bars, digits=4)
    axes[1].set_xticks(x, ("eval 960", "eval 1280"))
    axes[1].set_ylim(0.0, 0.45)
    axes[1].set_title("mAP50-95 cross-resolution check")
    axes[1].set_ylabel("mAP50-95")
    axes[1].legend(fontsize=8)

    x = np.arange(2)
    for offset, resolution, payload in (
        (-width / 2.0, 960, baseline_hard),
        (width / 2.0, 1280, treatment_hard),
    ):
        values = [
            float(payload[surface]["false_boxes_per_1000_images"])
            for surface in ("easy_val", "hard_val")
        ]
        bars = axes[2].bar(
            x + offset,
            values,
            width,
            label=f"train/eval {resolution}",
            color=colors[resolution],
        )
        _annotate(axes[2], bars, digits=2)
    axes[2].set_xticks(x, ("easy negatives", "hard negatives"))
    axes[2].set_title("False boxes at confidence 0.25")
    axes[2].set_ylabel("boxes / 1,000 images")
    axes[2].legend(fontsize=8)

    for axis in axes:
        axis.grid(axis="y", alpha=0.22)
    figure.suptitle(
        "15m t-3 YOLO: frozen pre-holdout 960 vs 1280 comparison",
        fontsize=14,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(out, dpi=160)
    plt.close(figure)

    payload = {
        "experiment_id": EXPERIMENT_ID,
        "inputs": {
            "resolution_grid": {
                "path": str(grid_path),
                "sha256": sha256_file(grid_path),
            },
            "baseline_negative": {
                "path": str(baseline_hard_path),
                "sha256": sha256_file(baseline_hard_path),
            },
            "treatment_negative": {
                "path": str(treatment_hard_path),
                "sha256": sha256_file(treatment_hard_path),
            },
        },
        "output": {
            "path": str(out),
            "sha256": sha256_file(out),
            "size_bytes": out.stat().st_size,
        },
        "holdout_consumed": False,
        "active_or_frozen_changed": False,
        "promoted": False,
        "production_eligible": False,
    }
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--grid", type=Path, default=DEFAULT_RESULTS / "resolution_grid.json"
    )
    parser.add_argument(
        "--baseline-hard", type=Path, default=DEFAULT_BASELINE_HARD
    )
    parser.add_argument(
        "--treatment-hard",
        type=Path,
        default=DEFAULT_RESULTS / "hard_val_evaluation_native1280.json",
    )
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_RESULTS / "resolution_comparison.png"
    )
    parser.add_argument(
        "--receipt",
        type=Path,
        default=DEFAULT_RESULTS / "resolution_comparison_receipt.json",
    )
    args = parser.parse_args()
    payload = render(
        grid_path=args.grid.resolve(),
        baseline_hard_path=args.baseline_hard.resolve(),
        treatment_hard_path=args.treatment_hard.resolve(),
        out=args.out.resolve(),
        receipt=args.receipt.resolve(),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
