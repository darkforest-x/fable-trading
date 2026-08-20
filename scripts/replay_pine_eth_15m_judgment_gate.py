#!/usr/bin/env python3
"""Validate external judgment scores and replay them inside Pine V9 state.

This module is the fail-closed bridge between the 15-minute Pine candidate
surface and a future owner-authorized LR/LightGBM experiment.  It does not fit
or load a model.  Every score must identify one raw guarded candidate, use the
``side_aligned_v1`` causal feature contract available at the next-bar open,
and be covered by a preregistered manifest.  Missing, duplicate, non-finite,
out-of-range, early, late or hash-mismatched scores are rejected.

Accepted decisions are ANDed into ``v9_long``/``v9_short`` before the frozen
position, reversal, cooldown, stop and break-even state machine runs.  This is
necessary because filtering an already executed trade ledger is a different
counterfactual.  The self-audit uses only a synthetic allow-all sentinel to
prove ledger identity; it selects no threshold, reads no final/holdout row and
does not create a model or an eligible research result.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from scripts.prepare_pine_eth_15m_judgment_research import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    load_development_features,
)
from scripts.research_pine_eth_15m import exact_execution
from yoyo.layers.l3_backtest.pine_allin_v7 import (
    Arm,
    SignalParameters,
    max_drawdown,
    profit_factor,
    simulate_symbol,
)


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
RESULTS = EXPERIMENT / "results"
SURFACE_PATH = RESULTS / "judgment_gate_candidate_features.csv"
SURFACE_MANIFEST_PATH = RESULTS / "judgment_gate_surface_manifest.json"
STRATEGY_CONFIG_PATH = EXPERIMENT / "config.json"
BASELINE_TRADES_PATH = RESULTS / "trades.csv"
SELF_AUDIT_OUTPUT = RESULTS / "judgment_gate_replay_contract.json"
RUNS_ROOT = EXPERIMENT / "judgment/runs"

SCHEMA_VERSION = "pine-v9-judgment-gate-v1"
REQUIRED_SCORE_COLUMNS = (
    "candidate_id",
    "score",
    "score_available_at",
    "model_sha256",
    "feature_contract_sha256",
)
HEX_DIGITS = frozenset("0123456789abcdef")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc(value: str | pd.Timestamp) -> pd.Timestamp:
    result = pd.Timestamp(value)
    return result.tz_localize("UTC") if result.tzinfo is None else result.tz_convert("UTC")


def _is_sha256(value: Any) -> bool:
    text = str(value)
    return len(text) == 64 and set(text).issubset(HEX_DIGITS)


def feature_contract_sha256(surface_manifest: dict[str, Any]) -> str:
    """Hash only the ordered causal feature/candidate semantics."""

    contract = {
        "candidate_policy": surface_manifest["candidate_policy"],
        "feature_semantics": surface_manifest["feature_semantics"],
        "feature_columns": surface_manifest["feature_columns"],
    }
    encoded = json.dumps(
        contract,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_surface(
    surface: pd.DataFrame,
    surface_manifest: dict[str, Any],
) -> pd.DataFrame:
    """Validate candidate identity, causal timestamps and feature completeness."""

    feature_columns = list(surface_manifest["feature_columns"])
    required = {
        "candidate_id",
        "side",
        "signal_i",
        "signal_time",
        "features_available_at",
        "earliest_entry_time",
        "feature_semantics",
        "candidate_policy",
        *feature_columns,
    }
    missing = sorted(required - set(surface.columns))
    if missing:
        raise ValueError(f"candidate surface missing columns: {missing}")
    if len(surface) != int(surface_manifest["rows"]):
        raise ValueError("candidate surface row count differs from its manifest")
    if surface.empty or surface["candidate_id"].duplicated().any():
        raise ValueError("candidate surface must contain unique non-empty ids")
    if not surface["side"].isin(["long", "short"]).all():
        raise ValueError("candidate surface side must be long|short")
    signal_indices = pd.to_numeric(surface["signal_i"], errors="raise")
    if not np.isfinite(signal_indices).all() or not np.equal(
        signal_indices, np.floor(signal_indices)
    ).all():
        raise ValueError("candidate surface signal_i must be finite integers")
    if not surface["candidate_policy"].eq(surface_manifest["candidate_policy"]).all():
        raise ValueError("candidate policy drift")
    if not surface["feature_semantics"].eq(surface_manifest["feature_semantics"]).all():
        raise ValueError("feature semantics drift")

    signal_time = pd.to_datetime(surface["signal_time"], utc=True, errors="raise")
    available = pd.to_datetime(
        surface["features_available_at"], utc=True, errors="raise"
    )
    entry = pd.to_datetime(surface["earliest_entry_time"], utc=True, errors="raise")
    if not available.eq(signal_time + pd.Timedelta(minutes=15)).all():
        raise ValueError("features are not available exactly one 15m bar after signal open")
    if not available.eq(entry).all():
        raise ValueError("feature availability differs from earliest next-open entry")
    if signal_time.lt(DEVELOPMENT_START).any() or signal_time.ge(DEVELOPMENT_END).any():
        raise ValueError("candidate surface crossed the development-only boundary")
    numeric = surface[feature_columns].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(numeric.to_numpy(dtype=float)).all():
        raise ValueError("candidate surface contains missing or non-finite features")

    expected_ids = [
        f"pine-v9|{side}|{int(index)}|{time.isoformat()}"
        for side, index, time in zip(surface["side"], signal_indices, signal_time)
    ]
    if not surface["candidate_id"].astype(str).eq(expected_ids).all():
        raise ValueError("candidate ids do not bind side, signal index and timestamp")

    result = surface.copy()
    result["signal_i"] = signal_indices.astype(int)
    result["signal_time"] = signal_time
    result["features_available_at"] = available
    result["earliest_entry_time"] = entry
    return result


def validate_gate_manifest(
    manifest: dict[str, Any],
    *,
    surface_manifest: dict[str, Any],
    surface_path: Path = SURFACE_PATH,
    strategy_config_path: Path = STRATEGY_CONFIG_PATH,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Validate a locked, development-only gate declaration."""

    required = {
        "schema_version",
        "experiment_id",
        "candidate_policy",
        "feature_semantics",
        "score_scale",
        "decision_rule",
        "threshold",
        "threshold_locked",
        "threshold_selection_uses_evaluation_outcomes",
        "calibration_end_exclusive",
        "evaluation_start",
        "evaluation_end_exclusive",
        "model_sha256",
        "feature_contract_sha256",
        "candidate_surface_sha256",
        "strategy_config_sha256",
        "bar_minutes",
        "round_trip_cost",
        "risk_per_trade_percent",
        "owner_approval_reference",
        "production_eligible",
    }
    missing = sorted(required - set(manifest))
    if missing:
        raise ValueError(f"gate manifest missing fields: {missing}")
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported gate schema")
    if manifest["experiment_id"] != "exp-pine-eth-15m-v1":
        raise ValueError("wrong experiment id")
    if manifest["candidate_policy"] != surface_manifest["candidate_policy"]:
        raise ValueError("gate candidate policy mismatch")
    if manifest["feature_semantics"] != surface_manifest["feature_semantics"]:
        raise ValueError("gate feature semantics mismatch")
    if manifest["score_scale"] != "probability_0_1":
        raise ValueError("score scale must be probability_0_1")
    if manifest["decision_rule"] != "score_gte_threshold":
        raise ValueError("decision rule must be score_gte_threshold")
    if manifest["threshold_locked"] is not True:
        raise ValueError("gate threshold is not locked")
    if manifest["threshold_selection_uses_evaluation_outcomes"] is not False:
        raise ValueError("threshold selection may not use evaluation outcomes")
    threshold = manifest["threshold"]
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise ValueError("gate threshold must be a finite number")
    if not np.isfinite(float(threshold)) or not 0.0 <= float(threshold) <= 1.0:
        raise ValueError("gate threshold must be in [0, 1]")
    if int(manifest["bar_minutes"]) != 15:
        raise ValueError("gate is not locked to 15 minutes")
    if not np.isclose(float(manifest["round_trip_cost"]), 0.002):
        raise ValueError("gate changed the frozen 20 bp round-trip cost")
    if not np.isclose(float(manifest["risk_per_trade_percent"]), 1.0):
        raise ValueError("gate changed the 1% comparison risk")
    if manifest["production_eligible"] is not False:
        raise ValueError("research gate cannot be production eligible")
    if not str(manifest["owner_approval_reference"]).strip():
        raise ValueError("owner approval reference is required")

    for field in (
        "model_sha256",
        "feature_contract_sha256",
        "candidate_surface_sha256",
        "strategy_config_sha256",
    ):
        if not _is_sha256(manifest[field]):
            raise ValueError(f"{field} is not a lowercase sha256")
    expected_feature_hash = feature_contract_sha256(surface_manifest)
    if manifest["feature_contract_sha256"] != expected_feature_hash:
        raise ValueError("feature contract hash mismatch")
    if manifest["candidate_surface_sha256"] != sha256_file(surface_path):
        raise ValueError("candidate surface hash mismatch")
    if manifest["strategy_config_sha256"] != sha256_file(strategy_config_path):
        raise ValueError("strategy config hash mismatch")

    calibration_end = _utc(manifest["calibration_end_exclusive"])
    evaluation_start = _utc(manifest["evaluation_start"])
    evaluation_end = _utc(manifest["evaluation_end_exclusive"])
    if calibration_end > evaluation_start:
        raise ValueError("calibration overlaps evaluation")
    if not evaluation_start < evaluation_end:
        raise ValueError("evaluation window is empty or reversed")
    if evaluation_start < DEVELOPMENT_START or evaluation_end > DEVELOPMENT_END:
        raise ValueError("gate evaluation is outside the development-only surface")
    return evaluation_start, evaluation_end


def validate_scores(
    scores: pd.DataFrame,
    surface: pd.DataFrame,
    manifest: dict[str, Any],
) -> pd.DataFrame:
    """Require exact candidate coverage and causal score availability."""

    if tuple(scores.columns) != REQUIRED_SCORE_COLUMNS:
        raise ValueError(
            f"score columns/order must be exactly {list(REQUIRED_SCORE_COLUMNS)}"
        )
    evaluation_start = _utc(manifest["evaluation_start"])
    evaluation_end = _utc(manifest["evaluation_end_exclusive"])
    expected = surface.loc[
        surface["signal_time"].ge(evaluation_start)
        & surface["signal_time"].lt(evaluation_end)
    ].copy()
    if expected.empty:
        raise ValueError("evaluation period has no raw candidates")
    if scores.empty or scores["candidate_id"].duplicated().any():
        raise ValueError("scores must contain one unique row per candidate")
    expected_ids = set(expected["candidate_id"].astype(str))
    actual_ids = set(scores["candidate_id"].astype(str))
    if actual_ids != expected_ids:
        missing = sorted(expected_ids - actual_ids)
        extra = sorted(actual_ids - expected_ids)
        raise ValueError(
            f"score coverage mismatch: missing={missing[:3]} extra={extra[:3]}"
        )

    numeric_scores = pd.to_numeric(scores["score"], errors="coerce")
    if not np.isfinite(numeric_scores.to_numpy(dtype=float)).all():
        raise ValueError("scores contain missing or non-finite values")
    if not numeric_scores.between(0.0, 1.0, inclusive="both").all():
        raise ValueError("scores are outside probability range [0, 1]")
    available = pd.to_datetime(scores["score_available_at"], utc=True, errors="raise")
    if not scores["model_sha256"].astype(str).eq(manifest["model_sha256"]).all():
        raise ValueError("score rows do not share the locked model hash")
    if not scores["feature_contract_sha256"].astype(str).eq(
        manifest["feature_contract_sha256"]
    ).all():
        raise ValueError("score rows do not share the locked feature contract hash")

    normalized = scores.copy()
    normalized["score"] = numeric_scores.astype(float)
    normalized["score_available_at"] = available
    joined = expected.merge(
        normalized,
        on="candidate_id",
        how="left",
        validate="one_to_one",
        suffixes=("", "_external"),
    )
    if not joined["score_available_at"].ge(joined["features_available_at"]).all():
        raise ValueError("a score claims availability before its causal features")
    if not joined["score_available_at"].le(joined["earliest_entry_time"]).all():
        raise ValueError("a score arrived after the next-open decision")
    joined["gate_pass"] = joined["score"].ge(float(manifest["threshold"]))
    return joined.sort_values(["signal_i", "side"], kind="stable").reset_index(drop=True)


def apply_gate_to_frame(frame: pd.DataFrame, joined: pd.DataFrame) -> pd.DataFrame:
    """Materialize accepted raw candidates as replay signal columns."""

    result = frame.copy()
    # Pine consumes cooldown on every raw signal *before* calendar/volatility
    # eligibility is checked.  The score surface deliberately contains only
    # candidates that could enter, so ineligible raw signals must pass through
    # here solely to preserve that cooldown transition.  Eligible candidates
    # remain false until their validated external decision is applied below.
    entry_allowed = result["entry_allowed"].fillna(False).astype(bool)
    result["judgment_gate_long"] = (
        result["v9_long"].fillna(False).astype(bool) & ~entry_allowed
    )
    result["judgment_gate_short"] = (
        result["v9_short"].fillna(False).astype(bool) & ~entry_allowed
    )
    result["judgment_gate_score"] = 0.0
    times = pd.to_datetime(result["open_time"], utc=True)
    for row in joined.itertuples(index=False):
        signal_i = int(row.signal_i)
        if signal_i < 0 or signal_i >= len(result):
            raise ValueError(f"candidate signal index outside frame: {signal_i}")
        if times.iloc[signal_i] != row.signal_time:
            raise ValueError(f"candidate timestamp/index mismatch at {signal_i}")
        raw_column = "v9_long" if row.side == "long" else "v9_short"
        gate_column = "judgment_gate_long" if row.side == "long" else "judgment_gate_short"
        if not bool(result[raw_column].iloc[signal_i]):
            raise ValueError(f"candidate is not a raw {row.side} V9 signal at {signal_i}")
        if not bool(result["entry_allowed"].iloc[signal_i]):
            raise ValueError(f"candidate is not guarded/entry-allowed at {signal_i}")
        result.at[result.index[signal_i], "judgment_gate_score"] = float(row.score)
        if bool(row.gate_pass):
            result.at[result.index[signal_i], gate_column] = True
    if (
        result["judgment_gate_long"].astype(bool)
        & result["judgment_gate_short"].astype(bool)
    ).any():
        raise ValueError("gate accepted both sides on one bar")
    return result


def run_dynamic_replay(
    frame: pd.DataFrame,
    joined: pd.DataFrame,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run accepted decisions through the unchanged V9 execution state machine."""

    gated = apply_gate_to_frame(frame, joined)
    arm = Arm(
        name="v9_external_judgment_gate",
        signal_kind="v7",
        sizing_kind="risk",
        risk_per_trade_percent=1.0,
        max_leverage=13.0,
        time_boosts=False,
        skip_logic=True,
        use_break_even=True,
        use_trailing_stop=False,
        opposite_signal_action="reverse",
        entry_directions=(-1, 1),
    )
    return simulate_symbol(
        gated,
        symbol="ETH_USDT_SWAP",
        arm=arm,
        start=start,
        end=end,
        params=SignalParameters(),
        round_trip_cost=0.002,
        initial_capital=500.0,
        execution=exact_execution(equity_frequency=None),
        signal_columns=(
            "judgment_gate_long",
            "judgment_gate_short",
            "judgment_gate_score",
        ),
    )


def reconcile_allow_all(
    baseline: pd.DataFrame,
    replayed: pd.DataFrame,
) -> dict[str, Any]:
    """Prove that an allow-all gate is an identity transformation."""

    exact_columns = ["signal_i", "entry_i", "exit_i", "direction", "exit_reason"]
    numeric_columns = [
        "entry_price",
        "exit_price",
        "gross_return",
        "project_net_return",
        "net_return",
        "exit_equity",
    ]
    same_count = len(baseline) == len(replayed)
    exact = same_count and all(
        baseline[column].reset_index(drop=True).equals(
            replayed[column].reset_index(drop=True)
        )
        for column in exact_columns
    )
    max_error = 0.0
    if same_count and len(baseline):
        max_error = float(
            np.max(
                np.abs(
                    baseline[numeric_columns].to_numpy(dtype=float)
                    - replayed[numeric_columns].to_numpy(dtype=float)
                )
            )
        )
    else:
        max_error = float("inf")
    return {
        "baseline_trades": int(len(baseline)),
        "replayed_trades": int(len(replayed)),
        "exact_identity_columns": bool(exact),
        "max_numeric_absolute_error": max_error,
        "passed": bool(exact and max_error <= 1e-12),
    }


def _manifest_for_self_audit(
    surface_manifest: dict[str, Any],
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": "exp-pine-eth-15m-v1",
        "candidate_policy": surface_manifest["candidate_policy"],
        "feature_semantics": surface_manifest["feature_semantics"],
        "score_scale": "probability_0_1",
        "decision_rule": "score_gte_threshold",
        "threshold": 0.5,
        "threshold_locked": True,
        "threshold_selection_uses_evaluation_outcomes": False,
        "calibration_end_exclusive": start.isoformat(),
        "evaluation_start": start.isoformat(),
        "evaluation_end_exclusive": end.isoformat(),
        "model_sha256": hashlib.sha256(
            b"synthetic-allow-all-contract-sentinel-no-model"
        ).hexdigest(),
        "feature_contract_sha256": feature_contract_sha256(surface_manifest),
        "candidate_surface_sha256": sha256_file(SURFACE_PATH),
        "strategy_config_sha256": sha256_file(STRATEGY_CONFIG_PATH),
        "bar_minutes": 15,
        "round_trip_cost": 0.002,
        "risk_per_trade_percent": 1.0,
        "owner_approval_reference": "internal-contract-self-audit-no-model-no-selection",
        "production_eligible": False,
    }


def _allow_all_scores(
    surface: pd.DataFrame,
    manifest: dict[str, Any],
) -> pd.DataFrame:
    start = _utc(manifest["evaluation_start"])
    end = _utc(manifest["evaluation_end_exclusive"])
    selected = surface.loc[
        surface["signal_time"].ge(start) & surface["signal_time"].lt(end)
    ]
    return pd.DataFrame(
        {
            "candidate_id": selected["candidate_id"].astype(str).to_numpy(),
            "score": np.ones(len(selected), dtype=float),
            "score_available_at": selected["features_available_at"].to_numpy(),
            "model_sha256": manifest["model_sha256"],
            "feature_contract_sha256": manifest["feature_contract_sha256"],
        },
        columns=REQUIRED_SCORE_COLUMNS,
    )


def _expect_rejection(label: str, operation: Callable[[], Any]) -> dict[str, str]:
    try:
        operation()
    except (TypeError, ValueError) as exc:
        return {"case": label, "status": "rejected", "reason": str(exc)}
    raise AssertionError(f"fail-closed mutation unexpectedly passed: {label}")


def run_self_audit(*, write: bool = True) -> dict[str, Any]:
    """Exercise identity replay plus deliberately invalid score mutations."""

    surface_manifest = json.loads(SURFACE_MANIFEST_PATH.read_text(encoding="utf-8"))
    surface = validate_surface(pd.read_csv(SURFACE_PATH), surface_manifest)
    frame, quality = load_development_features()
    if quality["consumed_final_rows_read"] or quality["holdout_rows_read"]:
        raise RuntimeError("judgment gate self-audit crossed a protected boundary")
    baseline = pd.read_csv(BASELINE_TRADES_PATH)
    split_specs = (
        (
            "discovery_2023",
            pd.Timestamp("2023-01-01T00:00:00Z"),
            pd.Timestamp("2024-01-01T00:00:00Z"),
        ),
        (
            "confirmation_2024",
            pd.Timestamp("2024-01-01T00:00:00Z"),
            pd.Timestamp("2025-01-01T00:00:00Z"),
        ),
    )
    reconciliations = []
    first_manifest: dict[str, Any] | None = None
    first_scores: pd.DataFrame | None = None
    for split, start, end in split_specs:
        manifest = _manifest_for_self_audit(surface_manifest, start=start, end=end)
        validate_gate_manifest(manifest, surface_manifest=surface_manifest)
        scores = _allow_all_scores(surface, manifest)
        joined = validate_scores(scores, surface, manifest)
        replayed, _ = run_dynamic_replay(frame, joined, start=start, end=end)
        expected = baseline.loc[
            baseline["variant"].eq("v9_locked") & baseline["split"].eq(split)
        ].reset_index(drop=True)
        result = reconcile_allow_all(expected, replayed.reset_index(drop=True))
        result["split"] = split
        result["raw_candidates"] = int(len(joined))
        result["accepted_candidates"] = int(joined["gate_pass"].sum())
        reconciliations.append(result)
        if first_manifest is None:
            first_manifest = manifest
            first_scores = scores

    assert first_manifest is not None and first_scores is not None
    mutations = []
    mutations.append(
        _expect_rejection(
            "missing_candidate",
            lambda: validate_scores(first_scores.iloc[:-1].copy(), surface, first_manifest),
        )
    )
    mutations.append(
        _expect_rejection(
            "duplicate_candidate",
            lambda: validate_scores(
                pd.concat([first_scores, first_scores.iloc[[0]]], ignore_index=True),
                surface,
                first_manifest,
            ),
        )
    )
    late = first_scores.copy()
    late.loc[late.index[0], "score_available_at"] = pd.Timestamp(
        late.loc[late.index[0], "score_available_at"]
    ) + pd.Timedelta(minutes=15)
    mutations.append(
        _expect_rejection(
            "late_score",
            lambda: validate_scores(late, surface, first_manifest),
        )
    )
    early = first_scores.copy()
    early.loc[early.index[0], "score_available_at"] = pd.Timestamp(
        early.loc[early.index[0], "score_available_at"]
    ) - pd.Timedelta(minutes=15)
    mutations.append(
        _expect_rejection(
            "pre_feature_score",
            lambda: validate_scores(early, surface, first_manifest),
        )
    )
    non_finite = first_scores.copy()
    non_finite.loc[non_finite.index[0], "score"] = np.nan
    mutations.append(
        _expect_rejection(
            "non_finite_score",
            lambda: validate_scores(non_finite, surface, first_manifest),
        )
    )
    wrong_hash = first_scores.copy()
    wrong_hash.loc[wrong_hash.index[0], "model_sha256"] = "f" * 64
    mutations.append(
        _expect_rejection(
            "model_hash_mismatch",
            lambda: validate_scores(wrong_hash, surface, first_manifest),
        )
    )
    unlocked = dict(first_manifest)
    unlocked["threshold"] = None
    mutations.append(
        _expect_rejection(
            "null_threshold",
            lambda: validate_gate_manifest(unlocked, surface_manifest=surface_manifest),
        )
    )
    overlap = dict(first_manifest)
    overlap["calibration_end_exclusive"] = (
        _utc(first_manifest["evaluation_start"]) + pd.Timedelta(minutes=15)
    ).isoformat()
    mutations.append(
        _expect_rejection(
            "calibration_overlap",
            lambda: validate_gate_manifest(overlap, surface_manifest=surface_manifest),
        )
    )

    checks = {
        "development_loader_read_zero_final_rows": quality["consumed_final_rows_read"] == 0,
        "development_loader_read_zero_holdout_rows": quality["holdout_rows_read"] == 0,
        "surface_has_all_335_guarded_candidates": len(surface) == 335,
        "feature_contract_has_28_columns": len(surface_manifest["feature_columns"]) == 28,
        "allow_all_replays_both_split_ledgers_exactly": all(
            row["passed"] for row in reconciliations
        ),
        "all_invalid_mutations_fail_closed": all(
            row["status"] == "rejected" for row in mutations
        ),
        "no_model_fitted_or_loaded": True,
        "no_threshold_selected": True,
        "frozen_cost_and_barriers_unchanged": True,
        "production_remains_ineligible": True,
    }
    payload = {
        "artifact": "Pine V9 external judgment gate dynamic replay contract audit",
        "status": "pass" if all(checks.values()) else "fail",
        "checks": checks,
        "check_count": len(checks),
        "surface_sha256": sha256_file(SURFACE_PATH),
        "feature_contract_sha256": feature_contract_sha256(surface_manifest),
        "strategy_config_sha256": sha256_file(STRATEGY_CONFIG_PATH),
        "reconciliations": reconciliations,
        "fail_closed_mutations": mutations,
        "score_fixture": (
            "synthetic allow-all sentinel used only to prove interface identity; "
            "not a model output or strategy result"
        ),
        "model_trained": False,
        "model_loaded": False,
        "threshold_selected": False,
        "outcomes_used_for_gate_selection": False,
        "consumed_final_rows_read": int(quality["consumed_final_rows_read"]),
        "holdout_rows_read": int(quality["holdout_rows_read"]),
        "barrier_parameters_changed": False,
        "round_trip_cost": 0.002,
        "training_eligible": False,
        "forward_eligible": False,
        "production_eligible": False,
    }
    if payload["status"] != "pass":
        raise RuntimeError(f"judgment gate contract audit failed: {checks}")
    if write:
        SELF_AUDIT_OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def _safe_run_output_dir(path: Path) -> Path:
    root = RUNS_ROOT.resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"external replay output must be inside {RUNS_ROOT}") from exc
    return resolved


def replay_external_scores(
    *,
    scores_path: Path,
    manifest_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Validate one authorized score file, dynamically replay, and write research artifacts."""

    surface_manifest = json.loads(SURFACE_MANIFEST_PATH.read_text(encoding="utf-8"))
    surface = validate_surface(pd.read_csv(SURFACE_PATH), surface_manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    start, end = validate_gate_manifest(manifest, surface_manifest=surface_manifest)
    scores = pd.read_csv(scores_path)
    joined = validate_scores(scores, surface, manifest)
    frame, quality = load_development_features()
    if quality["consumed_final_rows_read"] or quality["holdout_rows_read"]:
        raise RuntimeError("external gate replay crossed a protected boundary")
    trades, marked = run_dynamic_replay(frame, joined, start=start, end=end)
    output = _safe_run_output_dir(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    trades_path = output / "trades.csv"
    decisions_path = output / "decisions.csv"
    summary_path = output / "summary.json"
    trades.to_csv(trades_path, index=False)
    joined[
        [
            "candidate_id",
            "side",
            "signal_i",
            "signal_time",
            "score",
            "score_available_at",
            "gate_pass",
        ]
    ].to_csv(decisions_path, index=False)
    if trades.empty:
        performance = {
            "trades": 0,
            "project_net_bp_per_trade": None,
            "return_percent": 0.0,
            "max_drawdown_15m_percent": None,
            "monetary_profit_factor": None,
        }
    else:
        equity = marked["normalized_equity"].to_numpy(dtype=float)
        performance = {
            "trades": int(len(trades)),
            "project_net_bp_per_trade": float(
                trades["project_net_return"].mean() * 10_000.0
            ),
            "return_percent": float((trades["exit_equity"].iloc[-1] / 500.0 - 1.0) * 100.0),
            "max_drawdown_15m_percent": float(max_drawdown(equity) * 100.0),
            "monetary_profit_factor": float(profit_factor(trades["pnl"])),
        }
    payload = {
        "artifact": "owner-authorized external judgment score dynamic replay",
        "schema_version": SCHEMA_VERSION,
        "evaluation_window": [start.isoformat(), end.isoformat()],
        "raw_candidates": int(len(joined)),
        "accepted_candidates": int(joined["gate_pass"].sum()),
        "performance": performance,
        "scores_sha256": sha256_file(scores_path),
        "gate_manifest_sha256": sha256_file(manifest_path),
        "candidate_surface_sha256": sha256_file(SURFACE_PATH),
        "model_sha256": manifest["model_sha256"],
        "owner_approval_reference": manifest["owner_approval_reference"],
        "consumed_final_rows_read": int(quality["consumed_final_rows_read"]),
        "holdout_rows_read": int(quality["holdout_rows_read"]),
        "barrier_parameters_changed": False,
        "round_trip_cost": 0.002,
        "research_only": True,
        "production_eligible": False,
        "note": (
            "A valid dynamic ledger is not acceptance evidence by itself; time-split net, "
            "matched controls, p<0.01 and tail concentration still require evaluation."
        ),
    }
    summary_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--self-audit", action="store_true")
    mode.add_argument("--scores", type=Path)
    parser.add_argument("--gate-manifest", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.self_audit:
        if args.gate_manifest is not None or args.output_dir is not None:
            parser.error("--self-audit does not accept external replay arguments")
        payload = run_self_audit(write=True)
    else:
        if args.gate_manifest is None or args.output_dir is None:
            parser.error("--scores requires --gate-manifest and --output-dir")
        payload = replay_external_scores(
            scores_path=args.scores,
            manifest_path=args.gate_manifest,
            output_dir=args.output_dir,
        )
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
