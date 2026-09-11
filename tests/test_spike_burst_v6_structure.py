"""Causality contracts for V6's one pending-window volume-price addition."""

from pathlib import Path
import re

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_v6_structure import detect


ROOT = Path(__file__).resolve().parents[1]


def _section(source: str, begin: str, end: str) -> str:
    assert source.count(begin) == source.count(end) == 1
    return source.split(begin, 1)[1].split(end, 1)[0]


def fixture(n: int = 12) -> pd.DataFrame:
    return pd.DataFrame({
        "open": 99.8, "high": 101.0, "low": 99.0, "close": 100.5,
        "md": -0.5, "sb": -0.6, "atr": 1.0, "ropeHigh": 100.0,
        "legacy_confirmed": False, "legacy_parent_high": 101.0,
        "legacy_parent_low": 99.5, "ready": True, "data_gap": False,
        "confirmed": True, "advance3": 1.5, "volume_ratio3": 1.5,
    }, index=pd.RangeIndex(n))


def shape(frame: pd.DataFrame, i: int, side: int = 1) -> None:
    if side == 1:
        frame.loc[i, ["open", "high", "low", "close"]] = [101.0, 101.7, 100.9, 101.6]
    else:
        frame.loc[i, ["open", "high", "low", "close"]] = [99.0, 99.1, 98.3, 98.4]


def legacy(frame: pd.DataFrame, i: int, side: int = 1) -> None:
    frame.loc[i, "legacy_confirmed"] = True
    if side == 1:
        frame.loc[i, ["open", "high", "low", "close", "md", "sb"]] = [100.5, 101.0, 100.0, 100.7, -0.2, -0.3]
    else:
        frame.loc[i, ["open", "high", "low", "close", "md", "sb"]] = [99.5, 100.0, 99.0, 99.3, 0.2, 0.3]


def final_weak(frame: pd.DataFrame, i: int, side: int = 1) -> None:
    if side == 1:
        frame.loc[i, ["open", "high", "low", "close", "md", "sb"]] = [100.5, 102.5, 99.5, 101.2, -0.1, -0.2]
    else:
        frame.loc[i, ["open", "high", "low", "close", "md", "sb"]] = [99.5, 100.5, 97.5, 98.8, 0.1, 0.2]


def test_seeded_prior_shape_allows_later_joint_completion_without_final_bar_shape():
    frame = fixture()
    shape(frame, 2)
    frame.loc[2, "md"] = -0.4
    legacy(frame, 3)
    final_weak(frame, 4)
    result = detect(frame)
    assert result.confirmed[result.confirmed].index.tolist() == [4]
    assert result.evidence.iloc[3] and result.evidence_i.iloc[3] == 2
    assert result.consumed_evidence.iloc[4] and result.consumed_evidence_i.iloc[4] == 2
    assert result.why_pending.iloc[3] == "await_momentum"


def test_missing_shape_explicitly_rejects_otherwise_v5_like_completion_then_later_latches():
    frame = fixture()
    legacy(frame, 3)
    final_weak(frame, 4)
    result = detect(frame)
    assert not result.confirmed.any()
    assert result.pending.iloc[4] and not result.evidence.iloc[4]
    assert result.why_pending.iloc[4] == "await_launch_evidence"
    shape(frame, 5)
    frame.loc[5, ["md", "sb"]] = [0.0, -0.1]
    assert detect(frame).confirmed.iloc[5]


def test_later_shape_requires_existing_rolling_envelope_and_gap_or_parent_break_clears_it():
    frame = fixture()
    legacy(frame, 3)
    final_weak(frame, 4)
    shape(frame, 5)
    frame.loc[5, ["advance3", "volume_ratio3"]] = [1.4, 1.5]
    assert not detect(frame).evidence.iloc[5]
    frame.loc[5, ["advance3", "md", "sb"]] = [1.5, 0.0, -0.1]
    accepted = detect(frame)
    assert accepted.confirmed.iloc[5]

    frame = fixture()
    legacy(frame, 3)
    frame.loc[4, "data_gap"] = True
    shape(frame, 5)
    assert not detect(frame).confirmed.any()


def test_flat_candle_is_non_evidence_and_new_provenance_cannot_seed_across_gap():
    frame = fixture()
    frame.loc[2, ["open", "high", "low", "close"]] = [101.0, 101.0, 101.0, 101.0]
    legacy(frame, 3)
    result = detect(frame)
    assert result.pending.iloc[3] and not result.evidence.iloc[3]

    frame = fixture()
    shape(frame, 2)
    frame.loc[3, "data_gap"] = True
    legacy(frame, 4)
    result = detect(frame)
    assert result.pending.iloc[4] and not result.evidence.iloc[4]
    assert result.why_pending.iloc[4] == "await_launch_evidence"
    frame = fixture()
    legacy(frame, 3)
    frame.loc[4, "close"] = 99.4
    shape(frame, 5)
    assert not detect(frame).confirmed.any()


def test_short_is_exact_reflection_and_prefix_is_causal():
    long = fixture()
    shape(long, 2)
    long.loc[2, "md"] = -0.4
    legacy(long, 3)
    final_weak(long, 4)
    baseline = detect(long)
    short = long.copy()
    short["open"] = 200.0 - long.open
    short["close"] = 200.0 - long.close
    short["high"] = 200.0 - long.low
    short["low"] = 200.0 - long.high
    short["ropeLow"] = 200.0 - long.ropeHigh
    short["legacy_parent_high"] = 200.0 - long.legacy_parent_low
    short["legacy_parent_low"] = 200.0 - long.legacy_parent_high
    short["md"] = -long.md
    short["sb"] = -long.sb
    short["advance3"] = -long.advance3
    reflected = detect(short, side=-1)
    for column in ("confirmed", "pending", "evidence", "consumed_evidence", "legacy_i", "body_support_i", "why_pending"):
        pd.testing.assert_series_equal(baseline[column], reflected[column])
    changed = long.copy()
    changed.loc[8:, ["open", "high", "low", "close", "md", "sb"]] = [200, 201, 199, 200, 9, 1]
    pd.testing.assert_frame_equal(detect(changed).iloc[:8], baseline.iloc[:8])


def test_pine_keeps_signal_contract_and_has_confirmed_reverse_reference_lifecycle():
    source = (ROOT / "yoyo/evaluation/pine/spike_burst_v6.pine").read_text()
    v5 = (ROOT / "yoyo/evaluation/pine/spike_burst_v5.pine").read_text()
    assert 'indicator("SPIKE V6 · 量价结构确认", shorttitle="SPIKE V6"' in source
    assert "const float minBody = 0.55" in source and "const float minEnd = 0.75" in source
    assert "RV>=4" in source and "TR>=3" in source
    assert "longLegacySeedEvidence" in source and "longEvidenceNow" in source
    assert "await_launch_evidence" in source
    assert 'consumedLaunchEvidence' in source and 'consumedLaunchEvidenceBar' in source
    assert 'confirmedSignal ? consumedLaunchEvidence : launchEvidence' in source
    assert source.count("alertcondition(") == 5
    assert "showExitLabels = input.bool(false" in source
    assert "int rrKeep = input.int(60" in source
    assert "input.color(color.white," in source
    assert "wickcolor=signalColor, bordercolor=signalColor" in source
    assert "bool showPanel = input.bool(true" in source
    assert "label.new(bar_index, signalSide == 1 ? low - atr * 0.35 : high + atr * 0.35, str.tostring(close, format.mintick)" in source
    assert _section(source, "// BEGIN UNCHANGED V2 RISK HELPERS", "// END UNCHANGED V2 RISK HELPERS") == _section(v5, "// BEGIN UNCHANGED V2 RISK HELPERS", "// END UNCHANGED V2 RISK HELPERS")
    state = _section(source, "// BEGIN REFERENCE STATE", "// END REFERENCE STATE")
    display = _section(source, "// BEGIN V2 RISK BOX DISPLAY", "// END V2 RISK BOX DISPLAY")
    assert state.index("f_path(trendSide") < state.index("bool oppositeStopSignal")
    assert "bool referenceReverse = false" in state
    assert "exitPeakR := peakR" in state and "exitProtection := protection" in state
    assert "if signalSide != 0 and (not endedThisBar or oppositeStopSignal) and signalSide != trendSide" in state
    assert "if trendSide != 0" in state and "referenceReverse := true" in state
    assert display.index("if not na(activeRR)") < display.index("if referenceStarted")
    assert "float groupPeakR = ended ? exitPeakR : peakR" in display
    assert "label.set_text(activeRR.entryTag, str.tostring(activeRR.entryPrice, format.mintick))" in display
    assert "V6 参考结束事件（1保护 / 2反向）" in source
    assert 'alertcondition(referenceReverse, "SPIKE 参考反向结束"' in source


def test_plot_call_floor_leaves_room_for_pine_multi_series_plot_costs():
    source = (ROOT / "yoyo/evaluation/pine/spike_burst_v6.pine").read_text()
    # This is intentionally only a source-level floor: TV can charge multiple
    # plot slots to multi-series calls. Native compilation remains authoritative.
    calls = re.findall(r"\bplot(?:shape|candle)?\s*\(", source)
    assert len(calls) == 47
    for removed in ("V6 本根V4旧确认诊断", "V6 最终确认", "V6 空头最终确认"):
        assert removed not in source
