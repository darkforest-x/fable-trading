#!/usr/bin/env python3
"""Map causal failure segments for frozen BTCUSDT.P 15m/5m K1->K2 trades.

Every diagnostic feature reads completed OHLCV through K2, rolling history
ending at K2, or the K2+1 entry open. Outcome columns alone read the registered
12-hour future path. This experiment evaluates 2023 outcomes only; 2024,
audit, and repository holdout outcomes remain unopened.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from scripts.optimize_btcusdtp_k1k2_independent_timeframes import (
    fold_table,
    json_value,
    utc,
    write_csv,
    write_json,
)
from scripts.optimize_btcusdtp_k1k2_intraday_preholdout import metric_row
from scripts.research_btcusdtp_k1k2_partial_runner import load_candidates
from scripts.research_btcusdtp_k1k2_stop_buffer import run_arm
from scripts.research_two_key_candle_ma_retest_1h import sha256_file

PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / (
    "experiments/active/exp-btcusdtp-k1k2-causal-failure-map-20260904-v1"
)
CONFIG_PATH = EXPERIMENT / "config.json"
RESULTS = EXPERIMENT / "results"
SCRIPT_PATH = Path(__file__).resolve()
TEAL = "#17A297"
ORANGE = "#F59E0B"
RED = "#F23645"
INK = "#26323A"


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator.astype(float).div(denominator.astype(float).replace(0.0, np.nan))


def add_context_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Add rolling features using only each row and earlier OHLCV columns."""

    out = frame.copy()
    atr = out["atr"].astype(float).replace(0.0, np.nan)
    close = out["close"].astype(float)
    ma = out["sma40_hl2"].astype(float)
    out["reference_ma_slope_4_atr"] = _safe_div(ma - ma.shift(4), atr * 4.0)
    out["reference_ma_slope_12_atr"] = _safe_div(ma - ma.shift(12), atr * 12.0)
    recent_slope = ma - ma.shift(4)
    prior_slope = ma.shift(4) - ma.shift(8)
    out["reference_ma_accel_4_atr"] = _safe_div(recent_slope - prior_slope, atr * 4.0)
    out["momentum_12_atr"] = _safe_div(close - close.shift(12), atr)
    out["momentum_48_atr"] = _safe_div(close - close.shift(48), atr)
    out["approach_pullback_3_atr"] = _safe_div(close.shift(3) - close, atr)
    out["atr_release_96"] = _safe_div(
        atr, atr.shift(1).rolling(96, min_periods=48).mean()
    )
    signed_ma_side = out["ma_shift_candle_side"].astype(float)
    signed_native = out["native_candle_side"].astype(float)
    out["ma_side_mean_12"] = signed_ma_side.rolling(12, min_periods=12).mean()
    out["ma_side_mean_48"] = signed_ma_side.rolling(48, min_periods=36).mean()
    out["native_side_mean_12"] = signed_native.rolling(12, min_periods=12).mean()
    return out.replace([np.inf, -np.inf], np.nan)


def attach_causal_features(candidates: pd.DataFrame, frame: pd.DataFrame) -> pd.DataFrame:
    """Attach features bounded by K2+1 open to each causal candidate.

    Source columns are K1/K2 indices and direction from ``candidates``;
    ``frame`` contributes OHLCV, ATR14, selected SMA(HL2), oscillator,
    structure, and rolling values through K2. The only K2+1 value used is its
    open, which is the frozen entry decision price. No K2+1 high/low/close or
    later row is read.
    """

    if candidates.empty:
        return candidates.copy()
    context = add_context_columns(frame)
    out = candidates.copy()
    k1 = out["k1_i"].astype(int).to_numpy()
    k2 = out["k2_i"].astype(int).to_numpy()
    direction = out["direction"].astype(float).to_numpy()

    def take(column: str, index: np.ndarray) -> np.ndarray:
        return context[column].to_numpy(dtype=float)[index]

    atr = take("atr", k2)
    close_k1 = take("close", k1)
    close_k2 = take("close", k2)
    ma_k2 = take("sma40_hl2", k2)
    out["reference_ma_slope_4_dir_atr"] = direction * take(
        "reference_ma_slope_4_atr", k2
    )
    out["reference_ma_slope_12_dir_atr"] = direction * take(
        "reference_ma_slope_12_atr", k2
    )
    out["reference_ma_accel_4_dir_atr"] = direction * take(
        "reference_ma_accel_4_atr", k2
    )
    out["k2_extension_dir_atr"] = direction * (close_k2 - ma_k2) / atr
    out["momentum_12_dir_atr"] = direction * take("momentum_12_atr", k2)
    out["momentum_48_dir_atr"] = direction * take("momentum_48_atr", k2)
    out["approach_pullback_3_dir_atr"] = direction * take(
        "approach_pullback_3_atr", k2
    )
    out["k1_to_k2_retrace_dir_atr"] = direction * (close_k1 - close_k2) / atr
    out["k1_volume_ratio_20"] = take("volume_ratio_20", k1)
    out["k2_volume_ratio_20"] = take("volume_ratio_20", k2)
    out["k2_atr_release_24"] = take("atr_release_24", k2)
    out["k2_atr_release_96"] = take("atr_release_96", k2)
    out["ma_shift_osc_dir"] = direction * take("ma_shift_osc", k2)
    out["ma_shift_osc_delta_dir"] = direction * take("ma_shift_osc_delta", k2)
    out["k2_range_atr"] = take("range_atr", k2)
    out["prior_ma_side_share_12_dir"] = (
        1.0 + direction * take("ma_side_mean_12", k2)
    ) / 2.0
    out["prior_ma_side_share_48_dir"] = (
        1.0 + direction * take("ma_side_mean_48", k2)
    ) / 2.0
    out["prior_native_colour_share_12_dir"] = (
        1.0 + direction * take("native_side_mean_12", k2)
    ) / 2.0
    entry_open = take("open", k2 + 1)
    out["entry_gap_dir_atr"] = direction * (entry_open - close_k2) / atr
    out["structure_state_dir"] = direction * take("market_break_state", k2)
    times = context["open_time"].iloc[k2].reset_index(drop=True)
    out["utc_hour"] = times.dt.hour.to_numpy(dtype=int)
    out["utc_weekday"] = times.dt.weekday.to_numpy(dtype=int)

    highs = context["high"].to_numpy(dtype=float)
    lows = context["low"].to_numpy(dtype=float)
    peak_extension: list[float] = []
    retrace_from_peak: list[float] = []
    for first, last, side, anchor, scale, terminal in zip(
        k1, k2, direction, close_k1, atr, close_k2
    ):
        if not np.isfinite(scale) or scale <= 0.0:
            peak_extension.append(np.nan)
            retrace_from_peak.append(np.nan)
            continue
        if side > 0:
            peak = (float(np.max(highs[first : last + 1])) - anchor) / scale
        else:
            peak = (anchor - float(np.min(lows[first : last + 1]))) / scale
        terminal_extension = side * (terminal - anchor) / scale
        peak_extension.append(peak)
        retrace_from_peak.append(peak - terminal_extension)
    out["path_peak_extension_dir_atr"] = peak_extension
    out["path_retrace_from_peak_atr"] = retrace_from_peak
    return out.replace([np.inf, -np.inf], np.nan)


def half_label(values: pd.Series) -> pd.Series:
    stamps = pd.to_datetime(values, utc=True)
    return stamps.map(lambda stamp: f"{stamp.year}H{1 if stamp.month <= 6 else 2}")


def build_masks(
    events: pd.DataFrame, config: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, np.ndarray]]:
    """Freeze continuous thresholds from H1 feature values and build all masks."""

    labels = half_label(events["entry_time"])
    h1 = labels.eq("2023H1")
    definitions: list[dict[str, Any]] = []
    masks: dict[str, np.ndarray] = {}
    operations: list[tuple[str, float, Callable[[pd.Series, float], pd.Series]]] = [
        ("le_q25", 0.25, lambda values, threshold: values.le(threshold)),
        ("le_q50", 0.50, lambda values, threshold: values.le(threshold)),
        ("ge_q50", 0.50, lambda values, threshold: values.ge(threshold)),
        ("ge_q75", 0.75, lambda values, threshold: values.ge(threshold)),
    ]
    expected = list(config["continuous_features"])
    missing = sorted(set(expected) - set(events.columns))
    if missing:
        raise RuntimeError(f"registered causal features missing from ledger: {missing}")
    for feature in expected:
        values = pd.to_numeric(events[feature], errors="coerce")
        source = values[h1 & values.notna()]
        if source.empty:
            continue
        for suffix, quantile, comparator in operations:
            threshold = float(source.quantile(quantile, interpolation="linear"))
            arm_id = f"{feature}__{suffix}"
            mask = comparator(values, threshold) & values.notna()
            definitions.append(
                {
                    "arm_id": arm_id,
                    "feature": feature,
                    "operator": "<=" if suffix.startswith("le") else ">=",
                    "quantile": quantile,
                    "threshold": threshold,
                    "kind": "continuous",
                }
            )
            masks[arm_id] = mask.to_numpy(dtype=bool)

    binary: dict[str, pd.Series] = {
        "long_only": events["direction"].eq(1),
        "short_only": events["direction"].eq(-1),
        "structure_aligned": events["structure_state_dir"].gt(0.0),
        "structure_not_opposite": events["structure_state_dir"].ge(0.0),
        "weekend": events["utc_weekday"].ge(5),
        "weekday": events["utc_weekday"].lt(5),
        "utc_00_06": events["utc_hour"].between(0, 5),
        "utc_06_12": events["utc_hour"].between(6, 11),
        "utc_12_18": events["utc_hour"].between(12, 17),
        "utc_18_24": events["utc_hour"].between(18, 23),
    }
    for arm_id in config["diagnostic_design"]["binary_arms"]:
        mask = binary[str(arm_id)].fillna(False)
        definitions.append(
            {
                "arm_id": str(arm_id),
                "feature": str(arm_id),
                "operator": "is_true",
                "quantile": None,
                "threshold": None,
                "kind": "binary",
            }
        )
        masks[str(arm_id)] = mask.to_numpy(dtype=bool)
    return definitions, masks


def evaluate_masks(
    events: pd.DataFrame,
    definitions: list[dict[str, Any]],
    masks: dict[str, np.ndarray],
    config: dict[str, Any],
    bar: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    labels = half_label(events["entry_time"]).to_numpy(dtype=str)
    y = events["net_return"].to_numpy(dtype=float) * 1e4
    baseline = {
        fold: float(y[labels == fold].mean()) for fold in ("2023H1", "2023H2")
    }
    fixed = config["timeframe_fixed"][bar]
    minimum_total = int(fixed["minimum_arm_events_total"])
    minimum_fold = int(fixed["minimum_arm_events_per_discovery_fold"])
    rows: list[dict[str, Any]] = []
    eligible_masks: list[np.ndarray] = []
    for definition in definitions:
        mask = masks[str(definition["arm_id"])]
        half_counts = {fold: int((mask & (labels == fold)).sum()) for fold in baseline}
        means = {
            fold: float(y[mask & (labels == fold)].mean())
            if half_counts[fold]
            else np.nan
            for fold in baseline
        }
        improvements = {
            fold: means[fold] - baseline[fold] if np.isfinite(means[fold]) else np.nan
            for fold in baseline
        }
        eligible = bool(
            int(mask.sum()) >= minimum_total
            and min(half_counts.values()) >= minimum_fold
            and all(np.isfinite(list(improvements.values())))
        )
        stable = float(min(improvements.values())) if eligible else np.nan
        combined_net = float(y[mask].mean()) if mask.any() else np.nan
        rows.append(
            {
                "bar": bar,
                **definition,
                "events": int(mask.sum()),
                "retention": float(mask.mean()),
                "2023H1_events": half_counts["2023H1"],
                "2023H2_events": half_counts["2023H2"],
                "2023H1_net_bp": means["2023H1"],
                "2023H2_net_bp": means["2023H2"],
                "2023H1_improvement_bp": improvements["2023H1"],
                "2023H2_improvement_bp": improvements["2023H2"],
                "stable_improvement_bp": stable,
                "combined_net_bp": combined_net,
                "eligible": eligible,
            }
        )
        if eligible:
            eligible_masks.append(mask)

    table = pd.DataFrame(rows)
    null = familywise_permutation_p(
        y,
        labels,
        eligible_masks,
        int(config["permutation"]["resamples"]),
        int(config["permutation"]["seed"]) + (15 if bar == "15m" else 5),
    )
    return table, {
        "baseline_half_net_bp": baseline,
        "eligible_arms": len(eligible_masks),
        **null,
    }


def familywise_permutation_p(
    y: np.ndarray,
    labels: np.ndarray,
    masks: list[np.ndarray],
    resamples: int,
    seed: int,
) -> dict[str, Any]:
    """Return max-statistic p for stable improvement across every eligible arm."""

    if not masks:
        return {
            "observed_max_stable_improvement_bp": np.nan,
            "familywise_permutation_p": np.nan,
            "permutation_resamples": resamples,
        }
    halves = [labels == "2023H1", labels == "2023H2"]
    matrices: list[np.ndarray] = []
    counts: list[np.ndarray] = []
    observed_improvements: list[np.ndarray] = []
    for half in halves:
        matrix = np.vstack([mask[half] for mask in masks]).astype(float)
        count = matrix.sum(axis=1)
        matrices.append(matrix)
        counts.append(count)
        # NumPy 1.26 linked to macOS Accelerate emits spurious floating-point
        # warnings for these small, finite boolean-mask matmuls. Explicit
        # einsum avoids that backend path and keeps the arithmetic auditable.
        observed_sum = np.einsum("ij,j->i", matrix, y[half], optimize=False)
        observed_improvements.append(observed_sum / count - float(y[half].mean()))
    observed_scores = np.minimum(observed_improvements[0], observed_improvements[1])
    observed_max = float(np.max(observed_scores))
    rng = np.random.default_rng(seed)
    exceed = 0
    batch_size = 250
    done = 0
    while done < resamples:
        batch = min(batch_size, resamples - done)
        perm_improvements: list[np.ndarray] = []
        for half, matrix, count in zip(halves, matrices, counts):
            values = y[half]
            shuffled = np.column_stack([rng.permutation(values) for _ in range(batch)])
            perm_sum = np.einsum("ij,jk->ik", matrix, shuffled, optimize=False)
            perm_improvements.append(
                perm_sum / count[:, None] - float(values.mean())
            )
        maxima = np.minimum(perm_improvements[0], perm_improvements[1]).max(axis=0)
        exceed += int(np.count_nonzero(maxima >= observed_max - 1e-12))
        done += batch
    return {
        "observed_max_stable_improvement_bp": observed_max,
        "familywise_permutation_p": float((exceed + 1) / (resamples + 1)),
        "permutation_resamples": resamples,
    }


def nominate_arm(
    table: pd.DataFrame, config: dict[str, Any]
) -> tuple[dict[str, Any] | None, str]:
    passing = table.loc[
        table["eligible"].astype(bool)
        & table["stable_improvement_bp"].ge(5.0)
        & table["combined_net_bp"].ge(-10.0)
    ].copy()
    if passing.empty:
        return None, "no_arm_passed_registered_nomination_rule"
    passing = passing.sort_values(
        ["stable_improvement_bp", "combined_net_bp", "events", "arm_id"],
        ascending=[False, False, False, True],
        kind="mergesort",
    )
    return json_value(passing.iloc[0].to_dict()), "one_arm_nominated_for_separate_exact_test"


def make_plot(table: pd.DataFrame, bar: str, output: Path) -> None:
    eligible = table.loc[table["eligible"].astype(bool)].nlargest(
        15, "stable_improvement_bp"
    )
    fig, ax = plt.subplots(figsize=(10.8, 6.2))
    if eligible.empty:
        ax.text(0.5, 0.5, "No sample-eligible arms", ha="center", va="center")
        ax.set_axis_off()
    else:
        ordered = eligible.sort_values("stable_improvement_bp")
        colors = np.where(ordered["combined_net_bp"].ge(0.0), TEAL, ORANGE)
        ax.barh(ordered["arm_id"], ordered["stable_improvement_bp"], color=colors)
        ax.axvline(5.0, color=RED, linestyle="--", linewidth=1.0, label="nomination floor")
        ax.axvline(0.0, color=INK, linewidth=0.8)
        ax.set_xlabel("min(H1 improvement, H2 improvement), bp/trade")
        ax.set_title(f"BTCUSDT.P {bar}: stable 2023 causal segments")
        ax.legend(frameon=False)
        ax.grid(axis="x", alpha=0.2)
    fig.tight_layout()
    fig.savefig(output, dpi=170)
    plt.close(fig)


def discovery_phase(config: dict[str, Any]) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    start = utc(config["window"]["discovery_start_inclusive"])
    end = utc(config["window"]["discovery_end_exclusive"])
    receipt: dict[str, Any] = {
        "phase": "causal_failure_map_complete_future_periods_unopened",
        "config_sha256": sha256_file(CONFIG_PATH),
        "script_sha256": sha256_file(SCRIPT_PATH),
        "outcome_window": [start, end],
        "confirmation_rows_read": 0,
        "audit_rows_read": 0,
        "holdout_rows_read": 0,
        "timeframes": {},
    }
    source_rows: list[dict[str, Any]] = []
    all_tables: list[pd.DataFrame] = []
    for bar in ("15m", "5m"):
        print(f"[{bar}] loading safe source and 2023 candidates", flush=True)
        frame, universe, candidates, params, quality = load_candidates(
            config, bar, start, end
        )
        enriched = attach_causal_features(candidates, frame)
        decisions, events = run_arm(enriched, frame, config, bar, params, 0.0)
        if events.empty:
            raise RuntimeError(f"{bar}: frozen baseline produced no discovery events")
        definitions, masks = build_masks(events, config)
        table, null = evaluate_masks(events, definitions, masks, config, bar)
        nomination, reason = nominate_arm(table, config)
        write_csv(events, RESULTS / f"discovery_{bar}_baseline_trades.csv.gz")
        write_csv(decisions, RESULTS / f"discovery_{bar}_baseline_decisions.csv.gz")
        write_csv(table, RESULTS / f"discovery_{bar}_arm_metrics.csv")
        write_csv(
            fold_table(events, list(config["window"]["discovery_folds"])),
            RESULTS / f"discovery_{bar}_baseline_folds.csv",
        )
        make_plot(table, bar, RESULTS / f"discovery_{bar}_stable_segments.png")
        best = (
            json_value(table.loc[table["eligible"].astype(bool)].nlargest(1, "stable_improvement_bp").iloc[0].to_dict())
            if table["eligible"].astype(bool).any()
            else None
        )
        receipt["timeframes"][bar] = {
            "source": {**quality, "holdout_rows_read": 0},
            "universe_pairs": len(universe),
            "period_candidates": len(candidates),
            "baseline_metrics": metric_row(events),
            "baseline_folds": fold_table(
                events, list(config["window"]["discovery_folds"])
            ).to_dict("records"),
            "tested_arms": len(table),
            "nomination_reason": reason,
            "nominated_arm": nomination,
            "best_observed_arm": best,
            "multiple_search_null": null,
            "exact_single_variable_test_required": nomination is not None,
        }
        source_rows.append({**quality, "bar": bar, "outcome_rows_used": len(events)})
        all_tables.append(table)
        print(
            f"[{bar}] baseline={events['net_return'].mean()*1e4:.2f}bp "
            f"best_stable={null['observed_max_stable_improvement_bp']:.2f}bp "
            f"FWER-p={null['familywise_permutation_p']:.4f} nomination={reason}",
            flush=True,
        )
    write_csv(pd.concat(all_tables, ignore_index=True), RESULTS / "discovery_arm_metrics.csv")
    write_csv(pd.DataFrame(source_rows), RESULTS / "source_receipt.csv")
    write_json(RESULTS / "selection_receipt.json", receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["discovery"], default="discovery")
    args = parser.parse_args()
    config = load_config()
    if args.phase == "discovery":
        discovery_phase(config)


if __name__ == "__main__":
    main()
