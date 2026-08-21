#!/usr/bin/env python3
"""Evaluate the fixed ETH 15m V14 literal-release correction.

This runner performs no profile or threshold search.  It freezes V13's
selected ``dense_l1`` setup and changes only release confirmation to require
``TR[t] / ATR[t-1] >= 1`` and a positive increase in side-signed close-to-rope
distance.  All features use rows through completed decision bar ``t`` and
entry remains ``open[t+1]``.  Data loading stops before 2026-03-01 and never
parses the repository holdout beginning 2026-05-04.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from scripts.backtest_pine_eth_15m_v12_preholdout import build_v12_feature_frame
from scripts.generate_pine_eth_15m_dense_release import write_pine
from scripts.research_pine_eth_15m import current_commit, load_config, load_research_frame
from scripts.research_pine_eth_15m_dense_start import (
    CONTROL_SENSITIVITY_SEEDS,
    HOLDOUT_START,
    LOCKED_PERIODS,
    PRIMARY_PERIOD_NAMES,
    SAFE_END,
    Variant,
    _concat,
    _json_safe,
    add_profile_columns,
    control_sensitivity,
    export_feature_rows,
    load_preregistration,
    run_variant_period,
)
from yoyo.layers.l2_judgment.pine_dense_release import (
    add_dense_release_v2_features,
    dense_release_v2_gate_mask,
)
from yoyo.layers.l2_judgment.pine_dense_start import add_six_ma_dense_start_features


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-dense-release-v2"
RESULTS = EXPERIMENT / "results"
CHARTS = RESULTS / "charts"
SUMMARY_OUTPUT = RESULTS / "dense_release_summary.json"
TRADES_OUTPUT = RESULTS / "dense_release_trades.csv"
CONTROLS_OUTPUT = RESULTS / "dense_release_controls.csv"
PAIRS_OUTPUT = RESULTS / "dense_release_pairs.csv"
EQUITY_OUTPUT = RESULTS / "dense_release_equity.csv"
FEATURE_ROWS_OUTPUT = RESULTS / "dense_release_feature_rows.csv"
CONTROL_SENSITIVITY_OUTPUT = RESULTS / "dense_release_control_sensitivity.csv"
PREREGISTRATION = EXPERIMENT / "preregistration.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def add_v14_columns(frame: pd.DataFrame, profile_id: str = "dense_l1") -> pd.DataFrame:
    """Add V14 full-state signal columns without changing the V13 setup."""

    _, profiles = load_preregistration()
    profile = next(profile for profile in profiles if profile.profile_id == profile_id)
    out = frame.copy()
    long_pass = dense_release_v2_gate_mask(out, profile, side="long")
    short_pass = dense_release_v2_gate_mask(out, profile, side="short")
    out["v14r_long_pass"] = long_pass
    out["v14r_short_pass"] = short_pass
    guarded = out["entry_allowed"].fillna(False).astype(bool)
    raw_long = out["v9_long"].fillna(False).astype(bool)
    raw_short = out["v9_short"].fillna(False).astype(bool)
    out["v14r_long"] = raw_long & (~guarded | long_pass)
    out["v14r_short"] = raw_short & (~guarded | short_pass)
    out["v14r_score"] = np.where(
        raw_long,
        out["dense_release_v2_score_long"],
        np.where(raw_short, out["dense_release_v2_score_short"], 0.0),
    )
    return out


def _attach_release_features(frame: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return trades
    out = trades.copy()
    source = frame.iloc[out["signal_i"].astype(int).to_numpy()]
    is_long = out["direction"].eq("long").to_numpy()
    out["true_range_atr_ratio"] = source[
        "dense_release_true_range_atr_ratio"
    ].to_numpy(dtype=float)
    for name, long_column, short_column in (
        (
            "prior_distance_atr",
            "dense_release_prior_distance_atr_long",
            "dense_release_prior_distance_atr_short",
        ),
        (
            "breakout_expansion_atr",
            "dense_release_breakout_expansion_atr_long",
            "dense_release_breakout_expansion_atr_short",
        ),
        (
            "dense_release_v2_score",
            "dense_release_v2_score_long",
            "dense_release_v2_score_short",
        ),
    ):
        out[name] = np.where(
            is_long,
            source[long_column].to_numpy(dtype=float),
            source[short_column].to_numpy(dtype=float),
        )
    return out


def _feature_export(frame: pd.DataFrame) -> pd.DataFrame:
    _, profiles = load_preregistration()
    selected = next(profile for profile in profiles if profile.profile_id == "dense_l1")
    rows = export_feature_rows(frame, profiles, selected)
    if rows.empty:
        return rows
    source = frame.iloc[rows["signal_i"].astype(int).to_numpy()]
    is_long = rows["side"].eq("long").to_numpy()
    rows["true_range_atr_ratio"] = source[
        "dense_release_true_range_atr_ratio"
    ].to_numpy(dtype=float)
    rows["prior_distance_atr"] = np.where(
        is_long,
        source["dense_release_prior_distance_atr_long"].to_numpy(dtype=float),
        source["dense_release_prior_distance_atr_short"].to_numpy(dtype=float),
    )
    rows["breakout_expansion_atr"] = np.where(
        is_long,
        source["dense_release_breakout_expansion_atr_long"].to_numpy(dtype=float),
        source["dense_release_breakout_expansion_atr_short"].to_numpy(dtype=float),
    )
    rows["v14r_gate_pass"] = np.where(
        is_long,
        source["v14r_long_pass"].to_numpy(dtype=bool),
        source["v14r_short_pass"].to_numpy(dtype=bool),
    )
    rows["outcome_label_included"] = False
    rows["training_eligible"] = False
    return rows


def make_charts(summary: pd.DataFrame, equity: pd.DataFrame) -> None:
    CHARTS.mkdir(parents=True, exist_ok=True)
    variants = ["v12f_ma6_w8_full_gate", "v13d_dense_l1", "v14r_dense_release"]
    labels = ["V12F", "V13 dense", "V14 literal release"]
    colors = ["#D97706", "#94A3B8", "#6B8E23"]
    periods = ["discovery_2023", "confirmation_2024", "final_preholdout_2025_202602"]
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.5))
    width = 0.24
    x = np.arange(len(periods))
    for axis, (column, title, scale) in zip(
        axes,
        [
            ("return_percent", "Return (%)", 1.0),
            ("stop_within_24h_rate", "Stopped within 24h (%)", 100.0),
            ("win_rate", "Net win rate (%)", 100.0),
        ],
    ):
        for offset, (variant, label, color) in enumerate(zip(variants, labels, colors)):
            values = []
            for period in periods:
                row = summary.loc[
                    summary["variant"].eq(variant) & summary["period"].eq(period)
                ]
                values.append(float(row.iloc[0][column]) * scale)
            axis.bar(x + (offset - 1) * width, values, width, label=label, color=color)
        axis.set_xticks(x, ["2023", "2024 exposed", "final pre-HO"], rotation=18)
        axis.set_title(title)
        axis.axhline(0.0, color="#334155", linewidth=0.7)
        axis.grid(axis="y", alpha=0.2)
    axes[-1].legend(fontsize=8)
    fig.suptitle("V14 release correction; all execution and barrier settings unchanged")
    fig.tight_layout()
    fig.savefig(CHARTS / "dense_release_mechanism_comparison.png", dpi=170)
    plt.close(fig)

    fig, axes = plt.subplots(1, 3, figsize=(14, 4.4))
    for axis, period in zip(axes, periods):
        subset = equity.loc[equity["period"].eq(period)]
        for variant, label, color in zip(variants, labels, colors):
            group = subset.loc[subset["variant"].eq(variant)].sort_values("open_time")
            if group.empty:
                continue
            axis.plot(
                pd.to_datetime(group["open_time"], utc=True),
                group["normalized_equity"],
                label=label,
                color=color,
                linewidth=1.2,
            )
        axis.set_title(period)
        axis.set_ylabel("Normalized equity")
        axis.grid(alpha=0.2)
        axis.tick_params(axis="x", rotation=25)
    axes[-1].legend(fontsize=8)
    fig.suptitle("ETH 15m marked equity; 1% risk, 20bp round trip")
    fig.tight_layout()
    fig.savefig(CHARTS / "dense_release_equity.png", dpi=170)
    plt.close(fig)


def main() -> None:
    prereg = json.loads(PREREGISTRATION.read_text(encoding="utf-8"))
    raw, quality = load_research_frame(load_config())
    times = pd.to_datetime(raw["open_time"], utc=True)
    if quality["holdout_rows_read"] != 0 or times.max() >= SAFE_END or SAFE_END >= HOLDOUT_START:
        raise RuntimeError("V14 holdout-safe time contract failed")
    _, profiles = load_preregistration()
    selected = next(profile for profile in profiles if profile.profile_id == "dense_l1")
    frame = build_v12_feature_frame(raw)
    frame = add_six_ma_dense_start_features(frame)
    frame = add_profile_columns(frame, profiles)
    frame = add_dense_release_v2_features(frame)
    frame = add_v14_columns(frame)
    pine_path, pine_manifest = write_pine()

    variants = (
        Variant(
            "v12f_ma6_w8_full_gate",
            "v12f_long",
            "v12f_short",
            "v9_score",
            "ma6_w8_long_pass",
            "ma6_w8_short_pass",
        ),
        Variant(
            "v13d_dense_l1",
            "v13d_dense_l1_long",
            "v13d_dense_l1_short",
            "v13d_dense_l1_score",
            "v13d_dense_l1_long_pass",
            "v13d_dense_l1_short_pass",
        ),
        Variant(
            "v14r_dense_release",
            "v14r_long",
            "v14r_short",
            "v14r_score",
            "v14r_long_pass",
            "v14r_short_pass",
        ),
    )
    rows: list[dict[str, Any]] = []
    trades_all: list[pd.DataFrame] = []
    equity_all: list[pd.DataFrame] = []
    controls_all: list[pd.DataFrame] = []
    pairs_all: list[pd.DataFrame] = []
    for variant_index, variant in enumerate(variants):
        for period_index, period in enumerate(LOCKED_PERIODS):
            trades, marked, controls, pairs, row = run_variant_period(
                frame,
                variant,
                period,
                seed=20_261_400 + variant_index * 100 + period_index,
                stage="fixed_post_review_analyst_exposed",
                with_controls=True,
            )
            trades = _attach_release_features(frame, trades)
            rows.append(row)
            if not trades.empty:
                trades_all.append(trades)
            if not marked.empty:
                equity_all.append(marked)
            if not controls.empty:
                controls_all.append(controls)
            if not pairs.empty:
                pairs_all.append(pairs)

    summary = pd.DataFrame(rows)
    trades_output = _concat(trades_all)
    equity_output = _concat(equity_all)
    controls_output = _concat(controls_all)
    pairs_output = _concat(pairs_all)
    features = _feature_export(frame)
    sensitivity = control_sensitivity(frame, trades_output, "v14r_dense_release")
    make_charts(summary, equity_output)

    key = summary.loc[summary["period"].isin(PRIMARY_PERIOD_NAMES)].copy()
    v13 = key.loc[key["variant"].eq("v13d_dense_l1")].set_index("period")
    v14 = key.loc[key["variant"].eq("v14r_dense_release")].set_index("period")
    comparison = []
    for period in sorted(PRIMARY_PERIOD_NAMES):
        comparison.append(
            {
                "period": period,
                "v13_return_percent": float(v13.loc[period, "return_percent"]),
                "v14_return_percent": float(v14.loc[period, "return_percent"]),
                "return_delta_percentage_points": float(
                    v14.loc[period, "return_percent"] - v13.loc[period, "return_percent"]
                ),
                "v13_early_stop_rate": float(v13.loc[period, "stop_within_24h_rate"]),
                "v14_early_stop_rate": float(v14.loc[period, "stop_within_24h_rate"]),
                "early_stop_delta_percentage_points": float(
                    (v14.loc[period, "stop_within_24h_rate"] - v13.loc[period, "stop_within_24h_rate"])
                    * 100.0
                ),
                "v14_positive_multi_day_trades": int(v14.loc[period, "positive_multi_day_trades"]),
            }
        )
    acceptance = {
        "early_stop_rate_lower_every_primary_period": bool(
            (v14["stop_within_24h_rate"] < v13["stop_within_24h_rate"]).all()
        ),
        "positive_multi_day_winner_every_primary_period": bool(
            v14["positive_multi_day_trades"].ge(1).all()
        ),
        "positive_return_every_primary_period": bool(v14["return_percent"].gt(0.0).all()),
        "positive_matched_control_excess_every_primary_period": bool(
            v14["candidate_minus_control_bp_per_trade"].gt(0.0).all()
        ),
        "ranking_p_lt_0p01_every_primary_period": bool(
            v14["primary_score_top_decile_permutation_p"].lt(0.01).all()
        ),
    }
    acceptance["passes_all_gates"] = bool(all(acceptance.values()))

    payload = {
        "artifact": "ETH 15m V14 fixed literal-release analyst-exposed robustness study",
        "preregistration": prereg,
        "exposure_warning": prereg["exposure_warning"],
        "data_quality": quality,
        "results": rows,
        "v13_vs_v14": comparison,
        "acceptance": acceptance,
        "pine": pine_manifest,
        "matched_control": {
            "contract": "ETH x UTC month x HK 6h x prior-month ATR quintile x copied horizon",
            "assignment_sensitivity_seeds_per_primary_period": CONTROL_SENSITIVITY_SEEDS,
        },
        "code_provenance": {
            "git_commit_at_run": current_commit(),
            "runner_sha256": _sha256(Path(__file__)),
            "release_feature_sha256": _sha256(
                PROJECT / "yoyo/layers/l2_judgment/pine_dense_release.py"
            ),
            "pine_generator_sha256": _sha256(
                PROJECT / "scripts/generate_pine_eth_15m_dense_release.py"
            ),
        },
        "outputs": {
            "pine": str(pine_path.relative_to(PROJECT)),
            "trades": str(TRADES_OUTPUT.relative_to(PROJECT)),
            "controls": str(CONTROLS_OUTPUT.relative_to(PROJECT)),
            "pairs": str(PAIRS_OUTPUT.relative_to(PROJECT)),
            "equity": str(EQUITY_OUTPUT.relative_to(PROJECT)),
            "feature_rows": str(FEATURE_ROWS_OUTPUT.relative_to(PROJECT)),
            "control_sensitivity": str(CONTROL_SENSITIVITY_OUTPUT.relative_to(PROJECT)),
        },
        "holdout_rows_read": 0,
        "model_trained_or_scored": False,
        "official_tradingview_parity_passed": False,
        "training_eligible": False,
        "forward_eligible": False,
        "production_eligible": False,
    }

    RESULTS.mkdir(parents=True, exist_ok=True)
    trades_output.to_csv(TRADES_OUTPUT, index=False)
    controls_output.to_csv(CONTROLS_OUTPUT, index=False)
    pairs_output.to_csv(PAIRS_OUTPUT, index=False)
    equity_output.to_csv(EQUITY_OUTPUT, index=False)
    features.to_csv(FEATURE_ROWS_OUTPUT, index=False)
    sensitivity.to_csv(CONTROL_SENSITIVITY_OUTPUT, index=False)
    SUMMARY_OUTPUT.write_text(
        json.dumps(_json_safe(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        summary.loc[
            summary["period"].isin(PRIMARY_PERIOD_NAMES),
            [
                "variant",
                "period",
                "trades",
                "return_percent",
                "max_drawdown_15m_percent",
                "win_rate",
                "stop_within_24h_rate",
                "candidate_minus_control_bp_per_trade",
            ],
        ].to_string(index=False)
    )
    print(f"summary={SUMMARY_OUTPUT}")
    print("holdout_rows_read=0")


if __name__ == "__main__":
    main()
