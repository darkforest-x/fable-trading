"""Static contract checks for the fixed V14R Pine generator."""
from __future__ import annotations

from scripts.generate_pine_eth_15m_dense_release import (
    SOURCE,
    build_v14_dense_release,
    selected_profile,
)


def _pine() -> str:
    return build_v14_dense_release(
        SOURCE.read_text(encoding="utf-8"),
        selected_profile(),
    )


def test_v14_release_is_confirmed_bar_causal_and_literal() -> None:
    pine = _pine()
    assert pine.startswith("//@version=6\n")
    assert "timeframe.in_seconds() != 900" in pine
    assert "denseTrueRange / atr[1]" in pine
    assert "close[1] - denseRopeUpper[1]" in pine
    assert "denseRopeLower[1] - close[1]" in pine
    assert "denseLongBreakoutExpansionAtr > DENSE_MIN_BREAKOUT_EXPANSION_ATR" in pine
    assert "denseShortBreakoutExpansionAtr > DENSE_MIN_BREAKOUT_EXPANSION_ATR" in pine
    assert "request.security" not in pine
    assert "lookahead" not in pine


def test_v14_changes_no_barrier_cost_or_position_setting() -> None:
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
    assert "PAPER ONLY" in pine


def test_v14_generation_is_deterministic() -> None:
    assert _pine() == _pine()
