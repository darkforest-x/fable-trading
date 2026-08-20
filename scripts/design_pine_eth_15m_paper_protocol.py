#!/usr/bin/env python3
"""Preregister a blocked, paper-only forward comparison for V9/V10/V11.

This script starts no scanner, forward logger, automation, or order path.  It
reads compact pre-holdout artifacts only, hashes the three Pine surfaces, and
turns historical signal arrival rates into planning estimates.  Formal paper
collection remains blocked until venue-specific TradingView ledger parity is
approved.  The protocol freezes mutually exclusive single-variable arms and
prevents repeated peeking or combining V10 with V11.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
RESULTS = EXPERIMENT / "results"
PINE = EXPERIMENT / "pine"
OUTPUT = RESULTS / "paper_forward_protocol.json"
FINAL_START = pd.Timestamp("2025-01-01T00:00:00Z")
FINAL_END = pd.Timestamp("2026-03-01T00:00:00Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _months_for_trades(*, observed_trades: int, period_days: float, target: int) -> float:
    monthly_rate = observed_trades / period_days * 365.2425 / 12.0
    return target / monthly_rate


def main() -> None:
    config = json.loads((EXPERIMENT / "config.json").read_text(encoding="utf-8"))
    summary = json.loads((RESULTS / "summary.json").read_text(encoding="utf-8"))
    v11 = json.loads((RESULTS / "v11_long_only_summary.json").read_text(encoding="utf-8"))
    manifest = json.loads(
        (PINE / "paper_variants_manifest.json").read_text(encoding="utf-8")
    )
    split = pd.read_csv(RESULTS / "split_summary.csv")
    period_days = (FINAL_END - FINAL_START).total_seconds() / 86_400.0
    source_by_arm = {
        "V9": PINE / "allin_eth_15m_v9_research.pine",
        "V10": PINE / "allin_eth_15m_v10_volume_paper.pine",
        "V11": PINE / "allin_eth_15m_v11_long_only_paper.pine",
    }
    historical_counts = {
        "V9": int(
            split.loc[
                split["variant"].eq("v9_locked")
                & split["period"].eq("final_preholdout_2025_202602"),
                "trades",
            ].iloc[0]
        ),
        "V10": int(
            split.loc[
                split["variant"].eq("v10_volume_hypothesis")
                & split["period"].eq("final_preholdout_2025_202602"),
                "trades",
            ].iloc[0]
        ),
        "V11": int(v11["final_preholdout"]["trades"]),
    }
    arm_changes = {
        "V9": "frozen baseline: SMA10/60 + EMA100 + slope12 + oscillator 0.1",
        "V10": "exactly one change from V9: vol_ratio_mean8 >= 1",
        "V11": "exactly one change from V9: long entries only",
    }
    arms: dict[str, Any] = {}
    for arm in ("V9", "V10", "V11"):
        observed = historical_counts[arm]
        rate = observed / period_days * 365.2425 / 12.0
        arms[arm] = {
            "source": str(source_by_arm[arm].relative_to(PROJECT)),
            "sha256": sha256(source_by_arm[arm]),
            "single_variable_contract": arm_changes[arm],
            "historical_consumed_period_trades": observed,
            "historical_trades_per_30p44_days": rate,
            "planning_months_to_100_fresh_trades": _months_for_trades(
                observed_trades=observed,
                period_days=period_days,
                target=100,
            ),
            "historical_rate_is_forecast": False,
            "minimum_fresh_trades_for_formal_read": 100,
        }

    payload = {
        "protocol": "ETH 15m mutually exclusive paper-forward comparison",
        "created_from_consumed_data_only": True,
        "holdout_rows_read": 0,
        "formal_collection_started": False,
        "forward_log_written": False,
        "live_or_paper_order_sent": False,
        "blocked": True,
        "blocking_gate": (
            "venue-specific TradingView compile plus 110-trade signal/entry/exit/fee "
            "ledger parity has not passed"
        ),
        "tradingview_parity_passed": bool(summary["tradingview_parity_passed"]),
        "forward_eligible": bool(config["eligibility"]["forward_eligible"]),
        "paper_risk_profile": {
            "primary_risk_per_trade_percent": 0.5,
            "comparison_shadow_risk_percent": 1.0,
            "reason": (
                "Path bootstrap shows lower drawdown at 0.5%; sizing is a damage-control "
                "profile and not evidence of stronger alpha. No orders are authorized."
            ),
        },
        "arms": arms,
        "combined_v10_v11_arm_allowed": False,
        "formal_evaluation": {
            "timing": (
                "one formal read after every arm reaches 100 fresh trades; monthly views "
                "may monitor data quality only and may not select parameters"
            ),
            "time_ordered": True,
            "venue_exact_fees_slippage_and_funding_required": True,
            "matched_random_control": (
                "same symbol x pre-entry calendar block x causal volatility bucket; exact "
                "non-reused starts; copied side and holding/exit contract"
            ),
            "familywise_alpha": 0.01,
            "multiple_arm_adjustment": "Holm correction across V9/V10/V11",
            "success_requires_all": [
                "net expectancy after venue-exact total costs is positive",
                "matched-control excess is positive with Holm-adjusted p < 0.01",
                "week-block absolute-return 95% interval lower bound is above zero",
                "leave-largest-winner-out expectancy remains positive",
                "TradingView ledger parity remains exact",
            ],
            "judgment_layer_if_later_authorized": (
                "LR/LightGBM must be a separately preregistered fourth experiment, trained "
                "only on eligible data and evaluated inside dynamic stateful replay"
            ),
        },
        "manifest_consistency": {
            "combined_v10_v11_generated": manifest["combined_v10_v11_generated"],
            "production_eligible": manifest["production_eligible"],
            "tradingview_parity_passed": manifest["tradingview_parity_passed"],
        },
        "owner_decisions_required_before_start": [
            "select the exact TradingView ETH perpetual venue and export its ledger",
            "approve paper collection after parity passes",
            "approve any change to break-even, TP/SL, ATR floor, or cost assumptions separately",
        ],
    }
    if payload["tradingview_parity_passed"] or payload["forward_eligible"]:
        raise RuntimeError("protocol must fail closed while parity/forward eligibility are false")
    if payload["manifest_consistency"] != {
        "combined_v10_v11_generated": False,
        "production_eligible": False,
        "tradingview_parity_passed": False,
    }:
        raise RuntimeError("paper variant manifest no longer fails closed")
    OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
