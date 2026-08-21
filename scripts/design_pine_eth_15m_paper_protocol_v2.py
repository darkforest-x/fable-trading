#!/usr/bin/env python3
"""Design the blocked V9/V12F ETH 15-minute shadow-forward protocol.

This is a successor to the historical V9/V10/V11 protocol and never rewrites
that artifact.  It reads only committed compact pre-holdout ledgers and source
files; it does not read market bars, repository holdout, start a scanner, write
a forward log, compile Pine, or send any order.  V2 remains blocked until both
frozen sources pass exact-venue official compilation, bounded TradingView trade
ledger reconciliation, fee/funding review, and a new owner approval followed by
a prospective activation timestamp.  Backfilling before activation is banned.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT = Path(__file__).resolve().parents[1]
BASE_EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
BASE_RESULTS = BASE_EXPERIMENT / "results"
PINE = BASE_EXPERIMENT / "pine"
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-forward-v2"
RESULTS = EXPERIMENT / "results"
OUTPUT = RESULTS / "paper_forward_protocol_v2.json"
PREDECESSOR = BASE_RESULTS / "paper_forward_protocol.json"
FINAL_START = pd.Timestamp("2025-01-01T00:00:00Z")
FINAL_END = pd.Timestamp("2026-03-01T00:00:00Z")
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
PROPOSED_VENUE = "OKX:ETHUSDT.P"
MINIMUM_FRESH_TRADES = 100

ARM_SPECS = {
    "V9": {
        "source": PINE / "allin_eth_15m_v9_research.pine",
        "ledger": BASE_RESULTS / "trades.csv",
        "variant": "v9_locked",
        "period_column": "split",
        "period": "final_preholdout_2025_202602",
        "expected_trades": 110,
        "compile_receipt": BASE_RESULTS / "tradingview_compile_receipt.json",
        "reconciliation": BASE_RESULTS / "tradingview_reconciliation.json",
        "change_contract": "frozen V9 baseline",
    },
    "V12F": {
        "source": PINE / "allin_eth_15m_v12f_ma6_w8_full_gate_paper.pine",
        "ledger": BASE_RESULTS / "optimized_pine_variants_primary_trades.csv",
        "variant": "v12f_ma6_w8_full_gate",
        "period_column": "period",
        "period": "final_preholdout_2025_202602",
        "expected_trades": 97,
        "compile_receipt": BASE_RESULTS / "tradingview_compile_receipt_v12f.json",
        "reconciliation": BASE_RESULTS / "tradingview_reconciliation_v12f.json",
        "change_contract": "exactly V9 plus frozen MA6 W8 full-state gate",
    },
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _planning_months(observed_trades: int) -> tuple[float, float]:
    period_days = (FINAL_END - FINAL_START).total_seconds() / 86_400.0
    monthly_rate = observed_trades / period_days * 365.2425 / 12.0
    return monthly_rate, MINIMUM_FRESH_TRADES / monthly_rate


def _canonical_count(spec: dict[str, Any]) -> int:
    frame = pd.read_csv(spec["ledger"], usecols=["variant", spec["period_column"]])
    selected = frame.loc[
        frame["variant"].eq(spec["variant"])
        & frame[spec["period_column"]].eq(spec["period"])
    ]
    count = int(len(selected))
    if count != int(spec["expected_trades"]):
        raise RuntimeError(
            f"{spec['variant']} canonical count drifted: "
            f"expected {spec['expected_trades']}, found {count}"
        )
    return count


def _compile_gate(spec: dict[str, Any], source_hash: str) -> dict[str, Any]:
    path = Path(spec["compile_receipt"])
    if not path.exists():
        return {
            "receipt_present": False,
            "official_compiler_passed": False,
            "source_hash_matches": False,
            "venue_matches": False,
            "timeframe_matches": False,
            "pine_version_matches": False,
            "parity_window_matches": False,
            "input_values_match_frozen_contract": False,
        }
    receipt = json.loads(path.read_text(encoding="utf-8"))
    receipt_window = receipt.get("strategy_research_window")
    parity_window_matches = False
    if isinstance(receipt_window, list) and len(receipt_window) == 2:
        try:
            parity_window_matches = bool(
                pd.Timestamp(receipt_window[0]) == FINAL_START
                and pd.Timestamp(receipt_window[1]) == FINAL_END
            )
        except (TypeError, ValueError):
            parity_window_matches = False
    return {
        "receipt_present": True,
        "official_compiler_passed": bool(
            receipt.get("official_pine_compiler_run")
            and int(receipt.get("pine_compile_error_count", -1)) == 0
        ),
        "source_hash_matches": receipt.get("source_sha256") == source_hash,
        "venue_matches": receipt.get("venue_symbol") == PROPOSED_VENUE,
        "timeframe_matches": receipt.get("chart_interval") == "15m",
        "pine_version_matches": int(receipt.get("pine_version", -1)) == 6,
        "parity_window_matches": parity_window_matches,
        "input_values_match_frozen_contract": bool(
            receipt.get("input_values_match_frozen_contract") is True
        ),
        "receipt_path": str(path.relative_to(PROJECT)),
    }


def _ledger_gate(arm: str, spec: dict[str, Any]) -> dict[str, Any]:
    path = Path(spec["reconciliation"])
    if not path.exists():
        return {
            "receipt_present": False,
            "exact_ledger_parity_passed": False,
            "price_time_parity_passed": False,
            "fee_semantics_verified": False,
            "expected_trades": int(spec["expected_trades"]),
        }
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if receipt.get("variant") != arm.lower():
        raise RuntimeError(f"{arm} reconciliation variant identity mismatch")
    if int(receipt.get("expected_trades", -1)) != int(spec["expected_trades"]):
        raise RuntimeError(f"{arm} reconciliation expected trade count drifted")
    if int(receipt.get("holdout_rows_read", -1)) != 0:
        raise RuntimeError(f"{arm} reconciliation claims holdout access")
    return {
        "receipt_present": True,
        "exact_ledger_parity_passed": bool(
            receipt.get("status") == "pass"
            and receipt.get("tradingview_parity_passed") is True
            and receipt.get("price_time_parity_passed") is True
            and receipt.get("fee_semantics_verified") is True
            and receipt.get("project_20bp_cost_rededucted") is False
        ),
        "price_time_parity_passed": bool(receipt.get("price_time_parity_passed")),
        "fee_semantics_verified": bool(receipt.get("fee_semantics_verified")),
        "expected_trades": int(spec["expected_trades"]),
        "receipt_path": str(path.relative_to(PROJECT)),
    }


def build_protocol() -> dict[str, Any]:
    arms: dict[str, Any] = {}
    blocking_gates: list[str] = []
    for arm, spec in ARM_SPECS.items():
        source_hash = sha256_file(spec["source"])
        canonical_count = _canonical_count(spec)
        monthly_rate, months_to_formal = _planning_months(canonical_count)
        compile_gate = _compile_gate(spec, source_hash)
        ledger_gate = _ledger_gate(arm, spec)
        if not all(
            (
                compile_gate["official_compiler_passed"],
                compile_gate["source_hash_matches"],
                compile_gate["venue_matches"],
                compile_gate["timeframe_matches"],
                compile_gate["pine_version_matches"],
                compile_gate["parity_window_matches"],
                compile_gate["input_values_match_frozen_contract"],
            )
        ):
            blocking_gates.append(
                f"{arm} official compiler/source/venue/timeframe/window/input receipt"
            )
        if not ledger_gate["exact_ledger_parity_passed"]:
            blocking_gates.append(f"{arm} exact {canonical_count}-trade ledger parity")
        arms[arm] = {
            "source": str(spec["source"].relative_to(PROJECT)),
            "source_sha256": source_hash,
            "change_contract": spec["change_contract"],
            "canonical_preholdout_trades": canonical_count,
            "historical_trades_per_30p44_days": monthly_rate,
            "planning_months_to_100_fresh_trades": months_to_formal,
            "historical_rate_is_forecast": False,
            "minimum_fresh_trades_for_formal_read": MINIMUM_FRESH_TRADES,
            "compile_gate": compile_gate,
            "ledger_gate": ledger_gate,
        }

    blocking_gates.extend(
        [
            "owner confirms exact venue OKX:ETHUSDT.P for both arms",
            "venue-exact commission, slippage and funding review for both arms",
            "owner explicitly approves prospective log-only paper collection",
            "activation timestamp is recorded after all approvals; no backfill",
        ]
    )
    return {
        "schema_version": "pine-eth-15m-paper-forward-v2",
        "protocol": "ETH 15m frozen V9 versus V12F prospective shadow comparison",
        "status": "blocked",
        "supersedes_for_future_planning_only": str(PREDECESSOR.relative_to(PROJECT)),
        "predecessor_preserved_sha256": sha256_file(PREDECESSOR),
        "comparison_scope": {
            "arms": ["V9", "V12F"],
            "forbidden": ["V10", "V11", "V12E", "V12T", "L2_model"],
        },
        "created_from_compact_preholdout_artifacts_only": True,
        "market_bar_rows_read": 0,
        "compact_exposed_final_ledger_rows_read": sum(
            arm["canonical_preholdout_trades"] for arm in arms.values()
        ),
        "holdout_rows_read": 0,
        "proposed_exact_venue": PROPOSED_VENUE,
        "venue_owner_confirmed": False,
        "venue_lock": {
            "owner_selected_symbol": None,
            "proposed_symbol": PROPOSED_VENUE,
            "timeframe": "15m",
            "tick_size": 0.01,
        },
        "windows": {
            "parity_start": FINAL_START.isoformat(),
            "parity_end_exclusive": FINAL_END.isoformat(),
            "repository_holdout_start": HOLDOUT_START.isoformat(),
        },
        "bar_minutes": 15,
        "arms": arms,
        "combined_arm_allowed": False,
        "formal_collection_started": False,
        "activation_time": None,
        "backfill_before_activation_allowed": False,
        "forward_log_written": False,
        "scanner_started": False,
        "paper_or_live_order_sent": False,
        "blocked": True,
        "blocking_gates": blocking_gates,
        "parity_gate": {
            "both_compiler_receipts_exact": all(
                all(
                    arm["compile_gate"][field]
                    for field in (
                        "official_compiler_passed",
                        "source_hash_matches",
                        "venue_matches",
                        "timeframe_matches",
                        "pine_version_matches",
                        "parity_window_matches",
                        "input_values_match_frozen_contract",
                    )
                )
                for arm in arms.values()
            ),
            "both_exact_ledgers_passed": all(
                arm["ledger_gate"]["exact_ledger_parity_passed"]
                for arm in arms.values()
            ),
            "fee_semantics_verified_for_both": all(
                arm["ledger_gate"]["fee_semantics_verified"]
                for arm in arms.values()
            ),
            "funding_and_venue_slippage_reviewed": False,
            "exact_parity_passed": False,
        },
        "shadow_accounting": {
            "execution": "next 15m open after confirmed signal",
            "comparison_risk_per_trade_percent": 1.0,
            "damage_control_view_percent": 0.5,
            "damage_control_is_derived_display_only": True,
            "round_trip_cost_floor": 0.002,
            "historical_pine_commission_per_side": 0.001,
            "tradingview_net_profit_must_include_commission": True,
            "project_20bp_cost_may_be_deducted_from_tradingview_net_again": False,
            "exact_cost_fields_required": [
                "entry_commission",
                "exit_commission",
                "slippage",
                "funding",
            ],
            "no_broker_order": True,
        },
        "append_only_log_contract": {
            "one_row_per_event_per_arm": True,
            "identity_fields": [
                "protocol_version",
                "arm",
                "source_sha256",
                "venue_symbol",
                "signal_time",
                "side",
            ],
            "causal_fields": [
                "signal_observed_at",
                "next_open_time",
                "next_open_price",
                "data_freshness_seconds",
            ],
            "outcome_fields": [
                "exit_time",
                "exit_price",
                "exit_reason",
                "entry_commission",
                "exit_commission",
                "slippage",
                "funding",
                "net_return",
            ],
            "writes_before_activation": False,
        },
        "formal_evaluation": {
            "timing": (
                "one formal read only after both arms each reach 100 genuinely fresh "
                "trades; interim views are data-quality/latency only"
            ),
            "time_ordered": True,
            "primary_hypotheses": [
                "V9 minus matched random control",
                "V12F minus matched random control",
                "V12F minus V9 by prospective calendar block",
            ],
            "multiple_testing": "Holm correction across the three primary hypotheses",
            "familywise_alpha": 0.01,
            "success_requires_all": [
                "venue-exact net expectancy is positive",
                "matched-control excess is positive with Holm-adjusted p < 0.01",
                "week-block absolute-return 95% interval lower bound is above zero",
                "leave-largest-winner-out expectancy remains positive",
                "both TradingView parity receipts remain exact",
            ],
            "parameter_selection_from_interim_data_allowed": False,
        },
        "owner_decisions_required_before_start": [
            "confirm OKX:ETHUSDT.P as the exact venue",
            "provide/export V9 and V12F bounded historical TradingView ledgers",
            "approve prospective log-only collection after every gate passes",
            "approve any barrier, break-even, ATR, cost or risk change separately",
        ],
        "owner_approval": {
            "required": True,
            "reference": None,
            "scope": ["V9", "V12F", "venue", "fresh_forward"],
        },
        "training_eligible": False,
        "forward_eligible": False,
        "production_eligible": False,
    }


def main() -> None:
    payload = build_protocol()
    if payload["blocked"] is not True or payload["formal_collection_started"] is not False:
        raise RuntimeError("paper V2 must remain blocked and not started")
    if payload["forward_log_written"] or payload["paper_or_live_order_sent"]:
        raise RuntimeError("paper V2 design may not mutate execution state")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
