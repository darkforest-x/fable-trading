"""V1 derivative contracts: frozen signal predicates and reference lifecycle.

These are source contracts, not a Pine compiler or a backtest. TradingView's
native compilation and visual acceptance remain explicit separate checks.
"""
import hashlib
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "yoyo/evaluation/pine/spike_burst_v1.pine"
DISPLAY = ROOT / "yoyo/evaluation/pine/spike_burst_v1_display.pine"
V6 = ROOT / "yoyo/evaluation/pine/spike_burst_v6.pine"
V7 = ROOT / "yoyo/evaluation/pine/spike_burst_v7.pine"
SOURCE_SHA = "18bbb6955fdf12e124688003799c44fc2a641f11a342edcf478157b1c9641fe2"
BEGIN, END = "// BEGIN BURST DISPLAY ONLY\n", "// END BURST DISPLAY ONLY\n"


def display_layer():
    source = DISPLAY.read_text()
    assert source.count(BEGIN) == source.count(END) == 1
    return source.split(BEGIN, 1)[1].split(END, 1)[0]


def _block(text: str, begin: str, end: str) -> str:
    return text.split(begin, 1)[1].split(end, 1)[0]


def test_frozen_source_is_pinned_and_derivative_keeps_exact_signal_helpers():
    original = BASE.read_bytes()
    assert hashlib.sha256(original).hexdigest() == SOURCE_SHA
    frozen, candidate = BASE.read_text(), DISPLAY.read_text()
    for begin, end in (
        ("// BEGIN BURST PURE HELPERS", "// END BURST PURE HELPERS"),
    ):
        assert _block(candidate, begin, end) == _block(frozen, begin, end)
    # The revision may alter reference ownership, never the strict V1 force
    # gates or their default thresholds.
    for line in (
        'float minVolume = input.float(4.0,',
        'float minExpansion = input.float(3.0,',
        'bool leadUp = canLead and direction != "空头"',
        'bool leadDown = canLead and direction != "多头"',
        'bool burst = leadingSide != 0 or f_burst(',
    ):
        assert line in candidate
    assert 'string direction = input.string("双向"' in candidate
    assert "reference-exit-r2" in candidate
    assert "behind_chart=false" in candidate
    assert 'color=color.new(signalColor, 91), textcolor=signalColor' in candidate
    assert '"入场参考 · "' not in candidate
    assert '"↑ 爆发"' not in candidate
    assert "下根初始保护" not in candidate


def test_display_is_after_state_machine_and_cannot_write_strategy_variables():
    source = DISPLAY.read_text()
    assert source.index(BEGIN) > source.index("// END BURST STATE MACHINE")
    body = display_layer()
    assigned = re.findall(r"\b([A-Za-z_]\w*)\s*:=", body)
    assert assigned and all(name.startswith("rr") for name in assigned)
    assert not re.search(r"\b(?:plot|fill|barcolor|bgcolor|hline|alertcondition|request\.security)\s*\(", body)
    # Only confirmed state-derived peak is consumed, never a current bar high.
    code = "\n".join(line.split("//", 1)[0] for line in body.splitlines())
    assert not re.search(r"\b(?:high|low|open|close)\b", code)
    assert "if barstate.isconfirmed and showRisk and rrShow" in code


def test_reverse_reference_is_confirmed_and_old_state_is_snapshotted_before_reset():
    state = _block(DISPLAY.read_text(), "// BEGIN BURST STATE MACHINE", "// END BURST STATE MACHINE")
    assert state.index("f_path(trendSide") < state.index("bool sameDirection = trendSide == side")
    assert "bool reverseExit = false" in DISPLAY.read_text()
    assert "exitPeakR := peakR" in state and "exitProtection := protection" in state
    assert "trendSide := 0" in state
    assert "trendSide := riskValid ? side : 0" in state
    assert "if pendingSide != 0 and (not endedThisBar or pendingSide != exitSide)" in state
    assert "if not sameDirection" in state
    assert '"R 已到"' not in state


def test_v1_risk_plot_suppresses_overlapping_initial_stop_when_box_is_visible():
    source = DISPLAY.read_text()
    assert "bool protectionOverlapsInitial" in source
    assert "color protectionColor = rrShow and protectionOverlapsInitial ? na" in source
    assert "color initialColor = rrShow ? na" in source


def test_r_reference_prices_are_visible_on_entry_without_new_plot_slots():
    """All chart variants pre-draw live 1R/2R/3R price references.

    The 400-line budget retains planned lines with all60 bounded groups;
    price captions are live-only and reached 3R/5R/10R remain historical.
    """
    for path in (DISPLAY, V6, V7):
        source = path.read_text()
        assert 'showMilestones = input.bool(true, "R 参考与里程碑"' in source
        assert "float targetR = targetSlot + 1.0" in source
        assert 'str.tostring(targetR, "#") + "R · " + str.tostring(targetPrice, format.mintick)' in source
        assert "预先显示的 " in source
        assert "color.new(bull, 72)" in source
        # The live 3R guide is replaced by the old reached milestone, avoiding
        # a duplicate line while preserving the 3R/5R/10R achievement record.
        assert "if slot == 0" in source
        assert "float level = slot == 0 ? 3.0 : slot == 1 ? 5.0 : 10.0" in source
        assert "line.delete(array.get(rrTargetLines, 2))" in source
        assert "max_lines_count=400" in source
        assert "array<line> targetLines" in source
        assert "label.delete(array.get(rrTargetTags, targetSlot))" in source

    # Lines and labels are created as drawings, never plot series. V6's
    # existing source-level plot ceiling protects V7's copied display layer.
    assert "plot(" not in display_layer()
    assert len(re.findall(r"\bplot(?:shape|candle)?\s*\(", V6.read_text())) == 47
    assert len(re.findall(r"\bplot(?:shape|candle)?\s*\(", V7.read_text())) == 33


def test_v6_and_v7_keep_the_same_frozen_stop_and_path_helpers():
    v6, v7 = V6.read_text(), V7.read_text()
    begin, end = "// BEGIN UNCHANGED V2 RISK HELPERS", "// END UNCHANGED V2 RISK HELPERS"
    assert _block(v6, begin, end) == _block(v7, begin, end)
