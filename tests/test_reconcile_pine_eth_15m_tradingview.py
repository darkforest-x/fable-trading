"""TradingView reconciliation must require an exact 110-trade ledger."""
import pandas as pd

from scripts.reconcile_pine_eth_15m_tradingview import (
    load_canonical,
    reconcile_frames,
)


def normalized_from_canonical() -> tuple[pd.DataFrame, pd.DataFrame]:
    canonical = load_canonical()
    normalized = canonical[
        ["direction", "entry_time", "exit_time", "entry_price", "exit_price"]
    ].copy()
    normalized["commission_total"] = 0.0
    normalized["net_profit"] = 0.0
    return canonical, normalized


def test_exact_synthetic_export_passes_ledger_but_not_eligibility() -> None:
    canonical, normalized = normalized_from_canonical()
    payload = reconcile_frames(canonical, normalized)
    assert payload["status"] == "pass"
    assert payload["entry_time_direction_matches"] == 110
    assert payload["exit_time_matches"] == 110
    assert payload["tradingview_parity_passed"] is True
    assert payload["fee_accounting_manually_reviewed"] is False
    assert payload["production_eligible"] is False
    assert payload["forward_eligible"] is False


def test_price_or_count_mismatch_fails_closed() -> None:
    canonical, normalized = normalized_from_canonical()
    normalized.loc[normalized.index[0], "exit_price"] += 0.02
    mismatch = reconcile_frames(canonical, normalized)
    assert mismatch["status"] == "fail"
    assert mismatch["tradingview_parity_passed"] is False

    short = reconcile_frames(canonical, normalized.iloc[:-1].copy())
    assert short["status"] == "fail"
    assert short["tradingview_trades"] == 109
