"""The generated V15 Pine must preserve the preregistered soft-score contract."""
from __future__ import annotations

from scripts.generate_pine_eth_15m_trend_ensemble import (
    SOURCE,
    build_v15_trend_ensemble,
    load_profiles,
)


def _pine(profile_id: str = "soft_l2") -> str:
    profile = next(profile for profile in load_profiles() if profile.profile_id == profile_id)
    return build_v15_trend_ensemble(SOURCE.read_text(encoding="utf-8"), profile)


def test_generated_pine_has_preregistered_multi_speed_components() -> None:
    pine = _pine()
    assert pine.startswith("//@version=6\n")
    assert "timeframe.in_seconds() != 900" in pine
    for constant in (
        "TREND_EWMAC_FAST_1 = 8",
        "TREND_EWMAC_SLOW_1 = 32",
        "TREND_EWMAC_FAST_2 = 16",
        "TREND_EWMAC_SLOW_2 = 64",
        "TREND_EWMAC_FAST_3 = 32",
        "TREND_EWMAC_SLOW_3 = 128",
        "TREND_DONCHIAN_1 = 24",
        "TREND_DONCHIAN_2 = 48",
        "TREND_DONCHIAN_3 = 96",
        "TREND_WEIGHT = 0.80",
        "TREND_DENSE_WEIGHT = 0.20",
        "TREND_MIN_QUALITY = 0.55",
    ):
        assert constant in pine
    assert pine.count("math.tanh(((ta.ema(close") == 3
    assert pine.count("f_priorChannelPosition(close, ta.highest(high[1]") == 3
    assert "trendForecast = (trendEwmac1 + trendEwmac2 + trendEwmac3 + trendDonchian1 + trendDonchian2 + trendDonchian3) / 6.0" in pine


def test_generated_pine_keeps_dense_soft_and_v12f_w8_gate() -> None:
    pine = _pine()
    assert pine.count("f_crossAnyEvent(rope") == 15
    assert "math.sum(densePairwiseCrossEvents[1], DENSE_WINDOW)" in pine
    assert "math.sum(denseBandwidthAtr[1], DENSE_WINDOW)" in pine
    assert "trendLongQuality = TREND_WEIGHT * trendLongSupport + TREND_DENSE_WEIGHT * denseLongScore" in pine
    assert "bool v15LongPass = sixMaLongPass and trendLongPass" in pine
    assert "bool v15ShortPass = sixMaShortPass and trendShortPass" in pine
    assert "bool gatedRawLong = rawLong and (not gateCandidateEligible or v15LongPass)" in pine
    assert "bool gatedRawShort = rawShort and (not gateCandidateEligible or v15ShortPass)" in pine


def test_generated_pine_preserves_execution_and_has_no_higher_tf_or_lookahead() -> None:
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
    assert "request.security" not in pine
    assert "lookahead" not in pine
    assert "strategy.close(" not in pine
    assert "PAPER ONLY" in pine


def test_generation_is_deterministic_for_all_profiles() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    for profile in load_profiles():
        assert build_v15_trend_ensemble(source, profile) == build_v15_trend_ensemble(
            source, profile
        )
