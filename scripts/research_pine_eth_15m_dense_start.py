#!/usr/bin/env python3
"""Select and evaluate the causal ETH 15m V13 dense-start Pine gate.

At a decision bar ``t`` the composite formation features use only
``[t-12,t-1]`` and the release features use the completed bar ``t``.  Entry
remains ``open[t+1]``.  Profile selection is restricted to 2023H1/2023H2;
2024 and the already-consumed final-preholdout segment are evaluated only
after the profile is locked.  The repository holdout beginning 2026-05-04 is
never parsed, hashed, charted or scored.

The only changed strategy variable is the full-state signal gate.  ATR4/3%
initial protection, 1.5%/0.1% break-even, 1% risk, 13x cap, cooldown, reversal
semantics and 20bp round-trip cost are copied from frozen V9/V12F.  This script
exports an LR-compatible causal feature table but never fits or scores a model.
"""
from __future__ import annotations

from dataclasses import dataclass
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
from scripts.generate_pine_eth_15m_dense_start import write_selected_pine
from scripts.research_pine_eth_15m import (
    INITIAL_CAPITAL,
    Period,
    block_signflip,
    build_matched_controls,
    current_commit,
    exact_execution,
    load_config,
    load_research_frame,
    pair_controls,
    summarize,
)
from yoyo.evaluation.permutation import permutation_test
from yoyo.layers.l2_judgment.pine_dense_start import (
    DEFAULT_ATR_RELEASE_WINDOW,
    DEFAULT_DENSITY_WINDOW,
    DEFAULT_SLOPE_LAG,
    DenseStartProfile,
    add_six_ma_dense_start_features,
    dense_start_gate_mask,
)
from yoyo.layers.l3_backtest.pine_allin_v7 import (
    Arm,
    SignalParameters,
    auc_from_scores,
    simulate_symbol,
)


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-dense-start-v1"
PREREGISTRATION = EXPERIMENT / "preregistration.json"
RESULTS = EXPERIMENT / "results"
CHARTS = RESULTS / "charts"

PROFILE_SEARCH_OUTPUT = RESULTS / "dense_start_profile_search.csv"
SUMMARY_OUTPUT = RESULTS / "dense_start_summary.json"
TRADES_OUTPUT = RESULTS / "dense_start_trades.csv"
CONTROLS_OUTPUT = RESULTS / "dense_start_controls.csv"
PAIRS_OUTPUT = RESULTS / "dense_start_pairs.csv"
CONTROL_SENSITIVITY_OUTPUT = RESULTS / "dense_start_control_sensitivity.csv"
FEATURE_ROWS_OUTPUT = RESULTS / "dense_start_feature_rows.csv"
EQUITY_OUTPUT = RESULTS / "dense_start_equity.csv"

SAFE_END = pd.Timestamp("2026-03-01T00:00:00Z")
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
ROUND_TRIP_COST = 0.002
CONTROLS_PER_TRADE = 3
PERMUTATIONS = 10_000
SIGNFLIP_RESAMPLES = 20_000
CONTROL_SENSITIVITY_SEEDS = 8

SELECTION_PERIODS = (
    Period("2023H1", pd.Timestamp("2023-01-01T00:00:00Z"), pd.Timestamp("2023-07-01T00:00:00Z")),
    Period("2023H2", pd.Timestamp("2023-07-01T00:00:00Z"), pd.Timestamp("2024-01-01T00:00:00Z")),
)
LOCKED_PERIODS = (
    *SELECTION_PERIODS,
    Period("discovery_2023", pd.Timestamp("2023-01-01T00:00:00Z"), pd.Timestamp("2024-01-01T00:00:00Z")),
    Period("2024H1", pd.Timestamp("2024-01-01T00:00:00Z"), pd.Timestamp("2024-07-01T00:00:00Z")),
    Period("2024H2", pd.Timestamp("2024-07-01T00:00:00Z"), pd.Timestamp("2025-01-01T00:00:00Z")),
    Period("confirmation_2024", pd.Timestamp("2024-01-01T00:00:00Z"), pd.Timestamp("2025-01-01T00:00:00Z")),
    Period("final_preholdout_2025_202602", pd.Timestamp("2025-01-01T00:00:00Z"), SAFE_END),
)
PRIMARY_PERIOD_NAMES = {
    "discovery_2023",
    "confirmation_2024",
    "final_preholdout_2025_202602",
}


@dataclass(frozen=True)
class Variant:
    """One locked full-state signal arm under the exact V9 execution contract."""

    name: str
    long_column: str
    short_column: str
    score_column: str
    gate_long_column: str | None = None
    gate_short_column: str | None = None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not np.isfinite(float(value)) else float(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def _concat(frames: list[pd.DataFrame]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame()
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=FutureWarning)
        return pd.concat(frames, ignore_index=True)


def load_preregistration(path: Path = PREREGISTRATION) -> tuple[dict[str, Any], list[DenseStartProfile]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if int(payload["scope"]["bar_minutes"]) != 15:
        raise ValueError("dense-start experiment is fixed to 15-minute bars")
    if int(payload["scope"]["holdout_rows_allowed"]) != 0:
        raise ValueError("dense-start experiment must fail closed on holdout access")
    profiles = [
        DenseStartProfile.from_mapping(row)
        for row in payload["ordered_strictness_profiles"]
    ]
    if not profiles:
        raise ValueError("preregistration contains no strictness profile")
    return payload, profiles


def add_profile_columns(
    frame: pd.DataFrame,
    profiles: list[DenseStartProfile],
) -> pd.DataFrame:
    """Add full-state profile arms and transparent side-aware scores."""

    out = frame.copy()
    guarded = out["entry_allowed"].fillna(False).astype(bool)
    raw_long = out["v9_long"].fillna(False).astype(bool)
    raw_short = out["v9_short"].fillna(False).astype(bool)
    for profile in profiles:
        prefix = f"v13d_{profile.profile_id}"
        long_pass = dense_start_gate_mask(out, profile, side="long")
        short_pass = dense_start_gate_mask(out, profile, side="short")
        out[f"{prefix}_long_pass"] = long_pass
        out[f"{prefix}_short_pass"] = short_pass
        # Match V12F state semantics: out-of-guard raw signals retain the V9
        # cooldown/reversal state, while guarded candidates must pass V13.
        out[f"{prefix}_long"] = raw_long & (~guarded | long_pass)
        out[f"{prefix}_short"] = raw_short & (~guarded | short_pass)
        out[f"{prefix}_score"] = np.where(
            raw_long,
            out["dense_start_score_long"],
            np.where(raw_short, out["dense_start_score_short"], 0.0),
        )
    return out


def _standard_arm(name: str) -> Arm:
    return Arm(
        name=name,
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


def _attach_dense_features(frame: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    """Attach only signal-bar causal feature values to a trade ledger."""

    if trades.empty:
        return trades
    out = trades.copy()
    source = frame.iloc[out["signal_i"].astype(int).to_numpy()]
    is_long = out["direction"].eq("long").to_numpy()
    direct = {
        "pre_pairwise_cross_count": f"dense_pre_pairwise_cross_count_{DEFAULT_DENSITY_WINDOW}",
        "pre_pairwise_cross_breadth": f"dense_pre_pairwise_cross_breadth_{DEFAULT_DENSITY_WINDOW}",
        "pre_bandwidth_atr_mean": f"dense_pre_bandwidth_atr_mean_{DEFAULT_DENSITY_WINDOW}",
        "pre_bandwidth_atr_max": f"dense_pre_bandwidth_atr_max_{DEFAULT_DENSITY_WINDOW}",
        "atr_release_ratio": f"dense_atr_release_ratio_{DEFAULT_ATR_RELEASE_WINDOW}",
        "oscillator_score": "v9_score",
    }
    for output, column in direct.items():
        out[output] = source[column].to_numpy()
    sided = {
        "pre_cross_imbalance": (
            f"dense_pre_cross_imbalance_long_{DEFAULT_DENSITY_WINDOW}",
            f"dense_pre_cross_imbalance_short_{DEFAULT_DENSITY_WINDOW}",
        ),
        "current_alignment": ("dense_current_alignment_long", "dense_current_alignment_short"),
        "directional_order_entropy": ("dense_order_entropy_long", "dense_order_entropy_short"),
        "breakout_distance_atr": ("dense_breakout_distance_atr_long", "dense_breakout_distance_atr_short"),
        "slope_coherence": (
            f"dense_slope_coherence_long_{DEFAULT_SLOPE_LAG}",
            f"dense_slope_coherence_short_{DEFAULT_SLOPE_LAG}",
        ),
        "signed_mean_slope_atr": (
            f"dense_signed_mean_slope_atr_long_{DEFAULT_SLOPE_LAG}",
            f"dense_signed_mean_slope_atr_short_{DEFAULT_SLOPE_LAG}",
        ),
        "dense_start_score": ("dense_start_score_long", "dense_start_score_short"),
    }
    for output, (long_column, short_column) in sided.items():
        out[output] = np.where(
            is_long,
            source[long_column].to_numpy(dtype=float),
            source[short_column].to_numpy(dtype=float),
        )
    up = source[f"dense_pre_cross_up_count_{DEFAULT_DENSITY_WINDOW}"].to_numpy(dtype=float)
    down = source[f"dense_pre_cross_down_count_{DEFAULT_DENSITY_WINDOW}"].to_numpy(dtype=float)
    out["pre_directional_cross_count"] = np.where(is_long, up, down)
    out["pre_opposite_cross_count"] = np.where(is_long, down, up)
    out["training_eligible"] = False
    return out


def _ranking_metrics(
    trades: pd.DataFrame,
    *,
    score_column: str,
    prefix: str,
    seed: int,
) -> dict[str, Any]:
    if len(trades) < 2:
        return {
            f"{prefix}_auc_positive_trade": np.nan,
            f"{prefix}_top_decile_trades": int(len(trades)),
            f"{prefix}_top_decile_gross_bp_per_trade": np.nan,
            f"{prefix}_top_decile_net_bp_per_trade": np.nan,
            f"{prefix}_top_decile_win_rate": np.nan,
            f"{prefix}_top_decile_permutation_p": np.nan,
        }
    scores = trades[score_column].to_numpy(dtype=float)
    outcomes = trades["project_net_return"].to_numpy(dtype=float)
    labels = outcomes > 0.0
    k = max(1, int(np.ceil(len(trades) * 0.10)))
    top_indices = np.argsort(scores, kind="stable")[::-1][:k]
    top = trades.iloc[top_indices]
    perm = permutation_test(
        scores,
        outcomes,
        n_permutations=PERMUTATIONS,
        alternative="greater",
        seed=seed,
    )
    return {
        f"{prefix}_auc_positive_trade": (
            float(auc_from_scores(scores, labels)) if labels.any() and (~labels).any() else np.nan
        ),
        f"{prefix}_top_decile_trades": int(k),
        f"{prefix}_top_decile_gross_bp_per_trade": float(top["gross_return"].mean() * 10_000.0),
        f"{prefix}_top_decile_net_bp_per_trade": float(top["project_net_return"].mean() * 10_000.0),
        f"{prefix}_top_decile_win_rate": float((top["project_net_return"] > 0.0).mean()),
        f"{prefix}_top_decile_permutation_p": float(perm.p_value),
    }


def _mechanism_metrics(trades: pd.DataFrame) -> dict[str, Any]:
    if trades.empty:
        return {
            "stop_within_24h_trades": 0,
            "stop_within_24h_rate": np.nan,
            "positive_multi_day_trades": 0,
            "gross_ge_10pct_trades": 0,
            "gross_ge_20pct_trades": 0,
            "best_gross_return_percent": np.nan,
            "longest_holding_days": np.nan,
        }
    early_stops = trades["exit_reason"].eq("stop") & trades["holding_bars"].le(96)
    positive_multi_day = trades["holding_bars"].ge(96) & trades["project_net_return"].gt(0.0)
    return {
        "stop_within_24h_trades": int(early_stops.sum()),
        "stop_within_24h_rate": float(early_stops.mean()),
        "positive_multi_day_trades": int(positive_multi_day.sum()),
        "gross_ge_10pct_trades": int(trades["gross_return"].ge(0.10).sum()),
        "gross_ge_20pct_trades": int(trades["gross_return"].ge(0.20).sum()),
        "best_gross_return_percent": float(trades["gross_return"].max() * 100.0),
        "longest_holding_days": float(trades["holding_bars"].max() / 96.0),
    }


def _candidate_counts(frame: pd.DataFrame, variant: Variant, period: Period) -> dict[str, int]:
    times = pd.to_datetime(frame["open_time"], utc=True)
    active = times.ge(period.start) & times.lt(period.end)
    guarded = frame["entry_allowed"].fillna(False).astype(bool)
    raw_long = frame["v9_long"].fillna(False).astype(bool)
    raw_short = frame["v9_short"].fillna(False).astype(bool)
    raw = active & guarded & (raw_long | raw_short)
    if variant.gate_long_column and variant.gate_short_column:
        accepted = active & guarded & (
            (raw_long & frame[variant.gate_long_column].fillna(False).astype(bool))
            | (raw_short & frame[variant.gate_short_column].fillna(False).astype(bool))
        )
    else:
        accepted = raw
    return {
        "raw_guarded_candidates": int(raw.sum()),
        "gate_pass_candidates": int(accepted.sum()),
        "gate_rejected_candidates": int((raw & ~accepted).sum()),
    }


def _matched_controls(
    frame: pd.DataFrame,
    trades: pd.DataFrame,
    *,
    variant: str,
    period: Period,
    seed: int,
    stage: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    if trades.empty:
        return pd.DataFrame(), pd.DataFrame(), {
            "matched_control_status": "not_applicable_no_trades",
            "matched_control_net_bp_per_trade": np.nan,
            "candidate_minus_control_bp_per_trade": np.nan,
            "week_block_signflip_p_one_sided": np.nan,
        }
    try:
        controls = build_matched_controls(
            frame,
            trades,
            period,
            controls_per_trade=CONTROLS_PER_TRADE,
            seed=f"dense-start|{stage}|{variant}|{period.name}",
            params=SignalParameters(osc_threshold=0.1),
        )
    except RuntimeError as exc:
        return pd.DataFrame(), pd.DataFrame(), {
            "matched_control_status": f"unavailable_fail_closed: {exc}",
            "matched_control_net_bp_per_trade": np.nan,
            "candidate_minus_control_bp_per_trade": np.nan,
            "week_block_signflip_p_one_sided": np.nan,
        }
    pairs = pair_controls(trades, controls)
    signflip = block_signflip(pairs, n_resamples=SIGNFLIP_RESAMPLES, seed=seed)
    controls = controls.copy()
    pairs = pairs.copy()
    for output in (controls, pairs):
        output["variant"] = variant
        output["period"] = period.name
        output["stage"] = stage
    return controls, pairs, {
        "matched_control_status": "complete_exact_3_per_trade",
        "matched_control_net_bp_per_trade": float(
            pairs["control_mean_project_net"].mean() * 10_000.0
        ),
        "candidate_minus_control_bp_per_trade": float(pairs["excess_return"].mean() * 10_000.0),
        "week_block_signflip_p_one_sided": float(signflip["p_value"]),
        "matched_control_week_blocks": int(signflip["n_blocks"]),
    }


def run_variant_period(
    frame: pd.DataFrame,
    variant: Variant,
    period: Period,
    *,
    seed: int,
    stage: str,
    with_controls: bool,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    trades, marked = simulate_symbol(
        frame,
        symbol="ETH_USDT_SWAP",
        arm=_standard_arm(variant.name),
        start=period.start,
        end=period.end,
        params=SignalParameters(osc_threshold=0.1),
        round_trip_cost=ROUND_TRIP_COST,
        initial_capital=INITIAL_CAPITAL,
        execution=exact_execution(equity_frequency=None),
        signal_columns=(variant.long_column, variant.short_column, variant.score_column),
    )
    if not trades.empty:
        trades = trades.copy()
        trades["variant"] = variant.name
        trades["period"] = period.name
        trades["stage"] = stage
        trades["trade_id"] = [
            f"{variant.name}|{period.name}|{int(row.signal_i)}|{int(row.entry_i)}|{row.direction}"
            for row in trades.itertuples(index=False)
        ]
        trades = _attach_dense_features(frame, trades)
    if not marked.empty:
        marked = marked.copy()
        marked["variant"] = variant.name
        marked["period"] = period.name
        marked["stage"] = stage
    row = summarize(
        trades,
        marked,
        variant=variant.name,
        period=period.name,
        risk_percent=1.0,
    )
    row["stage"] = stage
    row.update(_candidate_counts(frame, variant, period))
    row.update(_mechanism_metrics(trades))
    if not trades.empty:
        row.update(
            _ranking_metrics(
                trades,
                score_column="score",
                prefix="primary_score",
                seed=seed,
            )
        )
        # A deliberately weak one-feature comparator: smaller pre-rope width
        # receives the higher score.  It prevents the composite from claiming
        # credit for information already carried by compression alone.
        ranked = trades.copy()
        ranked["compression_only_score"] = -ranked["pre_bandwidth_atr_mean"]
        row.update(
            _ranking_metrics(
                ranked,
                score_column="compression_only_score",
                prefix="compression_only",
                seed=seed + 1,
            )
        )
    else:
        row.update(_ranking_metrics(trades, score_column="score", prefix="primary_score", seed=seed))
        row.update(
            _ranking_metrics(
                trades,
                score_column="score",
                prefix="compression_only",
                seed=seed + 1,
            )
        )
    controls = pd.DataFrame()
    pairs = pd.DataFrame()
    if with_controls:
        controls, pairs, control_metrics = _matched_controls(
            frame,
            trades,
            variant=variant.name,
            period=period,
            seed=seed + 10_000,
            stage=stage,
        )
        row.update(control_metrics)
    else:
        row.update(
            {
                "matched_control_status": "not_run_nonreported_intermediate",
                "matched_control_net_bp_per_trade": np.nan,
                "candidate_minus_control_bp_per_trade": np.nan,
                "week_block_signflip_p_one_sided": np.nan,
            }
        )
    return trades, marked, controls, pairs, row


def _profile_variant(profile: DenseStartProfile) -> Variant:
    prefix = f"v13d_{profile.profile_id}"
    return Variant(
        name=prefix,
        long_column=f"{prefix}_long",
        short_column=f"{prefix}_short",
        score_column=f"{prefix}_score",
        gate_long_column=f"{prefix}_long_pass",
        gate_short_column=f"{prefix}_short_pass",
    )


def select_profile(
    frame: pd.DataFrame,
    profiles: list[DenseStartProfile],
) -> tuple[DenseStartProfile, pd.DataFrame, list[pd.DataFrame], list[pd.DataFrame]]:
    """Select once on 2023 halves using the preregistered lexicographic rule."""

    rows: list[dict[str, Any]] = []
    controls_all: list[pd.DataFrame] = []
    pairs_all: list[pd.DataFrame] = []
    for profile_index, profile in enumerate(profiles):
        variant = _profile_variant(profile)
        for period_index, period in enumerate(SELECTION_PERIODS):
            _, _, controls, pairs, row = run_variant_period(
                frame,
                variant,
                period,
                seed=20_260_821 + profile_index * 100 + period_index,
                stage="profile_selection_2023_only",
                with_controls=True,
            )
            row.update(
                {
                    "profile_id": profile.profile_id,
                    "profile_order": profile_index,
                    "min_pre_pairwise_crosses": profile.min_pre_pairwise_crosses,
                    "max_pre_bandwidth_atr_mean": profile.max_pre_bandwidth_atr_mean,
                    "min_current_alignment": profile.min_current_alignment,
                    "min_pre_cross_imbalance": profile.min_pre_cross_imbalance,
                    "min_slope_coherence": profile.min_slope_coherence,
                    "min_atr_release_ratio": profile.min_atr_release_ratio,
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
        qualified = bool(len(group) == 2 and group["trades"].ge(10).all())
        aggregates.append(
            {
                "profile_id": profile.profile_id,
                "profile_order": profile_index,
                "qualified_min_10_each_half": qualified,
                "worst_half_return_percent": float(group["return_percent"].min()),
                "worst_half_net_bp_per_trade": float(group["project_net_bp_per_trade"].min()),
                "worst_half_win_rate": float(group["win_rate"].min()),
                "max_half_drawdown_percent": float(group["max_drawdown_15m_percent"].max()),
            }
        )
    aggregate = pd.DataFrame(aggregates)
    qualified = aggregate.loc[aggregate["qualified_min_10_each_half"]].copy()
    if qualified.empty:
        raise RuntimeError("preregistered fail-closed rule: no profile has >=10 trades in each 2023 half")
    qualified = qualified.sort_values(
        [
            "worst_half_return_percent",
            "worst_half_net_bp_per_trade",
            "worst_half_win_rate",
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
    profiles: list[DenseStartProfile],
    selected: DenseStartProfile,
) -> pd.DataFrame:
    """Export all guarded raw V9 candidates without future outcome labels."""

    raw_long = frame["v9_long"].fillna(False).astype(bool)
    raw_short = frame["v9_short"].fillna(False).astype(bool)
    mask = frame["entry_allowed"].fillna(False).astype(bool) & (raw_long | raw_short)
    indices = np.flatnonzero(mask.to_numpy())
    rows = pd.DataFrame(
        {
            "signal_i": indices,
            "signal_time": pd.to_datetime(frame.iloc[indices]["open_time"], utc=True).to_numpy(),
            "side": np.where(raw_long.iloc[indices].to_numpy(), "long", "short"),
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
    pseudo = _attach_dense_features(frame, pseudo)
    for column in pseudo.columns:
        if column not in {"signal_i", "direction"}:
            rows[column] = pseudo[column].to_numpy()
    for profile in profiles:
        prefix = f"v13d_{profile.profile_id}"
        rows[f"passes_{profile.profile_id}"] = np.where(
            rows["side"].eq("long"),
            frame.iloc[indices][f"{prefix}_long_pass"].to_numpy(dtype=bool),
            frame.iloc[indices][f"{prefix}_short_pass"].to_numpy(dtype=bool),
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
    """Repeat deterministic matched assignment without changing the strategy."""

    rows: list[dict[str, Any]] = []
    period_by_name = {period.name: period for period in LOCKED_PERIODS}
    for period_name in sorted(PRIMARY_PERIOD_NAMES):
        selected_trades = trades.loc[
            trades["variant"].eq(selected_variant) & trades["period"].eq(period_name)
        ].copy()
        period = period_by_name[period_name]
        for seed_index in range(CONTROL_SENSITIVITY_SEEDS):
            seed_text = f"dense-sensitivity|{selected_variant}|{period_name}|{seed_index}"
            try:
                controls = build_matched_controls(
                    frame,
                    selected_trades,
                    period,
                    controls_per_trade=CONTROLS_PER_TRADE,
                    seed=seed_text,
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


def make_charts(
    search: pd.DataFrame,
    summary: pd.DataFrame,
    equity: pd.DataFrame,
    selected_variant: str,
) -> None:
    CHARTS.mkdir(parents=True, exist_ok=True)
    colors = {
        "v9_frozen_baseline": "#2563EB",
        "v12f_ma6_w8_full_gate": "#D97706",
        selected_variant: "#6B8E23",
    }
    labels = {
        "v9_frozen_baseline": "V9 baseline",
        "v12f_ma6_w8_full_gate": "V12F net-cross",
        selected_variant: "V13 dense-start",
    }

    selected_search = search.drop_duplicates("profile_id").sort_values("profile_order")
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))
    axes[0].bar(
        selected_search["profile_id"],
        selected_search["worst_half_return_percent"],
        color=["#6B8E23" if value else "#94A3B8" for value in selected_search["selected"]],
    )
    axes[0].axhline(0.0, color="#334155", linewidth=0.8)
    axes[0].set_title("2023 selection: worst-half return")
    axes[0].set_ylabel("Compounded return (%)")
    axes[1].plot(
        selected_search["profile_id"],
        selected_search["worst_half_net_bp_per_trade"],
        marker="o",
        color="#2563EB",
        label="worst-half net bp/trade",
    )
    axes[1].axhline(0.0, color="#334155", linewidth=0.8)
    axes[1].set_title("Strictness robustness")
    axes[1].set_ylabel("bp / trade after 20bp cost")
    axes[1].grid(alpha=0.2)
    fig.suptitle("Preregistered dense-start profiles (selection rows only)")
    fig.tight_layout()
    fig.savefig(CHARTS / "dense_start_profile_selection.png", dpi=170)
    plt.close(fig)

    periods = ["discovery_2023", "confirmation_2024", "final_preholdout_2025_202602"]
    metrics = [
        ("return_percent", "Return (%)", False),
        ("max_drawdown_15m_percent", "Max drawdown (%)", False),
        ("win_rate", "Net win rate", True),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.5))
    width = 0.24
    x = np.arange(len(periods))
    variants = ["v9_frozen_baseline", "v12f_ma6_w8_full_gate", selected_variant]
    for axis, (column, title, as_percent) in zip(axes, metrics):
        for offset, variant in enumerate(variants):
            values = []
            for period in periods:
                row = summary.loc[
                    summary["variant"].eq(variant) & summary["period"].eq(period)
                ]
                value = float(row.iloc[0][column]) if not row.empty else np.nan
                values.append(value * 100.0 if as_percent else value)
            axis.bar(
                x + (offset - 1) * width,
                values,
                width,
                label=labels[variant],
                color=colors[variant],
            )
        axis.set_xticks(x, ["2023", "2024 OOS", "final pre-HO"], rotation=18)
        axis.set_title(title)
        axis.axhline(0.0, color="#334155", linewidth=0.7)
        axis.grid(axis="y", alpha=0.2)
    axes[2].legend(loc="best", fontsize=8)
    fig.suptitle("Locked 15-minute strategy comparison (same risk, stops and cost)")
    fig.tight_layout()
    fig.savefig(CHARTS / "dense_start_locked_comparison.png", dpi=170)
    plt.close(fig)

    fig, axes = plt.subplots(1, 3, figsize=(14, 4.4), sharey=False)
    for axis, period in zip(axes, periods):
        subset = equity.loc[equity["period"].eq(period)]
        for variant in variants:
            group = subset.loc[subset["variant"].eq(variant)].sort_values("open_time")
            if group.empty:
                continue
            group = group.drop_duplicates("open_time", keep="last")
            axis.plot(
                pd.to_datetime(group["open_time"], utc=True),
                group["normalized_equity"],
                label=labels[variant],
                color=colors[variant],
                linewidth=1.2,
            )
        axis.set_title(period)
        axis.set_ylabel("Normalized equity")
        axis.grid(alpha=0.2)
        axis.tick_params(axis="x", rotation=25)
    axes[-1].legend(loc="best", fontsize=8)
    fig.suptitle("Marked equity; 1% risk and 20bp round-trip cost")
    fig.tight_layout()
    fig.savefig(CHARTS / "dense_start_equity_by_period.png", dpi=170)
    plt.close(fig)


def main() -> None:
    prereg, profiles = load_preregistration()
    config = load_config()
    raw, quality = load_research_frame(config)
    times = pd.to_datetime(raw["open_time"], utc=True)
    if quality["holdout_rows_read"] != 0:
        raise RuntimeError("holdout row reached dense-start research")
    if times.max() >= SAFE_END or SAFE_END >= HOLDOUT_START:
        raise RuntimeError("safe-end/holdout time contract failed")

    frame = add_six_ma_dense_start_features(build_v12_feature_frame(raw))
    frame = add_profile_columns(frame, profiles)
    selected, search, selection_controls, selection_pairs = select_profile(frame, profiles)
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
        _profile_variant(selected),
    )
    all_trades: list[pd.DataFrame] = []
    all_equity: list[pd.DataFrame] = []
    all_controls: list[pd.DataFrame] = selection_controls.copy()
    all_pairs: list[pd.DataFrame] = selection_pairs.copy()
    result_rows: list[dict[str, Any]] = []
    for variant_index, variant in enumerate(variants):
        for period_index, period in enumerate(LOCKED_PERIODS):
            trades, marked, controls, pairs, row = run_variant_period(
                frame,
                variant,
                period,
                seed=20_261_000 + variant_index * 100 + period_index,
                stage=(
                    "locked_confirmation"
                    if period.name.startswith("2024") or period.name == "confirmation_2024"
                    else "locked_description"
                ),
                with_controls=True,
            )
            result_rows.append(row)
            if not trades.empty:
                all_trades.append(trades)
            if not marked.empty:
                all_equity.append(marked)
            if not controls.empty:
                all_controls.append(controls)
            if not pairs.empty:
                all_pairs.append(pairs)

    result = pd.DataFrame(result_rows)
    by_key = {(row["variant"], row["period"]): row for row in result_rows}
    for row in result_rows:
        baseline = by_key[("v9_frozen_baseline", row["period"])]
        v12 = by_key[("v12f_ma6_w8_full_gate", row["period"])]
        row["return_delta_vs_v9_percentage_points"] = float(
            row["return_percent"] - baseline["return_percent"]
        )
        row["return_delta_vs_v12f_percentage_points"] = float(
            row["return_percent"] - v12["return_percent"]
        )
        row["win_rate_delta_vs_v12f_percentage_points"] = float(
            (row["win_rate"] - v12["win_rate"]) * 100.0
        )
        row["early_stop_rate_delta_vs_v12f_percentage_points"] = float(
            (row["stop_within_24h_rate"] - v12["stop_within_24h_rate"]) * 100.0
        )
    result = pd.DataFrame(result_rows)

    trades_output = _concat(all_trades)
    equity_output = _concat(all_equity)
    controls_output = _concat(all_controls)
    pairs_output = _concat(all_pairs)
    feature_rows = export_feature_rows(frame, profiles, selected)
    sensitivity = control_sensitivity(frame, trades_output, f"v13d_{selected.profile_id}")
    make_charts(search, result, equity_output, f"v13d_{selected.profile_id}")

    selected_primary = result.loc[
        result["variant"].eq(f"v13d_{selected.profile_id}")
        & result["period"].isin(PRIMARY_PERIOD_NAMES)
    ]
    acceptance = {
        "economic_gate_all_primary_top_decile_positive": bool(
            selected_primary["primary_score_top_decile_net_bp_per_trade"].gt(0.0).all()
        ),
        "permutation_p_lt_0p01_all_primary": bool(
            selected_primary["primary_score_top_decile_permutation_p"].lt(0.01).all()
        ),
        "matched_control_excess_positive_all_primary": bool(
            selected_primary["candidate_minus_control_bp_per_trade"].gt(0.0).all()
        ),
    }
    acceptance["passes_preregistered_economic_gate"] = bool(all(acceptance.values()))

    payload = {
        "artifact": "ETH 15m V13 causal dense-start preregistered preholdout study",
        "selected_profile": selected.profile_id,
        "selection_rows_only": [period.name for period in SELECTION_PERIODS],
        "locked_evaluation_order_respected": True,
        "data_quality": quality,
        "frozen_execution_contract": {
            "bar_minutes": 15,
            "round_trip_cost": ROUND_TRIP_COST,
            "atr_mult": 4.0,
            "max_sl_percent": 3.0,
            "break_even_trigger_percent": 1.5,
            "break_even_offset_percent": 0.1,
            "risk_per_trade_percent": 1.0,
            "max_leverage": 13.0,
            "barrier_changed": False,
        },
        "causal_feature_contract": prereg["causal_sequence"],
        "profile_selection": search.to_dict(orient="records"),
        "results": result_rows,
        "acceptance": acceptance,
        "matched_control": {
            "contract": "ETH x UTC month x HK 6h x previous-month ATR quintile x copied horizon",
            "controls_per_trade": CONTROLS_PER_TRADE,
            "week_signflip_resamples": SIGNFLIP_RESAMPLES,
            "assignment_sensitivity_seeds_per_primary_period": CONTROL_SENSITIVITY_SEEDS,
        },
        "ranking": {
            "primary_v13_score": "equal-weight density + compression + direction + release diagnostic",
            "single_feature_baseline": "negative pre-rope mean bandwidth/ATR",
            "top_fraction": 0.10,
            "permutations": PERMUTATIONS,
            "acceptance_p": 0.01,
        },
        "pine": pine_manifest,
        "code_provenance": {
            "git_commit_at_run": current_commit(),
            "backtest_script_sha256": _sha256(Path(__file__)),
            "feature_module_sha256": _sha256(
                PROJECT / "yoyo/layers/l2_judgment/pine_dense_start.py"
            ),
            "pine_generator_sha256": _sha256(
                PROJECT / "scripts/generate_pine_eth_15m_dense_start.py"
            ),
            "execution_engine_sha256": _sha256(
                PROJECT / "yoyo/layers/l3_backtest/pine_allin_v7.py"
            ),
        },
        "outputs": {
            "profile_search": str(PROFILE_SEARCH_OUTPUT.relative_to(PROJECT)),
            "trades": str(TRADES_OUTPUT.relative_to(PROJECT)),
            "controls": str(CONTROLS_OUTPUT.relative_to(PROJECT)),
            "pairs": str(PAIRS_OUTPUT.relative_to(PROJECT)),
            "control_sensitivity": str(CONTROL_SENSITIVITY_OUTPUT.relative_to(PROJECT)),
            "feature_rows": str(FEATURE_ROWS_OUTPUT.relative_to(PROJECT)),
            "equity": str(EQUITY_OUTPUT.relative_to(PROJECT)),
            "pine": str(pine_path.relative_to(PROJECT)),
        },
        "holdout_rows_read": 0,
        "model_trained_or_scored": False,
        "official_tradingview_parity_passed": False,
        "training_eligible": False,
        "forward_eligible": False,
        "production_eligible": False,
    }

    RESULTS.mkdir(parents=True, exist_ok=True)
    search.to_csv(PROFILE_SEARCH_OUTPUT, index=False)
    trades_output.to_csv(TRADES_OUTPUT, index=False)
    controls_output.to_csv(CONTROLS_OUTPUT, index=False)
    pairs_output.to_csv(PAIRS_OUTPUT, index=False)
    sensitivity.to_csv(CONTROL_SENSITIVITY_OUTPUT, index=False)
    feature_rows.to_csv(FEATURE_ROWS_OUTPUT, index=False)
    equity_output.to_csv(EQUITY_OUTPUT, index=False)
    SUMMARY_OUTPUT.write_text(
        json.dumps(_json_safe(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"selected_profile={selected.profile_id}")
    print(
        result.loc[
            result["period"].isin(PRIMARY_PERIOD_NAMES),
            [
                "variant",
                "period",
                "trades",
                "return_percent",
                "max_drawdown_15m_percent",
                "win_rate",
                "project_net_bp_per_trade",
                "candidate_minus_control_bp_per_trade",
            ],
        ].to_string(index=False)
    )
    print(f"summary={SUMMARY_OUTPUT}")
    print("holdout_rows_read=0")


if __name__ == "__main__":
    main()
