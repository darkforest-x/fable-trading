#!/usr/bin/env python3
"""Fail-closed static contract audit for the canonical V9 Pine surface.

This is not a Pine compiler and does not claim TradingView parity.  It hashes
the source, parses frozen scalar constants, and checks the exact textual
execution/safety constructs that must agree with the research config and
Python replay.  It also keeps the cost-underwater break-even warning visible.
No market data or model artifact is read.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
PINE = EXPERIMENT / "pine/allin_eth_15m_v9_research.pine"
CONFIG = EXPERIMENT / "config.json"
OUTPUT = EXPERIMENT / "results/pine_static_contract.json"


def parse_constant(source: str, name: str) -> float:
    match = re.search(
        rf"(?m)^const\s+(?:int|float)\s+{re.escape(name)}\s*=\s*([-+]?[0-9]+(?:\.[0-9]+)?)\s*$",
        source,
    )
    if match is None:
        raise ValueError(f"missing or non-literal Pine constant {name}")
    return float(match.group(1))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    source = PINE.read_text(encoding="utf-8")
    signal = config["signal_contract"]
    execution = config["frozen_execution"]
    expected_constants: dict[str, float] = {
        "FAST_LEN": float(signal["fast_sma"]),
        "SLOW_LEN": float(signal["slow_sma"]),
        "REGIME_LEN": float(signal["regime_ema"]),
        "ATR_LEN": float(signal["atr_length"]),
        "ATR_MULT": 4.0,
        "MAX_SL_PERCENT": 3.0,
        "OSC_BASIS_LEN": float(signal["oscillator_basis_sma"]),
        "OSC_PERCENTILE_LEN": float(signal["oscillator_percentile_window"]),
        "OSC_PERCENTILE": float(signal["oscillator_percentile"]),
        "OSC_CHANGE_LAG": float(signal["oscillator_change_lag"]),
        "OSC_HMA_LEN": float(signal["oscillator_hma"]),
        "OSC_THRESHOLD": float(signal["locked_oscillator_threshold"]),
        "SLOW_SLOPE_LAG": 12.0,
        "MIN_ATR_PERCENT": float(signal["minimum_atr_percent"]),
        "MAX_ATR_PERCENT": float(signal["maximum_atr_percent"]),
        "RISK_PER_TRADE_PERCENT": float(execution["default_risk_per_trade_percent"]),
        "MAX_LEVERAGE": float(execution["max_leverage"]),
        "BREAK_EVEN_TRIGGER_PERCENT": 1.5,
        "BREAK_EVEN_OFFSET_PERCENT": 0.1,
    }
    parsed = {name: parse_constant(source, name) for name in expected_constants}
    checks: dict[str, bool] = {
        "pine_v6": source.startswith("//@version=6\n"),
        "all_constants_match_config": all(
            parsed[name] == expected for name, expected in expected_constants.items()
        ),
        "timeframe_guard_900_seconds": "timeframe.in_seconds() != 900" in source,
        "eth_base_guard": 'syminfo.basecurrency != "ETH"' in source,
        "confirmed_bar_gate": "barstate.isconfirmed" in source,
        "next_open_order_semantics": "process_orders_on_close = false" in source,
        "historical_tick_recalc_disabled": "calc_on_every_tick = false" in source,
        "order_fill_recalc_disabled": "calc_on_order_fills = false" in source,
        "bar_magnifier_disabled": "use_bar_magnifier = false" in source,
        "pyramiding_zero": "pyramiding = 0" in source,
        "commission_per_fill_0p10_percent": "commission_value = 0.10" in source,
        "slippage_explicit_zero": "slippage = 0" in source,
        "fixed_quantity_with_risk_sizing": bool(
            "default_qty_type = strategy.fixed" in source
            and "strategy.equity * targetLeverage / close" in source
        ),
        "hl2_source": "float source = hl2" in source,
        "sma10_sma60_cross": bool(
            "ta.crossover(fastMa, slowMa)" in source
            and "ta.crossunder(fastMa, slowMa)" in source
        ),
        "ema100_regime": "float regimeMa = ta.ema(close, REGIME_LEN)" in source,
        "ema200_slope12_direction": bool(
            "float ema200 = ta.ema(close, 200)" in source
            and "ema200 / ema200[SLOW_SLOPE_LAG] - 1.0" in source
        ),
        "percentile_denominator_guard": bool(
            "percentile99 > 0.0" in source and "percentileSafe ? difference / percentile99" in source
        ),
        "hk_calendar_gate": bool(
            'hour(time, "Asia/Hong_Kong")' in source
            and "weekdayHk != dayofweek.sunday" in source
        ),
        "single_reversal_without_strategy_close": bool(
            source.count('strategy.entry("Long"') == 1
            and source.count('strategy.entry("Short"') == 1
            and "strategy.close(" not in source
        ),
        "research_end_force_close_only": source.count("strategy.close_all(") == 1,
        "no_request_security": "request.security" not in source,
        "no_lookahead": "lookahead" not in source,
        "no_volume_gate_in_v9": "vol_ratio" not in source.lower(),
        "research_alerts_marked": source.count("RESEARCH ONLY") >= 5,
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    round_trip_cost_bp = float(execution["project_round_trip_cost"]) * 10_000.0
    lock_bp = parsed["BREAK_EVEN_OFFSET_PERCENT"] * 100.0
    payload: dict[str, Any] = {
        "audit": "canonical V9 Pine static contract",
        "source": str(PINE.relative_to(PROJECT)),
        "sha256": sha256(PINE),
        "status": "pass" if not failed else "fail",
        "checks": checks,
        "failed": failed,
        "check_count": len(checks),
        "parsed_constants": parsed,
        "break_even_cost_warning": {
            "lock_bp": lock_bp,
            "frozen_round_trip_cost_bp": round_trip_cost_bp,
            "project_net_if_filled_at_lock_bp": lock_bp - round_trip_cost_bp,
            "is_true_project_net_break_even": lock_bp >= round_trip_cost_bp,
            "barrier_changed": False,
        },
        "official_pine_compiler_run": False,
        "tradingview_parity_passed": False,
        "production_eligible": False,
        "holdout_rows_read": 0,
    }
    if failed:
        raise RuntimeError(f"Pine static contract failed: {failed}")
    if payload["break_even_cost_warning"]["is_true_project_net_break_even"]:
        raise RuntimeError("expected cost-underwater break-even warning disappeared")
    OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
