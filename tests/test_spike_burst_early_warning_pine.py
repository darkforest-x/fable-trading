"""V3 Pine source contracts, not a native compiler or market-performance test.

Protect the frozen two-stage intervention from accidental V2 gates, backdating,
reference-holding suppression, and changed risk algebra. Behavioral causality
is tested separately in the Python implementation; native TV acceptance remains
an explicit separate delivery step.
"""
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
PINE = ROOT / "yoyo/evaluation/pine/spike_burst_v3_early_warning.pine"
V2 = ROOT / "yoyo/evaluation/pine/spike_burst_v2_progressive.pine"


def _source():
    return PINE.read_text()


def _block(begin, end):
    text = _source()
    assert text.count(begin) == text.count(end) == 1
    return text.split(begin, 1)[1].split(end, 1)[0]


def _code(text):
    return "\n".join(line.split("//", 1)[0] for line in text.splitlines())


@pytest.mark.parametrize("kind,name,value", [
    ("int", "maLen", "34"), ("int", "sigLen", "9"),
    ("int", "denseLen", "12"), ("float", "denseWidth", "3.0"),
    ("int", "denseCrosses", "2"), ("int", "breakoutLookback", "12"),
    ("int", "cooldownBars", "12"), ("int", "confirmMaxAge", "3"),
    ("float", "minAdvance", "1.5"), ("float", "minVolume", "1.5"),
    ("int", "stopLen", "5"), ("float", "stopBuffer", "0.2"),
    ("float", "riskFloor", "2.0"), ("float", "armR", "2.0"),
    ("float", "trailAtr", "4.0"),
])
def test_frozen_hypothesis_and_risk_constants(kind, name, value):
    assert f"const {kind} {name} = {value}\n" in _source()


def test_early_trigger_contains_only_ready_breakout_and_two_fast_mas():
    text = _source()
    trigger = next(line for line in text.splitlines() if line.startswith("bool earlyRaw ="))
    assert trigger == "bool earlyRaw = ready and close > priorHigh and close > math.max(s20, e20)"
    assert "float priorHigh = ta.highest(high[1], breakoutLookback)" in text
    # Full-condition rising edge and unconditional accepted-signal cooldown.
    state = _block("// BEGIN ALERT STATE:", "// END ALERT STATE")
    assert "earlySignal := earlyRaw and not previousEarlyRaw and cooldownPassed" in state
    assert "previousEarlyRaw := earlyRaw" in state
    assert "bar_index - lastAcceptedBar >= cooldownBars" in state
    assert re.search(r"if earlySignal\n\s+lastAcceptedBar := bar_index", state)
    assert not re.search(r"\b(?:trendSide|risk|entryBar|protection|referenceStarted|peakR)\b", _code(state.split("\n", 1)[1]))


def test_confirmation_parent_time_and_quality_are_frozen():
    text = _source()
    quality = next(line for line in text.splitlines() if line.startswith("bool confirmQuality ="))
    assert quality == "bool confirmQuality = ready and recentDense and advance3 >= minAdvance and volumeRatio3 >= minVolume and md >= sb and middle > middle[1]"
    assert not any(name in quality for name in ("efficiency3", "closePosition", "aboveSix", "open", "md > 0"))
    state = _block("// BEGIN ALERT STATE:", "// END ALERT STATE")
    assert "parentHigh := priorHigh" in state
    assert "parentPrice := close" in state
    assert "int age = na(parentBar) ? na : bar_index - parentBar" in state
    assert "age > confirmMaxAge" in state
    assert "age >= 0 and age <= confirmMaxAge and close > parentHigh and confirmQuality" in state
    assert re.search(r"confirmedSignal := true[\s\S]*?confirmationAge := age[\s\S]*?parentConfirmed := true", state)
    assert state.index("parentHigh := priorHigh") < state.index("confirmedSignal := true")
    assert "and not parentConfirmed and age >= 0" in state


def test_all_alert_mutations_are_in_confirmed_bar_block():
    state = _block("// BEGIN ALERT STATE:", "// END ALERT STATE")
    assignments = [line for line in state.splitlines() if ":=" in line]
    assert assignments and all(line.startswith("    ") for line in assignments)
    assert "if barstate.isconfirmed\n" in state
    assert _source().index("// END ALERT STATE") < _source().index("// BEGIN REFERENCE STATE")


def test_gap_clears_alert_context_without_changing_v1_recursive_features():
    text = _source()
    assert "time != time_close[1]" in text
    assert "bar_index >= math.max(340, 10 * maLen)" in text
    assert "bool ready = baseReady and segmentIndex >= breakoutLookback" in text
    assert "segmentIndex >= denseLen" in text
    state = _block("// BEGIN ALERT STATE:", "// END ALERT STATE")
    assert "if dataGap\n        lastAcceptedBar := na" in state
    reset = state.split("if dataGap", 1)[1].split("bool cooldownPassed", 1)[0]
    for field, value in (("previousEarlyRaw", "false"), ("parentBar", "na"), ("parentHigh", "na"), ("parentPrice", "na")):
        assert f"{field} := {value}" in reset
    assert "float tr = ta.tr(true)" in text
    for declaration in ("float smHigh = f_smma(high, maLen)",
                        "float e20 = ta.ema(close, 20)"):
        assert declaration in text


def test_v2_progressive_volume_and_density_windows_exclude_current_context():
    text = _source()
    assert "float pastWidth = ta.sma(width[1], denseLen)" in text
    assert "float pastCrosses = math.sum(flips[1], denseLen)" in text
    assert "float denseHitsRaw = math.sum(dense[1] ? 1.0 : 0.0, breakoutLookback)" in text
    assert "float denseHits = segmentIndex >= breakoutLookback ? denseHitsRaw : na" in text
    assert "float advance3 = atr[3] > 0 ? move3 / atr[3] : na" in text
    assert "float volumeBase3 = volumeMedian[3]" in text
    assert "not na(volume) and not na(volume[1]) and not na(volume[2])" in text
    assert "volumeSum3 / (3.0 * volumeBase3)" in text
    # Missing volumes are skipped for baseline, but no pre-gap baseline leaks.
    assert "if resetVolume\n    array.clear(validVolumes)" in text
    assert "array.size(validVolumes) == 20 ? array.median(validVolumes) : na" in text


def test_risk_helpers_are_exactly_frozen_v2_bytes_and_alerts_never_reset_hold():
    old = V2.read_text()
    expected = old.split("// Frozen signal-close inputs only;", 1)[1].split("// END BURST PURE HELPERS", 1)[0]
    actual = _block("// BEGIN UNCHANGED V2 RISK HELPERS\n", "// END UNCHANGED V2 RISK HELPERS\n")
    assert actual == "// Frozen signal-close inputs only;" + expected
    state = _block("// BEGIN REFERENCE STATE:", "// END REFERENCE STATE")
    assert "if earlySignal and trendSide == 0 and not endedThisBar" in state
    assert "f_risk(1, close, recentLow, atr, riskFloor, stopBuffer, syminfo.mintick)" in state
    assert "if ready and trendSide != 0 and bar_index > entryBar" in state
    display = _block("// BEGIN V2 RISK BOX DISPLAY", "// END V2 RISK BOX DISPLAY")
    assert "if referenceStarted" in display
    assert "if earlySignal" not in display
    assert "bool rrEnded = exitReference or referenceGap" in display
    assert "3R仅为观察位，不触发止盈" in display


def test_same_bar_combined_label_and_delayed_confirmation_use_actual_price():
    text = _source()
    labels = text.split("// One main-chart label", 1)[1].split("// BEGIN V2 RISK BOX DISPLAY", 1)[0]
    assert 'confirmedSignal ? "结构预警 · 已确认" : "结构预警 · 待确认"' in labels
    assert "else if confirmedSignal" in labels
    assert labels.count("label.new(bar_index, low,") == 2
    assert 'str.tostring(close, format.mintick)' in labels
    assert 'str.tostring(confirmationAge)' in labels
    assert "跟踪中 · 新预警" in labels
    assert "str.format_time(time_close," in labels
    assert "label.new(parentBar" not in text


def test_visual_contract_retains_six_ma_styles_zero_and_two_lines():
    text, old = _source(), V2.read_text()
    for line in old.splitlines():
        if line.startswith("plot(showMas ?") or line.startswith("hline(0,") or line.startswith(("plot(shownMd,", "plot(shownSb,")):
            assert line in text
    assert "barstate.isconfirmed ? md : md[1]" in text
    assert "barstate.isconfirmed ? sb : sb[1]" in text
    assert "input.bool(true, \"预警与确认标签\"" in text
    assert "SPIKE V3 · 最新收盘" in text
    assert "本脚本未计算" in text
    assert "plot.style_histogram" not in text


def test_source_forbids_future_offsets_cross_timeframe_and_short_alerts():
    code = _code(_source())
    for forbidden in ("request.security", "lookahead", "varip", "ta.pivothigh", "ta.pivotlow", "strategy.entry"):
        assert forbidden not in code
    assert not re.search(r"offset\s*=\s*-|\[\s*-\d", code)
    assert 'alertcondition(earlySignal,' in code
    assert 'alertcondition(confirmedSignal,' in code
    assert "burstDown" not in code and "alertcondition(exitDown" not in code
