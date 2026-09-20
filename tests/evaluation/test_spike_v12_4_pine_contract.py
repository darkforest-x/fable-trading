"""Bind the authorized 5m filter to the existing V12.3 entry/exit consumers.

The existing five-minute study tests timing, gap reset, EMA recurrence and
raw reverse exits. This source contract proves that the new Pine consumer
only inserts that gate and diagnostics, leaving all other behavior intact.
TradingView compilation and visible settings remain separate delivery checks.
"""
from pathlib import Path
import re

PINE = Path(__file__).resolve().parents[2] / "yoyo/evaluation/pine"


def test_parent_behavior_preserved_except_new_admission_and_diagnostics():
    old = (PINE / "spike_burst_v12_3.pine").read_text()
    new = (PINE / "spike_burst_v12_4.pine").read_text()
    stripped = re.sub(
        r"(?m)^ *// BEGIN CONFIRMED M15 EMA120 (\w+)\n.*?^ *// END CONFIRMED M15 EMA120 \1\n",
        "", new, flags=re.S,
    )
    stripped = stripped.replace("SPIKE V12.4", "SPIKE V12.3").replace('"V12.4 事件码', '"V12.3 事件码')
    stripped = stripped.replace('indicator("SPIKE V12.3 · 5m方向过滤"', 'indicator("SPIKE V12.3 · 15m · 1h SMA60"')
    stripped = stripped.replace(
        "// New private version: 5m uses completed 15m EMA120; 15m retains H1 SMA60.\n"
        "// Owner authorized the filter release; historical ranking is not validated profitability.",
        "// New private version: owner-selected H1 SMA60 admission on 15m only.",
    )
    stripped = stripped.replace(" and m15EmaEntryAllowed", "")
    assert stripped == old


def test_confirmed_m15_gate_is_only_in_entry_admission():
    s = (PINE / "spike_burst_v12_4.pine").read_text()
    assert 'bool useM15Ema120 = input.bool(true,' in s
    assert "useM15Ema120 and timeframe.isminutes and timeframe.multiplier == 5" in s
    assert "float alpha = 2.0 / 121.0" in s
    assert "average := not valid ? na : contiguous == 1 ? close : alpha * close + (1.0 - alpha) * average[1]" in s
    assert "float knownAverage = contiguous >= 1200 ? average : na" in s
    assert '[m15Ema120Raw, m15EmaCloseTime] = request.security(syminfo.tickerid, "15", f_m15Ema120(), gaps=barmerge.gaps_off, lookahead=barmerge.lookahead_on)' in s
    assert 'm15EmaCloseTime == time("15")' in s
    assert "rawSignalSide == 1 ? close > m15Ema120 : rawSignalSide == -1 ? close < m15Ema120 : false" in s
    assert "not m15EmaApplies or (m15EmaReady and m15EmaDirectionAllowed)" in s
    assert s.count("m15EmaEntryAllowed") == 2
    assert s.index("int rawSignalSide =") < s.index("bool m15EmaEntryAllowed") < s.index("confirmedSignal := confirmedSignal and v9EntryAllowed")
    assert "if rawSignalSide != 0 and (not endedThisBar or oppositeStopSignal) and rawSignalSide != trendSide" in s
    assert "if signalSide != 0 and validRisk" in s
    helper = s.split("// BEGIN CONFIRMED M15 EMA120 HELPER")[1].split("// END CONFIRMED M15 EMA120 HELPER")[0]
    assert "[knownAverage[1], time_close[1]]" in helper
