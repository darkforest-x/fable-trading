import copy
import json
from pathlib import Path

import pytest

from scripts.render_15m_ma_launch_t3_imgsz1280_comparison import (
    BASELINE_WEIGHT_SHA256,
    ComparisonRenderError,
    EXPERIMENT_ID,
    render,
    validate_payloads,
)


TREATMENT_SHA = "1" * 64


def payloads() -> tuple[dict, dict, dict]:
    cells = []
    for training_imgsz in (960, 1280):
        for inference_imgsz in (960, 1280):
            cells.append(
                {
                    "training_imgsz": training_imgsz,
                    "inference_imgsz": inference_imgsz,
                    "weight_sha256": (
                        BASELINE_WEIGHT_SHA256
                        if training_imgsz == 960
                        else TREATMENT_SHA
                    ),
                    "overall": {
                        "precision": 0.5,
                        "recall": 0.6,
                        "map50": 0.58,
                        "map50_95": 0.33,
                    },
                }
            )
    grid = {
        "experiment_id": EXPERIMENT_ID,
        "cells": cells,
        "holdout_consumed": False,
        "active_or_frozen_changed": False,
        "promoted": False,
        "production_eligible": False,
    }

    def negative(experiment_id: str, sha: str, imgsz: int) -> dict:
        return {
            "experiment_id": experiment_id,
            "weights_sha256": sha,
            "imgsz": imgsz,
            "confidence_threshold": 0.25,
            "threshold_tuned": False,
            "easy_val": {"images": 1470, "false_boxes_per_1000_images": 3.4},
            "hard_val": {"images": 1469, "false_boxes_per_1000_images": 2.7},
            "holdout_consumed": False,
            "active_or_frozen_changed": False,
            "promoted": False,
            "production_eligible": False,
        }

    return (
        grid,
        negative("exp-15m-ma-launch-t3-hardval-v1", BASELINE_WEIGHT_SHA256, 960),
        negative(EXPERIMENT_ID, TREATMENT_SHA, 1280),
    )


def test_validate_payloads_accepts_exact_two_by_two_contract() -> None:
    grid, baseline, treatment = payloads()
    by_key = validate_payloads(grid, baseline, treatment)
    assert set(by_key) == {(960, 960), (960, 1280), (1280, 960), (1280, 1280)}


def test_validate_payloads_rejects_duplicate_grid_cell() -> None:
    grid, baseline, treatment = payloads()
    grid["cells"][3] = copy.deepcopy(grid["cells"][0])
    with pytest.raises(ComparisonRenderError, match="duplicate"):
        validate_payloads(grid, baseline, treatment)


def test_validate_payloads_rejects_treatment_weight_mismatch() -> None:
    grid, baseline, treatment = payloads()
    treatment["weights_sha256"] = "2" * 64
    with pytest.raises(ComparisonRenderError, match="different treatment weights"):
        validate_payloads(grid, baseline, treatment)


def test_validate_payloads_rejects_holdout_receipt() -> None:
    grid, baseline, treatment = payloads()
    treatment["holdout_consumed"] = True
    with pytest.raises(ComparisonRenderError, match="pre-holdout"):
        validate_payloads(grid, baseline, treatment)


def test_render_writes_hash_bound_png_and_receipt(tmp_path: Path) -> None:
    grid, baseline, treatment = payloads()
    paths = {
        "grid": tmp_path / "grid.json",
        "baseline": tmp_path / "baseline.json",
        "treatment": tmp_path / "treatment.json",
    }
    for key, payload in (
        ("grid", grid),
        ("baseline", baseline),
        ("treatment", treatment),
    ):
        paths[key].write_text(json.dumps(payload), encoding="utf-8")
    out = tmp_path / "comparison.png"
    receipt = tmp_path / "comparison_receipt.json"
    result = render(
        grid_path=paths["grid"],
        baseline_hard_path=paths["baseline"],
        treatment_hard_path=paths["treatment"],
        out=out,
        receipt=receipt,
    )
    assert out.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert result["output"]["size_bytes"] == out.stat().st_size
    assert json.loads(receipt.read_text(encoding="utf-8"))["output"]["sha256"] == result["output"]["sha256"]
