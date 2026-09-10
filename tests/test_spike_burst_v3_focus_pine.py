"""Static contracts for the separate SPIKE FOCUS Pine research port.

These checks do not compile Pine or validate market performance. The Python
gate/box suites own behavioral causality; native TradingView compilation and
visual parity remain separate root-task acceptance steps.
"""
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
PINE = ROOT / "yoyo/evaluation/pine/spike_burst_v3_focus.pine"
V3 = ROOT / "yoyo/evaluation/pine/spike_burst_v3_early_warning.pine"


def source():
    return PINE.read_text()


def block(begin, end):
    text = source()
    assert text.count(begin) == text.count(end) == 1
    return text.split(begin, 1)[1].split(end, 1)[0]


def code(text):
    return "\n".join(line.split("//", 1)[0] for line in text.splitlines())


@pytest.mark.parametrize("kind,name,value", [
    ("int","maLen","34"),("int","sigLen","9"),("int","denseLen","12"),
    ("float","denseWidth","3.0"),("int","denseCrosses","2"),
    ("int","breakoutLookback","12"),("int","cooldownBars","12"),
    ("int","confirmMaxAge","3"),("float","minAdvance","1.5"),("float","minVolume","1.5"),
    ("int","stopLen","5"),("float","stopBuffer","0.2"),("float","riskFloor","2.0"),
    ("float","armR","2.0"),("float","trailAtr","4.0"),
    ("int","minQuiet","12"),("float","nearAtr","0.10"),("int","releaseBars","6"),
])
def test_fixed_constants(kind,name,value):
    assert f"const {kind} {name} = {value}\n" in source()


def test_mode_selection_is_fixed_and_default_reference_gate():
    text=source()
    assert 'input.string("A · 同段去重", "固定研究模式", options=["A · 同段去重", "B · 完整蓄势"]' in text
    assert 'bool useReferenceGate = mode == "A · 同段去重"' in text
    assert "input.float(" not in text
    assert text.count("indicator(")==1 and text.startswith("//@version=6")


def test_risk_helpers_are_exact_original_v3_bytes():
    expected=V3.read_text().split("// BEGIN UNCHANGED V2 RISK HELPERS\n",1)[1].split("// END UNCHANGED V2 RISK HELPERS\n",1)[0]
    assert block("// BEGIN UNCHANGED V2 RISK HELPERS\n","// END UNCHANGED V2 RISK HELPERS\n")==expected


def test_mode_a_updates_path_before_deciding_the_parent():
    state=block("// BEGIN FOCUS DETECTION STATE","// END FOCUS DETECTION STATE")
    assert state.index("[gateAlive,") < state.index("earlySignal :=")
    assert "gateEndedThisBar := not gateAlive" in state
    assert "earlySignal := candidateEdge and cooldownPassed and (not useReferenceGate or (not gateActive and not gateEndedThisBar))" in state
    assert "bar_index > gateEntryBar" in state
    assert "gateEntry := close" in state
    assert "gateProtection := gateNewStop" in state
    assert state.count("gateActive := true")==1
    child=state.split("if not na(parentBar) and not parentConfirmed",1)[1]
    assert "gateActive :=" not in child and "gateEntry :=" not in child
    assert "confirmationAfterGateExit := useReferenceGate and not gateActive" in child
    # After-exit child upgrades are allowed, but cannot restart gate occupancy.
    child_condition=next(line for line in state.splitlines() if line.strip().startswith("if not na(parentBar) and not parentConfirmed"))
    assert "gateActive" not in child_condition


def test_accepted_only_cooldown_and_every_condition_edge_consumed():
    state=block("// BEGIN FOCUS DETECTION STATE","// END FOCUS DETECTION STATE")
    assert "candidateEdge := selectedCondition and not previousCondition" in state
    assert "previousCondition := selectedCondition" in state
    assert re.search(r"if earlySignal\n\s+lastAcceptedBar := bar_index",state)
    assert state.count("lastAcceptedBar := bar_index")==1
    assert "bar_index - lastAcceptedBar >= cooldownBars" in state
    assert "cooldownBlocked := candidateEdge and not cooldownPassed" in state


def test_mode_b_complete_run_freezes_at_release_and_expires_at_six():
    state=block("// BEGIN FOCUS DETECTION STATE","// END FOCUS DETECTION STATE")
    assert "nearBand := quietCount >= minQuiet ? frozenNearBand : nearAtr * previousNearAtr" in state
    assert "math.max(math.abs(md), math.abs(sb)) <= nearBand" in state
    assert re.search(r"if quietCount == minQuiet\n\s+frozenNearBand := nearBand",state)
    assert "quietHigh := math.max(quietHigh, high)" in state
    assert "quietLow := math.min(quietLow, low)" in state
    near=state.split("if nearZero",1)[1].split("else if quietCount > 0",1)[0]
    assert "setupHigh := quietHigh" in near
    assert "setupConsumed := false" in near and "setupReleased := false" in near
    departure=state.split("else if quietCount > 0",1)[1].split("int releaseAge",1)[0]
    assert "setupReleased := true" in departure and "releaseBar := bar_index" in departure
    assert "setupHigh := high" not in departure
    assert "releaseAge >= releaseBars" in state
    assert "and not setupConsumed and not setupExpired" in state
    assert "selectedCondition := setupEligible and close > setupHigh and close > math.max(s20, e20)" in state
    assert "selectedBoundary := setupHigh" in state
    assert re.search(r"else\n\s+setupConsumed := true",state)


def test_parent_upgrade_uses_current_close_and_frozen_selected_box():
    state=block("// BEGIN FOCUS DETECTION STATE","// END FOCUS DETECTION STATE")
    assert "parentHigh := selectedBoundary" in state and "parentPrice := close" in state
    assert "int age = na(parentBar) ? na : bar_index - parentBar" in state
    assert "age > confirmMaxAge" in state
    assert "age >= 0 and age <= confirmMaxAge and close > parentHigh and confirmQuality" in state
    assert "confirmationAge := age" in state
    quality=next(line for line in source().splitlines() if line.startswith("bool confirmQuality ="))
    assert quality=="bool confirmQuality = baseReady and recentDense and advance3 >= minAdvance and volumeRatio3 >= minVolume and md >= sb and middle > middle[1]"
    assert not any(gate in quality for gate in ("aboveSix","efficiency3","closePosition","md > 0"))


def test_gap_resets_all_detection_state_and_pre_gap_band():
    state=block("// BEGIN FOCUS DETECTION STATE","// END FOCUS DETECTION STATE")
    reset=state.split("if dataGap\n",1)[1].split("// Occupancy stop",1)[0]
    for field,value in (("gateActive","false"),("gateEntryBar","na"),("previousCondition","false"),
                        ("lastAcceptedBar","na"),("parentBar","na"),("parentHigh","na")):
        assert f"{field} := {value}" in reset
    assert "if dataGap or not baseReady" in state
    structure_reset=state.split("if dataGap or not baseReady",1)[1].split("nearBand :=",1)[0]
    for field in ("previousNearAtr","frozenNearBand","setupHigh","releaseBar"):
        assert field+" := na" in structure_reset
    assert "time != time_close[1]" in source()


def test_mutations_only_commit_on_closed_bars_and_no_future_data():
    state=block("// BEGIN FOCUS DETECTION STATE","// END FOCUS DETECTION STATE")
    assert "if barstate.isconfirmed\n" in state
    assert all(line.startswith("    ") for line in state.splitlines() if ":=" in line or "+=" in line)
    text=code(source())
    for forbidden in ("request.security","lookahead","varip","ta.pivothigh","ta.pivotlow","strategy.entry"):
        assert forbidden not in text
    assert not re.search(r"offset\s*=\s*-|\[\s*-\d",text)


def test_display_reference_only_starts_at_valid_live_confirmation():
    state=block("// BEGIN CONFIRMATION DISPLAY REFERENCE","// END CONFIRMATION DISPLAY REFERENCE")
    assert "if confirmedSignal and not confirmationAfterGateExit" in state
    assert "if earlySignal" not in state
    assert "entry := close" in state
    assert "f_risk(1, close, recentLow, atr, riskFloor, stopBuffer, syminfo.mintick)" in state
    assert not re.search(r"\bgate(?:Entry|Risk|Stop|Protection|Peak)\b",code(state))
    detection=block("// BEGIN FOCUS DETECTION STATE","// END FOCUS DETECTION STATE")
    assert not re.search(r"\b(?:trendSide|entryBar|referenceStarted|visibleEntry)\b",code(detection))
    rr=block("// BEGIN V2 RISK BOX DISPLAY","// END V2 RISK BOX DISPLAY")
    assert "if referenceStarted" in rr and "if earlySignal" not in rr
    assert "确认参考 · " in rr and "预警参考 · " not in rr
    assert "确认收盘可视参考，不是实际成交；与A隐藏去重参考分别计算。" in rr
    assert "参考切换" in rr
    assert "3R仅为观察位，不触发止盈" in rr


def test_small_observation_no_box_and_historical_upgrade_visible():
    text=source()
    labels=text.split("// Observation is intentionally tiny",1)[1].split("// BEGIN V2 RISK BOX DISPLAY",1)[0]
    assert 'label.new(bar_index, low, "观察"' in labels
    assert '"确认" + endedNote' in labels
    assert 'confirmationAfterGateExit ? " · 旧参考已结束"' in labels
    assert "size=size.small" not in labels and labels.count("size=size.tiny")==2
    assert "label.new(parentBar" not in text
    assert 'alertcondition(confirmedSignal and not confirmationAfterGateExit,' in text
    assert 'alertcondition(confirmedSignal and confirmationAfterGateExit,' in text
    assert "此标记不创建盈亏框" in labels


def test_six_ma_zero_double_lines_and_frozen_volume_windows():
    text=source()
    for line in V3.read_text().splitlines():
        if line.startswith(("plot(showMas ?","hline(0,","plot(shownMd,","plot(shownSb,")):
            assert line in text
    assert "plot.style_histogram" not in text
    assert "float volumeBase3 = volumeMedian[3]" in text
    assert "not na(volume) and not na(volume[1]) and not na(volume[2])" in text
    assert "if resetVolume\n    array.clear(validVolumes)" in text
    assert "segmentIndex >= denseLen" in text
    assert "去重参考 ≠ 确认价 ≠ 成交" in text
    assert "最新收盘" in text
