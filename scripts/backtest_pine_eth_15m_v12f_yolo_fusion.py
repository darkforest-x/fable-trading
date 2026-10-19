#!/usr/bin/env python3
"""Backtest delayed V12F/Grade-A YOLO fusion on the safe ETH 15m prefix.

The replay compares three frozen arms without training or threshold search:
V12F Pine-only, Pine intersected with every raw YOLO proposal, and Pine
intersected with structurally plus causally semantic-valid YOLO proposals.
Intersection means same symbol, same direction, and closed interval overlap;
exact start-bar equality is not required.

For a fused event, ``combined_available_at`` is the close of the last bar that
the detector consumed.  The decision index is that completed bar and the fill
is the simultaneous next-bar open.  ATR14, signal-close stop distance, risk
sizing inputs, calendar/volatility eligibility, and score are therefore read
from the delayed decision bar, never from the earlier Pine bar or a future bar.
The frozen V12F execution keeps ATR4 capped at 3%, 1% equity risk, 13x maximum
leverage, 0.10% commission per fill, confirmed-bar break-even, reversal, skip,
and no fixed take profit.

Inputs: safe-prefix OHLCV plus the SHA-pinned fusion ledgers emitted by
``run_pine_eth_15m_v12f_yolo_fusion.py``.  Outputs are candidate, trade,
equity, matched-control, matched-pair, metric, and JSON summary ledgers.  This
script never reads a holdout row, trains, promotes, deploys, mutates ACTIVE or
forward state, or places an order.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import itertools
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from scripts.backtest_pine_eth_15m_v12_preholdout import build_v12_feature_frame
from scripts.research_pine_eth_15m import (
    Period,
    build_matched_controls,
    pair_controls,
    sha256_bounded_frame,
    summarize,
    week_bootstrap_ci,
)
from scripts.run_pine_eth_15m_v12f import (
    BAR,
    HOLDOUT_START,
    INITIAL_CAPITAL,
    PINE_SHA256,
    ROUND_TRIP_COST,
    build_signal_ledger,
    execution_parameters,
    file_sha256,
    signal_parameters,
    strategy_arm,
    validate_15m_frame,
    validate_frozen_pine,
    validate_period,
)
from scripts.run_pine_eth_15m_v12f_yolo_fusion import (
    GATE_PREREG_SHA256,
    MODEL_KEY,
    MODEL_SHA256,
    RENDERER_SHA256,
    SEMANTIC_GATE_SHA256,
    fuse_signal_intervals,
    validate_frozen_detector,
    validate_fusion_ledgers,
)
from yoyo.evaluation.permutation import permutation_test
from yoyo.layers.l3_backtest.pine_allin_v7 import (
    auc_from_scores,
    load_development_frame,
    simulate_symbol,
)

DEFAULT_START = pd.Timestamp("2025-01-01T00:00:00Z")
DEFAULT_END = pd.Timestamp("2026-03-01T00:00:00Z")
EXPECTED_BOUNDED_OHLCV_SHA256 = (
    "17f091b5e9b88f35feb5560c5a774a9277a21b64444d6ee2ea963cfacfc09159"
)
EXPECTED_FUSION_HASHES = {
    "pine_signals.csv": "cd4b167536356d7ec881b83a151399796016a8d8485578b641d500c1e128a8c4",
    "model_proposals.csv": "0b10d97284c404ddcb73957ae914760aac06125f5624637037dcef6cb4cbf8ef",
    "fused_signals.csv": "1d2413b209990f6deb977e2d326a8f645d131db67c835f1c10e75f603ad4a4fe",
    "summary.json": "496a48410e885beb4d76a28ad903daa6cb401dcaa7a574dd7ea8086243ec3748",
}
MODEL_STAGES = ("raw", "semantic")
CONTROLS_PER_TRADE = 3
PERMUTATIONS = 10_000
BOOTSTRAPS = 20_000
MONTE_CARLO_SIGNFLIPS = 50_000
SEED = 20260903
OUTPUT_NAMES = (
    "candidate_events.csv",
    "trades.csv",
    "equity.csv",
    "matched_controls.csv",
    "matched_pairs.csv",
    "metrics.csv",
    "summary.json",
)


class BacktestError(RuntimeError):
    """Fail closed on source, causality, matching, or evaluation drift."""


@dataclass(frozen=True)
class ArmSpec:
    """One frozen comparison arm."""

    name: str
    stage: str | None
    signal_columns: tuple[str, str, str]
    score_semantics: str


def _utc(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_convert("UTC") if stamp.tzinfo else stamp.tz_localize("UTC")


def _version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def _safe_number(value: object) -> float | None:
    result = float(value)
    return result if np.isfinite(result) else None


def validate_fusion_source(
    fusion_dir: Path,
    *,
    symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Load only the frozen pre-holdout fusion ledgers and validate identity."""

    if start != DEFAULT_START or end != DEFAULT_END:
        raise BacktestError(
            "this first economic replay is frozen to 2025-01-01..2026-03-01"
        )
    for name, expected in EXPECTED_FUSION_HASHES.items():
        path = fusion_dir / name
        if not path.is_file():
            raise FileNotFoundError(f"missing frozen fusion artifact: {path}")
        actual = file_sha256(path)
        if actual != expected:
            raise BacktestError(
                f"fusion artifact drifted: {name} expected={expected} actual={actual}"
            )

    pine = pd.read_csv(fusion_dir / "pine_signals.csv")
    proposals = pd.read_csv(fusion_dir / "model_proposals.csv")
    semantic = pd.read_csv(fusion_dir / "fused_signals.csv")
    summary = json.loads((fusion_dir / "summary.json").read_text(encoding="utf-8"))
    validate_fusion_ledgers(pine, proposals, semantic, stage="semantic")

    expected_period = {
        "start": start.isoformat(),
        "end_exclusive": end.isoformat(),
    }
    if summary.get("period") != expected_period:
        raise BacktestError("fusion summary period differs from the backtest period")
    if str(summary.get("symbol")) != symbol:
        raise BacktestError("fusion summary symbol differs from the backtest symbol")
    detector = summary.get("detector", {})
    identities = {
        "model_key": MODEL_KEY,
        "weights_sha256": MODEL_SHA256,
        "renderer_sha256": RENDERER_SHA256,
        "semantic_gate_sha256": SEMANTIC_GATE_SHA256,
        "morphology_prereg_sha256": GATE_PREREG_SHA256,
    }
    if any(detector.get(key) != value for key, value in identities.items()):
        raise BacktestError("fusion detector identities differ from frozen constants")
    if int(summary.get("data", {}).get("holdout_rows_read", -1)) != 0:
        raise BacktestError("fusion source reports a holdout read")
    if summary.get("safety", {}).get("preholdout_only") is not True:
        raise BacktestError("fusion source is not marked pre-holdout only")
    return pine, proposals, semantic, summary


def validate_regenerated_pine(
    expected: pd.DataFrame,
    actual: pd.DataFrame,
) -> None:
    """Require regenerated Pine event identities and causal times to match."""

    columns = (
        "signal_id",
        "symbol",
        "direction",
        "signal_i",
        "interval_start",
        "interval_end",
        "available_at",
    )
    left = expected.loc[:, columns].copy().reset_index(drop=True)
    right = actual.loc[:, columns].copy().reset_index(drop=True)
    for column in ("interval_start", "interval_end", "available_at"):
        left[column] = pd.to_datetime(left[column], utc=True)
        right[column] = pd.to_datetime(right[column], utc=True)
    try:
        pd.testing.assert_frame_equal(left, right, check_dtype=False)
    except AssertionError as exc:
        raise BacktestError("regenerated Pine ledger differs from frozen fusion input") from exc


def materialize_delayed_events(
    frame: pd.DataFrame,
    fused: pd.DataFrame,
    *,
    arm_name: str,
) -> pd.DataFrame:
    """Map fused availability to a completed decision bar and next-open fill.

    Columns used from ``frame`` are ``open_time``, ``close``, causal ATR14,
    ``atr_percent`` and ``entry_allowed`` at the decision row.  Each event uses
    no row later than ``combined_available_at - 15m`` for risk or eligibility.
    """

    columns = [
        "arm",
        "event_id",
        "source_pine_signal_id",
        "model_detection_id",
        "symbol",
        "direction",
        "original_pine_signal_i",
        "decision_i",
        "decision_bar_open",
        "combined_available_at",
        "entry_i",
        "entry_time",
        "delay_bars_from_pine",
        "overlap_seconds",
        "overlap_kind",
        "model_confidence",
        "model_confirmation_bars",
        "model_window_len",
        "decision_close",
        "decision_atr",
        "decision_atr_percent",
        "delayed_entry_allowed",
        "collapsed_same_bar_events",
    ]
    if fused.empty:
        return pd.DataFrame(columns=columns)
    times = pd.DatetimeIndex(pd.to_datetime(frame["open_time"], utc=True))
    if not times.is_unique:
        raise BacktestError("frame times must be unique")
    rows: list[dict[str, Any]] = []
    for item in fused.to_dict(orient="records"):
        available = _utc(item["combined_available_at"])
        entry_i = int(times.get_indexer([available])[0])
        if entry_i <= 0:
            raise BacktestError(
                f"combined availability is absent from the 15m grid: {available}"
            )
        decision_i = entry_i - 1
        decision_open = _utc(times[decision_i])
        if decision_open + BAR != available or _utc(times[entry_i]) != available:
            raise BacktestError("delayed decision/fill is not one contiguous 15m step")
        atr = float(frame.at[decision_i, "atr"])
        close = float(frame.at[decision_i, "close"])
        atr_percent = float(frame.at[decision_i, "atr_percent"])
        if not all(np.isfinite(value) for value in (atr, close, atr_percent)):
            raise BacktestError("delayed risk anchor contains a non-finite value")
        overlap_seconds = float(item["overlap_seconds"])
        rows.append(
            {
                "arm": arm_name,
                "event_id": f"{arm_name}|{item['fusion_id']}",
                "source_pine_signal_id": item["pine_signal_id"],
                "model_detection_id": item["model_detection_id"],
                "symbol": item["symbol"],
                "direction": item["direction"],
                "original_pine_signal_i": int(item["pine_signal_i"]),
                "decision_i": decision_i,
                "decision_bar_open": decision_open,
                "combined_available_at": available,
                "entry_i": entry_i,
                "entry_time": available,
                "delay_bars_from_pine": float(
                    item["availability_delay_bars_from_pine"]
                ),
                "overlap_seconds": overlap_seconds,
                "overlap_kind": (
                    "positive_duration" if overlap_seconds > 0.0 else "boundary_touch"
                ),
                "model_confidence": float(item["model_confidence"]),
                "model_confirmation_bars": int(item["model_confirmation_bars"]),
                "model_window_len": int(item["model_window_len"]),
                "decision_close": close,
                "decision_atr": atr,
                "decision_atr_percent": atr_percent,
                "delayed_entry_allowed": bool(frame.at[decision_i, "entry_allowed"]),
                "collapsed_same_bar_events": 1,
            }
        )
    events = pd.DataFrame(rows, columns=columns)
    retained: list[pd.Series] = []
    for _, group in events.groupby("decision_i", sort=True):
        if group["direction"].nunique() != 1:
            raise BacktestError("opposite fused directions share one delayed decision bar")
        chosen = group.sort_values(
            ["model_confidence", "model_detection_id", "event_id"],
            ascending=[False, True, True],
            kind="stable",
        ).iloc[0].copy()
        chosen["collapsed_same_bar_events"] = len(group)
        retained.append(chosen)
    result = pd.DataFrame(retained, columns=columns).reset_index(drop=True)
    if result["decision_i"].duplicated().any():
        raise BacktestError("delayed decision events are not unique after collapse")
    return result


def materialize_pine_events(
    frame: pd.DataFrame,
    pine: pd.DataFrame,
    *,
    arm_name: str,
) -> pd.DataFrame:
    """Represent Pine-only candidates in the common execution ledger schema."""

    rows: list[dict[str, Any]] = []
    for item in pine.to_dict(orient="records"):
        decision_i = int(item["signal_i"])
        rows.append(
            {
                "arm": arm_name,
                "event_id": f"{arm_name}|{item['signal_id']}",
                "source_pine_signal_id": item["signal_id"],
                "model_detection_id": None,
                "symbol": item["symbol"],
                "direction": item["direction"],
                "original_pine_signal_i": decision_i,
                "decision_i": decision_i,
                "decision_bar_open": _utc(frame.at[decision_i, "open_time"]),
                "combined_available_at": _utc(item["available_at"]),
                "entry_i": decision_i + 1,
                "entry_time": _utc(item["available_at"]),
                "delay_bars_from_pine": 0.0,
                "overlap_seconds": None,
                "overlap_kind": "pine_only",
                "model_confidence": None,
                "model_confirmation_bars": None,
                "model_window_len": None,
                "decision_close": float(frame.at[decision_i, "close"]),
                "decision_atr": float(frame.at[decision_i, "atr"]),
                "decision_atr_percent": float(frame.at[decision_i, "atr_percent"]),
                "delayed_entry_allowed": bool(frame.at[decision_i, "entry_allowed"]),
                "collapsed_same_bar_events": 1,
            }
        )
    return pd.DataFrame(rows)


def add_delayed_signal_columns(
    frame: pd.DataFrame,
    events: pd.DataFrame,
    *,
    prefix: str,
) -> tuple[str, str, str]:
    """Add sparse delayed signal and model-confidence columns to a frame."""

    long_col = f"{prefix}_long"
    short_col = f"{prefix}_short"
    score_col = f"{prefix}_score"
    frame[long_col] = False
    frame[short_col] = False
    frame[score_col] = 0.0
    for event in events.itertuples(index=False):
        index = int(event.decision_i)
        direction_col = long_col if str(event.direction) == "long" else short_col
        if bool(frame.at[index, long_col]) or bool(frame.at[index, short_col]):
            raise BacktestError("more than one delayed direction survived at a decision bar")
        frame.at[index, direction_col] = True
        frame.at[index, score_col] = float(event.model_confidence)
    return long_col, short_col, score_col


def attach_event_metadata(
    trades: pd.DataFrame,
    events: pd.DataFrame,
    *,
    arm_name: str,
) -> pd.DataFrame:
    """Join each executed state transition back to its unique source event."""

    if trades.empty:
        return trades.copy()
    metadata = events.rename(columns={"direction": "event_direction"}).copy()
    result = trades.merge(
        metadata,
        left_on=["signal_i", "direction"],
        right_on=["decision_i", "event_direction"],
        how="left",
        validate="many_to_one",
        suffixes=("", "_event"),
    )
    if result["event_id"].isna().any():
        raise BacktestError(f"executed trade lost its source event in {arm_name}")
    result["arm"] = arm_name
    result["trade_id"] = [
        f"{arm_name}|{int(row.signal_i)}|{int(row.entry_i)}|{row.direction}"
        for row in result.itertuples(index=False)
    ]
    if result["trade_id"].duplicated().any():
        raise BacktestError(f"trade identities are not unique in {arm_name}")
    return result


def ranking_metrics(trades: pd.DataFrame, *, seed: int) -> dict[str, Any]:
    """Evaluate the arm's frozen score without treating it as pool evidence."""

    if len(trades) < 2:
        return {
            "auc_positive_trade": None,
            "top_decile_trades": len(trades),
            "top_decile_gross_bp_per_trade": None,
            "top_decile_net_bp_per_trade": None,
            "top_decile_win_rate": None,
            "top_decile_permutation_p_one_sided": None,
            "reason": "fewer than two executed trades",
        }
    scores = trades["score"].to_numpy(dtype=float)
    outcomes = trades["project_net_return"].to_numpy(dtype=float)
    labels = outcomes > 0.0
    auc = (
        float(auc_from_scores(scores, labels))
        if bool(labels.any()) and bool((~labels).any())
        else None
    )
    k = max(1, int(np.ceil(len(trades) * 0.10)))
    indices = np.argsort(scores, kind="stable")[::-1][:k]
    top = trades.iloc[indices]
    result = permutation_test(
        scores,
        outcomes,
        n_permutations=PERMUTATIONS,
        alternative="greater",
        seed=seed,
    )
    return {
        "auc_positive_trade": auc,
        "top_decile_trades": k,
        "top_decile_gross_bp_per_trade": float(
            top["gross_return"].mean() * 10_000.0
        ),
        "top_decile_net_bp_per_trade": float(
            top["project_net_return"].mean() * 10_000.0
        ),
        "top_decile_win_rate": float(top["project_net_return"].gt(0.0).mean()),
        "top_decile_permutation_p_one_sided": float(result.p_value),
        "permutations": PERMUTATIONS,
    }


def blocked_signflip(
    pairs: pd.DataFrame,
    *,
    seed: int,
) -> dict[str, Any]:
    """Test weekly mean candidate-minus-control returns with intact weeks."""

    if pairs.empty:
        raise BacktestError("matched-pair ledger is empty")
    data = pairs.copy()
    data["utc_week"] = (
        pd.to_datetime(data["candidate_entry_time"], utc=True)
        .dt.tz_localize(None)
        .dt.to_period("W-SUN")
        .astype(str)
    )
    values = (
        data.groupby("utc_week", sort=True)["excess_return"].mean().to_numpy(float)
    )
    observed = float(values.mean())
    if len(values) <= 20:
        signs = np.asarray(list(itertools.product((-1.0, 1.0), repeat=len(values))))
        null = (signs * values).mean(axis=1)
        p_value = float(np.mean(null >= observed))
        method = "exact"
    else:
        rng = np.random.default_rng(seed)
        signs = rng.choice(
            np.asarray((-1.0, 1.0)),
            size=(MONTE_CARLO_SIGNFLIPS, len(values)),
        )
        null = (signs * values).mean(axis=1)
        p_value = float((1 + np.sum(null >= observed)) / (MONTE_CARLO_SIGNFLIPS + 1))
        method = "monte_carlo_plus_one"
    return {
        "method": method,
        "statistic_mean_excess_bp": observed * 10_000.0,
        "p_value_one_sided": p_value,
        "utc_week_blocks": len(values),
        "assignments_or_resamples": len(null),
        "null_mean_bp": float(null.mean() * 10_000.0),
        "null_sd_bp": float(null.std(ddof=0) * 10_000.0),
    }


def holm_adjust(values: Sequence[float | None]) -> list[float | None]:
    """Return family-wise Holm-adjusted p-values, preserving missing entries."""

    result: list[float | None] = [None] * len(values)
    present = [(index, float(value)) for index, value in enumerate(values) if value is not None]
    present.sort(key=lambda item: item[1])
    running = 0.0
    total = len(present)
    for rank, (index, value) in enumerate(present):
        running = max(running, min(1.0, (total - rank) * value))
        result[index] = running
    return result


def evaluate_arm(
    frame: pd.DataFrame,
    events: pd.DataFrame,
    spec: ArmSpec,
    *,
    period: Period,
    seed: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Run one stateful arm, exact matched controls, and uncertainty metrics."""

    trades, equity = simulate_symbol(
        frame,
        symbol="ETH_USDT_SWAP",
        arm=replace(strategy_arm(), name=spec.name),
        start=period.start,
        end=period.end,
        params=signal_parameters(),
        round_trip_cost=ROUND_TRIP_COST,
        initial_capital=INITIAL_CAPITAL,
        execution=execution_parameters(),
        signal_columns=spec.signal_columns,
    )
    trades = attach_event_metadata(trades, events, arm_name=spec.name)
    if not equity.empty:
        equity = equity.copy()
        equity["arm"] = spec.name
    base = summarize(
        trades,
        equity,
        variant=spec.name,
        period=period.name,
        risk_percent=strategy_arm().risk_per_trade_percent,
    )
    controls = build_matched_controls(
        frame,
        trades,
        period,
        controls_per_trade=CONTROLS_PER_TRADE,
        seed=f"v12f-yolo-fusion|{spec.name}|{SEED}",
        params=signal_parameters(),
        take_profit_percent=execution_parameters().take_profit_percent,
        take_profit_distance_basis=execution_parameters().take_profit_distance_basis,
    )
    pairs = pair_controls(trades, controls)
    controls = controls.assign(arm=spec.name)
    pairs = pairs.assign(arm=spec.name)
    signflip = blocked_signflip(pairs, seed=seed)
    absolute = trades[["entry_time", "project_net_return"]].rename(
        columns={
            "entry_time": "candidate_entry_time",
            "project_net_return": "absolute_return",
        }
    )
    absolute_ci = week_bootstrap_ci(
        absolute,
        "absolute_return",
        n_resamples=BOOTSTRAPS,
        seed=seed + 10_000,
    )
    excess_ci = week_bootstrap_ci(
        pairs,
        "excess_return",
        n_resamples=BOOTSTRAPS,
        seed=seed + 20_000,
    )
    delayed_allowed = int(events["delayed_entry_allowed"].astype(bool).sum())
    metrics = {
        **{key: _safe_number(value) if isinstance(value, float) else value for key, value in base.items()},
        "candidate_events": len(events),
        "delayed_entry_allowed_events": delayed_allowed,
        "delayed_entry_rejected_events": int(len(events) - delayed_allowed),
        "executed_trade_fraction_of_candidates": (
            float(len(trades) / len(events)) if len(events) else None
        ),
        "score_semantics": spec.score_semantics,
        "ranking": ranking_metrics(trades, seed=seed),
        "matched_control": {
            "controls_per_trade": CONTROLS_PER_TRADE,
            "control_rows": len(controls),
            "control_net_bp_per_trade": float(
                pairs["control_mean_project_net"].mean() * 10_000.0
            ),
            "candidate_minus_control_bp_per_trade": float(
                pairs["excess_return"].mean() * 10_000.0
            ),
            "week_signflip": signflip,
            "week_bootstrap_ci95_bp": excess_ci,
        },
        "absolute_week_bootstrap_ci95_bp": absolute_ci,
    }
    return trades, equity, controls, pairs, metrics


def metrics_frame(metrics: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    """Flatten the headline metrics into a human-auditable comparison table."""

    rows = []
    for item in metrics:
        ranking = item["ranking"]
        control = item["matched_control"]
        rows.append(
            {
                "arm": item["variant"],
                "candidate_events": item["candidate_events"],
                "delayed_entry_allowed_events": item[
                    "delayed_entry_allowed_events"
                ],
                "trades": item["trades"],
                "return_percent": item["return_percent"],
                "max_drawdown_15m_percent": item["max_drawdown_15m_percent"],
                "project_net_bp_per_trade": item["project_net_bp_per_trade"],
                "win_rate": item["win_rate"],
                "monetary_profit_factor": item["monetary_profit_factor"],
                "auc_positive_trade": ranking["auc_positive_trade"],
                "top_decile_trades": ranking["top_decile_trades"],
                "top_decile_net_bp_per_trade": ranking[
                    "top_decile_net_bp_per_trade"
                ],
                "top_decile_permutation_p_one_sided": ranking[
                    "top_decile_permutation_p_one_sided"
                ],
                "matched_control_net_bp_per_trade": control[
                    "control_net_bp_per_trade"
                ],
                "candidate_minus_control_bp_per_trade": control[
                    "candidate_minus_control_bp_per_trade"
                ],
                "matched_week_signflip_p_one_sided": control["week_signflip"][
                    "p_value_one_sided"
                ],
            }
        )
    return pd.DataFrame(rows)


def run(
    *,
    input_path: Path,
    fusion_dir: Path,
    output_dir: Path,
    symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[str, Any]:
    """Run the frozen three-arm safe-prefix economic comparison."""

    validate_frozen_pine()
    validate_period(start, end)
    validate_frozen_detector()
    existing = [output_dir / name for name in OUTPUT_NAMES if (output_dir / name).exists()]
    if existing:
        raise FileExistsError(f"refusing to overwrite existing outputs: {existing}")
    pine_saved, proposals, _semantic_saved, fusion_summary = validate_fusion_source(
        fusion_dir,
        symbol=symbol,
        start=start,
        end=end,
    )
    raw = load_development_frame(
        input_path,
        safe_end=end,
        holdout_start=HOLDOUT_START,
    )
    validate_15m_frame(raw)
    bounded_hash = sha256_bounded_frame(raw)
    if bounded_hash != EXPECTED_BOUNDED_OHLCV_SHA256:
        raise BacktestError(
            "bounded OHLCV prefix drifted: "
            f"expected={EXPECTED_BOUNDED_OHLCV_SHA256} actual={bounded_hash}"
        )
    frame = build_v12_feature_frame(raw)
    pine = build_signal_ledger(frame, symbol=symbol, start=start, end=end)
    validate_regenerated_pine(pine_saved, pine)

    period = Period("safe_prefix_2025_202602", start, end)
    all_events: list[pd.DataFrame] = []
    specs: list[ArmSpec] = []
    pine_spec = ArmSpec(
        name="pine_v12f",
        stage=None,
        signal_columns=("v12f_long", "v12f_short", "v9_score"),
        score_semantics="absolute Pine oscillator",
    )
    pine_events = materialize_pine_events(frame, pine, arm_name=pine_spec.name)
    all_events.append(pine_events)
    specs.append(pine_spec)
    events_by_arm = {pine_spec.name: pine_events}

    for stage in MODEL_STAGES:
        fused = fuse_signal_intervals(pine_saved, proposals, stage=stage)
        validate_fusion_ledgers(pine_saved, proposals, fused, stage=stage)
        name = f"pine_yolo_{stage}_delayed"
        events = materialize_delayed_events(frame, fused, arm_name=name)
        signal_columns = add_delayed_signal_columns(frame, events, prefix=name)
        spec = ArmSpec(
            name=name,
            stage=stage,
            signal_columns=signal_columns,
            score_semantics="Grade-A YOLO confidence",
        )
        all_events.append(events)
        specs.append(spec)
        events_by_arm[name] = events

    trade_parts: list[pd.DataFrame] = []
    equity_parts: list[pd.DataFrame] = []
    control_parts: list[pd.DataFrame] = []
    pair_parts: list[pd.DataFrame] = []
    metric_rows: list[dict[str, Any]] = []
    for offset, spec in enumerate(specs):
        trades, equity, controls, pairs, metrics = evaluate_arm(
            frame,
            events_by_arm[spec.name],
            spec,
            period=period,
            seed=SEED + offset,
        )
        trade_parts.append(trades)
        equity_parts.append(equity)
        control_parts.append(controls)
        pair_parts.append(pairs)
        metric_rows.append(metrics)

    model_metrics = [row for row in metric_rows if row["variant"] != "pine_v12f"]
    matched_adjusted = holm_adjust(
        [
            row["matched_control"]["week_signflip"]["p_value_one_sided"]
            for row in model_metrics
        ]
    )
    ranking_adjusted = holm_adjust(
        [row["ranking"]["top_decile_permutation_p_one_sided"] for row in model_metrics]
    )
    for row, matched_p, ranking_p in zip(
        model_metrics, matched_adjusted, ranking_adjusted
    ):
        row["matched_control"]["holm_p_across_model_arms"] = matched_p
        row["ranking"]["holm_p_across_model_arms"] = ranking_p
        row["economic_gate"] = {
            "top_decile_net_positive": (
                row["ranking"]["top_decile_net_bp_per_trade"] is not None
                and row["ranking"]["top_decile_net_bp_per_trade"] > 0.0
            ),
            "top_decile_permutation_holm_p_lt_0_01": (
                ranking_p is not None and ranking_p < 0.01
            ),
            "candidate_minus_control_positive": row["matched_control"][
                "candidate_minus_control_bp_per_trade"
            ]
            > 0.0,
            "matched_week_signflip_holm_p_lt_0_01": (
                matched_p is not None and matched_p < 0.01
            ),
        }
        row["economic_gate"]["passed"] = all(row["economic_gate"].values())

    candidates = pd.concat(all_events, ignore_index=True, sort=False)
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    equity = pd.concat(equity_parts, ignore_index=True, sort=False)
    controls = pd.concat(control_parts, ignore_index=True, sort=False)
    pairs = pd.concat(pair_parts, ignore_index=True, sort=False)
    table = metrics_frame(metric_rows)
    primary = next(
        row for row in metric_rows if row["variant"] == "pine_yolo_semantic_delayed"
    )
    summary = {
        "experiment": "Pine V12F x Grade-A YOLO delayed economic replay",
        "primary_arm": "pine_yolo_semantic_delayed",
        "comparators": ["pine_v12f", "pine_yolo_raw_delayed"],
        "symbol": symbol,
        "timeframe": "15m",
        "period": {"start": start.isoformat(), "end_exclusive": end.isoformat()},
        "source": {
            "bounded_ohlcv_sha256": bounded_hash,
            "bounded_rows": len(raw),
            "bounded_first_bar": _utc(raw["open_time"].iloc[0]).isoformat(),
            "bounded_last_bar": _utc(raw["open_time"].iloc[-1]).isoformat(),
            "pine_sha256": PINE_SHA256,
            "model_key": MODEL_KEY,
            "model_sha256": MODEL_SHA256,
            "fusion_artifact_sha256": EXPECTED_FUSION_HASHES,
            "fusion_summary": fusion_summary,
        },
        "contract": {
            "matching": "same symbol + same direction + closed interval overlap",
            "delayed_decision_bar": "combined_available_at - 15 minutes",
            "fill": "next bar open at combined_available_at",
            "risk_anchor": "delayed completed decision bar ATR14 and close",
            "eligibility": "re-evaluate frozen V12F calendar/volatility gate at delayed decision bar",
            "parameters": {
                "signal": asdict(signal_parameters()),
                "execution": {
                    **asdict(execution_parameters()),
                    "signal_bar_duration": "15min",
                },
                "arm": asdict(strategy_arm()),
                "round_trip_cost": ROUND_TRIP_COST,
            },
        },
        "statistics": {
            "matched_controls": (
                "same symbol, UTC month, Hong Kong 6h block, prior-month ATR quintile, "
                "direction, copied holding horizon, barriers, and cost"
            ),
            "controls_per_trade": CONTROLS_PER_TRADE,
            "block_unit": "UTC week",
            "signflip": (
                "exact for <=20 week blocks; otherwise deterministic 50,000-resample plus-one"
            ),
            "ranking_permutations": PERMUTATIONS,
            "week_bootstraps": BOOTSTRAPS,
            "multiple_comparisons": "Holm across the two model arms",
            "project_alpha": 0.01,
        },
        "arms": {row["variant"]: row for row in metric_rows},
        "verdict": (
            "pass_research_economic_gate"
            if primary["economic_gate"]["passed"]
            else "fail_research_economic_gate"
        ),
        "safety": {
            "holdout_rows_parsed_or_scored_by_backtest": 0,
            "full_file_hash_used_by_backtest": False,
            "preexisting_provenance_incident": (
                "A prior report command hashed the full source file and therefore read holdout "
                "bytes without parsing values or scoring a configuration; owner accounting pending."
            ),
            "training_or_fitting": False,
            "changes_active_frozen_forward_or_deployment": False,
            "places_orders": False,
            "production_eligible": False,
        },
        "runtime_versions": {
            name: _version(name)
            for name in ("numpy", "pandas", "scipy", "torch", "ultralytics")
        },
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    candidates.to_csv(output_dir / "candidate_events.csv", index=False)
    trades.to_csv(output_dir / "trades.csv", index=False)
    equity.to_csv(output_dir / "equity.csv", index=False)
    controls.to_csv(output_dir / "matched_controls.csv", index=False)
    pairs.to_csv(output_dir / "matched_pairs.csv", index=False)
    table.to_csv(output_dir / "metrics.csv", index=False)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--input", type=Path, required=True)
    result.add_argument("--fusion-dir", type=Path, required=True)
    result.add_argument("--output-dir", type=Path, required=True)
    result.add_argument("--symbol", default="ETH_USDT_SWAP")
    result.add_argument("--start", default=DEFAULT_START.isoformat())
    result.add_argument("--end-exclusive", default=DEFAULT_END.isoformat())
    return result


def main() -> int:
    args = parser().parse_args()
    summary = run(
        input_path=args.input,
        fusion_dir=args.fusion_dir,
        output_dir=args.output_dir,
        symbol=str(args.symbol),
        start=_utc(args.start),
        end=_utc(args.end_exclusive),
    )
    headline = {
        "experiment": summary["experiment"],
        "period": summary["period"],
        "verdict": summary["verdict"],
        "arms": summary["arms"],
        "safety": summary["safety"],
    }
    print(json.dumps(headline, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
