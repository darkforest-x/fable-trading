"""Generated V12 Pine arms must preserve their isolated causal contracts."""
from __future__ import annotations

import json

from scripts.generate_pine_eth_15m_optimized_variants import (
    MANIFEST,
    SOURCE,
    V12_ENTRY_OUTPUT,
    V12_FULL_OUTPUT,
    V12_TBSL_OUTPUT,
    build_v12_entry_only,
    build_v12_full_gate,
    build_v12_tbsl,
)


def test_generated_optimized_files_match_strict_generators() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert V12_FULL_OUTPUT.read_text(encoding="utf-8") == build_v12_full_gate(source)
    assert V12_ENTRY_OUTPUT.read_text(encoding="utf-8") == build_v12_entry_only(source)
    assert V12_TBSL_OUTPUT.read_text(encoding="utf-8") == build_v12_tbsl(source)


def test_w8_variants_use_twelve_causal_directional_pairs() -> None:
    for path in (V12_FULL_OUTPUT, V12_ENTRY_OUTPUT):
        pine = path.read_text(encoding="utf-8")
        assert pine.startswith("//@version=6\n")
        assert "timeframe.in_seconds() != 900" in pine
        assert "const int SIX_MA_CROSS_WINDOW = 8" in pine
        assert "const float SIX_MA_CROSS_THRESHOLD = 0.0" in pine
        assert pine.count("f_crossUpEvent(rope") == 12
        assert pine.count("f_crossDownEvent(rope") == 12
        assert "math.sum(sixMaCrossUpEvents, SIX_MA_CROSS_WINDOW)" in pine
        assert "request.security" not in pine
        assert "lookahead" not in pine


def test_full_gate_suppresses_rejected_guarded_state_transition() -> None:
    pine = V12_FULL_OUTPUT.read_text(encoding="utf-8")
    assert "bool rawSignal = gatedRawLong or gatedRawShort" in pine
    assert "bool longSignal = gatedRawLong and commonAllowed" in pine
    assert "bool shortSignal = gatedRawShort and commonAllowed" in pine
    assert "strategy.close(" not in pine


def test_entry_only_gate_keeps_raw_cooldown_and_exit_only_path() -> None:
    pine = V12_ENTRY_OUTPUT.read_text(encoding="utf-8")
    assert "bool rawSignal = rawLong or rawShort" in pine
    assert "if longSignal and strategy.position_size <= 0.0\n    if sixMaLongPass and targetQuantity > 0.0" in pine
    assert "if shortSignal and strategy.position_size >= 0.0\n    if sixMaShortPass and targetQuantity > 0.0" in pine
    assert 'strategy.close("Short", comment = "V12E rejected long closes short"' in pine
    assert 'strategy.close("Long", comment = "V12E rejected short closes long"' in pine


def test_tbsl_arm_freezes_signal_close_target_ticks_without_signal_gate() -> None:
    pine = V12_TBSL_OUTPUT.read_text(encoding="utf-8")
    assert "const float ATR_MULT = 3.0" in pine
    assert "const float MAX_SL_PERCENT = 3.0" in pine
    assert "const float TAKE_PROFIT_PERCENT = 30.0" in pine
    assert "signalTakeProfitDistance = close * TAKE_PROFIT_PERCENT / 100.0" in pine
    assert pine.count("profit = signalTakeProfitTicks") == 2
    assert pine.count("limit = takeProfitPrice") == 2
    assert "sixMaLongPass" not in pine
    assert "sixMaShortPass" not in pine


def test_manifest_blocks_combination_parity_and_production_claims() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["six_ma_contract"]["directional_pairs"] == 12
    assert manifest["six_ma_contract"]["future_bars"] == 0
    assert manifest["combined_variant_generated"] is False
    assert manifest["official_pine_compiler_run"] is False
    assert manifest["tradingview_parity_passed"] is False
    assert manifest["holdout_rows_read"] == 0
    assert manifest["production_eligible"] is False
    variants = {row["version"]: row for row in manifest["variants"]}
    assert variants["V12F"]["strict_single_variable"] is True
    assert variants["V12E"]["strict_single_variable"] is True
    assert variants["V12T"]["strict_single_variable"] is False
