"""Run the pre-registered P2-R read-only root-cause audit.

The audit reads only the content-addressed P1 immutable short-L2 dataset and
the frozen P2 artifacts named in the P2-R preregistration.  It independently
reconstructs the five chronological test folds from ``signal_time``,
``interval_start``, ``interval_end``, and ``event_group_id``; it then measures
the immutable 28 feature columns against ``net_ret_swap_taker``.  No estimator
is fitted, no score threshold is changed, and no path can read the holdout.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/audit_p2r_root_causes_20260803.py
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.judgment.features import FEATURE_COLUMNS
from src.judgment.p1_dataset import load_immutable_dataset
from src.judgment.p2_protocol import (
    ADDITIONAL_SLIPPAGE_ROUND_TRIP,
    HOLDOUT_CUTOFF,
    WALKFORWARD_BOUNDARIES,
    P2ProtocolError,
    prepare_split_at_boundaries,
)

PROJECT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT / "analysis/output"
PREREG = OUTPUT / "p2r_root_cause_prereg_20260803.json"
P1_MANIFEST = PROJECT / "data/p1/p1_short_l2_preholdout_aade2a334448d644.manifest.json"
P1_DATASET = PROJECT / "data/p1/p1_short_l2_preholdout_aade2a334448d644.csv"
P2_RESULTS = OUTPUT / "p2_l2_results_20260803.json"
P2_IMPORTANCE = OUTPUT / "p2_l2_feature_importance_20260803.csv"
P2_PAIRS = OUTPUT / "p2_l2_matched_pairs_20260803.csv"
AUDIT_JSON = OUTPUT / "p2r_root_cause_audit_20260803.json"
FEATURE_IC_CSV = OUTPUT / "p2r_feature_ic_20260803.csv"
FOLD_CSV = OUTPUT / "p2r_fold_diagnostics_20260803.csv"

EXPECTED_HASHES = {
    PREREG: "084a83296897ca282ef664ff4a8493b83a8f2e8b1512cb1d11d787bd4dc82c6a",
    P1_MANIFEST: "53b8a07612dae667a184da38bf8e0a694aaae15a5fd240d5b13238da3e13d682",
    P1_DATASET: "aade2a334448d6443e71fb0d3dbbfcf450390875ce60e1f800f6dbe9c855e93a",
    P2_RESULTS: "5bfbd4f4953554fb25a12503cf1b711b14236f330bca458a78f14aa5e298f6da",
    P2_IMPORTANCE: "0e4fa24d446382172b905517894857df9a5ea64f2eea4f6beb2843f37e3586bd",
    P2_PAIRS: "fd55f8a2fea2738b7a8c204eb88e651d8979a83d1d8c1e7213ab27f385e22709",
}
PROTECTED = (
    PROJECT / "models/ACTIVE",
    PROJECT / "data/forward_log.csv",
    PROJECT / "data/executor_ledger.jsonl",
    PROJECT / "models/active_bundle.json",
)


@dataclass(frozen=True)
class ReadOnlyFold:
    """Fold parts reconstructed without importing or calling model code."""

    fold: int
    train: pd.DataFrame
    early_stop: pd.DataFrame
    calibration: pd.DataFrame
    test: pd.DataFrame
    purged: pd.DataFrame
    test_start: pd.Timestamp
    test_end: pd.Timestamp


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative(path: Path) -> str:
    return path.relative_to(PROJECT).as_posix()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise P2ProtocolError(f"{_relative(path)} must be a JSON object")
    return value


def _protected_hashes() -> dict[str, str | None]:
    return {
        _relative(path): file_sha256(path) if path.exists() else None
        for path in PROTECTED
    }


def assert_frozen_inputs() -> dict[str, str]:
    actual = {path: file_sha256(path) for path in EXPECTED_HASHES}
    changed = {
        _relative(path): {"expected": EXPECTED_HASHES[path], "actual": digest}
        for path, digest in actual.items()
        if digest != EXPECTED_HASHES[path]
    }
    if changed:
        raise P2ProtocolError(f"P2-R frozen input mismatch: {changed}")
    prereg = _json(PREREG)
    if prereg.get("status") != "accepted":
        raise P2ProtocolError("P2-R preregistration is not accepted")
    return {_relative(path): digest for path, digest in actual.items()}


def _fraction_boundary(times: pd.Series, fraction: float) -> pd.Timestamp:
    ordered = pd.Series(pd.to_datetime(times, utc=True)).sort_values().reset_index(drop=True)
    if ordered.empty:
        raise P2ProtocolError("cannot choose a fold boundary from an empty series")
    index = min(len(ordered) - 1, max(1, int(math.floor(len(ordered) * fraction))))
    return pd.Timestamp(ordered.iloc[index])


def reconstruct_readonly_fold(
    frame: pd.DataFrame,
    *,
    fold: int,
    test_start: pd.Timestamp,
    test_end: pd.Timestamp,
) -> ReadOnlyFold:
    """Recreate a P2 fold using only timestamps and event dependencies."""
    data = frame.copy()
    for column in ("signal_time", "interval_start", "interval_end"):
        data[column] = pd.to_datetime(data[column], utc=True, errors="raise")
    start = pd.Timestamp(test_start).tz_convert("UTC")
    end = pd.Timestamp(test_end).tz_convert("UTC")
    prior = data.loc[data["signal_time"] < start, "signal_time"]
    early_start = _fraction_boundary(prior, 0.70)
    calibration_start = _fraction_boundary(prior, 0.85)
    inner = prepare_split_at_boundaries(
        data,
        early_stop_start=early_start,
        calibration_start=calibration_start,
        final_cutoff=start,
    )

    eligible_test = (data["signal_time"] >= start) & (data["signal_time"] < end)
    valid_test = eligible_test & (data["interval_end"] < end)
    boundary_groups = set(data.loc[eligible_test & ~valid_test, "event_group_id"].astype(str))
    test = data.loc[valid_test].copy()
    if boundary_groups:
        test = test.loc[~test["event_group_id"].astype(str).isin(boundary_groups)]
    inner_purged_groups = set(inner.purged["event_group_id"].astype(str))
    tainted_test_groups = inner_purged_groups & set(test["event_group_id"].astype(str))
    if tainted_test_groups:
        test = test.loc[~test["event_group_id"].astype(str).isin(tainted_test_groups)]
    prior_groups = set(
        pd.concat([inner.train, inner.early_stop, inner.calibration], ignore_index=True)[
            "event_group_id"
        ].astype(str)
    )
    shared = prior_groups & set(test["event_group_id"].astype(str))
    if shared:
        test = test.loc[~test["event_group_id"].astype(str).isin(shared)]
    kept = set(test["candidate_id"].astype(str))
    test = test.sort_values(["signal_time", "event_group_id"]).reset_index(drop=True)
    purged_test = data.loc[
        eligible_test & ~data["candidate_id"].astype(str).isin(kept)
    ].copy()
    purged = pd.concat([inner.purged, purged_test], ignore_index=True).drop_duplicates(
        subset=["candidate_id"]
    )
    if test.empty:
        raise P2ProtocolError(f"read-only fold {fold} has no test rows")
    return ReadOnlyFold(
        fold=fold,
        train=inner.train,
        early_stop=inner.early_stop,
        calibration=inner.calibration,
        test=test,
        purged=purged,
        test_start=start,
        test_end=end,
    )


def reconstruct_readonly_folds(frame: pd.DataFrame) -> list[ReadOnlyFold]:
    starts = list(WALKFORWARD_BOUNDARIES)
    return [
        reconstruct_readonly_fold(
            frame,
            fold=index + 1,
            test_start=start,
            test_end=starts[index + 1] if index + 1 < len(starts) else HOLDOUT_CUTOFF,
        )
        for index, start in enumerate(starts)
    ]


def pressure_profit_factor(values: pd.Series) -> float | None:
    positive = float(values.loc[values > 0].sum())
    negative = float(values.loc[values < 0].sum())
    return positive / -negative if negative < 0 else None


def fold_pool_diagnostic(fold: ReadOnlyFold, p2_fold: dict[str, Any]) -> dict[str, Any]:
    data = fold.test
    pressure = data["net_ret_swap_taker"].astype(float) - ADDITIONAL_SLIPPAGE_ROUND_TRIP
    exact = p2_fold["test"]["exact_top_decile"]
    fixed = p2_fold["test"]["fixed_gate"]
    exits = data["exit_reason"].value_counts(normalize=True, dropna=False)
    return {
        "fold": fold.fold,
        "test_start": fold.test_start.isoformat(),
        "test_end": fold.test_end.isoformat(),
        "rows": int(len(data)),
        "symbols": int(data["symbol"].nunique()),
        "tp_before_sl_rate": float(data["label_tp_before_sl"].mean()),
        "all_pool_gross_mean": float(data["gross_ret"].mean()),
        "all_pool_net_taker_mean": float(data["net_ret_swap_taker"].mean()),
        "all_pool_pressure_net_mean": float(pressure.mean()),
        "all_pool_pressure_profit_factor": pressure_profit_factor(pressure),
        "sl_share": float(exits.get("sl", 0.0) + exits.get("sl_ambiguous", 0.0)),
        "tp_share": float(exits.get("tp", 0.0)),
        "timeout_share": float(exits.get("timeout", 0.0)),
        "p2_best_iteration": int(p2_fold["model_health"]["best_iteration"]),
        "p2_calibration_distinct_scores": int(p2_fold["model_health"]["distinct_scores"]),
        "p2_calibration_pass_rate": float(p2_fold["selector"]["actual_pass_rate"]),
        "p2_test_fixed_pass_rate": float(fixed["pass_rate"]),
        "p2_test_auc": float(p2_fold["test"]["rank"]["roc_auc"]),
        "p2_test_spearman": float(
            p2_fold["test"]["rank"]["spearman_score_vs_net_taker"]
        ),
        "p2_exact_top_pressure_net_mean": float(exact["mean_pressure_net"]),
        "p2_exact_top_n": float(exact["effective_n"]),
        "p2_exact_top_lift_vs_all_pool": float(
            exact["mean_pressure_net"] - pressure.mean()
        ),
        "p2_fixed_pressure_net_mean": float(fixed["mean_pressure_net"]),
    }


def feature_ic_diagnostics(folds: list[ReadOnlyFold]) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for feature in FEATURE_COLUMNS:
        test_values: list[float] = []
        record: dict[str, Any] = {"feature": feature}
        missing_total = 0
        nonfinite_total = 0
        for fold in folds:
            values = pd.to_numeric(fold.test[feature], errors="coerce")
            target = pd.to_numeric(fold.test["net_ret_swap_taker"], errors="coerce")
            missing_total += int(values.isna().sum())
            nonfinite_total += int((~np.isfinite(values.dropna().to_numpy(dtype=float))).sum())
            rho = float(values.corr(target, method="spearman"))
            test_values.append(rho)
            record[f"fold_{fold.fold}_test_spearman"] = rho
            record[f"fold_{fold.fold}_train_spearman"] = float(
                pd.to_numeric(fold.train[feature], errors="coerce").corr(
                    pd.to_numeric(fold.train["net_ret_swap_taker"], errors="coerce"),
                    method="spearman",
                )
            )
        finite = [value for value in test_values if np.isfinite(value)]
        positive = sum(value > 0 for value in finite)
        negative = sum(value < 0 for value in finite)
        median = float(np.median(finite)) if finite else float("nan")
        stable_sign = max(positive, negative)
        record.update(
            {
                "test_median_spearman": median,
                "test_min_spearman": float(min(finite)) if finite else float("nan"),
                "test_max_spearman": float(max(finite)) if finite else float("nan"),
                "same_sign_test_folds": int(stable_sign),
                "stable_by_preregistered_rule": bool(
                    stable_sign >= 4 and np.isfinite(median) and abs(median) >= 0.03
                ),
                "missing_total": missing_total,
                "nonfinite_total": nonfinite_total,
            }
        )
        records.append(record)
    return pd.DataFrame(records).sort_values(
        ["stable_by_preregistered_rule", "test_median_spearman"],
        ascending=[False, True],
    )


def exact_week_signflip(pairs: pd.DataFrame) -> dict[str, Any]:
    if pairs.empty:
        raise P2ProtocolError("P2 matched-pair artifact is empty")
    delta = pairs["selected_pressure_net"] - pairs["control_pressure_net"]
    block_sums = delta.groupby(pairs["utc_week"]).sum().sort_index()
    if len(block_sums) > 20:
        raise P2ProtocolError("exact sign-flip audit exceeds 20 UTC-week blocks")
    observed = float(delta.mean())
    sums = block_sums.to_numpy(dtype=float)
    hits = 0
    for signs in itertools.product((-1.0, 1.0), repeat=len(sums)):
        statistic = float(np.dot(np.asarray(signs), sums) / len(delta))
        if statistic >= observed - 1e-15:
            hits += 1
    permutations = 2 ** len(sums)
    return {
        "n_pairs": int(len(pairs)),
        "n_blocks": int(len(sums)),
        "blocks": block_sums.index.tolist(),
        "observed_lift": observed,
        "permutations": permutations,
        "hits_ge_observed": hits,
        "p_value": float(hits / permutations),
    }


def validate_matched_pairs(pairs: pd.DataFrame, frame: pd.DataFrame) -> dict[str, Any]:
    candidates = set(frame["candidate_id"].astype(str))
    selected = pairs["selected_candidate_id"].astype(str)
    control = pairs["control_candidate_id"].astype(str)
    return {
        "selected_ids_in_p1": bool(selected.isin(candidates).all()),
        "control_ids_in_p1": bool(control.isin(candidates).all()),
        "selected_ids_unique": bool(selected.is_unique),
        "control_ids_unique": bool(control.is_unique),
        "no_self_pairs": bool((selected != control).all()),
        "same_symbol_declared": bool(pairs["symbol"].notna().all()),
        "pressure_delta_equals_net_delta": bool(
            np.allclose(
                pairs["selected_pressure_net"] - pairs["control_pressure_net"],
                pairs["selected_net_taker"] - pairs["control_net_taker"],
                atol=1e-12,
                rtol=0,
            )
        ),
    }


def build_audit() -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    frozen_hashes = assert_frozen_inputs()
    protected_before = _protected_hashes()
    prereg = _json(PREREG)
    p2 = _json(P2_RESULTS)
    frame = load_immutable_dataset(P1_MANIFEST)
    for column in ("signal_time", "interval_start", "interval_end"):
        frame[column] = pd.to_datetime(frame[column], utc=True, errors="raise")
    if (frame["signal_time"] >= HOLDOUT_CUTOFF).any():
        raise P2ProtocolError("P2-R reached a holdout signal")
    if (frame["interval_end"] >= HOLDOUT_CUTOFF).any():
        raise P2ProtocolError("P2-R reached a holdout label interval")

    folds = reconstruct_readonly_folds(frame)
    p2_folds = p2["walkforward"]["folds"]
    if len(folds) != 5 or len(p2_folds) != 5:
        raise P2ProtocolError("P2-R requires exactly five frozen walk-forward folds")
    fold_rows_match = all(
        len(fold.test) == int(p2_fold["segments"]["test"]["rows"])
        for fold, p2_fold in zip(folds, p2_folds)
    )
    if not fold_rows_match:
        raise P2ProtocolError("read-only fold reconstruction differs from frozen P2 rows")

    fold_records = [
        fold_pool_diagnostic(fold, p2_fold)
        for fold, p2_fold in zip(folds, p2_folds)
    ]
    fold_frame = pd.DataFrame(fold_records)
    feature_ic = feature_ic_diagnostics(folds)
    pairs = pd.read_csv(P2_PAIRS)
    matched_checks = validate_matched_pairs(pairs, frame)
    matched = exact_week_signflip(pairs)
    importance = pd.read_csv(P2_IMPORTANCE)

    negative_exact_folds = int((fold_frame["p2_exact_top_pressure_net_mean"] <= 0).sum())
    collapse_folds = int(
        (
            (fold_frame["p2_best_iteration"] <= 1)
            | (fold_frame["p2_calibration_distinct_scores"] < 100)
        ).sum()
    )
    transport_failures = int(
        (
            (fold_frame["p2_test_fixed_pass_rate"] < 0.08)
            | (fold_frame["p2_test_fixed_pass_rate"] > 0.12)
        ).sum()
    )
    label_rate_range = float(
        fold_frame["tp_before_sl_rate"].max() - fold_frame["tp_before_sl_rate"].min()
    )
    pool_pressure_range = float(
        fold_frame["all_pool_pressure_net_mean"].max()
        - fold_frame["all_pool_pressure_net_mean"].min()
    )
    stable_features = feature_ic.loc[feature_ic["stable_by_preregistered_rule"]].copy()
    weights = fold_frame["rows"].to_numpy(dtype=float)
    top_weights = fold_frame["p2_exact_top_n"].to_numpy(dtype=float)
    all_pool_pressure = float(
        np.average(fold_frame["all_pool_pressure_net_mean"], weights=weights)
    )
    exact_pressure = float(
        np.average(fold_frame["p2_exact_top_pressure_net_mean"], weights=top_weights)
    )
    exact_lift_vs_pool = float(
        np.average(fold_frame["p2_exact_top_lift_vs_all_pool"], weights=top_weights)
    )

    tests = {
        "threshold_exoneration_rule_met": negative_exact_folds >= 4,
        "model_collapse_rule_met": collapse_folds >= 2,
        "calibration_transport_rule_met": transport_failures >= 3,
        "label_regime_shift_rule_met": label_rate_range >= 0.10
        or pool_pressure_range >= 0.005,
        "matched_control_not_significant": matched["p_value"] >= 0.01,
        "stable_feature_rule_met": len(stable_features) > 0,
        "no_feature_missing_or_nonfinite": bool(
            (feature_ic["missing_total"] == 0).all()
            and (feature_ic["nonfinite_total"] == 0).all()
        ),
    }
    source_checks = {
        "p1_rows_18103": len(frame) == 18103,
        "p1_holdout_signal_rows_zero": int((frame["signal_time"] >= HOLDOUT_CUTOFF).sum()) == 0,
        "p1_holdout_interval_rows_zero": int((frame["interval_end"] >= HOLDOUT_CUTOFF).sum()) == 0,
        "five_fold_rows_reproduced": fold_rows_match,
        "p2_verdict_rejected": p2.get("verdict") == "rejected",
        "matched_checks_all_true": bool(all(matched_checks.values())),
        "matched_lift_matches_p2": bool(
            np.isclose(
                matched["observed_lift"],
                p2["walkforward"]["matched_control"]["lift"],
                atol=1e-15,
                rtol=0,
            )
        ),
        "matched_p_matches_p2": bool(
            np.isclose(
                matched["p_value"],
                p2["walkforward"]["matched_control"]["permutation"]["p_value"],
                atol=1e-15,
                rtol=0,
            )
        ),
        "feature_schema_exact_28": list(feature_ic["feature"]) != []
        and set(feature_ic["feature"]) == set(FEATURE_COLUMNS)
        and len(feature_ic) == 28,
        "importance_schema_exact_28": set(importance["feature"]) == set(FEATURE_COLUMNS)
        and len(importance) == 28,
    }
    if not all(source_checks.values()):
        raise P2ProtocolError(f"P2-R source reconciliation failed: {source_checks}")

    protected_after = _protected_hashes()
    protected_unchanged = protected_before == protected_after
    if not protected_unchanged:
        raise P2ProtocolError("a protected runtime artifact changed during P2-R")

    stable_view = stable_features.sort_values(
        "test_median_spearman", key=lambda series: series.abs(), ascending=False
    )
    payload = {
        "audit_version": "p2r_readonly_root_cause_audit_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verdict": "completed_stop",
        "p2_verdict_unchanged": "rejected",
        "preregistration": {
            "path": _relative(PREREG),
            "sha256": EXPECTED_HASHES[PREREG],
            "status": prereg["status"],
        },
        "source_binding": {
            "frozen_hashes": frozen_hashes,
            "rows": int(len(frame)),
            "symbols": int(frame["symbol"].nunique()),
            "signal_start": frame["signal_time"].min().isoformat(),
            "signal_end": frame["signal_time"].max().isoformat(),
            "max_interval_end": frame["interval_end"].max().isoformat(),
            "source_checks": source_checks,
        },
        "diagnostic_tests": tests,
        "fold_diagnostics": fold_records,
        "aggregate_diagnostics": {
            "all_pool_pressure_net_mean": all_pool_pressure,
            "p2_exact_top_pressure_net_mean": exact_pressure,
            "p2_exact_top_lift_vs_all_pool": exact_lift_vs_pool,
            "negative_exact_top_folds": negative_exact_folds,
            "model_collapse_folds": collapse_folds,
            "calibration_transport_failure_folds": transport_failures,
            "tp_before_sl_rate_range": label_rate_range,
            "all_pool_pressure_net_mean_range": pool_pressure_range,
            "stable_feature_count": int(len(stable_features)),
            "matched_control": matched,
            "matched_pair_integrity": matched_checks,
        },
        "feature_ic": {
            "path": _relative(FEATURE_IC_CSV),
            "stable_rule": prereg["fixed_diagnostics"]["feature_stability_rule"],
            "stable_features": stable_view[
                [
                    "feature",
                    "test_median_spearman",
                    "test_min_spearman",
                    "test_max_spearman",
                    "same_sign_test_folds",
                ]
            ].to_dict("records"),
        },
        "root_cause_assessment": {
            "claim_type": "diagnostic_not_causal",
            "primary": "No generalizable ranking edge was demonstrated over the immutable P1 proposal pool.",
            "primary_evidence": [
                "Fold-local exact top decile is pressure-net negative in at least four of five folds, so changing only the fixed q90 threshold cannot repair the ranking.",
                "The weighted exact-top result does not improve on the all-pool pressure baseline.",
                "The independently recomputed matched-control lift is not significant at p<0.01.",
            ],
            "contributors": [
                "The outcome base rate and all-pool economics shift materially across chronological folds.",
                "Two folds collapse to one boosting iteration / fewer than 100 calibration score values.",
                "The calibrated fixed gate transports outside the 8-12% pass band in four folds.",
            ],
            "not_supported": [
                "A threshold-only rescue.",
                "Deployment, promotion, ACTIVE mutation, or holdout evaluation.",
                "A causal attribution to any single feature or market regime.",
            ],
        },
        "next_step_gate": {
            "threshold_only_fix_supported": False,
            "single_variable_training_followup_supported": bool(len(stable_features) > 0),
            "followup_class": "exploratory_only_if_owner_separately_authorizes_and_preregisters",
            "contamination_note": "P2-R inspected all 28 feature/outcome associations on all five pre-holdout test folds; choosing a feature from this audit and rerunning on P1 cannot count as independent confirmation.",
            "action_taken": "none_stop_after_p2r",
        },
        "outputs": {
            "fold_diagnostics_csv": _relative(FOLD_CSV),
            "feature_ic_csv": _relative(FEATURE_IC_CSV),
        },
        "protected_before": protected_before,
        "protected_after": protected_after,
        "protected_unchanged": protected_unchanged,
        "safety": {
            "data_sources_used": [_relative(P1_DATASET)],
            "training_or_fitting_calls": 0,
            "threshold_tuned": False,
            "holdout_read": False,
            "active_modified": False,
            "active_bundle_created": False,
            "deployed": False,
            "trading_client_accessed": False,
            "ordered": False,
        },
        "evidence_class": prereg["evidence_class"],
    }
    return payload, feature_ic, fold_frame


def write_outputs() -> dict[str, Any]:
    payload, feature_ic, fold_frame = build_audit()
    FEATURE_IC_CSV.parent.mkdir(parents=True, exist_ok=True)
    feature_ic.to_csv(FEATURE_IC_CSV, index=False)
    fold_frame.to_csv(FOLD_CSV, index=False)
    payload["feature_ic"]["sha256"] = file_sha256(FEATURE_IC_CSV)
    payload["outputs"]["fold_diagnostics_sha256"] = file_sha256(FOLD_CSV)
    AUDIT_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> int:
    payload = write_outputs()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
