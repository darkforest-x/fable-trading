"""Generated Pine paper arms must remain one-variable derivatives of V9."""
import json

from scripts.generate_pine_eth_15m_paper_variants import (
    MANIFEST,
    SOURCE,
    V10_OUTPUT,
    V11_OUTPUT,
    build_v10,
    build_v11,
)


def test_generated_paper_files_match_strict_generators() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert V10_OUTPUT.read_text(encoding="utf-8") == build_v10(source)
    assert V11_OUTPUT.read_text(encoding="utf-8") == build_v11(source)


def test_v10_adds_volume_gate_without_changing_risk_or_barriers() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    v10 = V10_OUTPUT.read_text(encoding="utf-8")
    assert "volRatioMean8 >= VOLUME_RATIO_THRESHOLD" in v10
    assert v10.count("volumeExpansion and") == 2
    for line in (
        "const float ATR_MULT = 4.0",
        "const float MAX_SL_PERCENT = 3.0",
        "const float RISK_PER_TRADE_PERCENT = 1.0",
        "const float BREAK_EVEN_TRIGGER_PERCENT = 1.5",
        "commission_value = 0.10",
    ):
        assert line in source and line in v10


def test_v11_never_opens_short_but_keeps_short_signal_exit() -> None:
    v11 = V11_OUTPUT.read_text(encoding="utf-8")
    assert 'strategy.entry("Short"' not in v11
    assert 'strategy.close("Long", comment = "V11 short signal exits long"' in v11
    assert "bool rawShort =" in v11
    assert "volumeExpansion" not in v11


def test_manifest_refuses_combined_or_production_claim() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["combined_v10_v11_generated"] is False
    assert manifest["tradingview_parity_passed"] is False
    assert manifest["production_eligible"] is False
