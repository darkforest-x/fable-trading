#!/usr/bin/env python3
"""Select and evaluate the causal ETH 15m V15E soft trend ensemble.

The only strategy change is a quality threshold applied to guarded V12F
candidates.  Quality is 80% equal-weight multi-speed EWMAC/Donchian support
and 20% of the existing six-MA dense-start score.  Threshold selection uses
only 2023H1/H2.  Stops, break-even, sizing, cooldown, reversals and 20bp cost
remain frozen.  Loading stops before 2026-03-01 and never parses the repository
holdout beginning 2026-05-04.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
import warnings

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from scripts.backtest_pine_eth_15m_v12_preholdout import build_v12_feature_frame
from scripts.generate_pine_eth_15m_trend_ensemble import write_selected_pine
from scripts.research_pine_eth_15m import (
    Period,
    build_matched_controls,
    current_commit,
    load_config,
    load_research_frame,
    pair_controls,
)
from scripts.research_pine_eth_15m_dense_start import (
    CONTROLS_PER_TRADE,
    CONTROL_SENSITIVITY_SEEDS,
    HOLDOUT_START,
    LOCKED_PERIODS,
    PRIMARY_PERIOD_NAMES,
    SAFE_END,
    SELECTION_PERIODS,
    Variant,
    _json_safe,
    _ranking_metrics,
    run_variant_period,
)
from yoyo.layers.l2_judgment.pine_dense_start import add_six_ma_dense_start_features
from yoyo.layers.l2_judgment.pine_trend_ensemble import (
    TrendEnsembleProfile,
    add_trend_ensemble_features,
    trend_ensemble_gate_mask,
)
from yoyo.layers.l3_backtest.pine_allin_v7 import SignalParameters


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-trend-ensemble-v1"
PREREGISTRATION = EXPERIMENT / "preregistration.json"
RESULTS = EXPERIMENT / "results"
CHARTS = RESULTS / "charts"

PROFILE_SEARCH_OUTPUT = RESULTS / "trend_ensemble_profile_search.csv"
SUMMARY_OUTPUT = RESULTS / "trend_ensemble_summary.json"
TRADES_OUTPUT = RESULTS / "trend_ensemble_trades.csv"
CONTROLS_OUTPUT = RESULTS / "trend_ensemble_controls.csv"
PAIRS_OUTPUT = RESULTS / "trend_ensemble_pairs.csv"
CONTROL_SENSITIVITY_OUTPUT = RESULTS / "trend_ensemble_control_sensitivity.csv"
FEATURE_ROWS_OUTPUT = RESULTS / "trend_ensemble_feature_rows.csv"
EQUITY_OUTPUT = RESULTS / "trend_ensemble_equity.csv.gz"
PATH_DIFFERENCES_OUTPUT = RESULTS / "trend_ensemble_path_differences.csv"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _concat(frames: list[pd.DataFrame]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame()
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=FutureWarning)
        return pd.concat(frames, ignore_index=True)


def load_preregistration(
    path: Path = PREREGISTRATION,
) -> tuple[dict[str, Any], list[TrendEnsembleProfile]]:
    """Load and validate the frozen 15-minute, zero-holdout contract."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    scope = payload["scope"]
    if int(scope["bar_minutes"]) != 15:
        raise ValueError("trend-ensemble experiment is fixed to 15-minute bars")
    if int(scope["holdout_rows_allowed"]) != 0:
        raise ValueError("trend-ensemble experiment must fail closed on holdout access")
    if bool(scope["training_allowed"]) or bool(scope["barrier_change_allowed"]):
        raise ValueError("trend-ensemble experiment cannot train or change barriers")
    profiles = [
        TrendEnsembleProfile.from_mapping(row)
        for row in payload["ordered_threshold_profiles"]
    ]
    if not profiles:
        raise ValueError("preregistration contains no threshold profile")
    return payload, profiles


def add_profile_columns(
    frame: pd.DataFrame,
    profiles: list[TrendEnsembleProfile],
) -> pd.DataFrame:
    """Add V15 full-state columns while preserving V12F outside-guard state."""

    out = frame.copy()
    guarded = out["entry_allowed"].fillna(False).astype(bool)
    raw_long = out["v9_long"].fillna(False).astype(bool)
    raw_short = out["v9_short"].fillna(False).astype(bool)
    v12_long_pass = out["ma6_w8_long_pass"].fillna(False).astype(bool)
    v12_short_pass = out["ma6_w8_short_pass"].fillna(False).astype(bool)
    for profile in profiles:
        prefix = f"v15e_{profile.profile_id}"
        quality_long_pass = trend_ensemble_gate_mask(out, profile, side="long")
        quality_short_pass = trend_ensemble_gate_mask(out, profile, side="short")
        out[f"{prefix}_quality_long_pass"] = quality_long_pass
        out[f"{prefix}_quality_short_pass"] = quality_short_pass
        out[f"{prefix}_long_pass"] = v12_long_pass & quality_long_pass
        out[f"{prefix}_short_pass"] = v12_short_pass & quality_short_pass
        out[f"{prefix}_long"] = raw_long & (
            ~guarded | out[f"{prefix}_long_pass"]
        )
        out[f"{prefix}_short"] = raw_short & (
            ~guarded | out[f"{prefix}_short_pass"]
        )
        out[f"{prefix}_score"] = np.where(
            raw_long,
            out["trend_quality_long"],
            np.where(raw_short, out["trend_quality_short"], 0.0),
        )
    return out


def _profile_variant(profile: TrendEnsembleProfile) -> Variant:
    prefix = f"v15e_{profile.profile_id}"
    return Variant(
        name=prefix,
        long_column=f"{prefix}_long",
        short_column=f"{prefix}_short",
        score_column=f"{prefix}_score",
        gate_long_column=f"{prefix}_long_pass",
        gate_short_column=f"{prefix}_short_pass",
    )


def _attach_ensemble_features(frame: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    """Attach decision-bar-only trend features to a versioned trade ledger."""

    if trades.empty:
        return trades
    out = trades.copy()
    source = frame.iloc[out["signal_i"].astype(int).to_numpy()]
    is_long = out["direction"].eq("long").to_numpy()
    for column in (
        "trend_ewmac_forecast",
        "trend_donchian_forecast",
        "trend_ensemble_forecast",
        "trend_ensemble_horizon_dispersion",
    ):
        out[column] = source[column].to_numpy(dtype=float)
    for output, long_column, short_column in (
        ("ewmac_only_score", "trend_ewmac_support_long", "trend_ewmac_support_short"),
        (
            "donchian_only_score",
            "trend_donchian_support_long",
            "trend_donchian_support_short",
        ),
        ("trend_only_score", "trend_support_long", "trend_support_short"),
        ("dense_only_score", "dense_start_score_long", "dense_start_score_short"),
        (
            "trend_component_consensus",
            "trend_component_consensus_long",
            "trend_component_consensus_short",
        ),
        ("trend_quality", "trend_quality_long", "trend_quality_short"),
    ):
        out[output] = np.where(
            is_long,
            source[long_column].to_numpy(dtype=float),
            source[short_column].to_numpy(dtype=float),
        )
    return out


def _add_diagnostic_rankings(
    trades: pd.DataFrame,
    row: dict[str, Any],
    *,
    seed: int,
) -> None:
    for offset, (column, prefix) in enumerate(
        (
            ("trend_only_score", "trend_only"),
            ("ewmac_only_score", "ewmac_only"),
            ("donchian_only_score", "donchian_only"),
            ("dense_only_score", "dense_only"),
        )
    ):
        row.update(
            _ranking_metrics(
                trades,
                score_column=column,
                prefix=prefix,
                seed=seed + 100 + offset,
            )
        )


def run_period(
    frame: pd.DataFrame,
    variant: Variant,
    period: Period,
    *,
    seed: int,
    stage: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Run one frozen execution replay and add ensemble ranking diagnostics."""

    trades, marked, controls, pairs, row = run_variant_period(
        frame,
        variant,
        period,
        seed=seed,
        stage=stage,
        with_controls=True,
    )
    trades = _attach_ensemble_features(frame, trades)
    _add_diagnostic_rankings(trades, row, seed=seed)

    times = pd.to_datetime(frame["open_time"], utc=True)
    active = times.ge(period.start) & times.lt(period.end)
    guarded = frame["entry_allowed"].fillna(False).astype(bool)
    raw_long = frame["v9_long"].fillna(False).astype(bool)
    raw_short = frame["v9_short"].fillna(False).astype(bool)
    v12_candidates = active & guarded & (
        (raw_long & frame["ma6_w8_long_pass"].fillna(False).astype(bool))
        | (raw_short & frame["ma6_w8_short_pass"].fillna(False).astype(bool))
    )
    row["v12f_guarded_candidates"] = int(v12_candidates.sum())
    if variant.gate_long_column and variant.gate_short_column:
        accepted = active & guarded & (
            (raw_long & frame[variant.gate_long_column].fillna(False).astype(bool))
            | (raw_short & frame[variant.gate_short_column].fillna(False).astype(bool))
        )
        row["accepted_v12f_candidates"] = int((accepted & v12_candidates).sum())
        row["rejected_v12f_candidates"] = int((v12_candidates & ~accepted).sum())
    else:
        row["accepted_v12f_candidates"] = int(v12_candidates.sum())
        row["rejected_v12f_candidates"] = 0
    return trades, marked, controls, pairs, row


def select_profile(
    frame: pd.DataFrame,
    profiles: list[TrendEnsembleProfile],
) -> tuple[TrendEnsembleProfile, pd.DataFrame, list[pd.DataFrame], list[pd.DataFrame]]:
    """Select once on 2023 halves using the preregistered lexicographic rule."""

    rows: list[dict[str, Any]] = []
    controls_all: list[pd.DataFrame] = []
    pairs_all: list[pd.DataFrame] = []
    for profile_index, profile in enumerate(profiles):
        variant = _profile_variant(profile)
        for period_index, period in enumerate(SELECTION_PERIODS):
            _, _, controls, pairs, row = run_period(
                frame,
                variant,
                period,
                seed=20_261_500 + profile_index * 100 + period_index,
                stage="profile_selection_2023_only",
            )
            row.update(
                {
                    "profile_id": profile.profile_id,
                    "profile_order": profile_index,
                    "minimum_quality": profile.minimum_quality,
                }
            )
            rows.append(row)
            if not controls.empty:
                controls_all.append(controls)
            if not pairs.empty:
                pairs_all.append(pairs)

    search = pd.DataFrame(rows)
    aggregates: list[dict[str, Any]] = []
    for profile_index, profile in enumerate(profiles):
        group = search.loc[search["profile_id"].eq(profile.profile_id)]
        controls_complete = group["matched_control_status"].eq(
            "complete_exact_3_per_trade"
        ).all()
        qualified = bool(
            len(group) == 2 and group["trades"].ge(10).all() and controls_complete
        )
        aggregates.append(
            {
                "profile_id": profile.profile_id,
                "profile_order": profile_index,
                "qualified_min_10_each_half": qualified,
                "worst_half_matched_excess_bp_per_trade": float(
                    group["candidate_minus_control_bp_per_trade"].min()
                ),
                "worst_half_net_bp_per_trade": float(
                    group["project_net_bp_per_trade"].min()
                ),
                "worst_half_return_percent": float(group["return_percent"].min()),
                "max_half_drawdown_percent": float(
                    group["max_drawdown_15m_percent"].max()
                ),
            }
        )
    aggregate = pd.DataFrame(aggregates)
    qualified = aggregate.loc[aggregate["qualified_min_10_each_half"]].copy()
    if qualified.empty:
        raise RuntimeError(
            "preregistered fail-closed rule: no profile has >=10 trades and complete controls in each 2023 half"
        )
    qualified = qualified.sort_values(
        [
            "worst_half_matched_excess_bp_per_trade",
            "worst_half_net_bp_per_trade",
            "worst_half_return_percent",
            "max_half_drawdown_percent",
            "profile_order",
        ],
        ascending=[False, False, False, True, True],
        kind="stable",
    )
    selected_id = str(qualified.iloc[0]["profile_id"])
    search = search.merge(aggregate, on=["profile_id", "profile_order"], how="left")
    search["selected"] = search["profile_id"].eq(selected_id)
    selected = next(profile for profile in profiles if profile.profile_id == selected_id)
    return selected, search, controls_all, pairs_all


def export_feature_rows(
    frame: pd.DataFrame,
    profiles: list[TrendEnsembleProfile],
    selected: TrendEnsembleProfile,
) -> pd.DataFrame:
    """Export guarded V12F candidates with no future outcome label."""

    guarded = frame["entry_allowed"].fillna(False).astype(bool)
    raw_long = frame["v9_long"].fillna(False).astype(bool)
    raw_short = frame["v9_short"].fillna(False).astype(bool)
    long_candidates = raw_long & frame["ma6_w8_long_pass"].fillna(False).astype(bool)
    short_candidates = raw_short & frame["ma6_w8_short_pass"].fillna(False).astype(bool)
    mask = guarded & (long_candidates | short_candidates)
    indices = np.flatnonzero(mask.to_numpy())
    rows = pd.DataFrame(
        {
            "signal_i": indices,
            "signal_time": pd.to_datetime(
                frame.iloc[indices]["open_time"], utc=True
            ).to_numpy(),
            "side": np.where(long_candidates.iloc[indices].to_numpy(), "long", "short"),
        }
    )
    if rows.empty:
        return rows
    pseudo = pd.DataFrame(
        {
            "signal_i": indices,
            "direction": rows["side"],
        }
    )
    pseudo = _attach_ensemble_features(frame, pseudo)
    for column in pseudo.columns:
        if column not in {"signal_i", "direction"}:
            rows[column] = pseudo[column].to_numpy()
    source = frame.iloc[indices]
    for profile in profiles:
        prefix = f"v15e_{profile.profile_id}"
        rows[f"passes_{profile.profile_id}"] = np.where(
            rows["side"].eq("long"),
            source[f"{prefix}_long_pass"].to_numpy(dtype=bool),
            source[f"{prefix}_short_pass"].to_numpy(dtype=bool),
        )
    rows["selected_profile"] = selected.profile_id
    rows["selected_gate_pass"] = rows[f"passes_{selected.profile_id}"]
    rows["outcome_label_included"] = False
    rows["training_eligible"] = False
    return rows


def control_sensitivity(
    frame: pd.DataFrame,
    trades: pd.DataFrame,
    selected_variant: str,
) -> pd.DataFrame:
    """Repeat matched assignments without changing any strategy parameter."""

    rows: list[dict[str, Any]] = []
    period_by_name = {period.name: period for period in LOCKED_PERIODS}
    for period_name in sorted(PRIMARY_PERIOD_NAMES):
        selected_trades = trades.loc[
            trades["variant"].eq(selected_variant)
            & trades["period"].eq(period_name)
        ].copy()
        period = period_by_name[period_name]
        for seed_index in range(CONTROL_SENSITIVITY_SEEDS):
            try:
                controls = build_matched_controls(
                    frame,
                    selected_trades,
                    period,
                    controls_per_trade=CONTROLS_PER_TRADE,
                    seed=f"trend-ensemble|{selected_variant}|{period_name}|{seed_index}",
                    params=SignalParameters(osc_threshold=0.1),
                )
                pairs = pair_controls(selected_trades, controls)
                rows.append(
                    {
                        "variant": selected_variant,
                        "period": period_name,
                        "assignment_seed_index": seed_index,
                        "status": "complete",
                        "candidate_minus_control_bp_per_trade": float(
                            pairs["excess_return"].mean() * 10_000.0
                        ),
                    }
                )
            except RuntimeError as exc:
                rows.append(
                    {
                        "variant": selected_variant,
                        "period": period_name,
                        "assignment_seed_index": seed_index,
                        "status": f"unavailable_fail_closed: {exc}",
                        "candidate_minus_control_bp_per_trade": np.nan,
                    }
                )
    return pd.DataFrame(rows)


def build_path_differences(
    trades: pd.DataFrame,
    selected_variant: str,
) -> pd.DataFrame:
    """Return primary-period trades present on only one dynamic state path.

    A rejected signal can change the position held at later raw signals and
    therefore alter reversals, stops and cooldown.  This ledger intentionally
    compares dynamic paths by ``signal_i`` plus direction; it is descriptive
    post-outcome evidence and never feeds profile selection.
    """

    rows: list[pd.DataFrame] = []
    for period_name in sorted(PRIMARY_PERIOD_NAMES):
        baseline = trades.loc[
            trades["variant"].eq("v12f_ma6_w8_full_gate")
            & trades["period"].eq(period_name)
        ].copy()
        candidate = trades.loc[
            trades["variant"].eq(selected_variant)
            & trades["period"].eq(period_name)
        ].copy()
        baseline_keys = set(
            zip(baseline["signal_i"].astype(int), baseline["direction"].astype(str))
        )
        candidate_keys = set(
            zip(candidate["signal_i"].astype(int), candidate["direction"].astype(str))
        )
        for source, other_keys, membership in (
            (baseline, candidate_keys, "v12f_only"),
            (candidate, baseline_keys, "v15_only"),
        ):
            mask = [
                (int(signal_i), str(direction)) not in other_keys
                for signal_i, direction in zip(source["signal_i"], source["direction"])
            ]
            difference = source.loc[mask].copy()
            if difference.empty:
                continue
            difference["path_membership"] = membership
            rows.append(difference)
    output = _concat(rows)
    if output.empty:
        return output
    columns = [
        "period",
        "path_membership",
        "variant",
        "signal_i",
        "signal_time",
        "direction",
        "entry_time",
        "exit_time",
        "exit_reason",
        "holding_bars",
        "gross_return",
        "project_net_return",
        "trend_quality",
        "trend_only_score",
        "ewmac_only_score",
        "donchian_only_score",
        "dense_only_score",
    ]
    return output.loc[:, columns].sort_values(
        ["period", "signal_time", "path_membership"], kind="stable"
    )


def make_charts(
    search: pd.DataFrame,
    summary: pd.DataFrame,
    equity: pd.DataFrame,
    selected_variant: str,
) -> None:
    """Render compact selection, mechanism and equity evidence."""

    CHARTS.mkdir(parents=True, exist_ok=True)
    selected_search = search.drop_duplicates("profile_id").sort_values("profile_order")
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))
    colors = ["#6B8E23" if value else "#94A3B8" for value in selected_search["selected"]]
    axes[0].bar(
        selected_search["profile_id"],
        selected_search["worst_half_matched_excess_bp_per_trade"],
        color=colors,
    )
    axes[0].axhline(0.0, color="#334155", linewidth=0.8)
    axes[0].set_title("2023 selection: worst-half matched excess")
    axes[0].set_ylabel("bp / trade")
    axes[1].plot(
        selected_search["profile_id"],
        selected_search["worst_half_net_bp_per_trade"],
        marker="o",
        color="#2563EB",
    )
    axes[1].axhline(0.0, color="#334155", linewidth=0.8)
    axes[1].set_title("Worst-half net expectancy")
    axes[1].set_ylabel("bp / trade after 20bp cost")
    axes[1].grid(alpha=0.2)
    fig.suptitle("Preregistered V15E soft thresholds; 2023 rows only")
    fig.tight_layout()
    fig.savefig(CHARTS / "trend_ensemble_profile_selection.png", dpi=170)
    plt.close(fig)

    variants = ["v9_frozen_baseline", "v12f_ma6_w8_full_gate", selected_variant]
    labels = ["V9", "V12F", "V15E soft ensemble"]
    palette = ["#2563EB", "#D97706", "#6B8E23"]
    periods = ["discovery_2023", "confirmation_2024", "final_preholdout_2025_202602"]
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.5))
    x = np.arange(len(periods))
    width = 0.24
    for axis, (column, title, scale) in zip(
        axes,
        (
            ("return_percent", "Return (%)", 1.0),
            ("max_drawdown_15m_percent", "Max drawdown (%)", 1.0),
            ("win_rate", "Net win rate (%)", 100.0),
        ),
    ):
        for offset, (variant, label, color) in enumerate(zip(variants, labels, palette)):
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
    fig.suptitle("V15E comparison; all exits, risk and costs unchanged")
    fig.tight_layout()
    fig.savefig(CHARTS / "trend_ensemble_mechanism_comparison.png", dpi=170)
    plt.close(fig)

    fig, axes = plt.subplots(1, 3, figsize=(14, 4.4))
    for axis, period in zip(axes, periods):
        subset = equity.loc[equity["period"].eq(period)]
        for variant, label, color in zip(variants, labels, palette):
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
    fig.suptitle("ETH 15m marked equity; 1% risk and 20bp round trip")
    fig.tight_layout()
    fig.savefig(CHARTS / "trend_ensemble_equity.png", dpi=170)
    plt.close(fig)


def main() -> None:
    prereg, profiles = load_preregistration()
    raw, quality = load_research_frame(load_config())
    times = pd.to_datetime(raw["open_time"], utc=True)
    if quality["holdout_rows_read"] != 0:
        raise RuntimeError("holdout row reached trend-ensemble research")
    if times.max() >= SAFE_END or SAFE_END >= HOLDOUT_START:
        raise RuntimeError("safe-end/holdout time contract failed")

    frame = build_v12_feature_frame(raw)
    frame = add_six_ma_dense_start_features(frame)
    frame = add_trend_ensemble_features(frame)
    frame = add_profile_columns(frame, profiles)
    selected, search, selection_controls, selection_pairs = select_profile(frame, profiles)
    selected_variant = _profile_variant(selected)
    pine_path, pine_manifest = write_selected_pine(selected)

    variants = (
        Variant("v9_frozen_baseline", "v9_long", "v9_short", "v9_score"),
        Variant(
            "v12f_ma6_w8_full_gate",
            "v12f_long",
            "v12f_short",
            "v9_score",
            "ma6_w8_long_pass",
            "ma6_w8_short_pass",
        ),
        selected_variant,
    )
    rows: list[dict[str, Any]] = []
    trades_all: list[pd.DataFrame] = []
    equity_all: list[pd.DataFrame] = []
    controls_all: list[pd.DataFrame] = selection_controls.copy()
    pairs_all: list[pd.DataFrame] = selection_pairs.copy()
    for variant_index, variant in enumerate(variants):
        for period_index, period in enumerate(LOCKED_PERIODS):
            stage = (
                "locked_confirmation"
                if period.name.startswith("2024") or period.name == "confirmation_2024"
                else "locked_analyst_exposed_description"
            )
            trades, marked, controls, pairs, row = run_period(
                frame,
                variant,
                period,
                seed=20_261_600 + variant_index * 100 + period_index,
                stage=stage,
            )
            rows.append(row)
            if not trades.empty:
                trades_all.append(trades)
            if not marked.empty and period.name in PRIMARY_PERIOD_NAMES:
                equity_all.append(
                    marked[
                        [
                            "open_time",
                            "normalized_equity",
                            "arm",
                            "symbol",
                            "variant",
                            "period",
                            "stage",
                        ]
                    ].copy()
                )
            if not controls.empty:
                controls_all.append(controls)
            if not pairs.empty:
                pairs_all.append(pairs)

    summary = pd.DataFrame(rows)
    trades_output = _concat(trades_all)
    equity_output = _concat(equity_all)
    controls_output = _concat(controls_all)
    pairs_output = _concat(pairs_all)
    features = export_feature_rows(frame, profiles, selected)
    sensitivity = control_sensitivity(frame, trades_output, selected_variant.name)
    path_differences = build_path_differences(trades_output, selected_variant.name)
    make_charts(search, summary, equity_output, selected_variant.name)

    primary = summary.loc[summary["period"].isin(PRIMARY_PERIOD_NAMES)].copy()
    v12 = primary.loc[primary["variant"].eq("v12f_ma6_w8_full_gate")].set_index("period")
    v15 = primary.loc[primary["variant"].eq(selected_variant.name)].set_index("period")
    comparison: list[dict[str, Any]] = []
    runner_retention: list[bool] = []
    for period_name in sorted(PRIMARY_PERIOD_NAMES):
        v12_runners = int(v12.loc[period_name, "gross_ge_10pct_trades"])
        v15_runners = int(v15.loc[period_name, "gross_ge_10pct_trades"])
        minimum_retained = int(np.ceil(v12_runners / 2.0))
        runner_retention.append(v15_runners >= minimum_retained)
        comparison.append(
            {
                "period": period_name,
                "v12_return_percent": float(v12.loc[period_name, "return_percent"]),
                "v15_return_percent": float(v15.loc[period_name, "return_percent"]),
                "return_delta_percentage_points": float(
                    v15.loc[period_name, "return_percent"]
                    - v12.loc[period_name, "return_percent"]
                ),
                "v12_max_drawdown_percent": float(
                    v12.loc[period_name, "max_drawdown_15m_percent"]
                ),
                "v15_max_drawdown_percent": float(
                    v15.loc[period_name, "max_drawdown_15m_percent"]
                ),
                "v12_win_rate": float(v12.loc[period_name, "win_rate"]),
                "v15_win_rate": float(v15.loc[period_name, "win_rate"]),
                "v12_gross_ge_10pct_trades": v12_runners,
                "v15_gross_ge_10pct_trades": v15_runners,
                "minimum_runner_retention": minimum_retained,
            }
        )

    acceptance = {
        "positive_matched_control_excess_every_primary_period": bool(
            v15["candidate_minus_control_bp_per_trade"].gt(0.0).all()
        ),
        "positive_top_decile_net_every_primary_period": bool(
            v15["primary_score_top_decile_net_bp_per_trade"].gt(0.0).all()
        ),
        "top_decile_permutation_p_lt_0p01_every_primary_period": bool(
            v15["primary_score_top_decile_permutation_p"].lt(0.01).all()
        ),
        "retains_at_least_half_v12_large_runners_every_primary_period": bool(
            all(runner_retention)
        ),
        "early_stop_rate_not_worse_every_primary_period": bool(
            v15["stop_within_24h_rate"].le(v12["stop_within_24h_rate"]).all()
        ),
        "drawdown_not_worse_every_primary_period": bool(
            v15["max_drawdown_15m_percent"].le(
                v12["max_drawdown_15m_percent"] + 1e-9
            ).all()
        ),
    }
    acceptance["passes_all_gates"] = bool(all(acceptance.values()))

    RESULTS.mkdir(parents=True, exist_ok=True)
    search.to_csv(PROFILE_SEARCH_OUTPUT, index=False)
    trades_output.to_csv(TRADES_OUTPUT, index=False)
    controls_output.to_csv(CONTROLS_OUTPUT, index=False)
    pairs_output.to_csv(PAIRS_OUTPUT, index=False)
    equity_output.to_csv(EQUITY_OUTPUT, index=False, compression="gzip")
    features.to_csv(FEATURE_ROWS_OUTPUT, index=False)
    sensitivity.to_csv(CONTROL_SENSITIVITY_OUTPUT, index=False)
    path_differences.to_csv(PATH_DIFFERENCES_OUTPUT, index=False)

    payload = {
        "artifact": "ETH 15m V15E multi-speed soft trend-ensemble analyst-exposed study",
        "preregistration": prereg,
        "data_quality": quality,
        "selected_profile": {
            "profile_id": selected.profile_id,
            "minimum_quality": selected.minimum_quality,
        },
        "results": rows,
        "v12_vs_v15": comparison,
        "acceptance": acceptance,
        "pine": pine_manifest,
        "matched_control": {
            "contract": "ETH x UTC month x HK 6h x prior-month ATR quintile x copied horizon",
            "assignment_sensitivity_seeds_per_primary_period": CONTROL_SENSITIVITY_SEEDS,
        },
        "code_provenance": {
            "git_commit_at_run": current_commit(),
            "runner_sha256": _sha256(Path(__file__)),
            "feature_sha256": _sha256(
                PROJECT / "yoyo/layers/l2_judgment/pine_trend_ensemble.py"
            ),
            "pine_generator_sha256": _sha256(
                PROJECT / "scripts/generate_pine_eth_15m_trend_ensemble.py"
            ),
        },
        "outputs": {
            "pine": str(pine_path.relative_to(PROJECT)),
            "profile_search": str(PROFILE_SEARCH_OUTPUT.relative_to(PROJECT)),
            "trades": str(TRADES_OUTPUT.relative_to(PROJECT)),
            "controls": str(CONTROLS_OUTPUT.relative_to(PROJECT)),
            "pairs": str(PAIRS_OUTPUT.relative_to(PROJECT)),
            "equity": str(EQUITY_OUTPUT.relative_to(PROJECT)),
            "feature_rows": str(FEATURE_ROWS_OUTPUT.relative_to(PROJECT)),
            "control_sensitivity": str(
                CONTROL_SENSITIVITY_OUTPUT.relative_to(PROJECT)
            ),
            "path_differences": str(PATH_DIFFERENCES_OUTPUT.relative_to(PROJECT)),
        },
        "holdout_rows_read": 0,
        "model_trained_or_scored": False,
        "official_tradingview_parity_passed": False,
        "training_eligible": False,
        "forward_eligible": False,
        "production_eligible": False,
    }
    SUMMARY_OUTPUT.write_text(
        json.dumps(_json_safe(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        primary[
            [
                "variant",
                "period",
                "trades",
                "return_percent",
                "max_drawdown_15m_percent",
                "win_rate",
                "stop_within_24h_rate",
                "candidate_minus_control_bp_per_trade",
                "primary_score_top_decile_net_bp_per_trade",
                "primary_score_top_decile_permutation_p",
            ]
        ].to_string(index=False)
    )
    print(f"selected_profile={selected.profile_id}")
    print(f"summary={SUMMARY_OUTPUT}")
    print("holdout_rows_read=0")


if __name__ == "__main__":
    main()
