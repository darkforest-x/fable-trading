#!/usr/bin/env python3
"""Diagnose the registered BTCUSDT.P 15m two-stage K2 experiment.

All explanatory features are causal at the confirmation bar.  They use only
the registered trade ledger plus completed OHLCV-derived columns at or before
``confirmation_i``: SMA40(HL2), Pine ATR14, volume ratio, MA Shift oscillator,
market-break state, rope width/slope, and candle geometry.  Future bars are
used only by the frozen outcome resolver and target-R sensitivity labels.

The feature cuts and one-dimensional sensitivity grid are explicitly post-hoc
hypothesis-generation evidence.  They are never written back into the active
strategy, never open the audit window, and never read the repository holdout.
"""
from __future__ import annotations

import copy
import json
import math
import zipfile
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from scripts.optimize_btcusdtp_k1k2_intraday_preholdout import (
    add_control_metrics,
    build_matched_controls,
    fold_table,
    halfyear_label,
    period_events,
    profit_factor,
    robust_metrics,
    sha256_file,
    utc,
    write_csv,
    write_json,
)
from scripts.research_btcusdtp_k1k2_15m_two_stage_k2 import (
    BAR,
    CONFIG_PATH,
    EXPERIMENT,
    RESULTS,
    accept_k2_events,
    build_k2_event_candidates,
    load_config,
)
from scripts.optimize_btcusdtp_k1k2_intraday_preholdout import load_featured


FREQTRADE_RESULTS = RESULTS / "freqtrade"
STRATEGY_PATH = (
    EXPERIMENT / "freqtrade/user_data/strategies/FableTwoStageK2.py"
)
LOOKAHEAD_PATH = FREQTRADE_RESULTS / "lookahead_analysis.csv"
EPSILON = 1e-10
FOLDS = ["2023H1", "2023H2", "2024H1", "2024H2"]
BLUE = "#2563EB"
ORANGE = "#D97706"
INK = "#172033"
GRID = "#D9DEE8"
OPEN_BLUE = "#BFDBFE"


def _read_freqtrade_result() -> tuple[Path, dict[str, Any], pd.DataFrame]:
    pointer = json.loads((FREQTRADE_RESULTS / ".last_result.json").read_text())
    archive = FREQTRADE_RESULTS / str(pointer["latest_backtest"])
    with zipfile.ZipFile(archive) as handle:
        names = [
            name
            for name in handle.namelist()
            if name.endswith(".json") and not name.endswith("_config.json")
        ]
        if len(names) != 1:
            raise RuntimeError(f"unexpected Freqtrade result members: {names}")
        result = json.loads(handle.read(names[0]))["strategy"]["FableTwoStageK2"]
    trades = pd.DataFrame(result["trades"])
    trades["open_date"] = pd.to_datetime(trades["open_date"], utc=True)
    trades["direction"] = np.where(trades["is_short"].astype(bool), -1, 1)
    return archive, result, trades


def _summary_row(label: str, values: pd.Series, *, flat_epsilon: float) -> dict[str, Any]:
    array = values.astype(float)
    positive = array > flat_epsilon
    negative = array < -flat_epsilon
    return {
        "engine": label,
        "trades": int(len(array)),
        "mean_net_bp": float(array.mean() * 1e4),
        "median_net_bp": float(array.median() * 1e4),
        "directional_win_rate": float(positive.mean()),
        "flat_rate": float((~positive & ~negative).mean()),
        "profit_factor": float(profit_factor(array)),
    }


def _freqtrade_parity(
    native: pd.DataFrame, freqtrade: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, Any]]:
    native = native.copy()
    native["entry_time"] = pd.to_datetime(native["entry_time"], utc=True)
    fields = freqtrade["enter_tag"].str.split(",", expand=True)
    freqtrade = freqtrade.assign(
        tagged_stop=pd.to_numeric(fields[1]),
        tagged_atr=pd.to_numeric(fields[2]),
        tagged_delay=pd.to_numeric(fields[5]),
    )
    joined = native.merge(
        freqtrade,
        left_on=["entry_time", "direction"],
        right_on=["open_date", "direction"],
        how="outer",
        indicator=True,
        suffixes=("_native", "_freqtrade"),
    )
    if not joined["_merge"].eq("both").all():
        raise RuntimeError("Freqtrade and native entry sets do not match exactly")
    parity = pd.DataFrame(
        {
            "entry_time": joined["entry_time"],
            "direction": joined["direction"],
            "native_entry": joined["entry_price"],
            "freqtrade_entry": joined["open_rate"],
            "native_stop": joined["stop_price"],
            "freqtrade_tagged_stop": joined["tagged_stop"],
            "native_delay": joined["confirmation_delay_bars"],
            "freqtrade_tagged_delay": joined["tagged_delay"],
            "native_outcome": joined["outcome"],
            "freqtrade_exit_reason": joined["exit_reason"],
            "native_net_return": joined["net_return"],
            "freqtrade_net_return": joined["profit_ratio"],
        }
    )
    summary = {
        "native_entries": int(len(native)),
        "freqtrade_entries": int(len(freqtrade)),
        "exact_entry_keys": int(len(joined)),
        "entry_price_max_abs_error": float(
            np.abs(parity["native_entry"] - parity["freqtrade_entry"]).max()
        ),
        "stop_price_max_abs_error": float(
            np.abs(parity["native_stop"] - parity["freqtrade_tagged_stop"]).max()
        ),
        "delay_mismatches": int(
            parity["native_delay"].ne(parity["freqtrade_tagged_delay"]).sum()
        ),
        "mean_net_bp_difference_freqtrade_minus_native": float(
            (parity["freqtrade_net_return"] - parity["native_net_return"]).mean()
            * 1e4
        ),
    }
    return parity, summary


def _add_causal_features(trades: pd.DataFrame, frame: pd.DataFrame) -> pd.DataFrame:
    output = trades.copy()
    confirm = output["confirmation_i"].astype(int).to_numpy()
    direction = output["direction"].astype(int).to_numpy()
    atr = frame.loc[confirm, "atr"].to_numpy(dtype=float)
    sma = frame.loc[confirm, "sma40_hl2"].to_numpy(dtype=float)
    close = frame.loc[confirm, "close"].to_numpy(dtype=float)
    for lag in (3, 6, 12):
        prior_sma = frame.loc[confirm - lag, "sma40_hl2"].to_numpy(dtype=float)
        output[f"directional_sma_slope_{lag}_atr"] = (
            direction * (sma - prior_sma) / atr
        )
    output["confirmation_close_side_atr"] = direction * (close - sma) / atr
    for target, source in {
        "volume_ratio_20": "volume_ratio_20",
        "volume_z_96": "volume_z_96",
        "atr_release_24": "atr_release_24",
        "rope_width_atr": "rope_width_atr",
        "directional_rope_slope_4_atr": "rope_slope_atr_4",
        "directional_ma_shift_osc": "ma_shift_osc",
        "directional_ma_shift_osc_delta": "ma_shift_osc_delta",
        "directional_market_break_state": "market_break_state",
    }.items():
        values = frame.loc[confirm, source].to_numpy(dtype=float)
        if target.startswith("directional_"):
            values = direction * values
        output[target] = values
    return output


def _group_metrics(
    subset: pd.DataFrame,
    matched_pairs: pd.DataFrame,
    *,
    feature: str,
    bucket: str,
) -> dict[str, Any]:
    net = subset["net_return"].astype(float)
    positive = net > EPSILON
    negative = net < -EPSILON
    outcomes = subset["outcome"].astype(str)
    pairs = matched_pairs.loc[
        matched_pairs["setup_id"].isin(subset["setup_id"])
        & matched_pairs["match_status"].eq("matched_exact")
    ]
    row: dict[str, Any] = {
        "analysis_status": "posthoc_hypothesis_only",
        "feature": feature,
        "bucket": bucket,
        "events": int(len(subset)),
        "mean_net_bp": float(net.mean() * 1e4),
        "median_net_bp": float(net.median() * 1e4),
        "directional_win_rate": float(positive.mean()),
        "flat_rate": float((~positive & ~negative).mean()),
        "profit_factor": float(profit_factor(net)),
        "stop_rate": float(outcomes.str.startswith("sl").mean()),
        "target_rate": float(outcomes.eq("tp").mean()),
        "horizon_hit_4r_rate": float(subset["horizon_hit_4r"].mean()),
        "horizon_hit_5r_rate": float(subset["horizon_hit_5r"].mean()),
        "horizon_hit_6r_rate": float(subset["horizon_hit_6r"].mean()),
        "matched_events": int(len(pairs)),
        "matched_control_excess_bp": float(
            pairs["paired_excess_return"].mean() * 1e4
        )
        if len(pairs)
        else math.nan,
    }
    labels = subset["entry_time"].map(halfyear_label)
    for fold in FOLDS:
        fold_values = net.loc[labels.eq(fold)]
        row[f"{fold}_events"] = int(len(fold_values))
        row[f"{fold}_mean_net_bp"] = (
            float(fold_values.mean() * 1e4) if len(fold_values) else math.nan
        )
    return row


def feature_diagnostics(
    trades: pd.DataFrame, matched_pairs: pd.DataFrame, frame: pd.DataFrame
) -> pd.DataFrame:
    values = _add_causal_features(trades, frame)
    rows: list[dict[str, Any]] = []

    categorical = {
        "direction": values["direction"].map({1: "long", -1: "short"}),
        "confirmation_delay_bars": values["confirmation_delay_bars"].astype(str),
        "k1_to_touch_gap_bars": values["gap_bars"].astype(str),
    }
    for feature, groups in categorical.items():
        for bucket in sorted(groups.dropna().unique()):
            rows.append(
                _group_metrics(
                    values.loc[groups.eq(bucket)],
                    matched_pairs,
                    feature=feature,
                    bucket=str(bucket),
                )
            )

    binnings: dict[str, list[float]] = {
        "stop_distance_atr": [0.15, 0.50, 0.75, 1.00, 1.50, 2.50],
        "touch_depth_atr": [0.00, 0.10, 0.25, 0.50, 1.00, 1.50],
        "k1_body_ratio": [0.65, 0.70, 0.80, 0.90, 1.01],
        "k1_sma40_cross_depth_atr": [-0.05, 0.00, 0.10, 0.25, 0.50, 1.00, 10.0],
        "directional_sma_slope_6_atr": [-10.0, -0.10, 0.00, 0.10, 0.25, 0.50, 10.0],
        "volume_ratio_20": [0.00, 0.50, 0.75, 1.00, 1.50, 2.00, 100.0],
        "directional_ma_shift_osc": [-100.0, -0.50, -0.20, 0.00, 0.20, 0.50, 100.0],
        "directional_market_break_state": [-2.0, -0.5, 0.5, 2.0],
    }
    for feature, bins in binnings.items():
        groups = pd.cut(values[feature], bins, right=False, include_lowest=True)
        for bucket in groups.dropna().unique().sort_values():
            rows.append(
                _group_metrics(
                    values.loc[groups.eq(bucket)],
                    matched_pairs,
                    feature=feature,
                    bucket=str(bucket),
                )
            )
    return pd.DataFrame(rows)


def failure_modes(trades: pd.DataFrame) -> pd.DataFrame:
    outcomes = trades["outcome"].astype(str)
    stopped = outcomes.str.startswith("sl")
    mode = pd.Series("other", index=trades.index, dtype=str)
    mode.loc[stopped & trades["mfe_r"].lt(0.5)] = "stop_before_0.5R"
    mode.loc[
        stopped & trades["mfe_r"].ge(0.5) & trades["mfe_r"].lt(1.5)
    ] = "stop_after_0.5R_before_1.5R"
    mode.loc[stopped & trades["mfe_r"].ge(1.5)] = "stop_after_1.5R_wick_no_close_arm"
    mode.loc[outcomes.str.startswith("protected_stop")] = "protected_flat"
    mode.loc[outcomes.eq("tp")] = "target_3R"
    mode.loc[outcomes.eq("timeout")] = "timeout_48_bars"
    tagged = trades.assign(failure_mode=mode)
    rows = []
    for name, group in tagged.groupby("failure_mode", sort=False):
        rows.append(
            {
                "failure_mode": name,
                "events": int(len(group)),
                "share": float(len(group) / len(tagged)),
                "mean_net_bp": float(group["net_return"].mean() * 1e4),
                "contribution_to_all_trade_mean_bp": float(
                    group["net_return"].sum() / len(tagged) * 1e4
                ),
                "median_hold_bars": float(group["hold_bars"].median()),
                "mean_mfe_r_before_exit": float(group["mfe_r"].mean()),
                "mean_mae_r_before_exit": float(group["mae_r"].mean()),
            }
        )
    return pd.DataFrame(rows).sort_values(
        "contribution_to_all_trade_mean_bp", kind="mergesort"
    )


def target_sensitivity(
    config: dict[str, Any],
    frame: pd.DataFrame,
    decisions: pd.DataFrame,
    all_signal_indices: set[int],
) -> pd.DataFrame:
    start = utc(config["window"]["development_start_inclusive"])
    end = utc(config["window"]["development_end_exclusive"])
    rows: list[dict[str, Any]] = []
    for target_r in (2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0):
        variant = copy.deepcopy(config)
        variant["execution_frozen"]["target_r"] = target_r
        events = period_events(decisions, frame, variant, BAR, start, end)
        _, pairs = build_matched_controls(
            events,
            frame,
            variant,
            BAR,
            start,
            end,
            all_signal_indices,
        )
        metrics = robust_metrics(events, FOLDS, 80, 12)
        metrics = add_control_metrics(metrics, pairs)
        folds = fold_table(events, FOLDS)
        rows.append(
            {
                "analysis_status": "posthoc_one_dimensional_sensitivity_not_selection",
                "target_r": target_r,
                **metrics,
                **{
                    f"{row.fold}_mean_net_bp": float(row.mean_net_bp)
                    for row in folds.itertuples(index=False)
                },
            }
        )
    return pd.DataFrame(rows)


def rule_sensitivity(
    config: dict[str, Any], frame: pd.DataFrame
) -> pd.DataFrame:
    """Replay post-hoc one-variable hypotheses with common control exclusion.

    Every row changes exactly one named coordinate from the registered
    candidate.  This grid was chosen after the main result was visible, so it
    is diagnostic only even when a row looks favourable.
    """

    grid: dict[str, tuple[float, ...]] = {
        "k1_min_body_ratio": (0.65, 0.70, 0.75, 0.80),
        "k2_touch_depth_atr_min": (0.00, 0.10, 0.25, 0.50),
        "next_open_risk_atr_min": (0.15, 0.50, 0.75, 1.00, 1.25),
        "gap_max_bars_k1_to_touch": (2.0, 3.0, 4.0, 5.0, 6.0, 8.0),
        "maximum_confirmation_delay_bars": (0.0, 1.0, 2.0),
        "profit_protection_trigger_close_r": (1.00, 1.25, 1.50, 2.00),
    }
    start = utc(config["window"]["development_start_inclusive"])
    end = utc(config["window"]["development_end_exclusive"])
    arms: list[tuple[str, float, dict[str, Any], pd.DataFrame]] = []
    excluded: set[int] = set()
    for family, values in grid.items():
        for value in values:
            variant = copy.deepcopy(config)
            if family in {"k1_min_body_ratio", "k2_touch_depth_atr_min"}:
                variant["signal_frozen"][family] = value
            elif family == "gap_max_bars_k1_to_touch":
                variant["signal_frozen"][family] = int(value)
            elif family in {
                "next_open_risk_atr_min",
                "profit_protection_trigger_close_r",
            }:
                variant["execution_frozen"][family] = value
            elif family == "maximum_confirmation_delay_bars":
                variant["factor"]["candidate"][family] = int(value)
            delay = int(
                variant["factor"]["candidate"][
                    "maximum_confirmation_delay_bars"
                ]
            )
            candidates = build_k2_event_candidates(
                frame, variant, maximum_confirmation_delay_bars=delay
            )
            excluded.update(candidates["k2_i"].astype(int))
            arms.append((family, value, variant, candidates))

    rows: list[dict[str, Any]] = []
    for family, value, variant, candidates in arms:
        decisions = accept_k2_events(candidates, frame, variant)
        events = period_events(decisions, frame, variant, BAR, start, end)
        _, pairs = build_matched_controls(
            events, frame, variant, BAR, start, end, excluded
        )
        metrics = add_control_metrics(
            robust_metrics(events, FOLDS, 80, 12), pairs
        )
        folds = fold_table(events, FOLDS)
        rows.append(
            {
                "analysis_status": "posthoc_single_coordinate_hypothesis_only",
                "family": family,
                "value": value,
                **metrics,
                **{
                    f"{row.fold}_mean_net_bp": float(row.mean_net_bp)
                    for row in folds.itertuples(index=False)
                },
                "passes_original_full_gate": bool(
                    metrics["eligible"]
                    and float(metrics["mean_net_bp"]) > 0.0
                    and float(metrics["robust_score_bp"]) > 0.0
                    and float(metrics["worst_fold_net_bp"]) > -5.0
                    and folds["mean_net_bp"].gt(0.0).all()
                    and float(metrics["matched_control_excess_bp"]) > 0.0
                    and float(metrics["paired_signflip_p_one_sided"]) < 0.01
                ),
            }
        )
    return pd.DataFrame(rows)


def _style_axis(axis: plt.Axes) -> None:
    axis.spines[["top", "right"]].set_visible(False)
    axis.spines[["left", "bottom"]].set_color(INK)
    axis.tick_params(colors=INK, labelsize=9)
    axis.grid(axis="y", color=GRID, linewidth=0.7, alpha=0.75)
    axis.set_axisbelow(True)


def render_charts(
    metrics: pd.DataFrame,
    failures: pd.DataFrame,
    targets: pd.DataFrame,
) -> None:
    plt.rcParams.update({"font.family": "DejaVu Sans", "axes.titleweight": "bold"})

    folds = pd.read_csv(RESULTS / "development_baseline_same_bar_folds.csv")
    candidate_folds = pd.read_csv(RESULTS / "development_candidate_two_stage_folds.csv")
    x = np.arange(len(FOLDS))
    fig, axis = plt.subplots(figsize=(9.5, 5.2), constrained_layout=True)
    axis.bar(
        x - 0.18,
        folds["mean_net_bp"],
        width=0.36,
        color=OPEN_BLUE,
        edgecolor=BLUE,
        linewidth=1.1,
        label="Same-bar K2",
    )
    axis.bar(
        x + 0.18,
        candidate_folds["mean_net_bp"],
        width=0.36,
        color=ORANGE,
        edgecolor=INK,
        linewidth=0.8,
        label="Touch + confirm",
    )
    axis.axhline(0, color=INK, linewidth=1.0)
    axis.set_xticks(x, FOLDS)
    axis.set_ylabel("Mean net return (bp / trade)")
    axis.set_title(
        "Half-year net return by K2 representation",
        loc="left",
        fontsize=16,
        pad=28,
    )
    axis.text(
        0,
        1.012,
        "BTCUSDT.P 15m · 2023–2024 development · 20 bp round-trip cost",
        transform=axis.transAxes,
        color="#596273",
        fontsize=9,
    )
    axis.legend(frameon=False, ncol=2, loc="lower right")
    _style_axis(axis)
    fig.savefig(RESULTS / "chart_halfyear_k2_comparison.png", dpi=180)
    plt.close(fig)

    chart = failures.sort_values("contribution_to_all_trade_mean_bp").copy()
    chart["display_mode"] = chart["failure_mode"].map(
        {
            "stop_before_0.5R": "Stop before 0.5R",
            "stop_after_0.5R_before_1.5R": "Stop after 0.5R, before 1.5R",
            "stop_after_1.5R_wick_no_close_arm": "Stop after ≥1.5R wick, no close arm",
            "timeout_48_bars": "48-bar timeout",
            "protected_flat": "Protected near-flat",
            "target_3R": "3R target",
        }
    )
    fig, axis = plt.subplots(figsize=(9.5, 5.4), constrained_layout=True)
    colors = [ORANGE if value < 0 else BLUE for value in chart["contribution_to_all_trade_mean_bp"]]
    bars = axis.barh(
        chart["display_mode"],
        chart["contribution_to_all_trade_mean_bp"],
        color=colors,
        edgecolor=INK,
        linewidth=0.7,
    )
    axis.axvline(0, color=INK, linewidth=1.0)
    axis.bar_label(bars, fmt="%+.1f", padding=4, fontsize=9)
    axis.set_xlabel("Contribution to overall mean (bp / trade)")
    axis.set_title(
        "Return contribution by resolved trade path",
        loc="left",
        fontsize=16,
        pad=28,
    )
    axis.text(
        0,
        1.012,
        "100 candidate trades · contribution bars sum to −16.09 bp/trade",
        transform=axis.transAxes,
        color="#596273",
        fontsize=9,
    )
    axis.set_xlim(min(-18.5, chart["contribution_to_all_trade_mean_bp"].min() - 1), 20.5)
    _style_axis(axis)
    fig.savefig(RESULTS / "chart_failure_mode_contribution.png", dpi=180)
    plt.close(fig)

    fig, axis = plt.subplots(figsize=(9.5, 5.2), constrained_layout=True)
    for fold in FOLDS:
        axis.plot(
            targets["target_r"],
            targets[f"{fold}_mean_net_bp"],
            color="#9AA3B2",
            linewidth=0.9,
            marker="o",
            markersize=3,
            alpha=0.65,
        )
    axis.plot(
        targets["target_r"],
        targets["mean_net_bp"],
        color=BLUE,
        linewidth=2.2,
        marker="o",
        markersize=5,
        label="All development trades",
    )
    axis.axhline(0, color=INK, linewidth=1.0)
    axis.axvline(3.0, color=ORANGE, linewidth=1.0, linestyle="--", label="Frozen 3R")
    axis.set_xlabel("Take-profit multiple (R)")
    axis.set_ylabel("Mean net return (bp / trade)")
    axis.set_title(
        "Take-profit multiple sensitivity",
        loc="left",
        fontsize=16,
        pad=28,
    )
    axis.text(
        0,
        1.012,
        "Same 100 entries, stop/protection/cost unchanged; grey lines are half-years",
        transform=axis.transAxes,
        color="#596273",
        fontsize=9,
    )
    axis.legend(frameon=False, ncol=2, loc="lower right")
    _style_axis(axis)
    fig.savefig(RESULTS / "chart_target_r_sensitivity.png", dpi=180)
    plt.close(fig)


def main() -> None:
    config = load_config()
    frame, quality = load_featured(config, BAR)
    native = pd.read_csv(RESULTS / "development_candidate_two_stage_trades.csv.gz")
    native["entry_time"] = pd.to_datetime(native["entry_time"], utc=True)
    baseline_metrics = pd.read_csv(RESULTS / "development_metrics.csv")
    matched_pairs = pd.read_csv(
        RESULTS / "development_candidate_two_stage_matched_pairs.csv"
    )
    decisions = pd.read_csv(
        RESULTS / "development_candidate_two_stage_decisions.csv.gz"
    )
    decisions["entry_time"] = pd.to_datetime(decisions["entry_time"], utc=True)
    archive, freqtrade_result, freqtrade = _read_freqtrade_result()
    parity, parity_summary = _freqtrade_parity(native, freqtrade)
    lookahead = pd.read_csv(LOOKAHEAD_PATH)
    if len(lookahead) != 1 or bool(lookahead.loc[0, "has_bias"]):
        raise RuntimeError("Freqtrade lookahead gate did not pass")

    engine_metrics = pd.DataFrame(
        [
            _summary_row("native", native["net_return"], flat_epsilon=EPSILON),
            _summary_row(
                "freqtrade_2026.8",
                freqtrade["profit_ratio"],
                flat_epsilon=1e-5,
            ),
        ]
    )
    failures = failure_modes(native)
    diagnostics = feature_diagnostics(native, matched_pairs, frame)
    candidates = build_k2_event_candidates(
        frame,
        config,
        maximum_confirmation_delay_bars=int(
            config["factor"]["candidate"]["maximum_confirmation_delay_bars"]
        ),
    )
    targets = target_sensitivity(
        config,
        frame,
        decisions,
        set(candidates["k2_i"].astype(int)),
    )
    rules = rule_sensitivity(config, frame)

    write_csv(parity, RESULTS / "freqtrade_entry_parity.csv.gz")
    write_csv(engine_metrics, RESULTS / "development_engine_comparison.csv")
    write_csv(failures, RESULTS / "development_failure_modes.csv")
    write_csv(diagnostics, RESULTS / "development_feature_diagnostics.csv")
    write_csv(targets, RESULTS / "development_target_r_sensitivity.csv")
    write_csv(rules, RESULTS / "development_rule_sensitivity.csv")
    render_charts(baseline_metrics, failures, targets)

    current_tp = native.loc[native["outcome"].eq("tp")]
    delayed = native.loc[native["confirmation_delay_bars"].gt(0)]
    touch_deep = diagnostics.loc[
        diagnostics["feature"].eq("touch_depth_atr")
        & diagnostics["bucket"].isin(["[0.5, 1.0)", "[1.0, 1.5)"])
    ]
    receipt = {
        "status": "development_diagnosis_complete_audit_closed",
        "config_sha256": sha256_file(CONFIG_PATH),
        "analysis_script_sha256": sha256_file(Path(__file__)),
        "source": {**quality, "audit_outcomes_read": 0, "holdout_rows_read": 0},
        "main_result": baseline_metrics.to_dict("records"),
        "freqtrade": {
            "archive": str(archive.relative_to(EXPERIMENT)),
            "archive_sha256": sha256_file(archive),
            "strategy_sha256": sha256_file(STRATEGY_PATH),
            "version": "2026.8",
            "trades": int(len(freqtrade)),
            "mean_net_bp": float(freqtrade["profit_ratio"].mean() * 1e4),
            "profit_factor": float(freqtrade_result["profit_factor"]),
            "lookahead_has_bias": bool(lookahead.loc[0, "has_bias"]),
            "biased_entry_signals": int(lookahead.loc[0, "biased_entry_signals"]),
            "biased_exit_signals": int(lookahead.loc[0, "biased_exit_signals"]),
            **parity_summary,
        },
        "diagnosis": {
            "directional_wins": int(native["net_return"].gt(EPSILON).sum()),
            "economically_flat_protected_stops": int(
                native["outcome"].astype(str).str.startswith("protected_stop").sum()
            ),
            "stops": int(native["outcome"].astype(str).str.startswith("sl").sum()),
            "delayed_confirmation_events": int(len(delayed)),
            "delayed_confirmation_mean_net_bp": float(delayed["net_return"].mean() * 1e4),
            "current_3r_targets": int(len(current_tp)),
            "current_3r_targets_reaching_4r_within_horizon": int(
                current_tp["horizon_hit_4r"].sum()
            ),
            "current_3r_targets_reaching_5r_within_horizon": int(
                current_tp["horizon_hit_5r"].sum()
            ),
            "current_3r_targets_reaching_6r_within_horizon": int(
                current_tp["horizon_hit_6r"].sum()
            ),
            "deep_touch_bucket_events": int(touch_deep["events"].sum()),
            "posthoc_only": True,
        },
        "target_sensitivity_best_posthoc_mean": targets.sort_values(
            "mean_net_bp", ascending=False
        ).iloc[0][["target_r", "events", "mean_net_bp", "robust_score_bp", "worst_fold_net_bp"]].to_dict(),
        "rule_sensitivity": {
            "rows": int(len(rules)),
            "rows_passing_original_full_gate": int(
                rules["passes_original_full_gate"].sum()
            ),
            "best_posthoc_mean": rules.sort_values(
                "mean_net_bp", ascending=False
            ).iloc[0][
                [
                    "family",
                    "value",
                    "events",
                    "mean_net_bp",
                    "robust_score_bp",
                    "worst_fold_net_bp",
                    "matched_control_excess_bp",
                    "paired_signflip_p_one_sided",
                    "eligible",
                ]
            ].to_dict(),
        },
        "decision": {
            "candidate_passed": False,
            "audit_open_allowed": False,
            "tradingview_replacement_allowed": False,
            "holdout_rows_read": 0,
            "reason": "registered two-stage K2 failed mean, robustness, fold and significance gates",
        },
    }
    write_json(RESULTS / "development_diagnostic_receipt.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
