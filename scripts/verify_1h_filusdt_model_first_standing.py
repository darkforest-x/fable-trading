#!/usr/bin/env python3
"""Independently verify the frozen FIL 1h current-position diagnosis.

The verifier performs no network access and no model inference.  It hashes all
frozen inputs, independently recomputes the six close-based moving averages,
replays every current-bar position predicate without importing the production
gate, repeats Future Mutation, checks overlap deduplication, and validates the
separate review image.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import cv2
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-1h-filusdt-model-first-standing-gate-20260904-v2"
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
    """Recompute the six trailing close-based MAs without importing L1 code."""

    out = frame.copy()
    close = pd.to_numeric(out["close"], errors="raise")
    for period in (20, 60, 120):
        out[f"sma{period}"] = close.rolling(period).mean()
        out[f"ema{period}"] = close.ewm(span=period, adjust=False).mean()
    return out


def manual_decision(frame: pd.DataFrame, index: int, direction: str) -> dict[str, Any]:
    """Recompute the frozen predicate from exactly row ``t``."""

    current = frame.iloc[index]
    mas = current.loc[list(MA_COLS)].to_numpy(dtype=float)
    close = float(current["close"])
    if not bool(np.isfinite(np.concatenate(([close], mas))).all()):
        raise VerificationError(f"non-finite values at {index}")
    if direction == "LONG":
        edge = float(mas.max())
        beyond = close > edge
    elif direction == "SHORT":
        edge = float(mas.min())
        beyond = close < edge
    else:
        raise VerificationError(f"unsupported direction: {direction}")
    return {
        "passed": bool(beyond),
        "current_close": close,
        "current_bundle_edge": edge,
        "current_beyond_bundle": bool(beyond),
    }


def count_overlap_episodes(rows: Iterable[Mapping[str, Any]]) -> int:
    """Independently merge same-direction overlapping core intervals."""

    materialized = list(rows)
    episodes = 0
    for direction in ("LONG", "SHORT"):
        group = sorted(
            (row for row in materialized if str(row["gate_direction"]) == direction),
            key=lambda row: (int(row["core_start_i"]), int(row["core_end_i"])),
        )
        active_end: int | None = None
        for row in group:
            start = int(row["core_start_i"])
            end = int(row["core_end_i"])
            if active_end is None or start > active_end:
                episodes += 1
                active_end = end
            else:
                active_end = max(active_end, end)
    return episodes


def delivered_bool(value: object) -> bool:
    return str(value).lower() == "true"


def assert_float(actual: object, expected: float, name: str) -> None:
    if not bool(np.isclose(float(actual), float(expected), rtol=0.0, atol=1e-12)):
        raise VerificationError(f"{name} differs: {actual} != {expected}")


def main() -> int:
    output = RESULTS / "verification.json"
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if prereg["position_gate"]["prior_bar_condition"] is not None:
        raise VerificationError("the removed prior-bar condition returned")
    if int(prereg["position_gate"]["lookback_rows"]) != 1:
        raise VerificationError("position gate must read exactly one row")
    for item in prereg["frozen_inputs"].values():
        path = ROOT / str(item["path"])
        if sha256_file(path) != str(item["sha256"]):
            raise VerificationError(f"source hash drifted: {path}")

    raw = pd.read_csv(ROOT / prereg["frozen_inputs"]["source_candles"]["path"])
    frame = add_mas_independently(raw)
    decisions = pd.read_csv(RESULTS / "model_first_standing_decisions.csv")
    if len(decisions) != 8:
        raise VerificationError(f"expected 8 model proposals, got {len(decisions)}")

    actual_passes = 0
    flipped_passes = 0
    future_mutation_passes = 0
    passing_rows: list[dict[str, Any]] = []
    for _, row in decisions.iterrows():
        direction = str(row["gate_direction"])
        index = int(row["window_end_i"])
        actual = manual_decision(frame, index, direction)
        flipped = manual_decision(frame, index, "SHORT" if direction == "LONG" else "LONG")
        if delivered_bool(row["gate_passed"]) != bool(actual["passed"]):
            raise VerificationError(f"actual decision drifted: {row['candidate_id']}")
        if delivered_bool(row["flipped_gate_pass"]) != bool(flipped["passed"]):
            raise VerificationError(f"flipped decision drifted: {row['candidate_id']}")
        for key in ("current_close", "current_bundle_edge"):
            assert_float(row[f"gate_{key}"], float(actual[key]), f"{row['candidate_id']}:{key}")

        mutated = raw.copy()
        future = mutated.index > index
        multipliers = np.linspace(5.0, 50.0, int(future.sum()))
        for column in ("open", "high", "low", "close", "volume"):
            mutated.loc[future, column] = (
                mutated.loc[future, column].to_numpy(dtype=float) * multipliers
            )
        replay = manual_decision(add_mas_independently(mutated), index, direction)
        if replay != actual:
            raise VerificationError(f"future mutation changed {row['candidate_id']}")
        future_mutation_passes += 1
        actual_passes += int(actual["passed"])
        flipped_passes += int(flipped["passed"])
        if actual["passed"]:
            passing_rows.append(row.to_dict())

    episodes = count_overlap_episodes(passing_rows)
    summary = json.loads((RESULTS / "summary.json").read_text(encoding="utf-8"))
    expected = summary["pipeline"]
    if int(expected["actual_gate_passes"]) != actual_passes:
        raise VerificationError("summary actual pass count drifted")
    if int(expected["flipped_direction_gate_passes"]) != flipped_passes:
        raise VerificationError("summary flipped pass count drifted")
    if int(expected["deduplicated_actionable_events"]) != episodes:
        raise VerificationError("summary episode count drifted")
    if actual_passes != 8 or flipped_passes != 0 or episodes != 1:
        raise VerificationError("frozen expected 8/0/1 diagnosis drifted")

    chart = RESULTS / "review" / "FILUSDT_P_1h_model_first_standing_global.png"
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
        "deduplicated_events": episodes,
        "future_mutation_passes": future_mutation_passes,
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
