#!/usr/bin/env python3
"""Audit the static migration from the supplied V7.2 Pine to frozen V9.

This ledger verifies the attachment hash and records each execution-safety
change that distinguishes the original backtest surface from the 15-minute
research contract.  It does not compile Pine, read market data, change a
barrier, run a model or claim TradingView parity.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
CONFIG = EXPERIMENT / "config.json"
V9 = EXPERIMENT / "pine/allin_eth_15m_v9_research.pine"
OUTPUT = EXPERIMENT / "results/migration_audit.json"
COMPILER_RECEIPT = EXPERIMENT / "results/tradingview_compile_receipt.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def migration_checks(original: str, v9: str) -> dict[str, bool]:
    """Return explicit source-to-source safety and semantics checks."""

    return {
        "original_is_v5_and_v9_is_v6": bool(
            original.startswith("//@version=5") and v9.startswith("//@version=6")
        ),
        "tick_recalculation_disabled": bool(
            "calc_on_every_tick=true" in original
            and "calc_on_every_tick = false" in v9
        ),
        "commission_made_explicit": bool(
            "commission_value" not in original
            and "commission_value = 0.10" in v9
        ),
        "slippage_made_explicit": bool(
            "slippage" not in original and "slippage = 0" in v9
        ),
        "fixed_4x_time_boosts_removed": bool(
            "float pos_qty = 400" in original
            and "morning_boost_mult" in original
            and "thursday_boost_mult" in original
            and "float pos_qty = 400" not in v9
            and "morning_boost_mult" not in v9
            and "thursday_boost_mult" not in v9
        ),
        "risk_sizing_replaces_fixed_notional": bool(
            "strategy.equity * targetLeverage / close" in v9
            and "RISK_PER_TRADE_PERCENT" in v9
        ),
        "stop_is_anchored_to_fill_not_signal_close": bool(
            "sl_price := close - final_sl_dist" in original
            and "sl_price := close + final_sl_dist" in original
            and "strategy.position_avg_price - nz(pendingLongStopTicks" in v9
            and "strategy.position_avg_price + nz(pendingShortStopTicks" in v9
        ),
        "hk_time_filter_is_explicit": bool(
            "current_hour = hour" in original
            and 'hour(time, "Asia/Hong_Kong")' in v9
        ),
        "duplicate_close_plus_reverse_removed": bool(
            'strategy.close("Long"' in original
            and 'strategy.close("Short"' in original
            and "strategy.close(" not in v9
        ),
        "timeframe_and_eth_guards_added": bool(
            "timeframe.in_seconds()" not in original
            and "syminfo.basecurrency" not in original
            and "timeframe.in_seconds() != 900" in v9
            and 'syminfo.basecurrency != "ETH"' in v9
        ),
        "bounded_research_dates_added": bool(
            "researchStart" not in original
            and "researchEnd" not in original
            and "researchStart" in v9
            and "researchEnd" in v9
        ),
        "percentile_denominator_fails_closed": bool(
            "perc_r != 0 ? diff / perc_r : 0" in original
            and "not na(percentile99) and percentile99 > 0.0" in v9
        ),
        "bar_magnifier_decision_is_explicit": bool(
            "use_bar_magnifier" not in original
            and "use_bar_magnifier = false" in v9
        ),
        "pyramiding_decision_is_explicit": bool(
            "pyramiding" not in original and "pyramiding = 0" in v9
        ),
        "research_alerts_fail_visibly": bool(
            "RESEARCH ONLY" not in original and v9.count("RESEARCH ONLY") >= 5
        ),
    }


def main() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    source_path = Path(config["source_attachment"])
    original = source_path.read_text(encoding="utf-8")
    v9 = V9.read_text(encoding="utf-8")
    compiler_receipt = json.loads(COMPILER_RECEIPT.read_text(encoding="utf-8"))
    checks = migration_checks(original, v9)
    source_hash = sha256(source_path)
    expected_hash = config["source_attachment_sha256"]
    checks["source_attachment_hash_matches_config"] = source_hash == expected_hash
    failed = sorted(name for name, passed in checks.items() if not passed)
    if not compiler_receipt["official_pine_compiler_run"] or compiler_receipt["source_sha256"] != sha256(V9):
        raise RuntimeError("official compiler receipt does not match frozen V9")
    payload: dict[str, Any] = {
        "audit": "user-supplied ALLIN V7.2 to ETH 15m V9 migration ledger",
        "status": "pass" if not failed else "fail",
        "source_attachment": str(source_path),
        "source_attachment_sha256": source_hash,
        "v9_path": str(V9.relative_to(PROJECT)),
        "v9_sha256": sha256(V9),
        "checks": checks,
        "check_count": len(checks),
        "failed": failed,
        "preserved_alpha_ancestry": [
            "hl2 SMA10/SMA60 crossover",
            "EMA100 regime side",
            "ATR14 with 4x and 3% cap",
            "SMA40/p99/change10/HMA10 oscillator construction",
            "HK blocked hours and Sunday exclusion intent",
            "profit-triggered signal cooldown",
        ],
        "alpha_changes": [
            "EMA200 slow_slope_12 direction gate",
            "oscillator threshold 0.2 to locked 0.1",
        ],
        "execution_changes": [
            "1% stop-risk sizing replaces fixed 4x/time boosts",
            "commission and slippage are explicit",
            "initial stop is anchored to actual next-open fill",
            "single reversal replaces entry plus duplicate strategy.close",
            "confirmed close, 15m/ETH/date guards and explicit HK timezone",
        ],
        "known_unresolved_semantics": [
            "TradingView official compilation passed in a separate receipt; historical trade-export parity has not passed",
            "the configured +0.1% break-even lock remains -0.1% after 0.2% round-trip cost",
            "OKX proxy bars are not asserted equal to an unspecified TradingView ETHUSDT.P venue",
        ],
        "market_rows_read": 0,
        "holdout_rows_read": 0,
        "barrier_parameters_changed": False,
        "training_or_scoring_performed": False,
        "official_pine_compiler_run": True,
        "tradingview_parity_passed": False,
        "production_eligible": False,
    }
    if failed:
        raise RuntimeError(f"Pine migration audit failed: {failed}")
    OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
