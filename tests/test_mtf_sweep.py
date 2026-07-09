from __future__ import annotations

from scripts.mtf_sweep import CONFIGS, MAJOR_5M_SYMBOLS, SWAP_MAKER_COST, SWAP_TAKER_COST, include_symbol


def test_mtf_sweep_configs_match_research_agenda() -> None:
    configs = {(cfg.name, cfg.bar, cfg.horizon_bars) for cfg in CONFIGS}

    assert ("h7_5m_h96", "5m", 96) in configs
    assert ("h7_5m_h144", "5m", 144) in configs
    assert ("h7_5m_h216", "5m", 216) in configs
    assert ("h8_30m_h24", "30m", 24) in configs
    assert ("h8_30m_h48", "30m", 48) in configs
    assert ("h8_30m_h72", "30m", 72) in configs
    assert ("h8_1h_h24", "1H", 24) in configs
    assert ("h8_1h_h48", "1H", 48) in configs
    assert ("h8_1h_h72", "1H", 72) in configs
    assert SWAP_MAKER_COST == 0.0006
    assert SWAP_TAKER_COST == 0.0010


def test_mtf_sweep_symbol_filters() -> None:
    assert "BTC_USDT_SWAP" in MAJOR_5M_SYMBOLS
    assert include_symbol("BTC_USDT_SWAP", "5m")
    assert not include_symbol("AAVE_USDT_SWAP", "5m")
    assert include_symbol("AAVE_USDT_SWAP", "30m")
    assert include_symbol("AAVE_USDT_SWAP", "1H")
    assert not include_symbol("BTC_USDT", "30m")
