#!/usr/bin/env python3
"""Augment the causal side-specific L2 with historical reference events.

The frozen real-L1 candidate ledger has only 417 independent training blocks.
The repository also contains 10,000 accepted positive and 10,000 nuisance-
matched negative reference windows with exact symbol/time/source lineage.  This
runner reconnects those windows to their OHLCV, samples the existing 28 causal
L2 features at each image's final visible bar, and recomputes TP5/SL2/72 future
outcomes from the next open.  The old morphology positive/negative flag is
metadata only: it is never used as an economic target or feature.

Only reference events whose decision time is inside the frozen training period
are opened.  Every source CSV is read with ``nrows`` capped at the last required
outcome bar, and the last physically opened timestamp is asserted pre-holdout.
Overlapping 168-bar inputs plus 72-bar outcomes are collapsed into connected
same-symbol dependency blocks.  If a block contains a real L1 proposal, that
proposal is the representative; otherwise the earliest reference event is.

The comparison changes one variable only: baseline training rows versus the
same rows plus reference outcomes.  LONG/SHORT tune rows, q90 thresholds,
final-validation rows, features, model parameters, barriers, cost and matched
controls remain frozen.  This script cannot fetch, promote, deploy, mutate
ACTIVE/frozen/forward state, send Telegram or place orders.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import lightgbm as lgb
import numpy as np
import pandas as pd

from scripts.research_15m_ma_launch_l2_global_context import (
    outcome_permutation_pvalue,
    safe_metrics,
    selected_metrics,
)
from scripts.research_15m_ma_launch_l2_short_window_side_split import (
    empirical_percentile,
    strict_matched_control_metrics,
)
from yoyo.layers.l2_judgment.features import (
    FEATURE_COLUMNS,
    add_features,
    extract_feature_rows_for_side,
)
from yoyo.layers.l2_judgment.train import LGB_PARAMS, train_model


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-l2-reference-augmentation-v1"
EXPERIMENT_DIR = ROOT / "experiments" / "active" / EXPERIMENT_ID
PREREG_PATH = EXPERIMENT_DIR / "preregistration.json"
RESULTS_DIR = EXPERIMENT_DIR / "results"
OUTPUT_DIR = ROOT / "analysis" / "output" / "ma_launch_l2_reference_augmentation_v1"
REPORT_PATH = ROOT / "analysis" / "p3_15m_ma_launch_l2_reference_augmentation_20260902.md"
SIDES = ("long", "short")
BAR_DELTA = pd.Timedelta(minutes=15)
CONTEXT_BARS = 168
SEED = 42
L2_DETERMINISTIC_PARAMS: dict[str, Any] = {
    "device_type": "cpu",
    "deterministic": True,
    "force_col_wise": True,
    "num_threads": 1,
    "data_random_seed": SEED,
    "feature_fraction_seed": SEED,
    "bagging_seed": SEED,
    "extra_seed": SEED,
}


class ReferenceAugmentationError(RuntimeError):
    """Fail closed when lineage, causality, split or model parity drifts."""


def utc(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_convert("UTC") if stamp.tzinfo else stamp.tz_localize("UTC")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    building = path.with_suffix(path.suffix + ".building")
    building.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    building.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_csv_prefix(path: Path, data_rows: int) -> str:
    """Hash header plus exactly ``data_rows`` physical CSV records.

    The function stops reading immediately after the declared prefix; it must
    not hash the complete source file because that could physically open
    unauthorized holdout rows.
    """

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for _ in range(int(data_rows) + 1):
            line = handle.readline()
            if not line:
                break
            digest.update(line)
    return digest.hexdigest()


def repo_path(value: object) -> Path:
    path = Path(str(value))
    resolved = path.resolve() if path.is_absolute() else (ROOT / path).resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ReferenceAugmentationError(f"path escapes repository: {value}") from exc
    return resolved


def repo_relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def require_committed_builder() -> str:
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=ROOT, text=True
    ).strip()
    if branch != "main":
        raise ReferenceAugmentationError(f"official run requires main, got {branch}")
    owned = [Path(__file__).resolve().relative_to(ROOT), PREREG_PATH.relative_to(ROOT)]
    dirty = subprocess.check_output(
        ["git", "status", "--short", "--", *map(str, owned)], cwd=ROOT, text=True
    ).strip()
    if dirty:
        raise ReferenceAugmentationError(f"builder/prereg must be committed before run:\n{dirty}")
    return git_head()


def bool_series(values: pd.Series, *, label: str) -> pd.Series:
    if pd.api.types.is_bool_dtype(values):
        return values.astype(bool)
    normalized = values.astype(str).str.strip().str.lower()
    unknown = sorted(set(normalized) - {"true", "false"})
    if unknown:
        raise ReferenceAugmentationError(f"{label} contains non-booleans: {unknown}")
    return normalized == "true"


def runtime_versions() -> dict[str, Any]:
    packages = ("lightgbm", "numpy", "pandas", "scikit-learn", "scipy")
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": {
            package: importlib.metadata.version(package) for package in packages
        },
    }


def verify_file(spec: Mapping[str, Any], label: str) -> Path:
    path = repo_path(spec["path"])
    if not path.is_file():
        raise ReferenceAugmentationError(f"missing {label}: {path}")
    actual = sha256_file(path)
    if actual != str(spec["sha256"]):
        raise ReferenceAugmentationError(
            f"{label} SHA drifted: {actual} != {spec['sha256']}"
        )
    return path


def load_preregistration(path: Path = PREREG_PATH) -> dict[str, Any]:
    prereg = read_json(path)
    if prereg.get("experiment_id") != EXPERIMENT_ID:
        raise ReferenceAugmentationError("experiment id drift")
    if tuple(prereg["frozen_contract"]["sides"]) != SIDES:
        raise ReferenceAugmentationError("side contract drift")
    if int(prereg["frozen_contract"]["feature_count"]) != len(FEATURE_COLUMNS):
        raise ReferenceAugmentationError("feature count drift")
    if prereg["model"]["deterministic_params"] != L2_DETERMINISTIC_PARAMS:
        raise ReferenceAugmentationError("deterministic model parameters drift")
    if any(bool(value) for value in prereg["safety"].values()):
        raise ReferenceAugmentationError("one or more safety switches drifted true")
    frozen = prereg["frozen_contract"]
    expected = (5.0, 2.0, 72, 0.0015, 0.002)
    observed = (
        float(frozen["tp_atr_multiple"]),
        float(frozen["sl_atr_multiple"]),
        int(frozen["horizon_bars"]),
        float(frozen["decision_atr_pct_min"]),
        float(frozen["round_trip_cost_fraction"]),
    )
    if observed != expected:
        raise ReferenceAugmentationError(f"economic contract drifted: {observed}")
    for key, label in (
        ("real_l1_candidate_dataset", "real L1 candidate dataset"),
        ("matched_controls", "matched controls"),
        ("reference_manifest", "reference manifest"),
        ("reference_build_summary", "reference build summary"),
        ("prior_side_split_receipt", "prior side-split receipt"),
        ("prior_side_split_scores", "prior side-split scores"),
    ):
        verify_file(prereg["inputs"][key], label)
    for key, label in (
        ("feature_builder", "feature builder"),
        ("label_builder", "label builder"),
        ("training_builder", "training builder"),
    ):
        verify_file(
            {"path": frozen[key], "sha256": frozen[f"{key}_sha256"]}, label
        )
    return prereg


def reference_side(record: Mapping[str, Any]) -> str:
    kind = str(record.get("sample_kind", ""))
    raw = record.get("direction") if kind == "positive" else record.get("paired_direction")
    side = str(raw or "").strip().lower()
    if side not in SIDES:
        raise ReferenceAugmentationError(
            f"reference side missing/invalid for {record.get('sample_id')}: {raw!r}"
        )
    return side


def eligible_reference_records(
    records: Sequence[Mapping[str, Any]], prereg: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Select reference rows without opening outcome bars or holdout files."""

    spec = prereg["inputs"]["reference_manifest"]
    if len(records) != int(spec["rows"]):
        raise ReferenceAugmentationError(
            f"reference manifest row count drift: {len(records)} != {spec['rows']}"
        )
    kinds = Counter(str(row.get("sample_kind")) for row in records)
    if kinds != Counter(
        {"positive": int(spec["positive_rows"]), "negative": int(spec["negative_rows"])}
    ):
        raise ReferenceAugmentationError(f"reference kind counts drifted: {dict(kinds)}")
    eligible_splits = set(prereg["reference_contract"]["eligible_manifest_splits"])
    train_end = utc(prereg["frozen_contract"]["train_available_at_end_exclusive"])
    selected: list[dict[str, Any]] = []
    stats: Counter[str] = Counter()
    for item in records:
        row = dict(item)
        split = str(row.get("split"))
        if split not in eligible_splits:
            stats[f"excluded_manifest_split:{split}"] += 1
            continue
        feature_time = utc(row["window_end_time"])
        available_at = feature_time + BAR_DELTA
        if available_at >= train_end:
            stats["excluded_at_or_after_train_cutoff"] += 1
            continue
        row["_side"] = reference_side(row)
        row["_feature_time"] = feature_time
        row["_available_at"] = available_at
        selected.append(row)
        stats[f"eligible_{row['sample_kind']}"] += 1
    exact_keys = [
        (
            str(row["symbol"]),
            str(row["_side"]),
            str(row["source_path"]),
            int(row["window_end_i"]),
        )
        for row in selected
    ]
    duplicate_count = len(exact_keys) - len(set(exact_keys))
    if duplicate_count:
        raise ReferenceAugmentationError(
            f"reference manifest has {duplicate_count} duplicate economic event keys"
        )
    return selected, stats


def normalize_prefix_frame(path: Path, *, nrows: int) -> pd.DataFrame:
    required = ["open_time", "open", "high", "low", "close", "volume"]
    frame = pd.read_csv(path, usecols=required, nrows=int(nrows))
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True, errors="coerce")
    for column in required[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame.isna().any().any():
        raise ReferenceAugmentationError(f"invalid OHLCV in opened prefix: {path}")
    if frame["open_time"].duplicated().any() or not frame["open_time"].is_monotonic_increasing:
        raise ReferenceAugmentationError(f"non-monotonic/duplicate OHLCV prefix: {path}")
    return frame.reset_index(drop=True)


def build_reference_rows(prereg: Mapping[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Materialize causal 28-feature economic rows from reference-window lineage."""

    manifest_path = repo_path(prereg["inputs"]["reference_manifest"]["path"])
    records = [
        json.loads(line)
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    selected, selection_stats = eligible_reference_records(records, prereg)
    by_path: dict[str, list[dict[str, Any]]] = {}
    for row in selected:
        by_path.setdefault(str(row["source_path"]), []).append(row)

    from yoyo.data.indicators import add_indicators
    from yoyo.layers.l2_judgment.labeling import label_candidate, label_short_candidate

    frozen = prereg["frozen_contract"]
    horizon = int(frozen["horizon_bars"])
    holdout = utc(frozen["holdout_start"])
    tune_start = utc(frozen["tune_available_at_start"])
    cost = float(frozen["round_trip_cost_fraction"])
    rows: list[dict[str, Any]] = []
    source_receipts: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    for number, source_value in enumerate(sorted(by_path), 1):
        group = by_path[source_value]
        source_path = repo_path(source_value)
        required_rows = max(int(row["window_end_i"]) + 1 + horizon for row in group)
        frame = normalize_prefix_frame(source_path, nrows=required_rows)
        prefix_rows = len(frame)
        if prefix_rows < required_rows:
            rejected["source_prefix_too_short_events"] += len(group)
            continue
        if utc(frame["open_time"].iloc[-1]) >= holdout:
            raise ReferenceAugmentationError(
                f"physically opened holdout row in {source_value}: {frame['open_time'].iloc[-1]}"
            )
        featured = add_features(add_indicators(frame))
        prefix_hash = sha256_csv_prefix(source_path, prefix_rows)
        kept_on_source = 0
        for record in group:
            signal_i = int(record["window_end_i"])
            feature_time = utc(featured["open_time"].iloc[signal_i])
            if feature_time != utc(record["window_end_time"]):
                raise ReferenceAugmentationError(
                    f"manifest/source time mismatch: {record['sample_id']}"
                )
            side = str(record["_side"])
            feature_row = extract_feature_rows_for_side(featured, [signal_i], side).iloc[0]
            if not np.isfinite(feature_row.to_numpy(dtype=float)).all():
                rejected["nonfinite_feature"] += 1
                continue
            labeler = label_candidate if side == "long" else label_short_candidate
            outcome = labeler(
                featured,
                signal_i,
                tp_mult=float(frozen["tp_atr_multiple"]),
                sl_mult=float(frozen["sl_atr_multiple"]),
                atr_pct_min=float(frozen["decision_atr_pct_min"]),
                horizon=horizon,
                entry="next_open",
            )
            if outcome is None:
                rejected["outcome_unavailable_or_atr_floor"] += 1
                continue
            available_at = feature_time + BAR_DELTA
            input_start_i = signal_i - CONTEXT_BARS + 1
            if input_start_i < 0:
                rejected["insufficient_168bar_context"] += 1
                continue
            exposure_start = utc(featured["open_time"].iloc[input_start_i])
            exposure_end = available_at + horizon * BAR_DELTA
            if exposure_end > tune_start:
                raise ReferenceAugmentationError(
                    f"reference training exposure reaches tune: {record['sample_id']}"
                )
            morphology_kind = str(record["sample_kind"])
            source_sample_id = str(record.get("source_sample_id") or record["sample_id"])
            row: dict[str, Any] = {
                "episode_id": f"reference:{morphology_kind}:{source_sample_id}",
                "symbol": str(record["symbol"]),
                "side": side,
                "class_id": 0 if side == "long" else 1,
                "split": "train",
                "event_source": "reference_window",
                "source_priority": 1,
                "reference_morphology_kind": morphology_kind,
                "reference_original_split": str(record["split"]),
                "reference_sample_id": str(record["sample_id"]),
                "reference_source_sample_id": source_sample_id,
                "source_path": repo_relative(source_path),
                "feature_bar_i": signal_i,
                "feature_bar_time": feature_time.isoformat(),
                "available_at": available_at.isoformat(),
                "signal_time": available_at.isoformat(),
                "exposure_start_time": exposure_start.isoformat(),
                "exposure_end_exclusive": exposure_end.isoformat(),
                "label": int(outcome.label),
                "outcome": str(outcome.outcome),
                "exit_offset": int(outcome.exit_offset),
                "entry_price": float(outcome.entry_price),
                "realized_ret": float(outcome.realized_ret),
                "net_ret": float(outcome.realized_ret) - cost,
            }
            row.update({column: float(feature_row[column]) for column in FEATURE_COLUMNS})
            rows.append(row)
            kept_on_source += 1
        source_receipts.append(
            {
                "source_path": repo_relative(source_path),
                "physical_data_rows_opened": prefix_rows,
                "physical_prefix_sha256": prefix_hash,
                "last_open_time_opened": utc(frame["open_time"].iloc[-1]).isoformat(),
                "eligible_manifest_rows": len(group),
                "economic_rows_built": kept_on_source,
            }
        )
        if number % 25 == 0 or number == len(by_path):
            print(
                f"reference [{number:03d}/{len(by_path):03d}] "
                f"events={len(rows):,} rejected={sum(rejected.values()):,}",
                flush=True,
            )
    dataset = pd.DataFrame(rows).sort_values(["available_at", "symbol", "episode_id"])
    if dataset.empty or dataset["episode_id"].duplicated().any():
        raise ReferenceAugmentationError("reference economic dataset is empty or duplicated")
    times = pd.to_datetime(dataset["exposure_end_exclusive"], utc=True)
    if (times > tune_start).any() or (times > holdout).any():
        raise ReferenceAugmentationError("reference outcome exposure crosses a frozen boundary")
    prefix_path = OUTPUT_DIR / "reference_source_prefixes.json"
    write_json(
        prefix_path,
        {
            "schema_version": 1,
            "sources": source_receipts,
            "source_count": len(source_receipts),
            "maximum_opened_timestamp": max(
                row["last_open_time_opened"] for row in source_receipts
            ),
            "holdout_rows_opened": 0,
        },
    )
    reference_path = OUTPUT_DIR / "reference_event_rows.csv"
    reference_path.parent.mkdir(parents=True, exist_ok=True)
    dataset.to_csv(reference_path, index=False)
    receipt = {
        "manifest_rows": len(records),
        "eligible_manifest_rows": len(selected),
        "selection_stats": dict(sorted(selection_stats.items())),
        "economic_rows": len(dataset),
        "economic_rows_by_side": dict(sorted(Counter(dataset["side"]).items())),
        "economic_rows_by_morphology_kind": dict(
            sorted(Counter(dataset["reference_morphology_kind"]).items())
        ),
        "economic_outcomes": dict(sorted(Counter(dataset["outcome"]).items())),
        "rejected": dict(sorted(rejected.items())),
        "source_count": len(source_receipts),
        "reference_path": repo_relative(reference_path),
        "reference_sha256": sha256_file(reference_path),
        "prefix_receipt_path": repo_relative(prefix_path),
        "prefix_receipt_sha256": sha256_file(prefix_path),
        "maximum_available_at": pd.to_datetime(dataset["available_at"], utc=True).max().isoformat(),
        "maximum_exposure_end_exclusive": times.max().isoformat(),
        "holdout_rows_opened": 0,
    }
    return dataset, receipt


def load_real_l1_dataset(prereg: Mapping[str, Any]) -> pd.DataFrame:
    spec = prereg["inputs"]["real_l1_candidate_dataset"]
    data = pd.read_csv(repo_path(spec["path"]))
    if len(data) != int(spec["rows"]):
        raise ReferenceAugmentationError("real L1 dataset row count drift")
    required = {
        "episode_id",
        "symbol",
        "side",
        "split",
        "available_at",
        "exposure_start_time",
        "exposure_end_exclusive",
        "dependency_representative",
        "label",
        "realized_ret",
        "net_ret",
        *FEATURE_COLUMNS,
    }
    missing = sorted(required - set(data.columns))
    if missing:
        raise ReferenceAugmentationError(f"real L1 dataset missing: {missing}")
    data["dependency_representative"] = bool_series(
        data["dependency_representative"], label="real dependency_representative"
    )
    for column in ("available_at", "exposure_start_time", "exposure_end_exclusive"):
        data[column] = pd.to_datetime(data[column], utc=True)
    learning = data["split"].isin(("train", "tune", "final_validation"))
    if not np.isfinite(
        data.loc[learning, list(FEATURE_COLUMNS)].to_numpy(dtype=float)
    ).all():
        raise ReferenceAugmentationError("real L1 learning rows have nonfinite features")
    holdout = utc(prereg["frozen_contract"]["holdout_start"])
    if data["exposure_end_exclusive"].max() > holdout:
        raise ReferenceAugmentationError("real L1 label exposure reaches holdout")
    return data


def assign_augmented_dependency_blocks(train_pool: pd.DataFrame) -> pd.DataFrame:
    """Collapse connected same-symbol input+outcome exposures.

    Representatives prefer a real L1 proposal over a reference row.  This keeps
    the target-domain event whenever auxiliary and target examples overlap.
    """

    required = {
        "episode_id",
        "symbol",
        "available_at",
        "exposure_start_time",
        "exposure_end_exclusive",
        "event_source",
        "source_priority",
    }
    missing = sorted(required - set(train_pool.columns))
    if missing:
        raise ReferenceAugmentationError(f"dependency pool missing: {missing}")
    out = train_pool.copy().reset_index(drop=True)
    out["_start"] = pd.to_datetime(out["exposure_start_time"], utc=True)
    out["_end"] = pd.to_datetime(out["exposure_end_exclusive"], utc=True)
    out["_available"] = pd.to_datetime(out["available_at"], utc=True)
    block_for: dict[int, str] = {}
    for symbol, group in out.sort_values(
        ["symbol", "_start", "_end", "_available", "episode_id"]
    ).groupby("symbol", sort=True):
        sequence = 0
        active_end: pd.Timestamp | None = None
        active_id = ""
        for index, row in group.iterrows():
            start, end = row["_start"], row["_end"]
            if active_end is None or start >= active_end:
                sequence += 1
                active_id = f"{symbol}_augmented_train_dependency_{sequence:06d}"
                active_end = end
            else:
                active_end = max(active_end, end)
            block_for[int(index)] = active_id
    out["dependency_block_id"] = pd.Series(block_for)
    out["dependency_block_size"] = out.groupby("dependency_block_id")[
        "episode_id"
    ].transform("size").astype(int)
    representative_indices = (
        out.sort_values(
            ["source_priority", "_available", "episode_id"], kind="mergesort"
        )
        .groupby("dependency_block_id", sort=False)
        .head(1)
        .index
    )
    out["dependency_representative"] = out.index.isin(representative_indices)
    if out.groupby("dependency_block_id")["symbol"].nunique().max() != 1:
        raise ReferenceAugmentationError("dependency block crosses symbols")
    return out.drop(columns=["_start", "_end", "_available"]).sort_values(
        ["available_at", "symbol", "episode_id"]
    )


def build_augmented_dataset(prereg: Mapping[str, Any]) -> dict[str, Any]:
    terminal = RESULTS_DIR / "dataset_receipt.json"
    if terminal.exists():
        return read_json(terminal)
    source_commit = require_committed_builder()
    reference, reference_receipt = build_reference_rows(prereg)
    real = load_real_l1_dataset(prereg)
    real_train = real[real["split"] == "train"].copy()
    real_train["event_source"] = "real_l1"
    real_train["source_priority"] = 0
    real_train["reference_morphology_kind"] = "not_applicable"
    all_columns = sorted(set(real_train.columns) | set(reference.columns))
    pool = pd.concat(
        [real_train.reindex(columns=all_columns), reference.reindex(columns=all_columns)],
        ignore_index=True,
    )
    if pool["episode_id"].duplicated().any():
        raise ReferenceAugmentationError("real/reference episode IDs overlap")
    augmented = assign_augmented_dependency_blocks(pool)
    representatives = augmented[augmented["dependency_representative"]].copy()
    original_real_reps = set(
        real_train.loc[real_train["dependency_representative"], "episode_id"].astype(str)
    )
    augmented_real_reps = set(
        representatives.loc[
            representatives["event_source"] == "real_l1", "episode_id"
        ].astype(str)
    )
    if not augmented_real_reps.issubset(set(real_train["episode_id"].astype(str))):
        raise ReferenceAugmentationError("augmented real representatives are not real L1 rows")
    dataset_path = OUTPUT_DIR / "augmented_train_pool.csv"
    augmented.to_csv(dataset_path, index=False)
    representative_path = OUTPUT_DIR / "augmented_train_representatives.csv"
    representatives.to_csv(representative_path, index=False)
    tune_ids = set(
        real.loc[
            (real["split"] == "tune") & real["dependency_representative"], "episode_id"
        ].astype(str)
    )
    final_ids = set(
        real.loc[
            (real["split"] == "final_validation") & real["dependency_representative"],
            "episode_id",
        ].astype(str)
    )
    payload = {
        "schema_version": 1,
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": source_commit,
        "reference": reference_receipt,
        "real_l1_rows": len(real),
        "real_l1_original_train_rows": len(real_train),
        "real_l1_original_train_representatives": len(original_real_reps),
        "augmented_train_rows": len(augmented),
        "augmented_train_dependency_blocks": int(
            augmented["dependency_block_id"].nunique()
        ),
        "augmented_train_representatives": len(representatives),
        "augmented_representatives_by_source": dict(
            sorted(Counter(representatives["event_source"]).items())
        ),
        "augmented_representatives_by_side": dict(
            sorted(Counter(representatives["side"]).items())
        ),
        "augmented_representatives_by_morphology_kind": dict(
            sorted(Counter(representatives["reference_morphology_kind"]).items())
        ),
        "real_representatives_retained_after_union": len(augmented_real_reps),
        "real_representatives_collapsed_by_reference_bridges": len(
            original_real_reps - augmented_real_reps
        ),
        "unchanged_real_l1_tune_representatives": len(tune_ids),
        "unchanged_real_l1_final_representatives": len(final_ids),
        "dataset_path": repo_relative(dataset_path),
        "dataset_sha256": sha256_file(dataset_path),
        "representative_path": repo_relative(representative_path),
        "representative_sha256": sha256_file(representative_path),
        "feature_columns": list(FEATURE_COLUMNS),
        "morphology_kind_used_as_feature": False,
        "source_indicator_used_as_feature": False,
        "holdout_rows_opened": 0,
        "production_eligible": False,
    }
    write_json(terminal, payload)
    return payload


def learning_rows(data: pd.DataFrame, split: str) -> pd.DataFrame:
    return data[(data["split"] == split) & data["dependency_representative"]].copy()


def train_side(
    train_rows: pd.DataFrame,
    real: pd.DataFrame,
    *,
    side: str,
    prefix: str,
    feature_columns: Sequence[str],
) -> tuple[dict[str, Any], pd.DataFrame, np.ndarray]:
    train = train_rows[train_rows["side"] == side].copy()
    tune = learning_rows(real[real["side"] == side], "tune")
    final_events = real[
        (real["side"] == side) & (real["split"] == "final_validation")
    ].copy()
    final = final_events[final_events["dependency_representative"]].copy()
    if min(len(train), len(tune), len(final)) == 0:
        raise ReferenceAugmentationError(f"{prefix}/{side} has an empty split")
    model = train_model(
        train,
        tune,
        feature_columns=feature_columns,
        objective="regression",
        params_override=L2_DETERMINISTIC_PARAMS,
    )
    tune_score = model.predict(
        tune[list(feature_columns)], num_iteration=model.best_iteration
    )
    threshold = float(np.quantile(tune_score, 0.9))
    final_score = model.predict(
        final_events[list(feature_columns)], num_iteration=model.best_iteration
    )
    scored = final_events.copy()
    scored[f"{prefix}_score"] = final_score
    scored[f"{prefix}_percentile"] = empirical_percentile(tune_score, final_score)
    scored[f"{prefix}_threshold"] = threshold
    scored[f"{prefix}_keep"] = final_score >= threshold
    model_dir = OUTPUT_DIR / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / f"{prefix}_{side}_{len(feature_columns)}f.txt"
    model.save_model(str(model_path))
    importance = pd.DataFrame(
        {
            "feature": list(feature_columns),
            "gain": model.feature_importance(importance_type="gain"),
            "split": model.feature_importance(importance_type="split"),
        }
    ).sort_values("gain", ascending=False)
    importance_path = OUTPUT_DIR / f"feature_importance_{prefix}_{side}_{len(feature_columns)}f.csv"
    importance.to_csv(importance_path, index=False)
    return (
        {
            "side": side,
            "prefix": prefix,
            "feature_columns": list(feature_columns),
            "splits": {
                "train": len(train),
                "tune": len(tune),
                "final_validation": len(final),
            },
            "train_source_counts": dict(
                sorted(Counter(train.get("event_source", pd.Series(["real_l1"] * len(train)))).items())
            ),
            "best_iteration": int(model.best_iteration),
            "tune_q90_threshold": threshold,
            "model_path": repo_relative(model_path),
            "model_sha256": sha256_file(model_path),
            "importance_path": repo_relative(importance_path),
            "importance_sha256": sha256_file(importance_path),
            "importance_top10": importance.head(10).to_dict("records"),
        },
        scored,
        tune_score,
    )


def metric_bundle(
    scored_by_side: Mapping[str, pd.DataFrame],
    controls: pd.DataFrame,
    *,
    prefix: str,
    cost: float,
) -> dict[str, Any]:
    combined = pd.concat([scored_by_side[side] for side in SIDES], ignore_index=True)
    final = combined[combined["dependency_representative"]].copy()
    score = final[f"{prefix}_percentile"].to_numpy(dtype=float)
    keep = final[f"{prefix}_keep"].to_numpy(dtype=bool)
    returns = final["realized_ret"].to_numpy(dtype=float)
    labels = final["label"].to_numpy(dtype=int)
    selected_ids = set(final.loc[keep, "episode_id"].astype(str))
    by_side: dict[str, Any] = {}
    for side in SIDES:
        arm = final[final["side"] == side]
        arm_keep = arm[f"{prefix}_keep"].to_numpy(dtype=bool)
        arm_score = arm[f"{prefix}_score"].to_numpy(dtype=float)
        by_side[side] = {
            "final_validation": safe_metrics(
                arm["label"].to_numpy(dtype=int),
                arm_score,
                arm["realized_ret"].to_numpy(dtype=float),
                cost,
            ),
            "frozen_q90": selected_metrics(arm, arm_keep, cost),
            "outcome_permutation_p": outcome_permutation_pvalue(
                arm_score, arm["realized_ret"].to_numpy(dtype=float)
            ),
        }
    return {
        "final_validation": safe_metrics(labels, score, returns, cost),
        "frozen_q90": selected_metrics(final, keep, cost),
        "outcome_permutation_p": outcome_permutation_pvalue(score, returns),
        "matched_control": strict_matched_control_metrics(
            final, controls, selected_ids, required_assignments=8
        ),
        "by_side": by_side,
        "scored": combined,
    }


def baseline_parity(
    models: Mapping[str, Mapping[str, Any]],
    scored_by_side: Mapping[str, pd.DataFrame],
    prereg: Mapping[str, Any],
) -> dict[str, Any]:
    prior_receipt = read_json(repo_path(prereg["inputs"]["prior_side_split_receipt"]["path"]))
    prior_scores = pd.read_csv(repo_path(prereg["inputs"]["prior_side_split_scores"]["path"]))
    prior_scores["dependency_representative"] = bool_series(
        prior_scores["dependency_representative"], label="prior dependency_representative"
    )
    prior_scores["l2_keep"] = bool_series(prior_scores["l2_keep"], label="prior l2_keep")
    rows: dict[str, Any] = {}
    for side in SIDES:
        new = scored_by_side[side]
        new = new[new["dependency_representative"]][
            ["episode_id", "baseline_score", "baseline_percentile", "baseline_keep"]
        ]
        old = prior_scores[
            (prior_scores["side"] == side) & prior_scores["dependency_representative"]
        ][["episode_id", "l2_score", "side_percentile_score", "l2_keep"]]
        merged = new.merge(old, on="episode_id", validate="one_to_one")
        if len(merged) != len(new) or len(merged) != len(old):
            raise ReferenceAugmentationError(f"baseline/prior IDs differ for {side}")
        score_delta = np.abs(merged["baseline_score"] - merged["l2_score"])
        percentile_delta = np.abs(
            merged["baseline_percentile"] - merged["side_percentile_score"]
        )
        prior_side = prior_receipt["sides"][side]
        rows[side] = {
            "model_bytes_equal": models[side]["model_sha256"]
            == prior_side["model_sha256"],
            "new_model_sha256": models[side]["model_sha256"],
            "prior_model_sha256": prior_side["model_sha256"],
            "score_max_abs_diff": float(score_delta.max()),
            "percentile_max_abs_diff": float(percentile_delta.max()),
            "threshold_abs_diff": abs(
                float(models[side]["tune_q90_threshold"])
                - float(prior_side["tune_score_q90_threshold"])
            ),
            "keep_exact": bool((merged["baseline_keep"] == merged["l2_keep"]).all()),
            "rows": len(merged),
        }
    tolerance = float(prereg["comparison"]["score_absolute_tolerance"])
    passed = all(
        row["model_bytes_equal"]
        and row["score_max_abs_diff"] <= tolerance
        and row["percentile_max_abs_diff"] <= tolerance
        and row["threshold_abs_diff"] <= tolerance
        and row["keep_exact"]
        for row in rows.values()
    )
    return {"sides": rows, "tolerance": tolerance, "passed": passed}


def economic_gate(
    augmented: Mapping[str, Any], baseline: Mapping[str, Any], prereg: Mapping[str, Any]
) -> dict[str, Any]:
    frozen = augmented["frozen_q90"]
    matched = augmented["matched_control"]
    by_side = augmented["by_side"]
    checks = {
        "aggregate_top_decile_net_positive": augmented["final_validation"]["top_decile"][
            "net_mean"
        ]
        > 0,
        "aggregate_frozen_q90_net_positive": frozen["net_mean"] > 0,
        "aggregate_outcome_permutation_p_lt_0_01": augmented["outcome_permutation_p"]
        < 0.01,
        "aggregate_minimum_30_selected_dependency_blocks": frozen["n"] >= 30,
        "aggregate_beats_matched_controls_every_assignment": matched[
            "all_assignments_positive"
        ],
        "all_selected_events_have_all_controls": matched[
            "selected_event_complete_coverage"
        ],
        "each_side_minimum_10_selected_dependency_blocks": all(
            by_side[side]["frozen_q90"]["n"] >= 10 for side in SIDES
        ),
        "neither_side_frozen_q90_net_negative": all(
            by_side[side]["frozen_q90"]["net_mean"] >= 0 for side in SIDES
        ),
        "augmented_frozen_q90_net_strictly_better_than_reproduced_baseline": frozen[
            "net_mean"
        ]
        > baseline["frozen_q90"]["net_mean"],
    }
    checks["passed"] = all(checks.values())
    return checks


def train_evaluate(prereg: Mapping[str, Any]) -> dict[str, Any]:
    terminal = RESULTS_DIR / "training_receipt.json"
    if terminal.exists():
        return read_json(terminal)
    source_commit = require_committed_builder()
    dataset_receipt = read_json(RESULTS_DIR / "dataset_receipt.json")
    if dataset_receipt["source_commit"] != source_commit:
        raise ReferenceAugmentationError("dataset was not built by current committed source")
    real = load_real_l1_dataset(prereg)
    augmented_pool = pd.read_csv(repo_path(dataset_receipt["dataset_path"]))
    augmented_pool["dependency_representative"] = bool_series(
        augmented_pool["dependency_representative"],
        label="augmented dependency_representative",
    )
    augmented_train = augmented_pool[augmented_pool["dependency_representative"]].copy()
    baseline_train = learning_rows(real, "train")
    controls = pd.read_csv(repo_path(prereg["inputs"]["matched_controls"]["path"]))
    baseline_models: dict[str, Any] = {}
    baseline_scored: dict[str, pd.DataFrame] = {}
    augmented_models: dict[str, Any] = {}
    augmented_scored: dict[str, pd.DataFrame] = {}
    one_feature_models: dict[str, Any] = {}
    one_feature_scored: dict[str, pd.DataFrame] = {}
    for side in SIDES:
        baseline_models[side], baseline_scored[side], _ = train_side(
            baseline_train,
            real,
            side=side,
            prefix="baseline",
            feature_columns=FEATURE_COLUMNS,
        )
        augmented_models[side], augmented_scored[side], _ = train_side(
            augmented_train,
            real,
            side=side,
            prefix="augmented",
            feature_columns=FEATURE_COLUMNS,
        )
        one_feature_models[side], one_feature_scored[side], _ = train_side(
            augmented_train,
            real,
            side=side,
            prefix="augmented_single_feature",
            feature_columns=("ma_spread_pct",),
        )
    parity = baseline_parity(baseline_models, baseline_scored, prereg)
    if not parity["passed"]:
        raise ReferenceAugmentationError(f"baseline reproduction failed: {parity}")
    cost = float(prereg["frozen_contract"]["round_trip_cost_fraction"])
    baseline_metrics = metric_bundle(
        baseline_scored, controls, prefix="baseline", cost=cost
    )
    augmented_metrics = metric_bundle(
        augmented_scored, controls, prefix="augmented", cost=cost
    )
    single_feature_metrics = metric_bundle(
        one_feature_scored,
        controls,
        prefix="augmented_single_feature",
        cost=cost,
    )
    baseline_out = baseline_metrics.pop("scored")
    augmented_out = augmented_metrics.pop("scored")
    single_feature_metrics.pop("scored")
    scored_path = OUTPUT_DIR / "final_validation_augmented_scored.csv"
    augmented_out.to_csv(scored_path, index=False)
    baseline_path = OUTPUT_DIR / "final_validation_baseline_reproduced.csv"
    baseline_out.to_csv(baseline_path, index=False)
    gate = economic_gate(augmented_metrics, baseline_metrics, prereg)
    training_params = dict(LGB_PARAMS)
    training_params.update(L2_DETERMINISTIC_PARAMS)
    training_params["objective"] = "regression"
    payload = {
        "schema_version": 1,
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": source_commit,
        "dataset_receipt_sha256": sha256_file(RESULTS_DIR / "dataset_receipt.json"),
        "reference_manifest_sha256": prereg["inputs"]["reference_manifest"]["sha256"],
        "feature_columns": list(FEATURE_COLUMNS),
        "training_params": training_params,
        "runtime": runtime_versions(),
        "baseline_reproduction": parity,
        "baseline_models": baseline_models,
        "augmented_models": augmented_models,
        "augmented_single_feature_models": one_feature_models,
        "baseline_metrics": baseline_metrics,
        "augmented_metrics": augmented_metrics,
        "augmented_single_feature_metrics": single_feature_metrics,
        "economic_gate": gate,
        "scored_validation_path": repo_relative(scored_path),
        "scored_validation_sha256": sha256_file(scored_path),
        "baseline_scored_path": repo_relative(baseline_path),
        "baseline_scored_sha256": sha256_file(baseline_path),
        "decision": "ACCEPT_REFERENCE_AUGMENTATION" if gate["passed"] else "REJECT_REFERENCE_AUGMENTATION",
        "holdout_consumed": False,
        "promoted": False,
        "deployed": False,
        "active_or_frozen_changed": False,
        "forward_state_changed": False,
        "orders_placed": False,
        "telegram_sent": False,
        "production_eligible": False,
    }
    write_json(terminal, payload)
    return payload


def verify_outputs(prereg: Mapping[str, Any]) -> dict[str, Any]:
    dataset_receipt = read_json(RESULTS_DIR / "dataset_receipt.json")
    training = read_json(RESULTS_DIR / "training_receipt.json")
    checks: dict[str, bool] = {
        "experiment_id": training.get("experiment_id") == EXPERIMENT_ID,
        "protocol": training.get("protocol") == prereg["protocol"],
        "dataset_receipt_hash": training.get("dataset_receipt_sha256")
        == sha256_file(RESULTS_DIR / "dataset_receipt.json"),
        "reference_dataset_hash": sha256_file(repo_path(dataset_receipt["reference"]["reference_path"]))
        == dataset_receipt["reference"]["reference_sha256"],
        "augmented_pool_hash": sha256_file(repo_path(dataset_receipt["dataset_path"]))
        == dataset_receipt["dataset_sha256"],
        "augmented_representatives_hash": sha256_file(
            repo_path(dataset_receipt["representative_path"])
        )
        == dataset_receipt["representative_sha256"],
        "source_prefix_receipt_hash": sha256_file(
            repo_path(dataset_receipt["reference"]["prefix_receipt_path"])
        )
        == dataset_receipt["reference"]["prefix_receipt_sha256"],
        "baseline_parity": training["baseline_reproduction"]["passed"],
        "no_holdout": dataset_receipt["holdout_rows_opened"] == 0
        and training["holdout_consumed"] is False,
        "no_production_mutation": all(
            training[key] is False
            for key in (
                "promoted",
                "deployed",
                "active_or_frozen_changed",
                "forward_state_changed",
                "orders_placed",
                "telegram_sent",
                "production_eligible",
            )
        ),
    }
    for group in ("baseline_models", "augmented_models", "augmented_single_feature_models"):
        for side in SIDES:
            item = training[group][side]
            checks[f"{group}_{side}_model_hash"] = (
                sha256_file(repo_path(item["model_path"])) == item["model_sha256"]
            )
            checks[f"{group}_{side}_importance_hash"] = (
                sha256_file(repo_path(item["importance_path"]))
                == item["importance_sha256"]
            )
    for key, hash_key in (
        ("scored_validation_path", "scored_validation_sha256"),
        ("baseline_scored_path", "baseline_scored_sha256"),
    ):
        checks[key] = sha256_file(repo_path(training[key])) == training[hash_key]
    payload = {
        "schema_version": 1,
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        "generated_at": training["generated_at"],
        "training_receipt_sha256": sha256_file(RESULTS_DIR / "training_receipt.json"),
        "checks": checks,
        "passed": all(checks.values()),
    }
    write_json(RESULTS_DIR / "verify_receipt.json", payload)
    if not payload["passed"]:
        raise ReferenceAugmentationError(f"verification failed: {checks}")
    return payload


def pct(value: float) -> str:
    return f"{float(value) * 100:+.3f}%"


def build_report(prereg: Mapping[str, Any]) -> Path:
    dataset = read_json(RESULTS_DIR / "dataset_receipt.json")
    training = read_json(RESULTS_DIR / "training_receipt.json")
    base = training["baseline_metrics"]
    aug = training["augmented_metrics"]
    one = training["augmented_single_feature_metrics"]
    ref = dataset["reference"]
    lines = [
        "# 15m L2 历史参考事件扩充实验（2026-09-02）",
        "",
        "## 结论先行",
        "",
        f"本轮裁决：**{training['decision']}**。这次真正把旧 10,000 正图与 10,000 匹配负图的事件血缘接回 K 线；原形态正负标签没有冒充盈亏，而是逐事件重新计算固定 TP5/SL2/72 收益。参考事件只加入训练，真实 L1 的 tune 与 final-validation 完全不变。",
        "",
        "## 数据统计",
        "",
        "| 项目 | 数量 |",
        "|---|---:|",
        f"| 原始参考 manifest | {ref['manifest_rows']:,} |",
        f"| 训练截止前可用参考窗口 | {ref['eligible_manifest_rows']:,} |",
        f"| 成功生成经济标签 | {ref['economic_rows']:,} |",
        f"| 原真实 L1 独立训练块 | {dataset['real_l1_original_train_representatives']:,} |",
        f"| 扩充后独立训练块 | {dataset['augmented_train_representatives']:,} |",
        f"| 固定 tune 独立事件 | {dataset['unchanged_real_l1_tune_representatives']:,} |",
        f"| 固定 final 独立事件 | {dataset['unchanged_real_l1_final_representatives']:,} |",
        f"| holdout 读取 | {dataset['holdout_rows_opened']} |",
        "",
        "图片数不是独立事件数：最新 Grade-A 8,000 图只有 1,043 个事件的 7–8 个位置变体；本轮使用的是旧 10,000 个正事件和 10,000 个匹配负事件的唯一血缘，并再次按完整输入＋标签暴露合并依赖块。",
        "",
        "## 与原模型同表对照",
        "",
        "| 配置 | top-decile 净均值 | q90 n | q90 净均值 | 胜率 | 置换 p |",
        "|---|---:|---:|---:|---:|---:|",
        f"| 原 side-split（精确复现） | {pct(base['final_validation']['top_decile']['net_mean'])} | {base['frozen_q90']['n']} | {pct(base['frozen_q90']['net_mean'])} | {base['frozen_q90']['win_rate']:.2%} | {base['outcome_permutation_p']:.6f} |",
        f"| 参考事件扩充 | {pct(aug['final_validation']['top_decile']['net_mean'])} | {aug['frozen_q90']['n']} | {pct(aug['frozen_q90']['net_mean'])} | {aug['frozen_q90']['win_rate']:.2%} | {aug['outcome_permutation_p']:.6f} |",
        f"| 扩充单特征 ma_spread | {pct(one['final_validation']['top_decile']['net_mean'])} | {one['frozen_q90']['n']} | {pct(one['frozen_q90']['net_mean'])} | {one['frozen_q90']['win_rate']:.2%} | {one['outcome_permutation_p']:.6f} |",
        "",
        f"全特征参考扩充 AUC={aug['final_validation']['roc_auc']:.4f}，PR-AUC={aug['final_validation']['pr_auc']:.4f}，Spearman={aug['final_validation']['spearman_score_vs_return']:.4f}。AUC 仅作诊断，裁决仍看扣成本收益、置换检验与匹配随机对照。",
        "",
        "## LONG / SHORT",
        "",
        "| 方向 | final n | q90 n | q90 净均值 | 胜率 | p |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for side in SIDES:
        item = aug["by_side"][side]
        lines.append(
            f"| {side.upper()} | {item['final_validation']['n']} | {item['frozen_q90']['n']} | {pct(item['frozen_q90']['net_mean'])} | {item['frozen_q90']['win_rate']:.2%} | {item['outcome_permutation_p']:.6f} |"
        )
    matched = aug["matched_control"]
    lines.extend(
        [
            "",
            "## 匹配随机对照",
            "",
            f"要求同币、同月、同 UTC 8 小时时段、同 ATR 桶、同方向、同 TP/SL/horizon/cost。完整 assignment 为 {matched['usable_assignment_count']}/{matched['required_assignment_count']}；所有 assignment 均跑赢={matched['all_assignments_positive']}；完整覆盖入选事件={matched['selected_event_complete_control_count']}/{matched['selected_event_count']}；平均事件减对照={pct(matched['mean_event_minus_control'])}。",
            "",
            "## 基线与无前视验证",
            "",
            f"原 LONG/SHORT 模型、分数、百分位、q90 阈值和 KEEP 决策精确复现：`{training['baseline_reproduction']['passed']}`。参考窗口的特征只读 `window_end_i` 及以前；标签从下一根开盘开始。每个源文件只物理读取到所需 72 根 outcome 的前缀，最大读取时间早于 holdout；`holdout_rows_opened=0`。",
            "",
            "## 解读",
            "",
            "本实验只回答：把历史参考窗口按真实收益重新标注后加入 L2 训练，能否改善真实 L1 候选的时间外排序。它不把形态负图当亏损，也不拿参考图片自身做最终验收。若结果失败，含义是这些自动参考事件与真实 L1 提案分布不一致或经济信息不足，不能继续靠堆图片数量解决。",
            "",
            "## 风险与诚实声明",
            "",
            "- 10,000 正例来自 Owner 接受的自动参考族，不是 10,000 个逐张手工 Gold；来源字段保留但未作为模型特征。",
            "- 参考正例的筛选曾使用 completed-history 形态证据，因此可能存在样本选择偏差；最终指标只在未改动的真实 L1 候选上计算。",
            "- 当前 L1 仍是使用 post-core 2–9 根的 completed-history 棑测器，不得冒充 tip 实盘信号。",
            "- 未读取 holdout，未 promote、部署、改 ACTIVE/frozen/forward、发 Telegram 或下单。",
            "",
            "## 复现命令",
            "",
            "```bash",
            "PYTHONPATH=. .venv/bin/python -m scripts.research_15m_ma_launch_l2_reference_augmentation --all",
            "python3 scripts/md_to_html.py analysis/p3_15m_ma_launch_l2_reference_augmentation_20260902.md --out-dir analysis/html",
            "```",
            "",
            "## 下一步",
            "",
            "只有全部预注册经济门通过，才值得申请新的未见时间段复验；本报告本身不授权 holdout、promote 或部署。",
            "",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    return REPORT_PATH


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dataset", action="store_true")
    parser.add_argument("--train-evaluate", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    if not any(
        (args.build_dataset, args.train_evaluate, args.verify, args.report, args.all)
    ):
        parser.error("select at least one action")
    prereg = load_preregistration()
    outputs: dict[str, Any] = {}
    if args.all or args.build_dataset:
        outputs["dataset"] = build_augmented_dataset(prereg)
    if args.all or args.train_evaluate:
        outputs["training"] = train_evaluate(prereg)
    if args.all or args.verify:
        outputs["verify"] = verify_outputs(prereg)
    if args.all or args.report:
        outputs["report"] = repo_relative(build_report(prereg))
    print(json.dumps(outputs, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
