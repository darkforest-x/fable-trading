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
