"""Static contracts for the closed-bar SPIKE MA-drift Pine indicator.

These assertions do not compile Pine or assess market performance. Native Pine
execution and visual inspection are intentionally owned by the integration task.
"""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
PINE = ROOT / "yoyo/evaluation/pine/spike_ma_drift_short_v1.pine"


def source() -> str:
    return PINE.read_text()


def code(text: str) -> str:
    return "\n".join(line.split("//", 1)[0] for line in text.splitlines())


def test_identity_fixed_rope_and_closed_bar_state_machine() -> None:
    text = source()
    assert text.startswith("//@version=6")
    assert 'indicator("SPIKE · 均线下压预警 V1", shorttitle="SPIKE 下压 V1", overlay=true' in text
    for line in (
        "float s20 = ta.sma(close, fastLen)",
        "float e20 = ta.ema(close, fastLen)",
        "float s60 = ta.sma(close, midLen)",
        "float e60 = ta.ema(close, midLen)",
        "float s120 = ta.sma(close, slowLen)",
        "float e120 = ta.ema(close, slowLen)",
    ):
        assert line in text
    assert "if barstate.isconfirmed\n    [nextPending," in text
    assert "// BEGIN PURE STEP HELPER" in text
    assert "// END PURE STEP HELPER" in text


def test_pre_warning_evidence_and_fixed_drift_geometry_are_causal() -> None:
    text = source()
    assert "const int blockLen = 3" in text
    assert "const int driftLen = 2 * blockLen" in text
    assert "const int compactLookback = 12" in text
    assert "float ropeWidthPriorAtr = atr[1] > 0 ? (ropeHigh - ropeLow) / atr[1] : na" in text
    assert "for offset = 1 to compactLookback - compactBars + 1" in text
    assert "float highBlockPrior = ta.sma(high[blockLen], blockLen)" in text
    assert "float lowBlockPrior = ta.sma(low[blockLen], blockLen)" in text
    assert "bool fastDown = s20 - s20[blockLen] < 0 and e20 - e20[blockLen] < 0" in text
    assert "bool mostlyUnderRope = underRopeCount >= math.ceil(driftLen * 0.60)" in text
    assert "float maxDriftTr = ta.highest(trPriorAtr, driftLen)" in text


def test_warning_freezes_wick_and_only_a_later_close_can_confirm() -> None:
    text = source()
    helper = text.split("// BEGIN PURE STEP HELPER\n", 1)[1].split("// END PURE STEP HELPER", 1)[0]
    assert "nextFrozenLow := setupLow" in helper
    assert "else if barId > priorWarnBar and breakout" in helper
    assert helper.index("if age > waitBars") < helper.index("else if reclaim") < helper.index("else if barId > priorWarnBar and breakout")
    assert "bool breakout = close < frozenLow and close < ropeLow" in text
    assert "f_step(pending, warnBar, frozenLow, lastEnd, ready, setup, reclaim, breakout, driftLow" in text
    assert "float frozenPlot = pending ? frozenLow : confirmSignal ? frozenBeforeStep : na" in text


def test_gap_warmup_cooldown_and_display_diagnostics_are_present() -> None:
    text = source()
    assert "time != time_close[1]" in text
    assert "int warmupBars = math.max(slowLen + atrLen, 3 * slowLen)" in text
    assert "segmentBars >= warmupBars" in text
    assert "barId - priorLastEnd > coolBars" in text
    assert 'plot(warningSignal ? 1 : 0, "预警诊断", display=display.data_window)' in text
    assert 'plot(confirmSignal ? 1 : 0, "确认诊断", display=display.data_window)' in text
    assert 'alertcondition(warningSignal, "SPIKE 下压预警"' in text
    assert 'alertcondition(confirmSignal, "SPIKE 下压确认"' in text


def test_no_future_data_or_execution_primitives() -> None:
    text = code(source())
    for forbidden in (
        "request.security",
        "lookahead",
        "varip",
        "ta.pivothigh",
        "ta.pivotlow",
        "strategy.",
    ):
        assert forbidden not in text
    assert not re.search(r"offset\s*=\s*-|\[\s*-", text)
