"""Synthetic causality contracts for the V5 closed-bar structural oracle."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.spike_burst_v5_structure import detect


ROOT = Path(__file__).resolve().parents[1]


def _section(source, begin, end):
    assert source.count(begin) == source.count(end) == 1
    return source.split(begin, 1)[1].split(end, 1)[0]


def fixture(n=24):
    """Closed, valid bars with no body support or V4 provenance by default."""
    return pd.DataFrame({
        "open": 99.8, "high": 101.0, "low": 99.0, "close": 100.5,
        "md": -0.5, "sb": -0.6, "atr": 1.0, "ropeHigh": 100.0,
        "legacy_confirmed": False, "legacy_parent_high": 100.5, "legacy_parent_low": 99.5,
        "ready": True, "data_gap": False, "confirmed": True,
    }, index=pd.RangeIndex(n))


def body(frame, i, md=-0.2, sb=-0.3):
    """Add a full body above the rope with a deliberately permitted lower wick."""
    frame.loc[i, ["open", "high", "low", "close", "md", "sb"]] = [101.0, 101.6, 99.0, 101.3, md, sb]


def legacy(frame, i, parent=100.5, parent_low=99.5, md=-0.2, sb=-0.3):
    frame.loc[i, ["open", "high", "low", "close", "md", "sb", "legacy_confirmed", "legacy_parent_high", "legacy_parent_low"]] = [99.8, 101.5, 99.0, 101.3, md, sb, True, parent, parent_low]


def hold_above_parent(frame, start, end, md=-0.2, sb=-0.3):
    """Keep a pending parent valid without creating a new full body."""
    frame.loc[start:end, ["open", "high", "low", "close", "md", "sb"]] = [99.8, 101.5, 99.0, 101.0, md, sb]


def test_pine_keeps_v4_feature_legacy_risk_and_visual_sections_frozen():
    v4 = (ROOT / "yoyo/evaluation/pine/spike_burst_v4.pine").read_text()
    v5 = (ROOT / "yoyo/evaluation/pine/spike_burst_v5.pine").read_text()
    assert _section(v5, "// BEGIN UNCHANGED V2 RISK HELPERS", "// END UNCHANGED V2 RISK HELPERS") == _section(v4, "// BEGIN UNCHANGED V2 RISK HELPERS", "// END UNCHANGED V2 RISK HELPERS")
    fields = _section(v5, "// BEGIN TWO STAGE FIELDS", "// END TWO STAGE FIELDS")
    fields = fields.replace("// Auxiliary parent-range support only. It does not alter V4 candidate,\n// confirmation-quality, or cooldown conditions.\nfloat priorLow = ta.lowest(low[1], breakoutLookback)\n", "")
    assert fields == _section(v4, "// BEGIN TWO STAGE FIELDS", "// END TWO STAGE FIELDS")
    legacy_engine = _section(v5, "// BEGIN LEGACY ENGINE", "// END LEGACY ENGINE")
    legacy_engine = legacy_engine.replace("// V5 intentionally does not display or alert this internal V4 confirmation.\n", "")
    legacy_engine = legacy_engine.replace(": retained byte-for-byte in behaviour for provenance only.", ": deliberately independent of risk-reference holding state.")
    for extra in (
        "var float parentLow = na\n",
        "float legacyConfirmationParentLow = na\n",
        "        parentLow := na\n",
        "        parentLow := priorLow\n",
        "        legacyConfirmationParentLow := parentLow\n",
    ):
        legacy_engine = legacy_engine.replace(extra, "")
    for old, new in (
        ("legacyEarlySignal", "earlySignal"),
        ("legacyConfirmedSignal", "confirmedSignal"),
        ("legacyConfirmationAge", "confirmationAge"),
        ("legacyConfirmationParentPrice", "confirmationParentPrice"),
        ("legacyConfirmationParentHigh", "confirmationParentHigh"),
    ):
        legacy_engine = legacy_engine.replace(old, new)
    assert legacy_engine == _section(v4, "// BEGIN ALERT STATE", "// END ALERT STATE")
    visible = _section(v5, "// BEGIN CONFIRMED DISPLAY", "// END CONFIRMED DISPLAY")
    assert "plotshape(confirmedSignal, title=\"确认多头\"" in visible
    assert "plotshape(confirmedShortSignal, title=\"确认空头\"" in visible
    assert "signalSide == 1 ? low - atr * 0.35 : high + atr * 0.35" in visible
    assert "offset=" not in v5
    assert "legacyConfirmedSignal" not in visible
    alerts = [line for line in v5.splitlines() if line.startswith("alertcondition(")]
    assert len(alerts) == 4 and all("legacy" not in line.lower() for line in alerts)
    assert "alertcondition(signalSide != 0, \"SPIKE V5 结构确认\"" in v5
    assert "alertcondition(confirmedSignal, \"SPIKE V5 多头结构确认\"" in v5
    assert "alertcondition(confirmedShortSignal, \"SPIKE V5 空头结构确认\"" in v5
    reference = _section(v5, "// BEGIN REFERENCE STATE", "// END REFERENCE STATE")
    assert "f_path(trendSide," in reference
    assert "if signalSide != 0 and trendSide == 0 and not endedThisBar" in reference
    assert "f_risk(signalSide, close, signalSide == 1 ? recentLow : recentHigh" in reference
    assert "trendSide := signalSide" in reference
    boxes = _section(v5, "// BEGIN V2 RISK BOX DISPLAY", "// END V2 RISK BOX DISPLAY")
    assert "int rrNewSide = trendSide" in boxes
    assert boxes.count("border_color=na, border_width=0") == 2
    assert "bool showMilestones = input.bool(true," in v5
    assert "barcolor(signalSide != 0 ? color.white : na" in v5
    for obsolete in ("minQuiet", "nearAtr", "releaseBars", "quietCount", "quietHigh", "quietLow", "frozenBand", "releaseBar"):
        assert obsolete not in v5
    assert "V5 本根V4旧确认诊断" in v5 and "V5 冻结V4父高" in v5 and "V5 冻结V4父低" in v5
    assert "V5 本根空头旧确认诊断" in v5 and "V5 空头最终确认" in v5 and "V5 空头等待原因代码" in v5
    assert "plot.style_histogram" not in v5 and "hline(0, \"零轴\"" in v5


def test_body_before_legacy_with_lower_wick_can_confirm_at_legacy_bar():
    f = fixture()
    body(f, 2, md=-0.4, sb=-0.5)
    legacy(f, 5, md=-0.2, sb=-0.3)
    out = detect(f)
    assert out.confirmed[out.confirmed].index.tolist() == [5]
    assert out.body_support_i.iloc[5] == 2
    assert out.legacy_i.iloc[5] == 5


def test_body_after_legacy_can_wait_more_than_six_bars_and_fires_on_body_bar():
    f = fixture()
    legacy(f, 2, md=-0.4, sb=-0.5)
    hold_above_parent(f, 3, 11, md=-0.3, sb=-0.4)
    body(f, 12, md=-0.2, sb=-0.3)
    out = detect(f)
    assert not out.confirmed.iloc[:12].any()
    assert out.confirmed.iloc[12]
    assert out.body_support_i.iloc[12] == 12


def test_open_below_rope_on_legacy_bar_waits_for_a_full_body():
    f = fixture()
    legacy(f, 4, md=-0.2, sb=-0.3)
    out = detect(f)
    assert not out.confirmed.iloc[4] and out.why_pending.iloc[4] == "await_body"
    body(f, 5, md=-0.1, sb=-0.2)
    assert detect(f).confirmed.iloc[5]


def test_close_at_rope_clears_body_but_keeps_valid_parent_waiting():
    f = fixture()
    body(f, 2, md=-0.4, sb=-0.5)
    legacy(f, 4, parent=99.5, md=0.0, sb=0.0)
    f.loc[5, ["open", "high", "low", "close", "md", "sb"]] = [99.8, 101.0, 99.0, 100.0, 0.0, 0.0]
    out = detect(f)
    assert pd.isna(out.body_support_i.iloc[5])
    assert out.pending.iloc[5] and out.why_pending.iloc[5] == "await_body"
    body(f, 6, md=0.1, sb=0.0)
    assert detect(f).confirmed.iloc[6]


def test_retest_below_parent_high_but_above_low_survives_for_later_full_body():
    f = fixture()
    legacy(f, 2, md=-0.4, sb=-0.5)
    f.loc[3, ["open", "high", "low", "close", "md", "sb"]] = [99.8, 101.0, 99.0, 100.25, -0.3, -0.4]
    out = detect(f)
    assert out.pending.iloc[3] and out.why_pending.iloc[3] == "await_body"
    body(f, 4, md=-0.2, sb=-0.3)
    assert detect(f).confirmed.iloc[4]
    equal_low = fixture()
    legacy(equal_low, 2, md=-0.4, sb=-0.5)
    equal_low.loc[3, ["open", "high", "low", "close", "md", "sb"]] = [99.8, 101.0, 99.0, 99.5, -0.3, -0.4]
    assert detect(equal_low).pending.iloc[3]


def test_close_strictly_below_frozen_parent_low_cancels_old_pending_provenance():
    f = fixture()
    legacy(f, 3, parent=101.4, parent_low=101.2, md=-0.3, sb=-0.4)
    f.loc[4, ["open", "high", "low", "close", "md", "sb"]] = [99.8, 101.5, 99.0, 101.1, -0.2, -0.3]
    out = detect(f)
    assert out.why_pending.iloc[4] == "parent_broken"
    body(f, 5, md=-0.1, sb=-0.2)
    assert not detect(f).confirmed.any()


def test_flat_md_does_not_finalise_but_a_later_upturn_can_and_negative_md_is_allowed():
    f = fixture()
    body(f, 2, md=-0.4, sb=-0.5)
    legacy(f, 4, md=0.0, sb=0.0)
    hold_above_parent(f, 5, 5, md=0.0, sb=0.0)
    hold_above_parent(f, 6, 6, md=0.1, sb=0.0)
    out = detect(f)
    assert not out.confirmed.iloc[4:6].any() and out.confirmed.iloc[6]
    f = fixture()
    body(f, 2, md=-0.4, sb=-0.5)
    legacy(f, 4, md=-0.2, sb=-0.3)
    assert detect(f).confirmed.iloc[4]


def test_no_event_without_legacy_and_repeated_bars_do_not_duplicate():
    f = fixture()
    body(f, 2, md=-0.4, sb=-0.5)
    f.loc[4:8, ["md", "sb"]] = [[-0.2, -0.3], [-0.1, -0.2], [0.0, -0.1], [0.1, 0.0], [0.2, 0.1]]
    assert not detect(f).confirmed.any()
    legacy(f, 4, md=-0.2, sb=-0.3)
    out = detect(f)
    assert out.confirmed.iloc[4] and out.confirmed.sum() == 1


def test_new_legacy_event_can_confirm_in_the_same_continuous_body_run():
    f = fixture()
    body(f, 2, md=-0.4, sb=-0.5)
    legacy(f, 4, md=-0.2, sb=-0.3)
    legacy(f, 9, md=0.2, sb=0.1)
    out = detect(f)
    assert out.confirmed[out.confirmed].index.tolist() == [4, 9]


@pytest.mark.parametrize("column, value, why", [("data_gap", True, "gap"), ("atr", np.nan, "unknown")])
def test_gap_or_unknown_clears_body_and_pending_provenance(column, value, why):
    f = fixture()
    legacy(f, 3, md=-0.3, sb=-0.4)
    f.loc[4, column] = value
    body(f, 5, md=-0.1, sb=-0.2)
    out = detect(f)
    assert out.why_pending.iloc[4] == why
    assert not out.pending.iloc[4] and pd.isna(out.legacy_i.iloc[4])
    assert not out.confirmed.any()


def test_unknown_or_inverted_legacy_provenance_never_arms_and_prior_atr_is_not_required():
    f = fixture()
    f.loc[3, "legacy_confirmed"] = True
    f.loc[3, "legacy_parent_low"] = np.nan
    body(f, 4, md=-0.2, sb=-0.3)
    assert not detect(f).confirmed.any()
    f = fixture()
    legacy(f, 3, parent=99.0, parent_low=100.0, md=-0.3, sb=-0.4)
    body(f, 4, md=-0.2, sb=-0.3)
    assert not detect(f).confirmed.any()
    f = fixture()
    f.loc[3, "atr"] = np.nan
    legacy(f, 4, md=-0.2, sb=-0.3)
    f.loc[4, "open"] = 101.0
    assert detect(f).confirmed.iloc[4]


def test_unconfirmed_tip_is_ignored_interior_tip_is_rejected_and_prefix_is_causal():
    f = fixture()
    body(f, 2, md=-0.4, sb=-0.5)
    legacy(f, 4, md=-0.2, sb=-0.3)
    baseline = detect(f)
    for end in (1, 4, 5, 12):
        pd.testing.assert_frame_equal(detect(f.iloc[:end]), baseline.iloc[:end])
    changed = f.copy()
    changed.loc[10:, ["open", "high", "low", "close", "md", "sb"]] = [200.0, 201.0, 199.0, 200.0, 10.0, 1.0]
    pd.testing.assert_frame_equal(detect(changed).iloc[:10], baseline.iloc[:10])
    f.loc[len(f) - 1, "confirmed"] = False
    assert not detect(f).confirmed.iloc[-1]
    f.loc[5, "confirmed"] = False
    with pytest.raises(ValueError, match="final tip"):
        detect(f)
