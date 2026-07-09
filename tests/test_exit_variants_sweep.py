from __future__ import annotations

from scripts.exit_variants_sweep import (
    CONFIGS,
    OUT_JSON,
    SWAP_MAKER_COST,
    SWAP_TAKER_COST,
    should_include_symbol,
)


def test_exit_variants_sweep_is_swap_mainline_only() -> None:
    assert set(CONFIGS) == {"tp5_sl2_base", "scaled_25_t3", "breakeven_15"}
    assert should_include_symbol("BTC_USDT_SWAP")
    assert not should_include_symbol("BTC_USDT")
    assert OUT_JSON.name == "exit_variants_swap.json"
    assert SWAP_MAKER_COST == 0.0006
    assert SWAP_TAKER_COST == 0.0010
