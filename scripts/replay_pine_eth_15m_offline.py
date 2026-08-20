#!/usr/bin/env python3
"""Rebuild the frozen ETH 15m V9 ledger in a dependency-light runtime.

This is a portability audit, not another optimization pass.  It reads only the
bounded development prefix ending before 2026-03-01, reconstructs the frozen
V9 causal signal from OHLCV columns available at decision bar ``t``, runs the
same stateful next-open execution engine, and reconciles every generated trade
against the canonical ledger.  Future bars are used only by the exit replay.

The script intentionally depends only on the standard library, NumPy, pandas,
and ``yoyo.layers.l3_backtest.pine_allin_v7`` so it can run inside a local,
network-disabled Linux container.  It does not inspect the project holdout,
fit or score a model, call TradingView, or touch production state.
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from yoyo.layers.l3_backtest.pine_allin_v7 import (
    Arm,
    ExecutionParameters,
    SignalParameters,
    add_indicators,
    load_development_frame,
    simulate_symbol,
)


EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
RESULTS = EXPERIMENT / "results"
CONFIG = EXPERIMENT / "config.json"
CANONICAL_TRADES = RESULTS / "trades.csv"
SAFE_END = pd.Timestamp("2026-03-01T00:00:00Z")
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
FINAL_START = pd.Timestamp("2025-01-01T00:00:00Z")
FINAL_SPLIT = "final_preholdout_2025_202602"
CANONICAL_VARIANT = "v9_locked"


def build_v9_frame(raw: pd.DataFrame) -> pd.DataFrame:
    """Add the exact causal V9 signal columns without importing L2 code.

    Inputs are OHLCV through each row.  The extra V9 feature is
    ``EMA200[t] / EMA200[t-12] - 1``; no future row is referenced.
    """

    params = SignalParameters(osc_threshold=0.1)
    out = add_indicators(raw, params)
    ema200 = out["close"].astype(float).ewm(span=200, adjust=False).mean()
    slope12 = ema200.pct_change(12)

    source = (out["high"].astype(float) + out["low"].astype(float)) / 2.0
    basis = source.rolling(
        params.osc_basis_len, min_periods=params.osc_basis_len
    ).mean()
    difference = source - basis
    percentile99 = difference.rolling(
        params.osc_percentile_len,
        min_periods=params.osc_percentile_len,
    ).quantile(params.osc_percentile / 100.0, interpolation="linear")
    percentile_safe = percentile99.gt(0.0)

    out["v9_long"] = (
        out["v7_long"] & percentile_safe & slope12.gt(0.0)
    ).fillna(False)
    out["v9_short"] = (
        out["v7_short"] & percentile_safe & slope12.lt(0.0)
    ).fillna(False)
    out["v9_score"] = out["osc"].abs().fillna(0.0)
    return out


def _execution() -> ExecutionParameters:
    return ExecutionParameters(
        stop_distance_basis="signal_close",
        sizing_price_basis="signal_close",
        tick_size=0.01,
        commission_per_side=0.001,
        skip_return_basis="net",
        force_close_at_end=True,
        equity_frequency=None,
    )


def _arm() -> Arm:
    return Arm(
        name=CANONICAL_VARIANT,
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


def _timestamp_equal(left: pd.Series, right: pd.Series) -> bool:
    return bool(
        pd.to_datetime(left, utc=True).reset_index(drop=True).equals(
            pd.to_datetime(right, utc=True).reset_index(drop=True)
        )
    )


def _numeric_error(left: pd.Series, right: pd.Series) -> float:
    delta = np.abs(
        pd.to_numeric(left, errors="raise").to_numpy(dtype=float)
        - pd.to_numeric(right, errors="raise").to_numpy(dtype=float)
    )
    return float(delta.max(initial=0.0))


def replay_and_reconcile(
    *,
    config_path: Path = CONFIG,
    canonical_path: Path = CANONICAL_TRADES,
) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config["instrument"]["bar_minutes"] != 15:
        raise ValueError("offline replay is frozen to 15-minute bars")
    if config["eligibility"]["holdout_consumed"] is not False:
        raise RuntimeError("holdout flag must remain false")
    configured_safe_end = pd.Timestamp(config["time_contract"]["safe_end_exclusive"])
    configured_holdout = pd.Timestamp(config["time_contract"]["holdout_start"])
    if configured_safe_end != SAFE_END or configured_holdout != HOLDOUT_START:
        raise ValueError("time safety contract changed")

    data_path = PROJECT / config["instrument"]["data_path"]
    raw = load_development_frame(
        data_path,
        safe_end=SAFE_END,
        holdout_start=HOLDOUT_START,
    )
    featured = build_v9_frame(raw)
    replayed, _ = simulate_symbol(
        featured,
        symbol=config["instrument"]["local_symbol"],
        arm=_arm(),
        start=FINAL_START,
        end=SAFE_END,
        params=SignalParameters(osc_threshold=0.1),
        round_trip_cost=0.002,
        initial_capital=500.0,
        execution=_execution(),
        signal_columns=("v9_long", "v9_short", "v9_score"),
    )

    canonical_all = pd.read_csv(canonical_path)
    canonical = canonical_all.loc[
        canonical_all["variant"].eq(CANONICAL_VARIANT)
        & canonical_all["split"].eq(FINAL_SPLIT)
    ].reset_index(drop=True)
    replayed = replayed.reset_index(drop=True)

    exact_columns = [
        "direction",
        "signal_i",
        "entry_i",
        "exit_i",
        "holding_bars",
        "exit_reason",
    ]
    time_columns = ["signal_time", "entry_time", "exit_time"]
    numeric_columns = [
        "entry_price",
        "exit_price",
        "quantity",
        "initial_stop_price",
        "initial_stop_distance",
        "score",
        "leverage",
        "gross_return",
        "net_return",
        "project_net_return",
        "commission_return",
        "pnl",
        "entry_equity",
        "exit_equity",
    ]

    same_count = len(replayed) == len(canonical) == 110
    exact_matches = {
        column: bool(
            same_count
            and replayed[column].reset_index(drop=True).equals(
                canonical[column].reset_index(drop=True)
            )
        )
        for column in exact_columns
    }
    time_matches = {
        column: bool(same_count and _timestamp_equal(replayed[column], canonical[column]))
        for column in time_columns
    }
    numeric_max_abs_error = {
        column: (
            _numeric_error(replayed[column], canonical[column]) if same_count else float("inf")
        )
        for column in numeric_columns
    }

    times = pd.to_datetime(raw["open_time"], utc=True)
    checks = {
        "contract_is_eth_swap_15m": bool(
            config["instrument"]["research_symbol"] == "ETH-USDT-SWAP"
            and config["instrument"]["bar_minutes"] == 15
        ),
        "safe_end_precedes_holdout": SAFE_END < HOLDOUT_START,
        "bounded_loader_read_zero_holdout_rows": bool(
            len(raw) > 0
            and times.max() < SAFE_END
            and int((times >= HOLDOUT_START).sum()) == 0
        ),
        "trade_count_exact": same_count,
        "categorical_and_index_columns_exact": all(exact_matches.values()),
        "timestamps_exact": all(time_matches.values()),
        "numeric_columns_within_1e_10": all(
            value <= 1e-10 for value in numeric_max_abs_error.values()
        ),
        "final_equity_exact_within_1e_10": bool(
            same_count
            and numeric_max_abs_error["exit_equity"] <= 1e-10
        ),
        "no_model_training_or_scoring": True,
        "tradingview_parity_not_claimed": True,
        "production_eligibility_unchanged_false": bool(
            config["eligibility"]["production_eligible"] is False
        ),
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    return {
        "status": "pass" if not failed else "fail",
        "scope": (
            "network-disabled portable market-data replay of frozen V9; "
            "not a TradingView compiler/export parity test"
        ),
        "runtime": {
            "python": platform.python_version(),
            "pandas": pd.__version__,
            "numpy": np.__version__,
            "platform": platform.platform(),
        },
        "data_contract": {
            "path": str(data_path),
            "bounded_rows_read": int(len(raw)),
            "first_bar": times.iloc[0].isoformat(),
            "last_bar": times.iloc[-1].isoformat(),
            "safe_end_exclusive": SAFE_END.isoformat(),
            "holdout_start": HOLDOUT_START.isoformat(),
            "holdout_rows_read": 0,
            "full_file_hash_intentionally_omitted": True,
        },
        "ledger": {
            "canonical_trade_count": int(len(canonical)),
            "replayed_trade_count": int(len(replayed)),
            "exact_matches": exact_matches,
            "time_matches": time_matches,
            "numeric_max_abs_error": numeric_max_abs_error,
            "final_exit_equity": (
                float(replayed["exit_equity"].iloc[-1]) if not replayed.empty else None
            ),
        },
        "checks": checks,
        "failed": failed,
        "check_count": len(checks),
        "official_pine_compiler_run": False,
        "tradingview_parity_passed": False,
        "model_training_or_scoring_performed": False,
        "holdout_consumed": False,
        "production_eligible": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--canonical-trades", type=Path, default=CANONICAL_TRADES)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = replay_and_reconcile(
        config_path=args.config,
        canonical_path=args.canonical_trades,
    )
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
