#!/usr/bin/env python3
"""Compare two frozen Grade-A validation prediction ledgers as a paired trial.

Inputs are the per-image JSONL ledgers emitted by
``evaluate_15m_ma_launch_owner_grade_a8000_val.py``.  The comparison requires
identical sample, event, direction, post-bar and negative-kind identities.
Positive variants are clustered by ``event_id`` and negative variants by
``negative_event_id``.  No OHLCV, holdout row, threshold search, model weight,
production state or trading state is read or changed.

Primary inference follows the pre-result HL2 protocol: exact paired binary
tests for earliest-available and post2 event hits, an event-cluster sign-flip
test for negative fired-image rate, Holm correction across those three tests,
and event-block bootstrap intervals for paired rate differences.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

DEFAULT_SEED = 20260903
DEFAULT_BOOTSTRAP_REPS = 20_000
DEFAULT_PERMUTATION_REPS = 100_000
PRIMARY_ALPHA = 0.05


class PairedValidationError(ValueError):
    """Raised when paired evaluation identities or contracts drift."""


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 digest."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    """Read one UTF-8 JSON object."""

    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read non-empty JSONL rows."""

    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _two_sided_exact_sign_p(control_only: int, treatment_only: int) -> float:
    """Return the exact two-sided paired-binomial p-value."""

    discordant = int(control_only) + int(treatment_only)
    if discordant == 0:
        return 1.0
    tail = sum(
        math.comb(discordant, index)
        for index in range(min(int(control_only), int(treatment_only)) + 1)
    ) / (2**discordant)
    return min(1.0, 2.0 * tail)


def paired_binary_summary(
    control: Sequence[bool], treatment: Sequence[bool]
) -> dict[str, Any]:
    """Summarize paired binary outcomes and their exact discordance test."""

    if not control or len(control) != len(treatment):
        raise PairedValidationError("paired binary inputs must be equal and non-empty")
    control_only = sum(
        bool(left) and not bool(right) for left, right in zip(control, treatment)
    )
    treatment_only = sum(
        bool(right) and not bool(left) for left, right in zip(control, treatment)
    )
    both = sum(bool(left) and bool(right) for left, right in zip(control, treatment))
    neither = len(control) - control_only - treatment_only - both
    control_positive = control_only + both
    treatment_positive = treatment_only + both
    return {
        "pairs": len(control),
        "control_positive": control_positive,
        "control_rate": control_positive / len(control),
        "treatment_positive": treatment_positive,
        "treatment_rate": treatment_positive / len(control),
        "rate_delta_treatment_minus_control": (treatment_positive - control_positive)
        / len(control),
        "control_only": control_only,
        "treatment_only": treatment_only,
        "both": both,
        "neither": neither,
        "paired_exact_two_sided_p": _two_sided_exact_sign_p(
            control_only, treatment_only
        ),
    }


def holm_adjust(p_values: Mapping[str, float]) -> dict[str, float]:
    """Return Holm family-wise adjusted p-values keyed like the inputs."""

    if not p_values:
        raise PairedValidationError("Holm correction requires at least one p-value")
    ordered = sorted((float(value), key) for key, value in p_values.items())
    if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value, _ in ordered):
        raise PairedValidationError("invalid p-value for Holm correction")
    adjusted: dict[str, float] = {}
    running = 0.0
    count = len(ordered)
    for rank, (value, key) in enumerate(ordered):
        running = max(running, min(1.0, (count - rank) * value))
        adjusted[key] = running
    return adjusted


def _bootstrap_rate_delta(
    control_numerators: np.ndarray,
    treatment_numerators: np.ndarray,
    denominators: np.ndarray,
    *,
    reps: int,
    seed: int,
    scale: float = 1.0,
) -> dict[str, Any]:
    """Bootstrap paired rate differences by resampling whole event blocks."""

    arrays = (control_numerators, treatment_numerators, denominators)
    if not len(control_numerators) or any(
        len(value) != len(control_numerators) for value in arrays
    ):
        raise PairedValidationError(
            "bootstrap block arrays must be equal and non-empty"
        )
    if reps < 1 or np.any(denominators <= 0):
        raise PairedValidationError("invalid bootstrap configuration")
    observed = scale * (
        float(treatment_numerators.sum()) / float(denominators.sum())
        - float(control_numerators.sum()) / float(denominators.sum())
    )
    rng = np.random.default_rng(seed)
    values = np.empty(reps, dtype=float)
    blocks = len(control_numerators)
    batch_size = 512
    for start in range(0, reps, batch_size):
        stop = min(reps, start + batch_size)
        indices = rng.integers(0, blocks, size=(stop - start, blocks))
        denominator = denominators[indices].sum(axis=1)
        values[start:stop] = scale * (
            treatment_numerators[indices].sum(axis=1) / denominator
            - control_numerators[indices].sum(axis=1) / denominator
        )
    return {
        "blocks": blocks,
        "reps": reps,
        "seed": seed,
        "observed_delta": observed,
        "ci95_percentile": [
            float(np.quantile(values, 0.025)),
            float(np.quantile(values, 0.975)),
        ],
    }


def _cluster_sign_flip_p(
    paired_block_differences: np.ndarray, *, reps: int, seed: int
) -> dict[str, Any]:
    """Test a paired total difference by random signs on whole event blocks."""

    differences = np.asarray(paired_block_differences, dtype=float)
    if not len(differences) or reps < 1 or not np.isfinite(differences).all():
        raise PairedValidationError("invalid cluster sign-flip inputs")
    observed = abs(float(differences.sum()))
    rng = np.random.default_rng(seed)
    extreme = 0
    batch_size = 512
    for start in range(0, reps, batch_size):
        stop = min(reps, start + batch_size)
        signs = rng.integers(0, 2, size=(stop - start, len(differences))) * 2 - 1
        totals = np.abs((signs * differences).sum(axis=1))
        extreme += int(np.sum(totals >= observed - 1e-12))
    return {
        "blocks": len(differences),
        "nonzero_blocks": int(np.count_nonzero(differences)),
        "reps": reps,
        "seed": seed,
        "observed_absolute_total_difference": observed,
        "two_sided_p_add_one": (extreme + 1) / (reps + 1),
        "method": "paired event-cluster Monte Carlo sign-flip",
    }


def _validate_evaluation(
    evaluation: Mapping[str, Any],
    predictions_path: Path,
    *,
    expected_manifest_sha256: str,
    expected_weights_sha256: str,
) -> None:
    """Fail closed on a frozen evaluator receipt and its prediction ledger."""

    if str(evaluation.get("manifest_sha256")) != expected_manifest_sha256:
        raise PairedValidationError("evaluation manifest identity drifted")
    if str(evaluation.get("weights_sha256")) != expected_weights_sha256:
        raise PairedValidationError("evaluation weight identity drifted")
    if str(evaluation.get("predictions_sha256")) != sha256_file(predictions_path):
        raise PairedValidationError("prediction ledger hash drifted")
    if evaluation.get("holdout_consumed") is not False:
        raise PairedValidationError("holdout must remain sealed")
    for key in ("active_or_frozen_changed", "promoted", "deployed"):
        if evaluation.get(key) is not False:
            raise PairedValidationError(f"unsafe evaluation state: {key}")


def _align_rows(
    control: Sequence[Mapping[str, Any]], treatment: Sequence[Mapping[str, Any]]
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Return deterministic pairs after enforcing all causal identities."""

    def index(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for source in rows:
            row = dict(source)
            sample_id = str(row["dataset_sample_id"])
            if sample_id in result:
                raise PairedValidationError(f"duplicate sample id: {sample_id}")
            result[sample_id] = row
        return result

    left = index(control)
    right = index(treatment)
    if set(left) != set(right):
        raise PairedValidationError("paired sample membership drifted")
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for sample_id in sorted(left):
        control_row = left[sample_id]
        treatment_row = right[sample_id]
        if control_row.get("sample_kind") != treatment_row.get("sample_kind"):
            raise PairedValidationError(f"sample kind drifted: {sample_id}")
        kind = str(control_row["sample_kind"])
        identity_keys = (
            ("event_id", "direction", "post_bars")
            if kind == "positive"
            else ("negative_event_id", "negative_kind")
        )
        for key in identity_keys:
            if control_row.get(key) != treatment_row.get(key):
                raise PairedValidationError(
                    f"paired identity drifted for {sample_id}: {key}"
                )
        pairs.append((control_row, treatment_row))
    return pairs


def _event_outcomes(
    pairs: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]], mode: str
) -> tuple[list[str], list[bool], list[bool]]:
    """Collapse positive variants to one paired outcome per requested surface."""

    grouped: defaultdict[str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]] = (
        defaultdict(list)
    )
    for left, right in pairs:
        grouped[str(left["event_id"])].append((left, right))
    ids: list[str] = []
    control: list[bool] = []
    treatment: list[bool] = []
    for event_id in sorted(grouped):
        variants = grouped[event_id]
        posts = [int(left["post_bars"]) for left, _ in variants]
        if len(posts) != len(set(posts)):
            raise PairedValidationError(f"duplicate post variant in event {event_id}")
        if mode == "earliest":
            target = min(posts)
            selected = [
                pair for pair in variants if int(pair[0]["post_bars"]) == target
            ]
        elif mode == "post2":
            selected = [pair for pair in variants if int(pair[0]["post_bars"]) == 2]
            if not selected:
                continue
        elif mode == "any":
            selected = variants
        else:
            raise PairedValidationError(f"unsupported event mode: {mode}")
        ids.append(event_id)
        control.append(any(bool(left["true_hit"]) for left, _ in selected))
        treatment.append(any(bool(right["true_hit"]) for _, right in selected))
    return ids, control, treatment


def _block_contributions(
    pairs: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]],
    *,
    event_key: str,
    control_value: Callable[[Mapping[str, Any]], float],
    treatment_value: Callable[[Mapping[str, Any]], float],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Reduce paired rows to numerator and denominator arrays per event block."""

    grouped: defaultdict[str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]] = (
        defaultdict(list)
    )
    for pair in pairs:
        grouped[str(pair[0][event_key])].append(pair)
    left_values: list[float] = []
    right_values: list[float] = []
    denominators: list[float] = []
    for event_id in sorted(grouped):
        rows = grouped[event_id]
        left_values.append(sum(control_value(left) for left, _ in rows))
        right_values.append(sum(treatment_value(right) for _, right in rows))
        denominators.append(float(len(rows)))
    return (
        np.asarray(left_values, dtype=float),
        np.asarray(right_values, dtype=float),
        np.asarray(denominators, dtype=float),
    )


def _paired_surface(
    control: Sequence[bool],
    treatment: Sequence[bool],
    *,
    bootstrap_reps: int,
    seed: int,
) -> dict[str, Any]:
    summary = paired_binary_summary(control, treatment)
    summary["event_block_bootstrap"] = _bootstrap_rate_delta(
        np.asarray(control, dtype=float),
        np.asarray(treatment, dtype=float),
        np.ones(len(control), dtype=float),
        reps=bootstrap_reps,
        seed=seed,
    )
    return summary


def compare_rows(
    control_rows: Sequence[Mapping[str, Any]],
    treatment_rows: Sequence[Mapping[str, Any]],
    *,
    bootstrap_reps: int = DEFAULT_BOOTSTRAP_REPS,
    permutation_reps: int = DEFAULT_PERMUTATION_REPS,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Compute the preregistered paired positive and negative surfaces."""

    pairs = _align_rows(control_rows, treatment_rows)
    positives = [pair for pair in pairs if pair[0]["sample_kind"] == "positive"]
    negatives = [pair for pair in pairs if pair[0]["sample_kind"] == "negative"]
    if not positives or not negatives:
        raise PairedValidationError("comparison requires positive and negative rows")

    primary: dict[str, dict[str, Any]] = {}
    for offset, mode in enumerate(("earliest", "post2")):
        _ids, left, right = _event_outcomes(positives, mode)
        primary[mode] = _paired_surface(
            left,
            right,
            bootstrap_reps=bootstrap_reps,
            seed=seed + offset,
        )

    negative_left, negative_right, negative_denominator = _block_contributions(
        negatives,
        event_key="negative_event_id",
        control_value=lambda row: float(int(row["boxes"]) > 0),
        treatment_value=lambda row: float(int(row["boxes"]) > 0),
    )
    negative_image_binary = paired_binary_summary(
        [int(left["boxes"]) > 0 for left, _ in negatives],
        [int(right["boxes"]) > 0 for _, right in negatives],
    )
    negative_block_test = _cluster_sign_flip_p(
        negative_right - negative_left,
        reps=permutation_reps,
        seed=seed + 2,
    )
    primary["negative_fired_image_rate"] = {
        **negative_image_binary,
        "event_block_bootstrap": _bootstrap_rate_delta(
            negative_left,
            negative_right,
            negative_denominator,
            reps=bootstrap_reps,
            seed=seed + 2,
        ),
        "event_cluster_sign_flip": negative_block_test,
        "primary_two_sided_p": negative_block_test["two_sided_p_add_one"],
        "paired_exact_image_p_is_descriptive_only": negative_image_binary[
            "paired_exact_two_sided_p"
        ],
    }

    raw_primary_p = {
        "earliest": float(primary["earliest"]["paired_exact_two_sided_p"]),
        "post2": float(primary["post2"]["paired_exact_two_sided_p"]),
        "negative_fired_image_rate": float(
            primary["negative_fired_image_rate"]["primary_two_sided_p"]
        ),
    }
    adjusted = holm_adjust(raw_primary_p)
    for name, value in adjusted.items():
        primary[name]["holm_adjusted_two_sided_p"] = value

    _ids, any_left, any_right = _event_outcomes(positives, "any")
    positive_left, positive_right, positive_denominator = _block_contributions(
        positives,
        event_key="event_id",
        control_value=lambda row: float(bool(row["true_hit"])),
        treatment_value=lambda row: float(bool(row["true_hit"])),
    )
    box_left, box_right, box_denominator = _block_contributions(
        negatives,
        event_key="negative_event_id",
        control_value=lambda row: float(row["boxes"]),
        treatment_value=lambda row: float(row["boxes"]),
    )

    secondary = {
        "any_variant_event_recall": _paired_surface(
            any_left,
            any_right,
            bootstrap_reps=bootstrap_reps,
            seed=seed + 3,
        ),
        "positive_image_recall": {
            **paired_binary_summary(
                [bool(left["true_hit"]) for left, _ in positives],
                [bool(right["true_hit"]) for _, right in positives],
            ),
            "event_block_bootstrap": _bootstrap_rate_delta(
                positive_left,
                positive_right,
                positive_denominator,
                reps=bootstrap_reps,
                seed=seed + 4,
            ),
        },
        "negative_false_boxes_per_1000_images": _bootstrap_rate_delta(
            box_left,
            box_right,
            box_denominator,
            reps=bootstrap_reps,
            seed=seed + 5,
            scale=1000.0,
        ),
        "positive_image_recall_by_direction": {},
    }
    for offset, direction in enumerate(("LONG", "SHORT"), start=6):
        subset = [pair for pair in positives if pair[0]["direction"] == direction]
        left, right, denominator = _block_contributions(
            subset,
            event_key="event_id",
            control_value=lambda row: float(bool(row["true_hit"])),
            treatment_value=lambda row: float(bool(row["true_hit"])),
        )
        secondary["positive_image_recall_by_direction"][direction] = {
            **paired_binary_summary(
                [bool(control["true_hit"]) for control, _ in subset],
                [bool(treatment["true_hit"]) for _, treatment in subset],
            ),
            "event_block_bootstrap": _bootstrap_rate_delta(
                left,
                right,
                denominator,
                reps=bootstrap_reps,
                seed=seed + offset,
            ),
        }

    improved_positive = [
        name
        for name in ("earliest", "post2")
        if float(primary[name]["rate_delta_treatment_minus_control"]) > 0.0
        and float(primary[name]["holm_adjusted_two_sided_p"]) < PRIMARY_ALPHA
    ]
    negative_not_increased = (
        float(
            primary["negative_fired_image_rate"]["rate_delta_treatment_minus_control"]
        )
        <= 0.0
    )
    return {
        "counts": {
            "paired_images": len(pairs),
            "positive_images": len(positives),
            "negative_images": len(negatives),
            "positive_events": len({str(left["event_id"]) for left, _ in positives}),
            "negative_events": len(
                {str(left["negative_event_id"]) for left, _ in negatives}
            ),
        },
        "primary_surfaces": primary,
        "secondary_surfaces": secondary,
        "multiplicity_control": {
            "holm_family": list(raw_primary_p),
            "raw_two_sided_p": raw_primary_p,
            "holm_adjusted_two_sided_p": adjusted,
            "alpha": PRIMARY_ALPHA,
        },
        "decision": {
            "demonstrated_hl2_improvement": bool(
                improved_positive and negative_not_increased
            ),
            "significantly_improved_positive_primary_surfaces": improved_positive,
            "negative_fired_image_rate_not_increased": negative_not_increased,
            "rule": (
                "At least one prespecified positive primary must improve with "
                "Holm-adjusted two-sided p<0.05, and observed negative fired-image "
                "rate must not increase."
            ),
            "mAP_can_override": False,
        },
    }


def compare(
    *,
    control_evaluation_path: Path,
    control_predictions_path: Path,
    treatment_evaluation_path: Path,
    treatment_predictions_path: Path,
    expected_control_manifest_sha256: str,
    expected_treatment_manifest_sha256: str,
    expected_control_weights_sha256: str,
    expected_treatment_weights_sha256: str,
    output: Path,
    generator_commit: str,
    bootstrap_reps: int,
    permutation_reps: int,
    seed: int,
) -> dict[str, Any]:
    """Validate input receipts, compare ledgers, and write one immutable result."""

    if output.exists():
        raise FileExistsError(f"refusing to overwrite paired result: {output}")
    control_evaluation = read_json(control_evaluation_path)
    treatment_evaluation = read_json(treatment_evaluation_path)
    _validate_evaluation(
        control_evaluation,
        control_predictions_path,
        expected_manifest_sha256=expected_control_manifest_sha256,
        expected_weights_sha256=expected_control_weights_sha256,
    )
    _validate_evaluation(
        treatment_evaluation,
        treatment_predictions_path,
        expected_manifest_sha256=expected_treatment_manifest_sha256,
        expected_weights_sha256=expected_treatment_weights_sha256,
    )
    metadata_keys = (
        "imgsz",
        "confidence_threshold",
        "nms_iou",
        "true_hit_iou",
        "threshold_tuned",
        "class_names",
    )
    for key in metadata_keys:
        if control_evaluation.get(key) != treatment_evaluation.get(key):
            raise PairedValidationError(f"evaluation contract drifted: {key}")
    for key in ("python", "torch", "ultralytics", "numpy", "device", "batch"):
        if control_evaluation["environment"].get(key) != treatment_evaluation[
            "environment"
        ].get(key):
            raise PairedValidationError(f"evaluation environment drifted: {key}")

    control_rows = read_jsonl(control_predictions_path)
    treatment_rows = read_jsonl(treatment_predictions_path)
    payload = {
        "schema_version": 1,
        "generator_commit": generator_commit,
        "generator_sha256": sha256_file(Path(__file__).resolve()),
        "created_by_python": platform.python_version(),
        "control": {
            "evaluation": str(control_evaluation_path),
            "evaluation_sha256": sha256_file(control_evaluation_path),
            "predictions": str(control_predictions_path),
            "predictions_sha256": sha256_file(control_predictions_path),
            "manifest_sha256": expected_control_manifest_sha256,
            "weights_sha256": expected_control_weights_sha256,
        },
        "treatment": {
            "evaluation": str(treatment_evaluation_path),
            "evaluation_sha256": sha256_file(treatment_evaluation_path),
            "predictions": str(treatment_predictions_path),
            "predictions_sha256": sha256_file(treatment_predictions_path),
            "manifest_sha256": expected_treatment_manifest_sha256,
            "weights_sha256": expected_treatment_weights_sha256,
        },
        "evaluation_contract": {key: control_evaluation[key] for key in metadata_keys},
        "inference": compare_rows(
            control_rows,
            treatment_rows,
            bootstrap_reps=bootstrap_reps,
            permutation_reps=permutation_reps,
            seed=seed,
        ),
        "holdout_consumed": False,
        "threshold_tuned": False,
        "promoted": False,
        "deployed": False,
        "active_or_frozen_changed": False,
        "production_eligible": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-evaluation", type=Path, required=True)
    parser.add_argument("--control-predictions", type=Path, required=True)
    parser.add_argument("--treatment-evaluation", type=Path, required=True)
    parser.add_argument("--treatment-predictions", type=Path, required=True)
    parser.add_argument("--control-manifest-sha256", required=True)
    parser.add_argument("--treatment-manifest-sha256", required=True)
    parser.add_argument("--control-weights-sha256", required=True)
    parser.add_argument("--treatment-weights-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--generator-commit", required=True)
    parser.add_argument("--bootstrap-reps", type=int, default=DEFAULT_BOOTSTRAP_REPS)
    parser.add_argument(
        "--permutation-reps", type=int, default=DEFAULT_PERMUTATION_REPS
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    payload = compare(
        control_evaluation_path=args.control_evaluation.resolve(),
        control_predictions_path=args.control_predictions.resolve(),
        treatment_evaluation_path=args.treatment_evaluation.resolve(),
        treatment_predictions_path=args.treatment_predictions.resolve(),
        expected_control_manifest_sha256=args.control_manifest_sha256,
        expected_treatment_manifest_sha256=args.treatment_manifest_sha256,
        expected_control_weights_sha256=args.control_weights_sha256,
        expected_treatment_weights_sha256=args.treatment_weights_sha256,
        output=args.output.resolve(),
        generator_commit=args.generator_commit,
        bootstrap_reps=args.bootstrap_reps,
        permutation_reps=args.permutation_reps,
        seed=args.seed,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
