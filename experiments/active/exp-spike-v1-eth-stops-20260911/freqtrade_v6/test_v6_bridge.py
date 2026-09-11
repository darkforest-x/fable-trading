"""Focused causal contracts for the V6 Freqtrade bridge builder."""
from pathlib import Path
import sys
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from build_v6_bridge import active_stop_plan


def _long_bars() -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=9, freq="1h", tz="UTC")
    close = [103, 103, 103, 103, 104, 105, 112, 110, 111]
    return pd.DataFrame({
        "open": close, "high": [x + 1 for x in close],
        "low": [102.5, 102.5, 102.5, 102.5, 102.5, 104, 106, 109.5, 110],
        "close": close, "atr": [1] * len(close),
    }, index=index)


def test_active_stop_is_prior_close_known_and_does_not_use_same_bar_high_low():
    bars = _long_bars()
    plan = active_stop_plan(bars, signal_i=4, side=1, floor_atr=2.0, trail_atr=3.0)
    # Signal close at index4 creates the first active stop for index5.
    assert plan[0]["current_time"] == bars.index[5]
    assert plan[0]["active_stop"] == 102.0
    # Index6 closes at 112 and arms the ratchet.  The improved protection is
    # visible only on index7, never retroactively on index6.
    assert plan[1]["current_time"] == bars.index[6]
    assert plan[1]["active_stop"] == 102.0
    assert plan[2]["current_time"] == bars.index[7]
    assert plan[2]["active_stop"] == 109.0


def test_short_active_stop_is_mirrored_and_is_open_known():
    index = pd.date_range("2025-01-01", periods=9, freq="1h", tz="UTC")
    close = [97, 97, 97, 97, 96, 95, 88, 90, 89]
    bars = pd.DataFrame({
        "open": close, "high": [97.5, 97.5, 97.5, 97.5, 97.5, 96, 94, 90.5, 90],
        "low": [x - 1 for x in close], "close": close, "atr": [1] * len(close),
    }, index=index)
    plan = active_stop_plan(bars, signal_i=4, side=-1, floor_atr=2.0, trail_atr=3.0)
    assert plan[0]["current_time"] == bars.index[5]
    assert plan[0]["active_stop"] == 98.0
    assert plan[1]["active_stop"] == 98.0
    assert plan[2]["active_stop"] == 91.0
