#!/usr/bin/env python3
"""Independently verify the frozen FIL 1h model-first breakout diagnosis.

The verifier performs no network access and no model inference.  It hashes the
three frozen source artifacts, independently recomputes the two-bar six-MA
crossing predicates from the candle CSV, reconciles every delivered decision,
and checks that the review figure is a separate readable image.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import cv2
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-1h-filusdt-model-first-breakout-gate-20260904-v1"
EXP_DIR = ROOT / "experiments" / "active" / EXPERIMENT_ID
PREREG = EXP_DIR / "preregistration.json"
RESULTS = EXP_DIR / "results"
MA_COLS = ("sma20", "sma60", "sma120", "ema20", "ema60", "ema120")


class VerificationError(RuntimeError):
    """Raised when an output cannot be reproduced from frozen bytes."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def add_mas_independently(frame: pd.DataFrame) -> pd.DataFrame:
    """Recompute the six close-based trailing MAs without importing L1 code."""

    out = frame.copy()
    close = pd.to_numeric(out["close"], errors="raise")
    for period in (20, 60, 120):
        out[f"sma{period}"] = close.rolling(period).mean()
        out[f"ema{period}"] = close.ewm(span=period, adjust=False).mean()
    return out


def manual_decision(frame: pd.DataFrame, index: int, direction: str) -> dict[str, Any]:
    """Recompute the frozen predicate from exactly rows t-1 and t."""

    previous = frame.iloc[index - 1]
    current = frame.iloc[index]
    previous_mas = previous.loc[list(MA_COLS)].to_numpy(dtype=float)
    current_mas = current.loc[list(MA_COLS)].to_numpy(dtype=float)
    if not bool(np.isfinite(np.concatenate((previous_mas, current_mas))).all()):
        raise VerificationError(f"non-finite MA values at {index}")
    previous_close = float(previous["close"])
    current_close = float(current["close"])
    if direction == "LONG":
        previous_edge = float(previous_mas.max())
        current_edge = float(current_mas.max())
        current_beyond = current_close > current_edge
        previous_not_beyond = previous_close <= previous_edge
    elif direction == "SHORT":
        previous_edge = float(previous_mas.min())
        current_edge = float(current_mas.min())
        current_beyond = current_close < current_edge
        previous_not_beyond = previous_close >= previous_edge
    else:
        raise VerificationError(f"unsupported direction: {direction}")
    return {
        "passed": bool(current_beyond and previous_not_beyond),
        "current_close": current_close,
        "current_bundle_edge": current_edge,
        "previous_close": previous_close,
        "previous_bundle_edge": previous_edge,
        "current_beyond_bundle": bool(current_beyond),
        "previous_not_beyond_bundle": bool(previous_not_beyond),
    }


def assert_float(actual: object, expected: float, name: str) -> None:
    if not bool(np.isclose(float(actual), float(expected), rtol=0.0, atol=1e-12)):
        raise VerificationError(f"{name} differs: {actual} != {expected}")


def main() -> int:
    output = RESULTS / "verification.json"
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    for item in prereg["frozen_inputs"].values():
        path = ROOT / str(item["path"])
        if sha256_file(path) != str(item["sha256"]):
            raise VerificationError(f"source hash drifted: {path}")

    candles = pd.read_csv(ROOT / prereg["frozen_inputs"]["source_candles"]["path"])
    frame = add_mas_independently(candles)
    decisions = pd.read_csv(RESULTS / "model_first_decisions.csv")
    if len(decisions) != 8:
        raise VerificationError(f"expected 8 model proposals, got {len(decisions)}")

    actual_passes = 0
    flipped_passes = 0
    for _, row in decisions.iterrows():
        direction = str(row["gate_direction"])
        index = int(row["window_end_i"])
        actual = manual_decision(frame, index, direction)
        flipped = manual_decision(frame, index, "SHORT" if direction == "LONG" else "LONG")
        delivered_pass = str(row["gate_passed"]).lower() == "true"
        delivered_flip = str(row["flipped_gate_pass"]).lower() == "true"
        if delivered_pass != bool(actual["passed"]):
            raise VerificationError(f"actual decision drifted: {row['candidate_id']}")
        if delivered_flip != bool(flipped["passed"]):
            raise VerificationError(f"flipped decision drifted: {row['candidate_id']}")
        for key in (
            "current_close",
            "current_bundle_edge",
            "previous_close",
            "previous_bundle_edge",
        ):
            assert_float(row[f"gate_{key}"], float(actual[key]), f"{row['candidate_id']}:{key}")
        actual_passes += int(actual["passed"])
        flipped_passes += int(flipped["passed"])

    reference_path = RESULTS / "code_only_references.csv"
    references = (
        pd.read_csv(reference_path)
        if reference_path.read_text(encoding="utf-8").strip()
        else pd.DataFrame()
    )
    if len(references) != 0:
        raise VerificationError("the frozen strict-reference interval should be empty")
    summary = json.loads((RESULTS / "summary.json").read_text(encoding="utf-8"))
    if int(summary["pipeline"]["actual_gate_passes"]) != actual_passes:
        raise VerificationError("summary actual pass count drifted")
    if int(summary["pipeline"]["flipped_direction_gate_passes"]) != flipped_passes:
        raise VerificationError("summary flipped pass count drifted")
    if actual_passes != 0 or flipped_passes != 0:
        raise VerificationError("frozen expected zero-pass diagnosis drifted")

    chart = RESULTS / "review" / "FILUSDT_P_1h_model_first_breakout_global.png"
    decoded = cv2.imread(str(chart), cv2.IMREAD_COLOR)
    if decoded is None or tuple(decoded.shape) != (1160, 1920, 3):
        raise VerificationError("review chart is missing or has the wrong dimensions")
    receipt: Mapping[str, Any] = {
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "passed": True,
        "network_reads": 0,
        "model_inference_calls": 0,
        "source_hashes_verified": len(prereg["frozen_inputs"]),
        "model_proposals_replayed": len(decisions),
        "actual_gate_passes": actual_passes,
        "flipped_gate_passes": flipped_passes,
        "future_mutation_passes_in_summary": int(
            summary["causality"]["future_mutation_passes"]
        ),
        "review_chart_sha256": sha256_file(chart),
        "review_chart_shape": list(decoded.shape),
    }
    output.write_text(
        json.dumps(dict(receipt), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(dict(receipt), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
