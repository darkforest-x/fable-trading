"""TradingView reconciliation must require each frozen ledger exactly."""
import pandas as pd
import pytest

from scripts.reconcile_pine_eth_15m_tradingview import (
    load_canonical,
    reconcile_frames,
)


def normalized_from_canonical(variant: str = "v9") -> tuple[pd.DataFrame, pd.DataFrame]:
    canonical = load_canonical(variant)
    normalized = canonical[
        ["direction", "entry_time", "exit_time", "entry_price", "exit_price"]
    ].copy()
    normalized["commission_total"] = 0.0
    normalized["net_profit"] = 0.0
    return canonical, normalized


@pytest.mark.parametrize(("variant", "expected"), [("v9", 110), ("v12f", 97)])
def test_exact_synthetic_export_passes_ledger_but_not_eligibility(
    variant: str,
    expected: int,
) -> None:
    canonical, normalized = normalized_from_canonical(variant)
    payload = reconcile_frames(canonical, normalized, variant=variant)
    assert payload["status"] == "pass"
    assert payload["variant"] == variant
    assert payload["expected_trades"] == expected
    assert payload["entry_time_direction_matches"] == expected
    assert payload["exit_time_matches"] == expected
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


def test_unknown_variant_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown variant"):
        load_canonical("unknown")
