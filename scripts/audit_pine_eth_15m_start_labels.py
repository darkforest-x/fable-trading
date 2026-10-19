#!/usr/bin/env python3
"""Automatically label all 2023--2024 ETH 15m Pine V9 raw candidates.

Features are the existing 28 side-aligned L2 columns, each computed from the
confirmed signal bar and prior history only.  The label path starts at ``t+1``
open and reads future ``open/high/low`` only until one frozen event occurs:
the +/-1.5% launch target, V9's unchanged initial stop, or the next guarded
opposite V9 signal's following open.  Ordered 3-minute OHLC resolves 15-minute
double touches; a remaining 3-minute double touch is excluded as ambiguous.

The loader is hard-bounded at 2025-01-01.  It does not read the consumed final
period or holdout, fit a model, select a threshold, or change TP/SL/cost inputs.
"""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.prepare_pine_eth_15m_gate_surface import (
    OUTPUT as SURFACE_OUTPUT,
    build_candidate_surface,
)
from scripts.prepare_pine_eth_15m_judgment_research import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    load_development_features,
)
from scripts.research_pine_eth_15m import exact_execution, load_config
from scripts.research_pine_eth_15m import Period, _atr_month_buckets
from yoyo.layers.l2_judgment.features import FEATURE_COLUMNS
from yoyo.layers.l2_judgment.pine_start_labels import label_pine_start_candidate
from yoyo.layers.l3_backtest.pine_allin_v7 import (
    SignalParameters,
    deterministic_control_indices,
    load_development_frame,
)


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
RESULTS = EXPERIMENT / "results"
THREE_MINUTE_PATH = PROJECT / "data/kline_deep/okx_ETH_USDT_SWAP_3m_525599.csv"
ROWS_OUTPUT = RESULTS / "judgment_start_label_rows.csv"
FEATURE_AUDIT_OUTPUT = RESULTS / "judgment_start_label_feature_audit.csv"
AUDIT_OUTPUT = RESULTS / "judgment_start_label_audit.json"
MATCHED_CONTROLS_OUTPUT = RESULTS / "judgment_start_label_matched_controls.csv"
PERMUTATIONS = 2_000
PERMUTATION_SEED = 15_335
CONTROLS_PER_CANDIDATE = 3
CONTROL_SEED = "pine-eth15m-start-label-control-v1"
BLOCK_RESAMPLES = 20_000
# This is a label contract, not an execution-setting alias.  Keep it explicit so
# a future break-even policy change cannot silently redefine historical labels.
LABEL_TARGET_PERCENT = 1.5


def _utc(value: str | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")


def validate_fifteen_minute_cadence(featured: pd.DataFrame) -> dict[str, int]:
    """Fail closed on duplicate, unsorted, or non-15m parent timestamps."""

    times = pd.to_datetime(featured["open_time"], utc=True, errors="raise")
    deltas = times.diff().dropna()
    result = {
        "duplicate_timestamps": int(times.duplicated().sum()),
        "non_increasing_timestamps": int(deltas.le(pd.Timedelta(0)).sum()),
        "non_15m_gaps": int(deltas.ne(pd.Timedelta(minutes=15)).sum()),
    }
    if any(result.values()):
        raise RuntimeError(f"15m cadence contract failed: {result}")
    return result


def next_opposite_signal_indices(surface: pd.DataFrame) -> dict[str, int | None]:
    """Map each candidate id to the next later guarded candidate of the other side."""

    by_side = {
        side: np.sort(surface.loc[surface["side"].eq(side), "signal_i"].astype(int).to_numpy())
        for side in ("long", "short")
    }
    result: dict[str, int | None] = {}
    for row in surface.itertuples(index=False):
        opposite = by_side["short" if row.side == "long" else "long"]
        offset = int(np.searchsorted(opposite, int(row.signal_i), side="right"))
        result[str(row.candidate_id)] = None if offset == len(opposite) else int(opposite[offset])
    return result


def load_lower_development(
    featured: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load the bounded, gapless 3m prefix and prove its full parents match 15m."""

    config = load_config()
    holdout_start = _utc(config["time_contract"]["holdout_start"])
    lower = load_development_frame(
        THREE_MINUTE_PATH,
        safe_end=DEVELOPMENT_END,
        holdout_start=holdout_start,
    )
    lower["open_time"] = pd.to_datetime(lower["open_time"], utc=True)
    numeric_columns = ["open", "high", "low", "close", "volume"]
    lower[numeric_columns] = lower[numeric_columns].apply(pd.to_numeric, errors="coerce")
    times = lower["open_time"]
    deltas = times.diff().dropna()
    base_quality = {
        "source_path": str(THREE_MINUTE_PATH.relative_to(PROJECT)),
        "rows": int(len(lower)),
        "first_bar": times.iloc[0].isoformat(),
        "last_bar": times.iloc[-1].isoformat(),
        "development_end_exclusive": DEVELOPMENT_END.isoformat(),
        "consumed_final_rows_read": int(times.ge(DEVELOPMENT_END).sum()),
        "holdout_rows_read": int(times.ge(holdout_start).sum()),
        "duplicate_timestamps": int(times.duplicated().sum()),
        "non_3m_gaps": int(deltas.ne(pd.Timedelta(minutes=3)).sum()),
        "null_ohlcv_cells": int(lower[numeric_columns].isna().sum().sum()),
    }
    refused = (
        "consumed_final_rows_read",
        "holdout_rows_read",
        "duplicate_timestamps",
        "non_3m_gaps",
        "null_ohlcv_cells",
    )
    if any(base_quality[key] for key in refused):
        raise RuntimeError(f"bounded 3m data quality failed: {base_quality}")

    indexed = lower.set_index("open_time").sort_index()
    aggregate = indexed.resample("15min", label="left", closed="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        subbar_count=("close", "count"),
    )
    full = aggregate.loc[aggregate["subbar_count"].eq(5)].copy()
    parents = featured.set_index(pd.to_datetime(featured["open_time"], utc=True))
    joined = parents[["open", "high", "low", "close"]].join(
        full[["open", "high", "low", "close"]],
        how="inner",
        lsuffix="_15m",
        rsuffix="_3m",
    )
    mismatches = {
        column: int(
            (joined[f"{column}_15m"] - joined[f"{column}_3m"]).abs().gt(1e-9).sum()
        )
        for column in ("open", "high", "low", "close")
    }
    if any(mismatches.values()):
        raise RuntimeError(f"3m full parents do not reproduce 15m OHLC: {mismatches}")
    quality = {
        **base_quality,
        "full_15m_parents": int(len(full)),
        "joined_full_parents": int(len(joined)),
        "parent_ohlc_mismatches": mismatches,
    }
    return lower, quality


def _lower_paths_for_parents(
    lower: pd.DataFrame,
    parent_times: set[pd.Timestamp],
) -> dict[pd.Timestamp, pd.DataFrame]:
    """Materialize only double-touch parents, avoiding 50k tiny DataFrames."""

    if not parent_times:
        return {}
    work = lower.copy()
    work["parent_time"] = work["open_time"].dt.floor("15min")
    selected = work.loc[work["parent_time"].isin(parent_times)]
    paths: dict[pd.Timestamp, pd.DataFrame] = {}
    for parent_time, group in selected.groupby("parent_time", sort=False):
        parent_time = _utc(parent_time)
        expected = pd.date_range(parent_time, periods=5, freq="3min")
        actual = pd.DatetimeIndex(group.sort_values("open_time")["open_time"])
        if len(group) == 5 and actual.equals(expected):
            paths[parent_time] = group.drop(columns="parent_time").reset_index(drop=True)
    return paths


def label_surface(
    featured: pd.DataFrame,
    surface: pd.DataFrame,
    lower: pd.DataFrame,
) -> pd.DataFrame:
    """Two-pass labeling: detect double touches, then resolve only those via 3m."""

    params = SignalParameters()
    execution = exact_execution(equity_frequency=None)
    if execution.tick_size is None:
        raise RuntimeError("frozen V9 tick size is unexpectedly unset")
    opposite = next_opposite_signal_indices(surface)

    def run(lower_paths: dict[pd.Timestamp, pd.DataFrame], subset: pd.DataFrame) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for candidate in subset.itertuples(index=False):
            outcome = label_pine_start_candidate(
                featured,
                signal_i=int(candidate.signal_i),
                side=str(candidate.side),
                opposite_signal_i=opposite[str(candidate.candidate_id)],
                lower_by_parent=lower_paths,
                target_percent=LABEL_TARGET_PERCENT,
                atr_mult=params.atr_mult,
                max_sl_percent=params.max_sl_percent,
                tick_size=float(execution.tick_size),
            )
            rows.append({"candidate_id": candidate.candidate_id, **asdict(outcome)})
        return rows

    first = pd.DataFrame(run({}, surface))
    ambiguous = first.loc[first["status"].eq("ambiguous")]
    ambiguous_parents = set(pd.to_datetime(ambiguous["event_time"], utc=True))
    paths = _lower_paths_for_parents(lower, ambiguous_parents)
    if not ambiguous.empty:
        retry_ids = set(ambiguous["candidate_id"])
        retry_surface = surface.loc[surface["candidate_id"].isin(retry_ids)]
        retry = pd.DataFrame(run(paths, retry_surface)).set_index("candidate_id")
        first = first.set_index("candidate_id")
        first.loc[retry.index, retry.columns] = retry
        first = first.reset_index()

    labels = first
    labels["start_label"] = pd.array(labels.pop("label"), dtype="Int64")
    labels["label_semantics"] = (
        "touch_1p5pct_before_frozen_initial_stop_or_next_guarded_opposite_v1"
    )
    labels["training_eligible"] = False
    labels["forward_eligible"] = False
    labels["production_eligible"] = False
    merged = surface.merge(labels, on="candidate_id", how="left", validate="one_to_one")
    if merged["status"].isna().any():
        raise RuntimeError("some raw candidates were not labeled")
    feature_time = pd.to_datetime(merged["features_available_at"], utc=True)
    label_time = pd.to_datetime(merged["label_available_at"], utc=True)
    if not label_time.gt(feature_time).all():
        raise RuntimeError("label availability does not strictly follow causal features")
    return merged


def _tie_aware_auc(scores: np.ndarray, labels: np.ndarray) -> float:
    positives = int(labels.sum())
    negatives = int(len(labels) - positives)
    if positives == 0 or negatives == 0:
        return float("nan")
    ranks = pd.Series(scores).rank(method="average").to_numpy(dtype=float)
    return float(
        (ranks[labels].sum() - positives * (positives + 1) / 2.0)
        / (positives * negatives)
    )


def build_feature_audit(rows: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Descriptive single-feature AUCs plus a deterministic shuffled-label null."""

    resolved = rows.loc[rows["status"].eq("resolved")].copy()
    labels = resolved["start_label"].astype(bool).to_numpy()
    signal_time = pd.to_datetime(resolved["signal_time"], utc=True)
    records = []
    rank_columns = []
    for feature in FEATURE_COLUMNS:
        scores = resolved[feature].to_numpy(dtype=float)
        auc = _tie_aware_auc(scores, labels)
        mask_2023 = signal_time.lt(pd.Timestamp("2024-01-01", tz="UTC")).to_numpy()
        auc_2023 = _tie_aware_auc(scores[mask_2023], labels[mask_2023])
        auc_2024 = _tie_aware_auc(scores[~mask_2023], labels[~mask_2023])
        records.append(
            {
                "feature": feature,
                "auc_all": auc,
                "oriented_auc_all": max(auc, 1.0 - auc),
                "preferred_direction": "higher" if auc >= 0.5 else "lower",
                "auc_2023": auc_2023,
                "auc_2024": auc_2024,
                "same_direction_both_years": bool((auc_2023 - 0.5) * (auc_2024 - 0.5) > 0.0),
            }
        )
        rank_columns.append(pd.Series(scores).rank(method="average").to_numpy(dtype=float))
    audit = pd.DataFrame(records).sort_values("oriented_auc_all", ascending=False).reset_index(drop=True)

    ranks = np.column_stack(rank_columns)
    positives = int(labels.sum())
    negatives = int(len(labels) - positives)
    observed = float(np.nanmax(np.abs(audit["auc_all"].to_numpy(dtype=float) - 0.5)))
    rng = np.random.default_rng(PERMUTATION_SEED)
    exceed = 0
    for _ in range(PERMUTATIONS):
        shuffled = rng.permutation(labels)
        rank_sums = ranks[shuffled].sum(axis=0)
        aucs = (rank_sums - positives * (positives + 1) / 2.0) / (positives * negatives)
        exceed += int(np.max(np.abs(aucs - 0.5)) >= observed)
    null = {
        "method": "shuffle resolved start labels; recompute maximum absolute AUC deviation across 28 features",
        "permutations": PERMUTATIONS,
        "seed": PERMUTATION_SEED,
        "observed_max_abs_auc_minus_half": observed,
        "familywise_permutation_p": float((exceed + 1) / (PERMUTATIONS + 1)),
        "note": "exploratory encoding audit only; overlapping future paths are not an iid training evaluation",
    }
    return audit, null


def build_matched_label_controls(
    featured: pd.DataFrame,
    candidate_rows: pd.DataFrame,
    lower: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Compare signal labels with exact month x HK-6h x causal-ATR controls."""

    times = pd.to_datetime(featured["open_time"], utc=True)
    period = Period("development_2023_2024", DEVELOPMENT_START, DEVELOPMENT_END)
    atr_bucket = _atr_month_buckets(featured, period, buckets=5)
    month = times.dt.strftime("%Y-%m").to_numpy()
    hk_block = (times.dt.tz_convert("Asia/Hong_Kong").dt.hour.to_numpy(dtype=int) // 6).astype(int)
    in_development = times.ge(DEVELOPMENT_START) & times.lt(DEVELOPMENT_END)
    raw_signal = (
        featured["v9_long"].fillna(False).to_numpy(dtype=bool)
        | featured["v9_short"].fillna(False).to_numpy(dtype=bool)
    )
    pool_mask = (
        in_development.to_numpy(dtype=bool)
        & featured["entry_allowed"].fillna(False).to_numpy(dtype=bool)
        & ~raw_signal
        & (atr_bucket >= 0)
    )
    pool_by_stratum: dict[tuple[str, int, int], list[int]] = {}
    for index in np.flatnonzero(pool_mask):
        key = (str(month[index]), int(hk_block[index]), int(atr_bucket[index]))
        pool_by_stratum.setdefault(key, []).append(int(index))

    used: set[int] = set()
    assignments: list[dict[str, Any]] = []
    ambiguous_controls_rejected = 0
    ordered_candidates = candidate_rows.sort_values(["signal_i", "candidate_id"], kind="stable")
    for candidate in ordered_candidates.itertuples(index=False):
        signal_i = int(candidate.signal_i)
        if atr_bucket[signal_i] < 0:
            raise RuntimeError(f"candidate lacks causal ATR bucket: {candidate.candidate_id}")
        if pd.notna(candidate.opposite_signal_i):
            horizon_bars = int(candidate.opposite_signal_i) - signal_i
        else:
            horizon_bars = len(featured) - (signal_i + 1)
        key = (str(month[signal_i]), int(hk_block[signal_i]), int(atr_bucket[signal_i]))
        eligible = [
            control_i
            for control_i in pool_by_stratum.get(key, [])
            if control_i not in used and control_i + horizon_bars + 1 < len(featured)
        ]
        ordered_eligible = deterministic_control_indices(
            str(candidate.candidate_id),
            eligible,
            n=len(eligible),
            seed=CONTROL_SEED,
        )
        accepted = 0
        for control_i in ordered_eligible:
            synthetic_opposite_i = int(control_i + horizon_bars)
            outcome = label_pine_start_candidate(
                featured,
                signal_i=int(control_i),
                side=str(candidate.side),
                opposite_signal_i=synthetic_opposite_i,
                lower_by_parent={},
            )
            if outcome.status == "ambiguous":
                paths = _lower_paths_for_parents(lower, {_utc(outcome.event_time)})
                outcome = label_pine_start_candidate(
                    featured,
                    signal_i=int(control_i),
                    side=str(candidate.side),
                    opposite_signal_i=synthetic_opposite_i,
                    lower_by_parent=paths,
                )
            if outcome.status != "resolved":
                ambiguous_controls_rejected += 1
                continue
            rank = accepted
            accepted += 1
            used.add(control_i)
            assignments.append(
                {
                    "candidate_id": str(candidate.candidate_id),
                    "candidate_signal_i": signal_i,
                    "candidate_signal_time": candidate.signal_time,
                    "candidate_side": str(candidate.side),
                    "candidate_start_label": int(candidate.start_label),
                    "control_rank": rank,
                    "control_signal_i": control_i,
                    "control_signal_time": times.iloc[control_i],
                    "copied_horizon_bars": horizon_bars,
                    "match_month": key[0],
                    "match_hk_6h_block": key[1],
                    "match_causal_atr_quintile": key[2],
                    "control_status": outcome.status,
                    "control_start_label": int(outcome.label),
                    "control_outcome": (
                        "copied_horizon_timeout"
                        if outcome.outcome == "opposite_signal_exit"
                        else outcome.outcome
                    ),
                    "control_event_time": outcome.event_time,
                    "control_resolution": outcome.resolution,
                    "control_entry_price": outcome.entry_price,
                    "control_target_price": outcome.target_price,
                    "control_initial_stop_price": outcome.initial_stop_price,
                }
            )
            if accepted == CONTROLS_PER_CANDIDATE:
                break
        if accepted != CONTROLS_PER_CANDIDATE:
            raise RuntimeError(
                "exact matched-control stratum underfilled after automatic ambiguity rejection: "
                f"candidate={candidate.candidate_id}, key={key}, available={len(eligible)}, "
                f"accepted={accepted}"
            )

    controls = pd.DataFrame(assignments)
    if not controls["control_status"].eq("resolved").all():
        bad = controls.loc[~controls["control_status"].eq("resolved")]
        raise RuntimeError(f"matched controls contain unresolved labels: {bad.head().to_dict('records')}")
    controls["control_start_label"] = controls["control_start_label"].astype(int)
    if controls["control_signal_i"].duplicated().any():
        raise RuntimeError("matched controls unexpectedly reused a signal bar")

    paired = controls.groupby("candidate_id", sort=False).agg(
        candidate_signal_time=("candidate_signal_time", "first"),
        candidate_start_label=("candidate_start_label", "first"),
        control_count=("control_start_label", "size"),
        control_mean_start_label=("control_start_label", "mean"),
    )
    if not paired["control_count"].eq(CONTROLS_PER_CANDIDATE).all():
        raise RuntimeError("matched control coverage is not exact")
    paired["excess"] = paired["candidate_start_label"] - paired["control_mean_start_label"]
    week = (
        pd.to_datetime(paired["candidate_signal_time"], utc=True)
        .dt.tz_localize(None)
        .dt.to_period("W-SUN")
        .astype(str)
    )
    weekly = paired.groupby(week, sort=True)["excess"].mean().to_numpy(dtype=float)
    observed = float(weekly.mean())
    rng = np.random.default_rng(PERMUTATION_SEED + 1)
    signs = rng.choice(np.asarray([-1.0, 1.0]), size=(BLOCK_RESAMPLES, len(weekly)))
    null = (signs * weekly).mean(axis=1)
    summary = {
        "contract": "same ETH x UTC month x HK 6h block x prior-month ATR quintile x copied horizon",
        "controls_per_candidate": CONTROLS_PER_CANDIDATE,
        "candidate_coverage": int(len(paired)),
        "control_rows": int(len(controls)),
        "duplicate_control_signal_indices": int(controls["control_signal_i"].duplicated().sum()),
        "ambiguous_controls_automatically_rejected_and_replaced": ambiguous_controls_rejected,
        "candidate_positive_rate": float(paired["candidate_start_label"].mean()),
        "matched_control_positive_rate": float(paired["control_mean_start_label"].mean()),
        "candidate_minus_control_percentage_points": float(paired["excess"].mean() * 100.0),
        "equal_week_excess_percentage_points": float(observed * 100.0),
        "week_block_signflip_p_one_sided": float(
            (np.sum(null >= observed) + 1) / (BLOCK_RESAMPLES + 1)
        ),
        "weeks": int(len(weekly)),
        "resamples": BLOCK_RESAMPLES,
        "seed": CONTROL_SEED,
        "cost_note": "first-touch label comparison has no realized-return cost metric; both sides use identical barriers and horizon",
    }
    return controls, summary


def _group_summary(rows: pd.DataFrame, column: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for value, group in rows.groupby(column, dropna=False):
        resolved = group.loc[group["status"].eq("resolved")]
        result[str(value)] = {
            "candidates": int(len(group)),
            "resolved": int(len(resolved)),
            "positive": int(resolved["start_label"].sum()),
            "positive_rate": None if resolved.empty else float(resolved["start_label"].mean()),
            "ambiguous": int(group["status"].eq("ambiguous").sum()),
            "censored": int(group["status"].eq("censored").sum()),
        }
    return result


def main() -> None:
    featured, feature_quality = load_development_features()
    if feature_quality["consumed_final_rows_read"] or feature_quality["holdout_rows_read"]:
        raise RuntimeError("feature loader crossed a protected boundary")
    feature_quality = {
        **feature_quality,
        "cadence": validate_fifteen_minute_cadence(featured),
    }
    surface = build_candidate_surface(featured)
    if SURFACE_OUTPUT.exists():
        existing = pd.read_csv(SURFACE_OUTPUT)
        if list(existing["candidate_id"]) != list(surface["candidate_id"]):
            raise RuntimeError("persisted raw-candidate surface differs from rebuilt surface")
    lower, lower_quality = load_lower_development(featured)
    rows = label_surface(featured, surface, lower)
    feature_audit, shuffled_null = build_feature_audit(rows)
    matched_controls, matched_summary = build_matched_label_controls(featured, rows, lower)

    resolved = rows.loc[rows["status"].eq("resolved")]
    rows["signal_year"] = pd.to_datetime(rows["signal_time"], utc=True).dt.year
    rows["training_eligible"] = False
    label_columns = [
        "candidate_id",
        "side",
        "signal_i",
        "signal_time",
        "features_available_at",
        "earliest_entry_time",
        "entry_i",
        "entry_time",
        "entry_price",
        "target_price",
        "initial_stop_price",
        "initial_stop_distance",
        "opposite_signal_i",
        "opposite_entry_time",
        "event_i",
        "event_time",
        "label_available_at",
        "bars_to_event",
        "status",
        "outcome",
        "resolution",
        "start_label",
        "label_semantics",
        "signal_year",
        "feature_semantics",
        *FEATURE_COLUMNS,
        "training_eligible",
        "forward_eligible",
        "production_eligible",
    ]
    RESULTS.mkdir(parents=True, exist_ok=True)
    rows.loc[:, label_columns].to_csv(ROWS_OUTPUT, index=False)
    feature_audit.to_csv(FEATURE_AUDIT_OUTPUT, index=False)
    matched_controls.to_csv(MATCHED_CONTROLS_OUTPUT, index=False)

    audit = {
        "artifact": "automatic Pine ETH 15m V9 raw-candidate +1.5% first-touch labels",
        "status": "complete development-only path audit; still training-ineligible under P0/P1",
        "label_contract": {
            "entry": "confirmed signal t -> t+1 open",
            "positive": "+1.5% side-adjusted target touched first",
            "negative": "frozen initial V9 stop or next guarded opposite V9 signal's following open first",
            "initial_stop": "min(4 * signal-bar ATR14, 3% * signal close), rounded to 0.01 tick",
            "15m_double_touch": "ordered gap-safe 3m path",
            "3m_double_touch": "ambiguous and excluded; never manually guessed",
            "right_boundary": DEVELOPMENT_END.isoformat(),
        },
        "feature_data_quality": feature_quality,
        "lower_timeframe_quality": lower_quality,
        "candidates": int(len(rows)),
        "resolved": int(len(resolved)),
        "positive": int(resolved["start_label"].sum()),
        "negative": int(resolved["start_label"].eq(0).sum()),
        "positive_rate": float(resolved["start_label"].mean()),
        "ambiguous": int(rows["status"].eq("ambiguous").sum()),
        "censored": int(rows["status"].eq("censored").sum()),
        "fifteen_minute_double_touches": int(
            rows["resolution"].isin(
                [
                    "ordered_lower_path",
                    "lower_open_gap",
                    "lower_bar_double_touch",
                    "15m_double_touch_no_lower_path",
                ]
            ).sum()
        ),
        "double_touches_resolved_by_3m": int(
            rows["resolution"].isin(["ordered_lower_path", "lower_open_gap"]).sum()
        ),
        "remaining_lower_path_ambiguity": int(
            rows["resolution"].eq("lower_bar_double_touch").sum()
        ),
        "double_touch_without_3m": int(
            rows["resolution"].eq("15m_double_touch_no_lower_path").sum()
        ),
        "outcomes": {str(key): int(value) for key, value in rows["outcome"].value_counts().items()},
        "by_side": _group_summary(rows, "side"),
        "by_year": _group_summary(rows, "signal_year"),
        "median_bars_to_event_resolved": float(resolved["bars_to_event"].median()),
        "p90_bars_to_event_resolved": float(resolved["bars_to_event"].quantile(0.9)),
        "features_available_before_every_label": bool(
            pd.to_datetime(rows["label_available_at"], utc=True).gt(
                pd.to_datetime(rows["features_available_at"], utc=True)
            ).all()
        ),
        "feature_audit": {
            "top_five": feature_audit.head(5).to_dict(orient="records"),
            "shuffled_label_null": shuffled_null,
        },
        "matched_random_control": matched_summary,
        "model_trained_or_scored": False,
        "threshold_selected": False,
        "consumed_final_rows_read": 0,
        "holdout_rows_read": 0,
        "training_eligible": False,
        "forward_eligible": False,
        "production_eligible": False,
        "outputs": [
            str(ROWS_OUTPUT.relative_to(PROJECT)),
            str(FEATURE_AUDIT_OUTPUT.relative_to(PROJECT)),
            str(MATCHED_CONTROLS_OUTPUT.relative_to(PROJECT)),
        ],
    }
    AUDIT_OUTPUT.write_text(json.dumps(audit, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(audit, indent=2, default=str))


if __name__ == "__main__":
    main()
