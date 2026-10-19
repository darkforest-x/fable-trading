#!/usr/bin/env python3
"""Run the frozen ALLIN ETH 15m V12F Pine strategy in Python.

This is a thin, auditable CLI around the repository's already reconciled Pine
indicator and execution engine.  It reads only OHLCV rows strictly before the
requested end, confirms signals at bar close, fills at the next 15-minute open,
charges 0.10% commission per fill, and uses the frozen V12F MA6-W8 full-state
gate.  It never loads a model, changes ACTIVE, writes a forward ledger, or
places an order.

Signal output deliberately carries an interval and an ``available_at`` time.
Future model fusion should match same-symbol, same-side intervals that overlap;
it must not require the Pine signal and detector box to land on one exact bar.
The actionable time is always the later of both causal availability times.

Input columns: ``open_time, open, high, low, close, volume``.  Indicator values
at row ``t`` use only rows through ``t``.  Future OHLC is read only by the
position replay after a next-open entry.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# Allow both ``python -m scripts.run_pine_eth_15m_v12f`` and direct execution.
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from scripts.backtest_pine_eth_15m_v12_preholdout import (
    build_v12_feature_frame,
)
from yoyo.layers.l3_backtest.pine_allin_v7 import (
    Arm,
    ExecutionParameters,
    SignalParameters,
    load_development_frame,
    max_drawdown,
    profit_factor,
    simulate_symbol,
)

BAR = pd.Timedelta(minutes=15)
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
MAX_SAFE_END = pd.Timestamp("2026-03-01T00:00:00Z")
INITIAL_CAPITAL = 500.0
ROUND_TRIP_COST = 0.002
PINE_PATH = (
    PROJECT
    / "experiments/active/exp-pine-eth-15m-v1/pine/"
    "allin_eth_15m_v12f_ma6_w8_full_gate_paper.pine"
)
PINE_SHA256 = "9e03c2959e403632a8db06c66ee43487d7388e0dfdaf31abe5ae32218c7567de"
OUTPUT_NAMES = ("signals.csv", "trades.csv", "equity.csv", "summary.json")


def _utc(value: str | pd.Timestamp) -> pd.Timestamp:
    result = pd.Timestamp(value)
    return result.tz_localize("UTC") if result.tzinfo is None else result.tz_convert("UTC")


def file_sha256(path: Path) -> str:
    """Hash one local file without following any research-data convention."""

    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_frozen_pine() -> None:
    """Refuse to run if the Python wrapper no longer names the frozen Pine."""

    if not PINE_PATH.is_file():
        raise FileNotFoundError(f"frozen Pine source is missing: {PINE_PATH}")
    actual = file_sha256(PINE_PATH)
    if actual != PINE_SHA256:
        raise RuntimeError(
            "frozen V12F Pine source drifted: "
            f"expected={PINE_SHA256} actual={actual}"
        )


def signal_parameters() -> SignalParameters:
    """Return the V12F Pine signal and barrier constants."""

    return SignalParameters(
        fast_len=10,
        slow_len=60,
        regime_len=100,
        atr_len=14,
        atr_mult=4.0,
        max_sl_percent=3.0,
        osc_basis_len=40,
        osc_percentile_len=200,
        osc_percentile=99.0,
        osc_change_lag=10,
        osc_hma_len=10,
        osc_threshold=0.1,
        min_atr_percent=0.1,
        max_atr_percent=10.0,
        break_even_trigger_percent=1.5,
        break_even_offset_percent=0.1,
    )


def execution_parameters() -> ExecutionParameters:
    """Return confirmed-close, next-open Pine accounting semantics."""

    return ExecutionParameters(
        stop_distance_basis="signal_close",
        sizing_price_basis="signal_close",
        sizing_equity_basis="signal_marked",
        leverage_stop_distance_basis="raw",
        tick_size=0.01,
        commission_per_side=0.001,
        skip_return_basis="net",
        force_close_at_end=True,
        equity_frequency=None,
        signal_bar_duration=BAR,
    )


def strategy_arm() -> Arm:
    """Return the frozen V12F risk, cooldown, and reversal policy."""

    return Arm(
        name="v12f_ma6_w8_full_gate",
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


def validate_period(start: pd.Timestamp, end: pd.Timestamp) -> None:
    """Keep this conversion replay outside the already-consumed holdout."""

    if start >= end:
        raise ValueError("start must be before end-exclusive")
    if end > MAX_SAFE_END:
        raise RuntimeError(
            "this conversion CLI is pre-holdout only: "
            f"end-exclusive must be <= {MAX_SAFE_END.isoformat()}"
        )


def validate_15m_frame(frame: pd.DataFrame) -> None:
    """Fail closed on non-15-minute or malformed chronological input."""

    times = pd.to_datetime(frame["open_time"], utc=True, errors="raise")
    if times.duplicated().any():
        raise ValueError("input contains duplicate open_time values")
    deltas = times.diff().dropna()
    if not deltas.eq(BAR).all():
        bad = int((deltas != BAR).sum())
        raise ValueError(f"input is not contiguous 15-minute OHLCV: bad_gaps={bad}")


def build_signal_ledger(
    featured: pd.DataFrame,
    *,
    symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    """Materialize trade-eligible V12F events with causal time intervals.

    A Pine crossover is a one-bar event.  ``interval_start`` is that bar's open
    and ``interval_end``/``available_at`` is its close.  A later detector join
    may intersect this closed interval with a model core/visibility interval;
    exact timestamp equality is intentionally not part of this contract.
    """

    times = pd.to_datetime(featured["open_time"], utc=True)
    date_allowed = times.ge(start) & (times + BAR).lt(end)
    long_mask = (
        date_allowed
        & featured["entry_allowed"].fillna(False)
        & featured["v12f_long"].fillna(False)
    )
    short_mask = (
        date_allowed
        & featured["entry_allowed"].fillna(False)
        & featured["v12f_short"].fillna(False)
    )
    indices = np.flatnonzero((long_mask | short_mask).to_numpy(dtype=bool))
    columns = [
        "signal_id",
        "symbol",
        "direction",
        "signal_i",
        "signal_bar_open",
        "interval_start",
        "interval_end",
        "available_at",
        "next_open_time",
        "close",
        "atr",
        "atr_percent",
        "oscillator",
        "ema200_slope12",
        "ma6_w8_cross_imbalance",
    ]
    rows: list[dict[str, Any]] = []
    for index in indices:
        direction = "long" if bool(long_mask.iloc[index]) else "short"
        signal_bar_open = times.iloc[index]
        available_at = signal_bar_open + BAR
        next_open_time = times.iloc[index + 1] if index + 1 < len(times) else pd.NaT
        imbalance = (
            featured.at[index, "six_ma_cross_up_count_8"]
            - featured.at[index, "six_ma_cross_down_count_8"]
        )
        if direction == "short":
            imbalance = -imbalance
        identity = f"v12f|{symbol}|{direction}|{signal_bar_open.isoformat()}".encode()
        rows.append(
            {
                "signal_id": "v12f_" + sha256(identity).hexdigest()[:24],
                "symbol": symbol,
                "direction": direction,
                "signal_i": int(index),
                "signal_bar_open": signal_bar_open,
                "interval_start": signal_bar_open,
                "interval_end": available_at,
                "available_at": available_at,
                "next_open_time": next_open_time,
                "close": float(featured.at[index, "close"]),
                "atr": float(featured.at[index, "atr"]),
                "atr_percent": float(featured.at[index, "atr_percent"]),
                "oscillator": float(featured.at[index, "osc"]),
                "ema200_slope12": float(featured.at[index, "slow_slope_12"]),
                "ma6_w8_cross_imbalance": float(imbalance),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _safe_float(value: float) -> float | None:
    return float(value) if np.isfinite(value) else None


def summarize_run(
    *,
    raw: pd.DataFrame,
    featured: pd.DataFrame,
    signals: pd.DataFrame,
    trades: pd.DataFrame,
    equity: pd.DataFrame,
    symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[str, Any]:
    """Build a compact result that distinguishes candidates from executions."""

    times = pd.to_datetime(featured["open_time"], utc=True)
    active = times.ge(start) & (times + BAR).lt(end) & featured["entry_allowed"].fillna(False)
    raw_v9 = active & (
        featured["v9_long"].fillna(False) | featured["v9_short"].fillna(False)
    )
    gated_v12f = active & (
        featured["v12f_long"].fillna(False) | featured["v12f_short"].fillna(False)
    )
    if trades.empty:
        final_equity = INITIAL_CAPITAL
        win_rate = None
        net_bp = None
        pine_net_bp = None
        pf = None
    else:
        final_equity = float(trades["exit_equity"].iloc[-1])
        win_rate = float(trades["project_net_return"].gt(0.0).mean())
        net_bp = float(trades["project_net_return"].mean() * 10_000.0)
        pine_net_bp = float(trades["net_return"].mean() * 10_000.0)
        pf = _safe_float(profit_factor(trades["pnl"]))
    dd = (
        _safe_float(max_drawdown(equity["normalized_equity"]))
        if not equity.empty
        else None
    )
    return {
        "strategy": "ALLIN ETH 15m V12F Paper",
        "pine_source": str(PINE_PATH.relative_to(PROJECT)),
        "pine_sha256": PINE_SHA256,
        "symbol": symbol,
        "timeframe": "15m",
        "period": {"start": start.isoformat(), "end_exclusive": end.isoformat()},
        "data": {
            "rows_read": len(raw),
            "first_bar": pd.Timestamp(raw["open_time"].iloc[0]).isoformat(),
            "last_bar": pd.Timestamp(raw["open_time"].iloc[-1]).isoformat(),
            "holdout_rows_read": 0,
        },
        "counts": {
            "raw_v9_candidates": int(raw_v9.sum()),
            "v12f_candidates": int(gated_v12f.sum()),
            "w8_rejected_candidates": int((raw_v9 & ~gated_v12f).sum()),
            "signal_rows": len(signals),
            "executed_trades": len(trades),
        },
        "performance": {
            "initial_equity": INITIAL_CAPITAL,
            "final_equity": final_equity,
            "return_percent": (final_equity / INITIAL_CAPITAL - 1.0) * 100.0,
            "max_drawdown_percent": dd * 100.0 if dd is not None else None,
            "win_rate": win_rate,
            "profit_factor": pf,
            "project_net_bp_per_trade": net_bp,
            "pine_commission_net_bp_per_trade": pine_net_bp,
        },
        "parameters": {
            "signal": asdict(signal_parameters()),
            "execution": {
                **asdict(execution_parameters()),
                "signal_bar_duration": "15min",
            },
            "risk_per_trade_percent": 1.0,
            "max_leverage": 13.0,
            "six_ma_lengths": [20, 60, 120],
            "six_ma_cross_window": 8,
            "six_ma_cross_threshold": 0.0,
            "round_trip_cost": ROUND_TRIP_COST,
        },
        "fusion_contract": {
            "matching": "same symbol + same direction + closed interval overlap",
            "exact_same_bar_required": False,
            "actionable_at": "max(pine.available_at, model.available_at)",
            "model_detection_used_in_this_run": False,
        },
        "safety": {
            "preholdout_only": True,
            "production_eligible": False,
            "places_orders": False,
            "writes_forward_log": False,
            "loads_model": False,
        },
    }


def run(
    *,
    input_path: Path,
    output_dir: Path,
    symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[str, Any]:
    """Execute the conversion replay and write four inspectable artifacts."""

    validate_frozen_pine()
    validate_period(start, end)
    existing = [output_dir / name for name in OUTPUT_NAMES if (output_dir / name).exists()]
    if existing:
        raise FileExistsError(f"refusing to overwrite existing outputs: {existing}")
    raw = load_development_frame(
        input_path,
        safe_end=end,
        holdout_start=HOLDOUT_START,
    )
    validate_15m_frame(raw)
    featured = build_v12_feature_frame(raw)
    signals = build_signal_ledger(
        featured,
        symbol=symbol,
        start=start,
        end=end,
    )
    trades, equity = simulate_symbol(
        featured,
        symbol=symbol,
        arm=strategy_arm(),
        start=start,
        end=end,
        params=signal_parameters(),
        round_trip_cost=ROUND_TRIP_COST,
        initial_capital=INITIAL_CAPITAL,
        execution=execution_parameters(),
        signal_columns=("v12f_long", "v12f_short", "v9_score"),
    )
    summary = summarize_run(
        raw=raw,
        featured=featured,
        signals=signals,
        trades=trades,
        equity=equity,
        symbol=symbol,
        start=start,
        end=end,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    signals.to_csv(output_dir / "signals.csv", index=False)
    trades.to_csv(output_dir / "trades.csv", index=False)
    equity.to_csv(output_dir / "equity.csv", index=False)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--input", type=Path, required=True, help="15m OHLCV CSV")
    result.add_argument("--output-dir", type=Path, required=True)
    result.add_argument("--symbol", default="ETH_USDT_SWAP")
    result.add_argument("--start", required=True, help="inclusive UTC timestamp")
    result.add_argument("--end-exclusive", required=True, help="exclusive UTC timestamp")
    return result


def main() -> int:
    args = parser().parse_args()
    summary = run(
        input_path=args.input,
        output_dir=args.output_dir,
        symbol=str(args.symbol),
        start=_utc(args.start),
        end=_utc(args.end_exclusive),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
