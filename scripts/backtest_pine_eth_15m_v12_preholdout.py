#!/usr/bin/env python3
"""Backtest isolated V12 Pine hypotheses on the holdout-safe ETH 15m prefix.

The script compares four independent arms: frozen V9, the causal six-MA W8
full-state gate, the same W8 factor as an entry-only gate, and the separately
selected TP30/ATR3 barrier arm.  It deliberately does not generate or score a
combined arm, because that would violate the repository's single-variable
experiment discipline.

Inputs are OKX ETH-USDT-SWAP 15-minute OHLCV rows strictly before
``2026-03-01T00:00:00Z``.  The loader fails closed before the repository
holdout at ``2026-05-04T00:00:00Z``.  All signal and W8 features at bar ``t``
use only close-derived SMA/EMA 20/60/120 values through ``t``; crosses use
``t`` and ``t-1`` and the W8 sum uses ``[t-7, t]``.  Future bars are consulted
only by the execution replay and matched-control outcomes.

The requested recent-half-year interval is recorded as 2026-02-21 through
2026-08-21 UTC, but only its safe overlap through 2026-03-01 is evaluated by
this script.  It never reads, hashes, charts or summarizes a holdout row.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
from typing import Any
import warnings

import numpy as np
import pandas as pd

from scripts.research_pine_eth_15m import (
    INITIAL_CAPITAL,
    Period,
    block_signflip,
    build_feature_frame,
    build_matched_controls,
    current_commit,
    exact_execution,
    load_config,
    load_research_frame,
    pair_controls,
    summarize,
)
from yoyo.datasets.ma_rope_filter import add_six_mas
from yoyo.evaluation.permutation import permutation_test
from yoyo.layers.l2_judgment.pine_cross_features import (
    add_six_ma_cross_count_features,
    side_aligned_six_ma_cross_frame,
)
from yoyo.layers.l3_backtest.pine_allin_v7 import (
    Arm,
    ExecutionParameters,
    SignalParameters,
    auc_from_scores,
    simulate_symbol,
)


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
RESULTS = EXPERIMENT / "results"
PINE_DIR = EXPERIMENT / "pine"
SUMMARY_OUTPUT = RESULTS / "optimized_pine_variants_preholdout.json"
TRADES_OUTPUT = RESULTS / "optimized_pine_variants_preholdout_trades.csv"
PRIMARY_TRADES_OUTPUT = RESULTS / "optimized_pine_variants_primary_trades.csv"
RECENT_SAFE_TRADES_OUTPUT = RESULTS / "optimized_pine_variants_recent_safe_trades.csv"
CONTROLS_OUTPUT = RESULTS / "optimized_pine_variants_preholdout_controls.csv"
PAIRS_OUTPUT = RESULTS / "optimized_pine_variants_preholdout_pairs.csv"

REQUESTED_RECENT_START = pd.Timestamp("2026-02-21T00:00:00Z")
REQUESTED_RECENT_END = pd.Timestamp("2026-08-21T00:00:00Z")
SAFE_END = pd.Timestamp("2026-03-01T00:00:00Z")
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
CONTROLS_PER_TRADE = 3
PERMUTATIONS = 10_000
SIGNFLIP_RESAMPLES = 20_000

PERIODS = (
    Period("discovery_2023", pd.Timestamp("2023-01-01T00:00:00Z"), pd.Timestamp("2024-01-01T00:00:00Z")),
    Period("confirmation_2024", pd.Timestamp("2024-01-01T00:00:00Z"), pd.Timestamp("2025-01-01T00:00:00Z")),
    Period("final_preholdout_2025_202602", pd.Timestamp("2025-01-01T00:00:00Z"), SAFE_END),
    Period("preholdout_2025_calendar", pd.Timestamp("2025-01-01T00:00:00Z"), pd.Timestamp("2026-01-01T00:00:00Z")),
    Period("preholdout_2026_jan_feb", pd.Timestamp("2026-01-01T00:00:00Z"), SAFE_END),
    Period("requested_half_year_safe_overlap", REQUESTED_RECENT_START, SAFE_END),
)


@dataclass(frozen=True)
class BacktestArm:
    """One isolated Pine hypothesis and its exact replay contract."""

    name: str
    pine_path: Path
    signal_columns: tuple[str, str, str]
    entry_gate_columns: tuple[str, str] | None
    params: SignalParameters
    execution: ExecutionParameters
    change_contract: str
    strict_single_variable: bool


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def build_v12_feature_frame(raw: pd.DataFrame) -> pd.DataFrame:
    """Add the causal W8 factor and both full-state gate signal columns.

    Columns used are OHLCV through the current row plus close-derived
    ``SMA/EMA 20/60/120``.  Each cross uses only current and previous MA
    values; W8 uses the current bar and seven preceding bars.  No future row
    is read.
    """

    frame = build_feature_frame(raw)
    frame = add_six_mas(frame)
    frame = add_six_ma_cross_count_features(frame, windows=(8,))
    long_aligned = side_aligned_six_ma_cross_frame(frame, window=8, side="long")
    short_aligned = side_aligned_six_ma_cross_frame(frame, window=8, side="short")
    six_ma_columns = ["sma20", "ema20", "sma60", "ema60", "sma120", "ema120"]
    frame["ma6_w8_ready"] = frame[six_ma_columns].notna().all(axis=1)
    frame["ma6_w8_long_pass"] = (
        frame["ma6_w8_ready"] & long_aligned["cross_imbalance"].ge(0)
    ).fillna(False)
    frame["ma6_w8_short_pass"] = (
        frame["ma6_w8_ready"] & short_aligned["cross_imbalance"].ge(0)
    ).fillna(False)

    # The historical full-state gate scored only guarded candidates. Raw
    # signals outside calendar/volatility guards still consume V9 cooldown.
    guarded = frame["entry_allowed"].fillna(False).astype(bool)
    frame["v12f_long"] = frame["v9_long"].fillna(False).astype(bool) & (
        ~guarded | frame["ma6_w8_long_pass"]
    )
    frame["v12f_short"] = frame["v9_short"].fillna(False).astype(bool) & (
        ~guarded | frame["ma6_w8_short_pass"]
    )
    return frame


def backtest_arms() -> tuple[BacktestArm, ...]:
    base_params = SignalParameters(osc_threshold=0.1)
    base_execution = exact_execution(equity_frequency=None)
    tbsl_params = replace(base_params, atr_mult=3.0, max_sl_percent=3.0)
    tbsl_execution = replace(
        base_execution,
        take_profit_percent=30.0,
        take_profit_distance_basis="signal_close",
        barrier_collision_policy="stop_first",
    )
    return (
        BacktestArm(
            name="v9_frozen_baseline",
            pine_path=PINE_DIR / "allin_eth_15m_v9_research.pine",
            signal_columns=("v9_long", "v9_short", "v9_score"),
            entry_gate_columns=None,
            params=base_params,
            execution=base_execution,
            change_contract="none (frozen comparator)",
            strict_single_variable=True,
        ),
        BacktestArm(
            name="v12f_ma6_w8_full_gate",
            pine_path=PINE_DIR / "allin_eth_15m_v12f_ma6_w8_full_gate_paper.pine",
            signal_columns=("v12f_long", "v12f_short", "v9_score"),
            entry_gate_columns=None,
            params=base_params,
            execution=base_execution,
            change_contract="MA6 W8 cross imbalance >= 0 gates the full guarded state transition",
            strict_single_variable=True,
        ),
        BacktestArm(
            name="v12e_ma6_w8_entry_only",
            pine_path=PINE_DIR / "allin_eth_15m_v12e_ma6_w8_entry_only_paper.pine",
            signal_columns=("v9_long", "v9_short", "v9_score"),
            entry_gate_columns=("ma6_w8_long_pass", "ma6_w8_short_pass"),
            params=base_params,
            execution=base_execution,
            change_contract="MA6 W8 filters opens only; rejected opposites still close",
            strict_single_variable=True,
        ),
        BacktestArm(
            name="v12t_tbsl_signal_close_ticks",
            pine_path=PINE_DIR / "allin_eth_15m_v12t_tbsl_paper.pine",
            signal_columns=("v9_long", "v9_short", "v9_score"),
            entry_gate_columns=None,
            params=tbsl_params,
            execution=tbsl_execution,
            change_contract="staged-selected TP30 plus ATR3 composite; stop cap remains 3%",
            strict_single_variable=False,
        ),
    )


def _signal_counts(frame: pd.DataFrame, arm: BacktestArm, period: Period) -> dict[str, int]:
    times = pd.to_datetime(frame["open_time"], utc=True)
    active = times.ge(period.start) & times.lt(period.end)
    guarded = frame["entry_allowed"].fillna(False).astype(bool)
    raw_long = frame["v9_long"].fillna(False).astype(bool)
    raw_short = frame["v9_short"].fillna(False).astype(bool)
    raw_guarded = active & guarded & (raw_long | raw_short)
    if arm.entry_gate_columns is not None or arm.name.startswith("v12f_"):
        accepted = active & guarded & (
            (raw_long & frame["ma6_w8_long_pass"].astype(bool))
            | (raw_short & frame["ma6_w8_short_pass"].astype(bool))
        )
    else:
        accepted = raw_guarded
    return {
        "raw_guarded_candidates": int(raw_guarded.sum()),
        "entry_gate_pass_candidates": int(accepted.sum()),
        "entry_gate_rejected_candidates": int((raw_guarded & ~accepted).sum()),
    }


def _ranking_metrics(trades: pd.DataFrame, *, seed: int) -> dict[str, Any]:
    if len(trades) < 2:
        return {
            "auc_positive_trade": np.nan,
            "top_decile_trades": int(len(trades)),
            "top_decile_gross_bp_per_trade": np.nan,
            "top_decile_net_bp_per_trade": np.nan,
            "top_decile_win_rate": np.nan,
            "top_decile_permutation_p": np.nan,
            "ranking_note": "fewer than two trades; ranking test not defined",
        }
    scores = trades["score"].to_numpy(dtype=float)
    outcomes = trades["project_net_return"].to_numpy(dtype=float)
    labels = outcomes > 0.0
    auc = auc_from_scores(scores, labels) if labels.any() and (~labels).any() else np.nan
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
        "auc_positive_trade": float(auc),
        "top_decile_trades": int(k),
        "top_decile_gross_bp_per_trade": float(top["gross_return"].mean() * 10_000.0),
        "top_decile_net_bp_per_trade": float(top["project_net_return"].mean() * 10_000.0),
        "top_decile_win_rate": float((top["project_net_return"] > 0.0).mean()),
        "top_decile_permutation_p": float(perm.p_value),
        "ranking_note": "oscillator magnitude ranks trades; ranking does not validate pool beta",
    }


def _matched_control_metrics(
    frame: pd.DataFrame,
    trades: pd.DataFrame,
    arm: BacktestArm,
    period: Period,
    *,
    seed: int,
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
            seed=f"pine-v12-preholdout|{arm.name}|{period.name}",
            params=arm.params,
            take_profit_percent=arm.execution.take_profit_percent,
            take_profit_distance_basis=arm.execution.take_profit_distance_basis,
        )
    except RuntimeError as exc:
        return pd.DataFrame(), pd.DataFrame(), {
            "matched_control_status": f"unavailable_fail_closed: {exc}",
            "matched_control_net_bp_per_trade": np.nan,
            "candidate_minus_control_bp_per_trade": np.nan,
            "week_block_signflip_p_one_sided": np.nan,
        }
    pairs = pair_controls(trades, controls)
    signflip = block_signflip(
        pairs,
        n_resamples=SIGNFLIP_RESAMPLES,
        seed=seed,
    )
    controls = controls.copy()
    pairs = pairs.copy()
    controls["arm"] = arm.name
    controls["period"] = period.name
    pairs["arm"] = arm.name
    pairs["period"] = period.name
    return controls, pairs, {
        "matched_control_status": "complete_exact_3_per_trade",
        "matched_control_net_bp_per_trade": float(
            pairs["control_mean_project_net"].mean() * 10_000.0
        ),
        "candidate_minus_control_bp_per_trade": float(
            pairs["excess_return"].mean() * 10_000.0
        ),
        "week_block_signflip_p_one_sided": float(signflip["p_value"]),
        "week_blocks": int(signflip["n_blocks"]),
    }


def run_arm_period(
    frame: pd.DataFrame,
    arm: BacktestArm,
    period: Period,
    *,
    seed: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    trades, marked = simulate_symbol(
        frame,
        symbol="ETH_USDT_SWAP",
        arm=_standard_arm(arm.name),
        start=period.start,
        end=period.end,
        params=arm.params,
        round_trip_cost=0.002,
        initial_capital=INITIAL_CAPITAL,
        execution=arm.execution,
        signal_columns=arm.signal_columns,
        entry_gate_columns=arm.entry_gate_columns,
    )
    if not trades.empty:
        trades = trades.copy()
        trades["variant"] = arm.name
        trades["period"] = period.name
        trades["trade_id"] = [
            f"{arm.name}|{period.name}|{int(row.signal_i)}|{int(row.entry_i)}|{row.direction}"
            for row in trades.itertuples(index=False)
        ]
    summary = summarize(
        trades,
        marked,
        variant=arm.name,
        period=period.name,
        risk_percent=1.0,
    )
    summary.update(_signal_counts(frame, arm, period))
    summary.update(_ranking_metrics(trades, seed=seed))
    controls, pairs, control_metrics = _matched_control_metrics(
        frame,
        trades,
        arm,
        period,
        seed=seed + 1_000,
    )
    summary.update(control_metrics)
    summary.update(
        {
            "change_contract": arm.change_contract,
            "strict_single_variable": arm.strict_single_variable,
            "pine_path": str(arm.pine_path.relative_to(PROJECT)),
            "pine_sha256": _sha256(arm.pine_path),
            "take_profit_percent": arm.execution.take_profit_percent,
            "take_profit_distance_basis": arm.execution.take_profit_distance_basis,
            "atr_mult": arm.params.atr_mult,
            "max_sl_percent": arm.params.max_sl_percent,
            "intrabar_barrier_collisions": int(
                trades.get("intrabar_barrier_collision", pd.Series(dtype=bool)).sum()
            ),
            "exit_reason_counts": (
                {str(k): int(v) for k, v in trades["exit_reason"].value_counts().items()}
                if not trades.empty
                else {}
            ),
        }
    )
    return trades, controls, pairs, summary


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not np.isfinite(float(value)) else float(value)
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    return value


def _concat_outputs(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Concatenate heterogeneous arm ledgers without pandas' known NA warning."""

    if not frames:
        return pd.DataFrame()
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="The behavior of DataFrame concatenation with empty or all-NA entries is deprecated",
            category=FutureWarning,
        )
        return pd.concat(frames, ignore_index=True)


def main() -> None:
    config = load_config()
    raw, quality = load_research_frame(config)
    if quality["holdout_rows_read"] != 0:
        raise RuntimeError("holdout row reached the preholdout V12 replay")
    if pd.to_datetime(raw["open_time"], utc=True).max() >= SAFE_END:
        raise RuntimeError("safe loader crossed its exclusive end")
    frame = build_v12_feature_frame(raw)

    all_trades: list[pd.DataFrame] = []
    all_controls: list[pd.DataFrame] = []
    all_pairs: list[pd.DataFrame] = []
    result_rows: list[dict[str, Any]] = []
    for arm_index, arm in enumerate(backtest_arms()):
        for period_index, period in enumerate(PERIODS):
            trades, controls, pairs, row = run_arm_period(
                frame,
                arm,
                period,
                seed=20_260_821 + arm_index * 100 + period_index,
            )
            result_rows.append(row)
            if not trades.empty:
                all_trades.append(trades)
            if not controls.empty:
                all_controls.append(controls)
            if not pairs.empty:
                all_pairs.append(pairs)

    by_key = {(row["variant"], row["period"]): row for row in result_rows}
    for row in result_rows:
        baseline = by_key[("v9_frozen_baseline", row["period"])]
        row["return_delta_vs_v9_percentage_points"] = (
            float(row["return_percent"] - baseline["return_percent"])
        )
        row["drawdown_delta_vs_v9_percentage_points"] = (
            None
            if row["max_drawdown_15m_percent"] is None
            or baseline["max_drawdown_15m_percent"] is None
            or not np.isfinite(float(row["max_drawdown_15m_percent"]))
            or not np.isfinite(float(baseline["max_drawdown_15m_percent"]))
            else float(
                row["max_drawdown_15m_percent"]
                - baseline["max_drawdown_15m_percent"]
            )
        )

    payload = {
        "artifact": "ETH 15m optimized Pine V12 isolated preholdout backtest",
        "status": "paper-only; full requested recent-half-year run awaits explicit holdout approval",
        "requested_recent_half_year": {
            "start_inclusive": REQUESTED_RECENT_START.isoformat(),
            "end_exclusive": REQUESTED_RECENT_END.isoformat(),
            "safe_overlap_evaluated": [
                REQUESTED_RECENT_START.isoformat(),
                SAFE_END.isoformat(),
            ],
            "unevaluated_gap_before_holdout": [
                SAFE_END.isoformat(),
                HOLDOUT_START.isoformat(),
            ],
            "holdout_segment_not_read": [
                HOLDOUT_START.isoformat(),
                REQUESTED_RECENT_END.isoformat(),
            ],
            "holdout_approval_required": True,
        },
        "frozen_contract": {
            "bar_minutes": 15,
            "venue_proxy": "OKX ETH-USDT-SWAP; not asserted identical to unspecified TradingView ETHUSDT.P",
            "round_trip_cost": 0.002,
            "risk_per_trade_percent": 1.0,
            "max_leverage": 13.0,
            "break_even": True,
            "cooldown": True,
            "reversal": True,
            "same_bar_collision": "stop_first",
        },
        "w8_contract": {
            "bundle": ["SMA20", "EMA20", "SMA60", "EMA60", "SMA120", "EMA120"],
            "directional_pairs": 12,
            "window_bars": 8,
            "threshold": 0,
            "future_bars": 0,
            "ready_gate": "all SMA/EMA 20/60/120 values must be non-null",
        },
        "data_quality": quality,
        "code_provenance": {
            "git_commit_at_run": current_commit(),
            "working_tree_execution": True,
            "backtest_script_sha256": _sha256(Path(__file__)),
            "pine_generator_sha256": _sha256(
                PROJECT / "scripts/generate_pine_eth_15m_optimized_variants.py"
            ),
            "execution_engine_sha256": _sha256(
                PROJECT / "yoyo/layers/l3_backtest/pine_allin_v7.py"
            ),
            "cross_feature_sha256": _sha256(
                PROJECT / "yoyo/layers/l2_judgment/pine_cross_features.py"
            ),
            "note": "content hashes, not git commit alone, identify the uncommitted research replay",
        },
        "periods": [
            {"name": period.name, "start": period.start.isoformat(), "end": period.end.isoformat()}
            for period in PERIODS
        ],
        "results": result_rows,
        "matched_control": {
            "contract": "ETH x UTC month x HK 6h x prior-month ATR quintile x copied horizon",
            "controls_per_trade": CONTROLS_PER_TRADE,
            "week_signflip_resamples": SIGNFLIP_RESAMPLES,
        },
        "ranking": {
            "score": "absolute V9 oscillator magnitude",
            "top_fraction": 0.10,
            "permutations": PERMUTATIONS,
            "acceptance_p": 0.01,
            "warning": "pool-internal ranking cannot replace matched random-entry controls",
        },
        "combined_variant_generated_or_tested": False,
        "model_trained_or_scored": False,
        "official_tradingview_parity_passed": False,
        "holdout_rows_read": 0,
        "training_eligible": False,
        "forward_eligible": False,
        "production_eligible": False,
    }

    RESULTS.mkdir(parents=True, exist_ok=True)
    trades_output = _concat_outputs(all_trades)
    controls_output = _concat_outputs(all_controls)
    pairs_output = _concat_outputs(all_pairs)
    trades_output.to_csv(TRADES_OUTPUT, index=False)
    primary_periods = {
        "discovery_2023",
        "confirmation_2024",
        "final_preholdout_2025_202602",
    }
    trades_output.loc[trades_output["period"].isin(primary_periods)].to_csv(
        PRIMARY_TRADES_OUTPUT,
        index=False,
    )
    trades_output.loc[
        trades_output["period"].eq("requested_half_year_safe_overlap")
    ].to_csv(RECENT_SAFE_TRADES_OUTPUT, index=False)
    controls_output.to_csv(CONTROLS_OUTPUT, index=False)
    pairs_output.to_csv(PAIRS_OUTPUT, index=False)
    normalized = _json_safe(payload)
    SUMMARY_OUTPUT.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    compact = pd.DataFrame(result_rows)[
        [
            "variant",
            "period",
            "trades",
            "return_percent",
            "max_drawdown_15m_percent",
            "win_rate",
            "project_net_bp_per_trade",
            "candidate_minus_control_bp_per_trade",
        ]
    ]
    print(compact.to_string(index=False))
    print(f"\nsummary={SUMMARY_OUTPUT}")
    print("holdout_rows_read=0")


if __name__ == "__main__":
    main()
