"""Run the pre-registered P2-M read-only mechanism audit.

The sole data source is the content-addressed P1 immutable short-L2 CSV.  The
script reconstructs the five frozen P2 folds without model code and compares
each immutable feature with four outcome views: taker-net return, TP-before-SL,
gross return measured in ATR units, and taker-net association within fold-local
ATR quintiles.  Columns used to derive the scale control are
``atr_at_signal``, ``entry_price_research``, and ``gross_ret``; no future field
is converted into a feature and no estimator is fitted.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/audit_p2m_mechanism_20260803.py
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from scripts.audit_p2r_root_causes_20260803 import (
    ReadOnlyFold,
    reconstruct_readonly_folds,
)
from src.judgment.features import FEATURE_COLUMNS
from src.judgment.p1_dataset import load_immutable_dataset
from src.judgment.p2_protocol import HOLDOUT_CUTOFF, P2ProtocolError

PROJECT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT / "analysis/output"
PREREG = OUTPUT / "p2m_mechanism_prereg_20260803.json"
P1_MANIFEST = PROJECT / "data/p1/p1_short_l2_preholdout_aade2a334448d644.manifest.json"
P1_DATASET = PROJECT / "data/p1/p1_short_l2_preholdout_aade2a334448d644.csv"
P2R_AUDIT = OUTPUT / "p2r_root_cause_audit_20260803.json"
P2R_FEATURE_IC = OUTPUT / "p2r_feature_ic_20260803.csv"
AUDIT_JSON = OUTPUT / "p2m_mechanism_audit_20260803.json"
FEATURE_CSV = OUTPUT / "p2m_feature_mechanism_20260803.csv"
FOLD_CSV = OUTPUT / "p2m_fold_target_mechanism_20260803.csv"

EXPECTED_HASHES = {
    PREREG: "5173f168a45161cea8587d0eb32792bfd263a2cfb4f730160b823bda7353691e",
    P1_MANIFEST: "53b8a07612dae667a184da38bf8e0a694aaae15a5fd240d5b13238da3e13d682",
    P1_DATASET: "aade2a334448d6443e71fb0d3dbbfcf450390875ce60e1f800f6dbe9c855e93a",
    P2R_AUDIT: "dec35ff2bf3a7600d13edd9f614892a3b5bed5e5d8a402d73242a7aca4d94def",
    P2R_FEATURE_IC: "3cb8346329dc60d5ec3418720f3f0a703164e7374106599f41705b11920d3f2d",
}
PROTECTED = (
    PROJECT / "models/ACTIVE",
    PROJECT / "data/forward_log.csv",
    PROJECT / "data/executor_ledger.jsonl",
    PROJECT / "models/active_bundle.json",
)


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
        raise P2ProtocolError(f"P2-M frozen input mismatch: {changed}")
    if _json(PREREG).get("status") != "accepted":
        raise P2ProtocolError("P2-M preregistration is not accepted")
    if _json(P2R_AUDIT).get("verdict") != "completed_stop":
        raise P2ProtocolError("P2-R audit is not a frozen completed input")
    return {_relative(path): digest for path, digest in actual.items()}


def _spearman(left: pd.Series, right: pd.Series) -> float | None:
    joined = pd.concat(
        [pd.to_numeric(left, errors="coerce"), pd.to_numeric(right, errors="coerce")],
        axis=1,
    ).replace([np.inf, -np.inf], np.nan).dropna()
    if len(joined) < 3 or joined.iloc[:, 0].nunique() < 2 or joined.iloc[:, 1].nunique() < 2:
        return None
    value = float(joined.iloc[:, 0].corr(joined.iloc[:, 1], method="spearman"))
    return value if np.isfinite(value) else None


def stable_association(values: Sequence[float | None]) -> dict[str, Any]:
    finite = [float(value) for value in values if value is not None and np.isfinite(value)]
    median = float(np.median(finite)) if finite else None
    positive = sum(value > 0 for value in finite)
    negative = sum(value < 0 for value in finite)
    same_sign = max(positive, negative)
    accepted = bool(
        len(finite) == 5
        and median is not None
        and same_sign >= 4
        and abs(median) >= 0.03
    )
    return {
        "fold_values": [None if value is None else float(value) for value in values],
        "finite_folds": len(finite),
        "median": median,
        "min": float(min(finite)) if finite else None,
        "max": float(max(finite)) if finite else None,
        "same_sign_folds": int(same_sign),
        "stable": accepted,
    }


def add_derived_targets(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    data = frame.copy()
    atr = pd.to_numeric(data["atr_at_signal"], errors="coerce")
    entry = pd.to_numeric(data["entry_price_research"], errors="coerce")
    gross = pd.to_numeric(data["gross_ret"], errors="coerce")
    scale = atr / entry
    normalized = gross / scale
    data["p2m_atr_return_scale"] = scale
    data["p2m_atr_normalized_gross"] = normalized
    quality = {
        "atr_missing": int(atr.isna().sum()),
        "entry_missing": int(entry.isna().sum()),
        "gross_missing": int(gross.isna().sum()),
        "atr_return_scale_nonpositive": int((scale <= 0).sum()),
        "atr_return_scale_nonfinite": int((~np.isfinite(scale.to_numpy(dtype=float))).sum()),
        "atr_normalized_gross_nonfinite": int(
            (~np.isfinite(normalized.to_numpy(dtype=float))).sum()
        ),
    }
    if any(quality.values()):
        raise P2ProtocolError(f"P2-M derived target quality failure: {quality}")
    return data, quality


def _atr_buckets(frame: pd.DataFrame) -> pd.Series:
    values = pd.to_numeric(frame["atr_pct"], errors="coerce")
    if values.isna().any() or (~np.isfinite(values.to_numpy(dtype=float))).any():
        raise P2ProtocolError("P2-M atr_pct is missing or non-finite")
    buckets = pd.qcut(values, q=5, labels=False, duplicates="drop")
    if buckets.isna().any() or buckets.nunique() != 5:
        raise P2ProtocolError("P2-M could not form five fold-local ATR quintiles")
    return buckets.astype(int)


def within_atr_spearman(frame: pd.DataFrame, feature: str) -> dict[str, Any]:
    buckets = _atr_buckets(frame)
    values: list[float] = []
    weights: list[int] = []
    bucket_rows: list[dict[str, Any]] = []
    for bucket in range(5):
        mask = buckets == bucket
        rho = _spearman(frame.loc[mask, feature], frame.loc[mask, "net_ret_swap_taker"])
        n = int(mask.sum())
        bucket_rows.append({"atr_quintile": bucket, "rows": n, "rho": rho})
        if rho is not None:
            values.append(rho)
            weights.append(n)
    if len(values) != 5:
        return {"rho": None, "buckets": bucket_rows}
    return {
        "rho": float(np.average(np.asarray(values), weights=np.asarray(weights))),
        "buckets": bucket_rows,
    }


def barrier_scale_diagnostics(frame: pd.DataFrame) -> dict[str, Any]:
    normalized = frame["p2m_atr_normalized_gross"]
    exit_reason = frame["exit_reason"].astype(str)
    tp = normalized.loc[exit_reason == "tp"]
    sl = normalized.loc[exit_reason.isin(["sl", "sl_ambiguous"])]
    timeout = normalized.loc[exit_reason == "timeout"]
    tp_median = float(tp.median())
    sl_median = float(sl.median())
    checks = {
        "tp_median_within_0_25_of_plus_5": abs(tp_median - 5.0) <= 0.25,
        "sl_median_within_0_25_of_minus_2": abs(sl_median + 2.0) <= 0.25,
    }
    return {
        "tp": {
            "rows": int(len(tp)),
            "median_atr_units": tp_median,
            "q10": float(tp.quantile(0.10)),
            "q90": float(tp.quantile(0.90)),
        },
        "sl": {
            "rows": int(len(sl)),
            "median_atr_units": sl_median,
            "q10": float(sl.quantile(0.10)),
            "q90": float(sl.quantile(0.90)),
        },
        "timeout": {
            "rows": int(len(timeout)),
            "median_atr_units": float(timeout.median()),
            "q10": float(timeout.quantile(0.10)),
            "q90": float(timeout.quantile(0.90)),
        },
        "checks": checks,
        "accepted": bool(all(checks.values())),
    }


def fold_target_diagnostics(fold: ReadOnlyFold) -> dict[str, Any]:
    data = fold.test
    barrier = barrier_scale_diagnostics(data)
    return {
        "fold": fold.fold,
        "test_start": fold.test_start.isoformat(),
        "test_end": fold.test_end.isoformat(),
        "rows": int(len(data)),
        "atr_return_scale_median": float(data["p2m_atr_return_scale"].median()),
        "tp_before_sl_rate": float(data["label_tp_before_sl"].mean()),
        "gross_mean": float(data["gross_ret"].mean()),
        "net_taker_mean": float(data["net_ret_swap_taker"].mean()),
        "atr_normalized_gross_mean": float(data["p2m_atr_normalized_gross"].mean()),
        "atr_normalized_gross_median": float(data["p2m_atr_normalized_gross"].median()),
        "tp_median_atr_units": barrier["tp"]["median_atr_units"],
        "sl_median_atr_units": barrier["sl"]["median_atr_units"],
        "timeout_median_atr_units": barrier["timeout"]["median_atr_units"],
        "atr_quintile_rows": [
            int(value)
            for value in _atr_buckets(data).value_counts().sort_index().tolist()
        ],
    }


def feature_mechanism_diagnostics(
    folds: list[ReadOnlyFold],
    *,
    p2r_stable_features: set[str],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    records: list[dict[str, Any]] = []
    details: dict[str, Any] = {}
    for feature in FEATURE_COLUMNS:
        net_values: list[float | None] = []
        label_values: list[float | None] = []
        normalized_values: list[float | None] = []
        within_values: list[float | None] = []
        atr_values: list[float | None] = []
        within_details: list[dict[str, Any]] = []
        for fold in folds:
            data = fold.test
            net_values.append(_spearman(data[feature], data["net_ret_swap_taker"]))
            label_values.append(_spearman(data[feature], data["label_tp_before_sl"]))
            normalized_values.append(
                _spearman(data[feature], data["p2m_atr_normalized_gross"])
            )
            within = within_atr_spearman(data, feature)
            within_values.append(within["rho"])
            within_details.append({"fold": fold.fold, **within})
            atr_values.append(_spearman(data[feature], data["atr_pct"]))

        net = stable_association(net_values)
        label = stable_association(label_values)
        normalized = stable_association(normalized_values)
        within = stable_association(within_values)
        atr = stable_association(atr_values)
        frozen_stable = feature in p2r_stable_features
        net_abs = abs(float(net["median"])) if net["median"] is not None else 0.0
        mechanical = bool(
            frozen_stable
            and net_abs > 0
            and all(
                metric["median"] is not None
                and abs(float(metric["median"])) <= 0.5 * net_abs
                for metric in (label, normalized, within)
            )
        )
        scale_robust = bool(
            frozen_stable and label["stable"] and normalized["stable"] and within["stable"]
        )
        if mechanical and scale_robust:
            classification = "mechanical_and_scale_robust"
        elif mechanical:
            classification = "mechanical_scale_dominant"
        elif scale_robust:
            classification = "scale_robust"
        elif frozen_stable:
            classification = "mixed"
        else:
            classification = "not_in_frozen_p2r_stable_subset"
        record = {
            "feature": feature,
            "p2r_stable": frozen_stable,
            "net_taker_median_rho": net["median"],
            "net_taker_same_sign_folds": net["same_sign_folds"],
            "tp_label_median_rho": label["median"],
            "tp_label_same_sign_folds": label["same_sign_folds"],
            "atr_normalized_gross_median_rho": normalized["median"],
            "atr_normalized_gross_same_sign_folds": normalized["same_sign_folds"],
            "within_atr_net_median_rho": within["median"],
            "within_atr_net_same_sign_folds": within["same_sign_folds"],
            "feature_vs_atr_pct_median_rho": atr["median"],
            "mechanical_scale_dominant": mechanical,
            "scale_robust": scale_robust,
            "classification": classification,
        }
        records.append(record)
        details[feature] = {
            "net_taker": net,
            "tp_label": label,
            "atr_normalized_gross": normalized,
            "within_atr_net_taker": within,
            "feature_vs_atr_pct": atr,
            "within_atr_buckets": within_details,
            "classification": classification,
        }
    frame = pd.DataFrame(records).sort_values(
        ["p2r_stable", "classification", "net_taker_median_rho"],
        ascending=[False, True, True],
    )
    return frame, details


def build_audit() -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    frozen_hashes = assert_frozen_inputs()
    protected_before = _protected_hashes()
    prereg = _json(PREREG)
    p2r_ic = pd.read_csv(P2R_FEATURE_IC)
    stable_features = set(
        p2r_ic.loc[p2r_ic["stable_by_preregistered_rule"].astype(bool), "feature"].astype(str)
    )
    if len(stable_features) != 20:
        raise P2ProtocolError("P2-M expected exactly 20 frozen P2-R-stable features")

    raw = load_immutable_dataset(P1_MANIFEST)
    data, derived_quality = add_derived_targets(raw)
    for column in ("signal_time", "interval_start", "interval_end"):
        data[column] = pd.to_datetime(data[column], utc=True, errors="raise")
    if (data["signal_time"] >= HOLDOUT_CUTOFF).any():
        raise P2ProtocolError("P2-M reached a holdout signal")
    if (data["interval_end"] >= HOLDOUT_CUTOFF).any():
        raise P2ProtocolError("P2-M reached a holdout label interval")

    folds = reconstruct_readonly_folds(data)
    if [len(fold.test) for fold in folds] != [2937, 2918, 2996, 2944, 3000]:
        raise P2ProtocolError("P2-M fold rows differ from the frozen P2-R reconstruction")
    fold_records = [fold_target_diagnostics(fold) for fold in folds]
    fold_frame = pd.DataFrame(fold_records)
    feature_frame, feature_details = feature_mechanism_diagnostics(
        folds,
        p2r_stable_features=stable_features,
    )
    barrier = barrier_scale_diagnostics(pd.concat([fold.test for fold in folds], ignore_index=True))

    frozen = feature_frame.loc[feature_frame["p2r_stable"]].copy()
    mechanical_count = int(frozen["mechanical_scale_dominant"].sum())
    robust_count = int(frozen["scale_robust"].sum())
    mixed_count = int((frozen["classification"] == "mixed").sum())
    both_count = int((frozen["classification"] == "mechanical_and_scale_robust").sum())
    global_mechanical = bool(barrier["accepted"] and mechanical_count / len(frozen) >= 0.75)
    global_robust = robust_count >= 1
    if global_mechanical and not global_robust:
        conclusion = "mechanical_scale_dominant_only"
        implication = "Stop the current return target/objective route; no training is supported."
    elif global_robust:
        conclusion = "scale_robust_association_exists"
        implication = (
            "At least one association survives all fixed scale controls, but this already-inspected P1 "
            "evidence is exploratory and does not authorize feature selection or training."
        )
    else:
        conclusion = "mixed_or_unresolved"
        implication = "Mechanism remains unresolved; do not resolve it by model or threshold search."

    source_checks = {
        "p1_rows_18103": len(data) == 18103,
        "p1_holdout_signal_rows_zero": int((data["signal_time"] >= HOLDOUT_CUTOFF).sum()) == 0,
        "p1_holdout_interval_rows_zero": int((data["interval_end"] >= HOLDOUT_CUTOFF).sum()) == 0,
        "five_fold_rows_reproduced": [len(fold.test) for fold in folds]
        == [2937, 2918, 2996, 2944, 3000],
        "feature_schema_exact_28": set(feature_frame["feature"]) == set(FEATURE_COLUMNS)
        and len(feature_frame) == 28,
        "frozen_p2r_stable_features_20": len(stable_features) == 20,
        "derived_target_quality_clean": not any(derived_quality.values()),
        "five_atr_quintiles_per_fold": all(
            len(record["atr_quintile_rows"]) == 5 and min(record["atr_quintile_rows"]) > 0
            for record in fold_records
        ),
    }
    if not all(source_checks.values()):
        raise P2ProtocolError(f"P2-M source reconciliation failed: {source_checks}")

    protected_after = _protected_hashes()
    if protected_before != protected_after:
        raise P2ProtocolError("a protected runtime artifact changed during P2-M")

    payload = {
        "audit_version": "p2m_readonly_mechanism_audit_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verdict": "completed_stop",
        "p2_verdict_unchanged": "rejected",
        "preregistration": {
            "path": _relative(PREREG),
            "sha256": EXPECTED_HASHES[PREREG],
            "status": prereg["status"],
        },
        "source_binding": {
            "data_source_used": _relative(P1_DATASET),
            "frozen_hashes": frozen_hashes,
            "rows": int(len(data)),
            "symbols": int(data["symbol"].nunique()),
            "signal_start": data["signal_time"].min().isoformat(),
            "signal_end": data["signal_time"].max().isoformat(),
            "max_interval_end": data["interval_end"].max().isoformat(),
            "source_checks": source_checks,
            "derived_target_quality": derived_quality,
        },
        "target_definitions": prereg["fixed_derived_targets"],
        "barrier_scale": barrier,
        "fold_target_mechanism": fold_records,
        "feature_mechanism": {
            "frozen_p2r_stable_count": int(len(frozen)),
            "mechanical_scale_dominant_count": mechanical_count,
            "scale_robust_count": robust_count,
            "both_count": both_count,
            "mixed_count": mixed_count,
            "classification_counts": {
                str(key): int(value)
                for key, value in frozen["classification"].value_counts().sort_index().items()
            },
            "features": feature_details,
            "csv_path": _relative(FEATURE_CSV),
        },
        "decision": {
            "global_mechanical_dominance": global_mechanical,
            "global_scale_robust_signal": global_robust,
            "conclusion": conclusion,
            "implication": implication,
            "training_allowed": False,
            "threshold_change_supported": False,
            "p2_reopened": False,
            "action_taken": "none_stop_after_p2m",
            "confirmation_note": "All associations use already-inspected P1 pre-holdout folds; no result is independent confirmation.",
        },
        "outputs": {
            "feature_mechanism_csv": _relative(FEATURE_CSV),
            "fold_target_mechanism_csv": _relative(FOLD_CSV),
        },
        "protected_before": protected_before,
        "protected_after": protected_after,
        "protected_unchanged": True,
        "safety": {
            "data_sources_used": [_relative(P1_DATASET)],
            "training_or_fitting_calls": 0,
            "model_or_feature_selected": False,
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
    return payload, feature_frame, fold_frame


def write_outputs() -> dict[str, Any]:
    payload, feature_frame, fold_frame = build_audit()
    FEATURE_CSV.parent.mkdir(parents=True, exist_ok=True)
    feature_frame.to_csv(FEATURE_CSV, index=False)
    fold_frame.to_csv(FOLD_CSV, index=False)
    payload["outputs"]["feature_mechanism_sha256"] = file_sha256(FEATURE_CSV)
    payload["outputs"]["fold_target_mechanism_sha256"] = file_sha256(FOLD_CSV)
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
