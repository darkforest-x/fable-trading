#!/usr/bin/env python3
"""Evaluate a causal two-stage K2 representation on BTCUSDT.P 15m.

Every signal feature uses completed OHLCV through the confirmation bar only.
The candidate changes K2 from one candle into an interval: a qualifying SMA40
wick touch may be followed by the earliest qualifying rejection at +0, +1 or
+2 bars. Delay zero is intentionally identical to the legacy same-bar K2.
Entry economics use the following bar's open. Only outcome resolution reads
the frozen 48-bar future path. The physical source ends before the repository
holdout at 2026-05-04.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.optimize_btcusdtp_k1k2_independent_timeframes import (
    audit_slice_label,
    ranking_metrics,
)
from scripts.optimize_btcusdtp_k1k2_intraday_preholdout import (
    BAR_DELTAS,
    accept_events,
    add_control_metrics,
    build_matched_controls,
    build_pair_universe,
    filter_pairs,
    fold_table,
    json_value,
    load_featured,
    period_events,
    robust_metrics,
    sha256_file,
    utc,
    write_csv,
    write_json,
)


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / (
    "experiments/active/"
    "exp-btcusdtp-k1k2-15m-two-stage-k2-preholdout-20260904-v1"
)
CONFIG_PATH = EXPERIMENT / "config.json"
RESULTS = EXPERIMENT / "results"
DEVELOPMENT_RECEIPT = RESULTS / "development_receipt.json"
SCRIPT_PATH = Path(__file__).resolve()
BAR = "15m"
CANDIDATE_COLUMNS = [
    "direction",
    "k1_i",
    "touch_i",
    "k2_i",
    "confirmation_i",
    "gap_bars",
    "confirmation_delay_bars",
    "k1_body_ratio",
    "k1_range_atr",
    "k1_close_location",
    "k1_sma40_cross_depth_atr",
    "touch_wick_share",
    "touch_body_ratio",
    "touch_rejection_close_location",
    "touch_depth_atr",
    "confirmation_wick_share",
    "confirmation_body_ratio",
    "confirmation_rejection_close_location",
    "k1_quality",
    "secondary_score",
]


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def frozen_params(config: dict[str, Any]) -> dict[str, Any]:
    signal = config["signal_frozen"]
    return {
        "gap_min_bars": int(signal["gap_min_bars_k1_to_touch"]),
        "gap_max_bars": int(signal["gap_max_bars_k1_to_touch"]),
        "k1_min_body_ratio": float(signal["k1_min_body_ratio"]),
        "k1_min_range_atr": float(signal["k1_min_range_atr"]),
        "k1_min_directional_close_location": float(
            signal["k1_min_directional_close_location"]
        ),
        "k1_min_sma40_cross_depth_atr": float(
            signal["k1_min_sma40_cross_depth_atr"]
        ),
        "k2_min_rejection_wick_share": float(
            signal["k2_min_rejection_wick_share"]
        ),
        "k2_max_body_ratio": float(signal["k2_max_body_ratio"]),
        "k2_min_rejection_close_location": float(
            signal["k2_min_rejection_close_location"]
        ),
        "k2_touch_depth_atr_min": float(signal["k2_touch_depth_atr_min"]),
        "k2_touch_depth_atr_max": float(signal["k2_touch_depth_atr_max"]),
        "oscillator_gate": str(signal["oscillator_gate"]),
        "k1_min_volume_ratio_20": signal["k1_min_volume_ratio_20"],
        "fee_to_risk_max": float(signal["fee_to_risk_max"]),
    }


def _side_geometry(frame: pd.DataFrame, direction: int) -> dict[str, np.ndarray]:
    """Return causal SMA40 geometry using only each row's completed OHLCV."""

    open_ = frame["open"].to_numpy(dtype=float)
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    sma = frame["sma40_hl2"].to_numpy(dtype=float)
    atr = frame["atr"].to_numpy(dtype=float)
    ranges = high - low
    bodies = np.abs(close - open_)
    with np.errstate(divide="ignore", invalid="ignore"):
        body_ratio = bodies / ranges
        range_atr = ranges / atr
        if direction > 0:
            k1_close = (close - low) / ranges
            entry_depth = (sma - open_) / atr
            exit_depth = (close - sma) / atr
            wick_share = (np.minimum(open_, close) - low) / ranges
            reject_close = (close - low) / ranges
            touch_depth = (sma - low) / atr
            close_side = (close - sma) / atr
            body_side = np.minimum(open_, close) >= sma
        else:
            k1_close = (high - close) / ranges
            entry_depth = (open_ - sma) / atr
            exit_depth = (sma - close) / atr
            wick_share = (high - np.maximum(open_, close)) / ranges
            reject_close = (high - close) / ranges
            touch_depth = (high - sma) / atr
            close_side = (sma - close) / atr
            body_side = np.maximum(open_, close) <= sma
    return {
        "body_ratio": body_ratio,
        "range_atr": range_atr,
        "k1_close_location": k1_close,
        "k1_cross_depth_atr": np.minimum(entry_depth, exit_depth),
        "wick_share": wick_share,
        "rejection_close_location": reject_close,
        "touch_depth_atr": touch_depth,
        "close_side_atr": close_side,
        "body_side": body_side,
    }


def build_k2_event_candidates(
    frame: pd.DataFrame,
    config: dict[str, Any],
    *,
    maximum_confirmation_delay_bars: int,
) -> pd.DataFrame:
    """Build deterministic K1/touch/confirmation events without future bars.

    Columns used are open, high, low, close, ATR14, SMA40(HL2), MA-side colour,
    segment id and timestamp. The longest feature window is the already-causal
    SMA40 at the current row. A confirmation at index ``i`` never reads beyond
    ``i``; the next-open economics are applied later.
    """

    if maximum_confirmation_delay_bars not in {0, 1, 2}:
        raise ValueError("registered confirmation delay must be 0, 1 or 2")
    params = frozen_params(config)
    segment = frame["segment_id"].to_numpy(dtype=int)
    times = pd.to_datetime(frame["open_time"], utc=True)
    open_ = frame["open"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    sma = frame["sma40_hl2"].to_numpy(dtype=float)
    ma_side = frame["ma_shift_candle_side"].to_numpy(dtype=int)
    n = len(frame)
    parts: list[pd.DataFrame] = []
    delta = BAR_DELTAS[BAR]

    for direction in (1, -1):
        geo = _side_geometry(frame, direction)
        finite_k1 = np.logical_and.reduce(
            [
                np.isfinite(geo["body_ratio"]),
                np.isfinite(geo["range_atr"]),
                np.isfinite(geo["k1_close_location"]),
                np.isfinite(geo["k1_cross_depth_atr"]),
            ]
        )
        k1_mask = (
            (direction * (close - open_) > 0.0)
            & (ma_side == direction)
            & finite_k1
            & (geo["body_ratio"] >= params["k1_min_body_ratio"])
            & (geo["range_atr"] >= params["k1_min_range_atr"])
            & (
                geo["k1_close_location"]
                >= params["k1_min_directional_close_location"]
            )
            & (
                geo["k1_cross_depth_atr"]
                >= params["k1_min_sma40_cross_depth_atr"]
            )
        )
        finite_k2 = np.logical_and.reduce(
            [
                np.isfinite(geo["body_ratio"]),
                np.isfinite(geo["wick_share"]),
                np.isfinite(geo["rejection_close_location"]),
                np.isfinite(geo["touch_depth_atr"]),
                np.isfinite(geo["close_side_atr"]),
            ]
        )
        touch_mask = (
            finite_k2
            & (geo["wick_share"] >= params["k2_min_rejection_wick_share"])
            & (geo["body_ratio"] <= params["k2_max_body_ratio"])
            & (geo["touch_depth_atr"] >= params["k2_touch_depth_atr_min"])
            & (geo["touch_depth_atr"] <= params["k2_touch_depth_atr_max"])
            & (geo["close_side_atr"] >= 0.0)
            & geo["body_side"]
        )
        confirmation_mask = (
            finite_k2
            & (geo["wick_share"] >= params["k2_min_rejection_wick_share"])
            & (geo["body_ratio"] <= params["k2_max_body_ratio"])
            & (
                geo["rejection_close_location"]
                >= params["k2_min_rejection_close_location"]
            )
            & (geo["close_side_atr"] >= 0.0)
            & geo["body_side"]
        )
        wrong_path = (
            ~np.isfinite(sma)
            | (direction * (close - sma) < 0.0)
            | (ma_side != direction)
        )
        prefix = np.concatenate(([0], np.cumsum(wrong_path.astype(np.int64))))

        for gap in range(params["gap_min_bars"], params["gap_max_bars"] + 1):
            touch_index = np.arange(gap, n, dtype=int)
            k1_index = touch_index - gap
            middle_bad = prefix[touch_index] - prefix[k1_index + 1]
            valid_touch = (
                touch_mask[touch_index]
                & k1_mask[k1_index]
                & (segment[touch_index] == segment[k1_index])
                & (middle_bad == 0)
            )
            if not valid_touch.any():
                continue
            touch_i = touch_index[valid_touch]
            k1_i = k1_index[valid_touch]
            chosen = np.full(len(touch_i), -1, dtype=int)
            for delay in range(maximum_confirmation_delay_bars + 1):
                pending = chosen < 0
                confirm_i = touch_i + delay
                in_bounds = confirm_i < n
                safe_confirm = np.minimum(confirm_i, n - 1)
                contiguous = (
                    in_bounds
                    & (segment[safe_confirm] == segment[touch_i])
                    & (
                        times.iloc[safe_confirm].reset_index(drop=True)
                        - times.iloc[touch_i].reset_index(drop=True)
                        == delay * delta
                    ).to_numpy(dtype=bool)
                )
                morphology = confirmation_mask[safe_confirm]
                if delay > 0:
                    morphology &= ma_side[safe_confirm] == direction
                    between_bad = prefix[safe_confirm] - prefix[touch_i + 1]
                    morphology &= between_bad == 0
                take = pending & contiguous & morphology
                chosen[take] = delay
            keep = chosen >= 0
            if not keep.any():
                continue
            touch_i = touch_i[keep]
            k1_i = k1_i[keep]
            delay = chosen[keep]
            confirm_i = touch_i + delay
            quality = np.mean(
                np.column_stack(
                    [
                        np.clip(geo["body_ratio"][k1_i], 0.0, 1.0),
                        np.clip(geo["range_atr"][k1_i] / 2.0, 0.0, 1.0),
                        np.clip(geo["k1_close_location"][k1_i], 0.0, 1.0),
                        np.clip(
                            (geo["k1_cross_depth_atr"][k1_i] + 0.05) / 0.50,
                            0.0,
                            1.0,
                        ),
                    ]
                ),
                axis=1,
            )
            parts.append(
                pd.DataFrame(
                    {
                        "direction": direction,
                        "k1_i": k1_i,
                        "touch_i": touch_i,
                        "k2_i": confirm_i,
                        "confirmation_i": confirm_i,
                        "gap_bars": gap,
                        "confirmation_delay_bars": delay,
                        "k1_body_ratio": geo["body_ratio"][k1_i],
                        "k1_range_atr": geo["range_atr"][k1_i],
                        "k1_close_location": geo["k1_close_location"][k1_i],
                        "k1_sma40_cross_depth_atr": geo["k1_cross_depth_atr"][k1_i],
                        "touch_wick_share": geo["wick_share"][touch_i],
                        "touch_body_ratio": geo["body_ratio"][touch_i],
                        "touch_rejection_close_location": geo[
                            "rejection_close_location"
                        ][touch_i],
                        "touch_depth_atr": geo["touch_depth_atr"][touch_i],
                        "confirmation_wick_share": geo["wick_share"][confirm_i],
                        "confirmation_body_ratio": geo["body_ratio"][confirm_i],
                        "confirmation_rejection_close_location": geo[
                            "rejection_close_location"
                        ][confirm_i],
                        "k1_quality": quality,
                        "secondary_score": quality,
                    }
                )
            )
    if not parts:
        return pd.DataFrame(columns=CANDIDATE_COLUMNS)
    candidates = pd.concat(parts, ignore_index=True)
    candidates = candidates.sort_values(
        [
            "k2_i",
            "direction",
            "confirmation_delay_bars",
            "k1_quality",
            "gap_bars",
            "touch_i",
        ],
        ascending=[True, False, True, False, True, False],
        kind="mergesort",
    )
    return candidates.drop_duplicates(["k2_i", "direction"], keep="first").reset_index(
        drop=True
    )


def accept_k2_events(
    candidates: pd.DataFrame,
    frame: pd.DataFrame,
    config: dict[str, Any],
) -> pd.DataFrame:
    """Apply next-open economics, global cooldown and K1 reuse causally."""

    if candidates.empty:
        return candidates.copy()
    params = frozen_params(config)
    execution = config["execution_frozen"]
    cost = float(execution["round_trip_cost_fraction"])
    by_key = {
        (int(row.k2_i), int(row.direction)): row._asdict()
        for row in candidates.itertuples(index=False)
    }
    accepted: list[dict[str, Any]] = []
    last_entry = -10**12
    last_k1: dict[int, int | None] = {1: None, -1: None}
    delta = BAR_DELTAS[BAR]
    for confirm_i in sorted(candidates["k2_i"].astype(int).unique()):
        entry_i = confirm_i + 1
        if entry_i >= len(frame):
            continue
        if (
            int(frame.loc[entry_i, "segment_id"])
            != int(frame.loc[confirm_i, "segment_id"])
            or frame.loc[entry_i, "open_time"] - frame.loc[confirm_i, "open_time"]
            != delta
        ):
            continue
        for direction in (1, -1):
            base = by_key.get((confirm_i, direction))
            if base is None:
                continue
            touch_i = int(base["touch_i"])
            interval = frame.loc[touch_i:confirm_i]
            entry = float(frame.loc[entry_i, "open"])
            stop = float(
                interval["low"].min() if direction > 0 else interval["high"].max()
            )
            risk = direction * (entry - stop)
            atr = float(frame.loc[confirm_i, "atr"])
            risk_atr = risk / atr if atr > 0.0 else float("nan")
            risk_fraction = risk / entry if entry > 0.0 else float("nan")
            fee_to_risk = cost / risk_fraction if risk_fraction > 0.0 else float("inf")
            if not (
                np.isfinite(risk_atr)
                and float(execution["next_open_risk_atr_min"])
                <= risk_atr
                <= float(execution["next_open_risk_atr_max"])
                and fee_to_risk <= float(params["fee_to_risk_max"])
            ):
                continue
            if entry_i - last_entry < int(execution["cooldown_bars"]):
                continue
            if last_k1[direction] is not None and int(base["k1_i"]) == last_k1[direction]:
                continue
            setup = (
                f"BTC-USDT-SWAP|{BAR}|{direction}|"
                f"{frame.loc[touch_i, 'open_time'].isoformat()}|"
                f"{frame.loc[confirm_i, 'open_time'].isoformat()}|{int(base['k1_i'])}"
            )
            row = dict(base)
            row.update(
                {
                    "bar": BAR,
                    "setup_id": hashlib.sha256(setup.encode()).hexdigest()[:16],
                    "entry_i": entry_i,
                    "entry_time": frame.loc[entry_i, "open_time"],
                    "entry_price": entry,
                    "stop_price": stop,
                    "risk_price": risk,
                    "risk_fraction": risk_fraction,
                    "stop_distance_atr": risk_atr,
                    "fee_to_risk": fee_to_risk,
                    "target_price": entry
                    + direction * risk * float(execution["target_r"]),
                    "interval_low": float(interval["low"].min()),
                    "interval_high": float(interval["high"].max()),
                }
            )
            accepted.append(row)
            last_entry = entry_i
            last_k1[direction] = int(base["k1_i"])
            break
    return pd.DataFrame(accepted).sort_values("entry_i", kind="mergesort").reset_index(
        drop=True
    )


def legacy_parity(
    frame: pd.DataFrame, config: dict[str, Any], same_bar: pd.DataFrame
) -> dict[str, Any]:
    """Fail closed unless delay-zero construction reproduces the legacy baseline."""

    params = frozen_params(config)
    selection_path = PROJECT / str(config["predecessor"]["selection_receipt"])
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    predecessor_config_path = selection_path.parents[1] / "config.json"
    if sha256_file(predecessor_config_path) != str(selection["config_sha256"]):
        raise RuntimeError("predecessor config no longer matches its selection receipt")
    predecessor_config = json.loads(predecessor_config_path.read_text(encoding="utf-8"))
    legacy = filter_pairs(
        build_pair_universe(frame, predecessor_config, BAR), params
    )
    keys = ["direction", "k1_i", "k2_i", "gap_bars"]
    left = legacy[keys].sort_values(keys, kind="mergesort").reset_index(drop=True)
    right = same_bar[keys].sort_values(keys, kind="mergesort").reset_index(drop=True)
    pd.testing.assert_frame_equal(left, right, check_dtype=False)
    legacy_accepted = accept_events(legacy, frame, predecessor_config, BAR, params)
    ours_accepted = accept_k2_events(same_bar, frame, config)
    accepted_keys = ["direction", "k1_i", "k2_i", "entry_i"]
    pd.testing.assert_frame_equal(
        legacy_accepted[accepted_keys].reset_index(drop=True),
        ours_accepted[accepted_keys].reset_index(drop=True),
        check_dtype=False,
    )
    np.testing.assert_allclose(
        legacy_accepted["stop_price"].to_numpy(dtype=float),
        ours_accepted["stop_price"].to_numpy(dtype=float),
        rtol=0.0,
        atol=1e-12,
    )
    return {
        "legacy_candidate_rows": len(legacy),
        "delay_zero_candidate_rows": len(same_bar),
        "legacy_accepted_rows": len(legacy_accepted),
        "delay_zero_accepted_rows": len(ours_accepted),
        "candidate_keys_exact": True,
        "accepted_keys_exact": True,
        "stop_prices_exact": True,
    }


def evaluate(
    candidates: pd.DataFrame,
    frame: pd.DataFrame,
    config: dict[str, Any],
    start: pd.Timestamp,
    end: pd.Timestamp,
    folds: list[str],
    excluded_signal_indices: set[int],
    *,
    labeler: Any = None,
) -> dict[str, Any]:
    decisions = accept_k2_events(candidates, frame, config)
    events = period_events(decisions, frame, config, BAR, start, end)
    controls, pairs = build_matched_controls(
        events,
        frame,
        config,
        BAR,
        start,
        end,
        excluded_signal_indices,
    )
    gate = config["development_gate"]
    metrics = robust_metrics(
        events,
        folds,
        int(gate["minimum_events_total"]),
        int(gate["minimum_events_per_fold"]),
    )
    metrics = {
        **metrics,
        **add_control_metrics({}, pairs),
        **ranking_metrics(events, resamples=20_000),
    }
    slices = (
        fold_table(events, folds, labeler=labeler)
        if labeler is not None
        else fold_table(events, folds)
    )
    return {
        "candidates": candidates,
        "decisions": decisions,
        "events": events,
        "controls": controls,
        "pairs": pairs,
        "metrics": metrics,
        "folds": slices,
    }


def development_passed(
    baseline: dict[str, Any], candidate: dict[str, Any]
) -> tuple[bool, list[str]]:
    metrics = candidate["metrics"]
    failures: list[str] = []
    if not bool(metrics["eligible"]):
        failures.append("sample_ineligible")
    if not float(metrics["mean_net_bp"]) > 0.0:
        failures.append("mean_net_not_positive")
    if not float(metrics["robust_score_bp"]) > 0.0:
        failures.append("robust_score_not_positive")
    if not float(metrics["worst_fold_net_bp"]) > -5.0:
        failures.append("worst_fold_below_minus_5bp")
    if not candidate["folds"]["mean_net_bp"].gt(0.0).all():
        failures.append("not_every_halfyear_positive")
    improvement = float(metrics["robust_score_bp"]) - float(
        baseline["metrics"]["robust_score_bp"]
    )
    if improvement < 5.0:
        failures.append("robust_improvement_below_5bp")
    if not float(metrics["matched_control_excess_bp"]) > 0.0:
        failures.append("matched_control_excess_not_positive")
    if not float(metrics["paired_signflip_p_one_sided"]) < 0.01:
        failures.append("paired_signflip_p_not_below_0.01")
    return not failures, failures


def sequence_changes(baseline: pd.DataFrame, candidate: pd.DataFrame) -> pd.DataFrame:
    keys = ["entry_time", "direction"]
    left_columns = keys + ["setup_id", "k1_i", "k2_i", "net_return"]
    right_columns = keys + [
        "setup_id",
        "k1_i",
        "touch_i",
        "k2_i",
        "confirmation_delay_bars",
        "net_return",
    ]
    merged = baseline[left_columns].merge(
        candidate[right_columns],
        on=keys,
        how="outer",
        suffixes=("_baseline", "_candidate"),
        indicator=True,
    )
    merged["sequence_change"] = merged["_merge"].map(
        {"left_only": "baseline_only", "right_only": "candidate_only", "both": "same_entry"}
    )
    return merged.drop(columns="_merge")


def write_evaluation(prefix: str, result: dict[str, Any]) -> None:
    write_csv(result["candidates"], RESULTS / f"{prefix}_candidates.csv.gz")
    write_csv(result["decisions"], RESULTS / f"{prefix}_decisions.csv.gz")
    write_csv(result["events"], RESULTS / f"{prefix}_trades.csv.gz")
    write_csv(result["controls"], RESULTS / f"{prefix}_matched_controls.csv.gz")
    write_csv(result["pairs"], RESULTS / f"{prefix}_matched_pairs.csv")
    write_csv(result["folds"], RESULTS / f"{prefix}_folds.csv")


def development_phase(config: dict[str, Any]) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    predecessor = PROJECT / str(config["predecessor"]["selection_receipt"])
    if sha256_file(predecessor) != str(
        config["predecessor"]["selection_receipt_sha256"]
    ):
        raise RuntimeError("frozen predecessor selection receipt SHA drift")
    frame, quality = load_featured(config, BAR)
    baseline_candidates = build_k2_event_candidates(
        frame, config, maximum_confirmation_delay_bars=0
    )
    parity = legacy_parity(frame, config, baseline_candidates)
    candidate_candidates = build_k2_event_candidates(
        frame,
        config,
        maximum_confirmation_delay_bars=int(
            config["factor"]["candidate"]["maximum_confirmation_delay_bars"]
        ),
    )
    start = utc(config["window"]["development_start_inclusive"])
    end = utc(config["window"]["development_end_exclusive"])
    folds = list(config["window"]["development_folds"])
    all_signals = set(baseline_candidates["k2_i"].astype(int)) | set(
        candidate_candidates["k2_i"].astype(int)
    )
    baseline = evaluate(
        baseline_candidates, frame, config, start, end, folds, all_signals
    )
    candidate = evaluate(
        candidate_candidates, frame, config, start, end, folds, all_signals
    )
    passed, failures = development_passed(baseline, candidate)
    changes = sequence_changes(baseline["events"], candidate["events"])
    write_evaluation("development_baseline_same_bar", baseline)
    write_evaluation("development_candidate_two_stage", candidate)
    write_csv(changes, RESULTS / "development_sequence_changes.csv")
    metrics = pd.DataFrame(
        [
            {"arm": "baseline_same_bar", **baseline["metrics"]},
            {"arm": "candidate_two_stage", **candidate["metrics"]},
        ]
    )
    write_csv(metrics, RESULTS / "development_metrics.csv")
    delay_counts = (
        candidate["events"]["confirmation_delay_bars"]
        .value_counts()
        .sort_index()
        .rename_axis("confirmation_delay_bars")
        .reset_index(name="events")
    )
    write_csv(delay_counts, RESULTS / "development_candidate_delay_counts.csv")
    receipt = {
        "phase": "development_complete_audit_unopened",
        "config_sha256": sha256_file(CONFIG_PATH),
        "script_sha256": sha256_file(SCRIPT_PATH),
        "predecessor_selection_sha256": sha256_file(predecessor),
        "source": {**quality, "audit_outcomes_read": 0, "holdout_rows_read": 0},
        "development_window": [start, end],
        "legacy_parity": parity,
        "baseline_metrics": baseline["metrics"],
        "candidate_metrics": candidate["metrics"],
        "candidate_delay_counts": dict(
            zip(
                delay_counts["confirmation_delay_bars"].astype(str),
                delay_counts["events"].astype(int),
            )
        ),
        "sequence_changes": changes["sequence_change"].value_counts().to_dict(),
        "robust_improvement_bp": float(candidate["metrics"]["robust_score_bp"])
        - float(baseline["metrics"]["robust_score_bp"]),
        "development_gate_passed": passed,
        "development_gate_failures": failures,
        "audit_open_allowed": passed,
        "holdout_rows_read": 0,
    }
    write_json(DEVELOPMENT_RECEIPT, receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))


def assert_development_committed(receipt: dict[str, Any]) -> None:
    paths = [
        str(DEVELOPMENT_RECEIPT.relative_to(PROJECT)),
        str(CONFIG_PATH.relative_to(PROJECT)),
        str(SCRIPT_PATH.relative_to(PROJECT)),
    ]
    for relative in paths:
        subprocess.run(
            ["git", "ls-files", "--error-unmatch", relative],
            cwd=PROJECT,
            check=True,
            stdout=subprocess.DEVNULL,
        )
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", *paths],
        cwd=PROJECT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if dirty:
        raise RuntimeError(f"development receipt/config/script must be committed: {dirty}")
    if receipt.get("config_sha256") != sha256_file(CONFIG_PATH):
        raise RuntimeError("development config SHA drift")
    if receipt.get("script_sha256") != sha256_file(SCRIPT_PATH):
        raise RuntimeError("development script SHA drift")


def audit_phase(config: dict[str, Any]) -> None:
    receipt = json.loads(DEVELOPMENT_RECEIPT.read_text(encoding="utf-8"))
    assert_development_committed(receipt)
    if not bool(receipt.get("audit_open_allowed")):
        raise RuntimeError("development futility gate: audit remains closed")
    frame, quality = load_featured(config, BAR)
    candidates = build_k2_event_candidates(
        frame,
        config,
        maximum_confirmation_delay_bars=int(
            config["factor"]["candidate"]["maximum_confirmation_delay_bars"]
        ),
    )
    start = utc(config["window"]["audit_start_inclusive"])
    end = utc(config["window"]["audit_end_exclusive"])
    slices = list(config["window"]["audit_slices"])
    result = evaluate(
        candidates,
        frame,
        config,
        start,
        end,
        slices,
        set(candidates["k2_i"].astype(int)),
        labeler=audit_slice_label,
    )
    complete = result["folds"].loc[result["folds"]["fold"].isin(["2025H1", "2025H2"])]
    passed = bool(
        float(result["metrics"]["mean_net_bp"]) > 0.0
        and float(result["metrics"]["matched_control_excess_bp"]) > 0.0
        and float(result["metrics"]["paired_signflip_p_one_sided"]) < 0.01
        and len(complete) == 2
        and complete["mean_net_bp"].gt(0.0).all()
    )
    write_evaluation("audit_candidate_two_stage", result)
    summary = {
        "phase": "qualified_frozen_audit_complete",
        "source": {**quality, "holdout_rows_read": 0},
        "candidate_metrics": result["metrics"],
        "audit_slices": result["folds"].to_dict("records"),
        "audit_success_gate_passed": passed,
        "holdout_rows_read": 0,
    }
    write_json(RESULTS / "audit_summary.json", summary)
    print(json.dumps(json_value(summary), ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["development", "audit"], required=True)
    args = parser.parse_args()
    config = load_config()
    if args.phase == "development":
        development_phase(config)
    else:
        audit_phase(config)


if __name__ == "__main__":
    main()
