"""The generated V13 Pine must preserve the preregistered causal contract."""
from __future__ import annotations

from scripts.generate_pine_eth_15m_dense_start import (
    SOURCE,
    build_v13_dense_start,
    load_profiles,
)


def _pine(profile_id: str = "dense_l2") -> str:
    profile = next(profile for profile in load_profiles() if profile.profile_id == profile_id)
    return build_v13_dense_start(SOURCE.read_text(encoding="utf-8"), profile)


def test_generated_dense_start_uses_prior_formation_and_current_release() -> None:
    pine = _pine()
    assert pine.startswith("//@version=6\n")
    assert "timeframe.in_seconds() != 900" in pine
    assert pine.count("f_crossAnyEvent(rope") == 15
    assert pine.count("f_crossUpEvent(rope") == 12
    assert pine.count("f_crossDownEvent(rope") == 12
    assert "math.sum(densePairwiseCrossEvents[1], DENSE_WINDOW)" in pine
    assert "math.sum(denseBandwidthAtr[1], DENSE_WINDOW)" in pine
    assert "close > denseRopeUpper" in pine
    assert "close < denseRopeLower" in pine
    assert "request.security" not in pine
    assert "lookahead" not in pine


def test_generated_dense_start_keeps_stop_cost_sizing_and_full_state_semantics() -> None:
    pine = _pine()
    for frozen in (
        "const float ATR_MULT = 4.0",
        "const float MAX_SL_PERCENT = 3.0",
        "const float BREAK_EVEN_TRIGGER_PERCENT = 1.5",
        "const float BREAK_EVEN_OFFSET_PERCENT = 0.1",
        "const float RISK_PER_TRADE_PERCENT = 1.0",
        "const float MAX_LEVERAGE = 13.0",
        "commission_value = 0.10",
    ):
        assert frozen in pine
    assert "bool rawSignal = gatedRawLong or gatedRawShort" in pine
    assert "bool longSignal = gatedRawLong and commonAllowed" in pine
    assert "bool shortSignal = gatedRawShort and commonAllowed" in pine
    assert "strategy.close(" not in pine
    assert "PAPER ONLY" in pine


def test_generation_is_deterministic_for_each_preregistered_profile() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    for profile in load_profiles():
        assert build_v13_dense_start(source, profile) == build_v13_dense_start(source, profile)
