"""Synthetic causality contracts for the V5 closed-bar structural oracle."""

import numpy as np
import pandas as pd
import pytest
from pathlib import Path

from yoyo.evaluation.spike_burst_v5_structure import detect


ROOT = Path(__file__).resolve().parents[1]


def _section(source, begin, end):
    assert source.count(begin) == source.count(end) == 1
    return source.split(begin, 1)[1].split(end, 1)[0]


def test_pine_keeps_v4_risk_visuals_but_hides_legacy_alerts_and_never_backfills():
    v4 = (ROOT / "yoyo/evaluation/pine/spike_burst_v4.pine").read_text()
    v5 = (ROOT / "yoyo/evaluation/pine/spike_burst_v5.pine").read_text()
    assert _section(v5, "// BEGIN UNCHANGED V2 RISK HELPERS", "// END UNCHANGED V2 RISK HELPERS") == _section(v4, "// BEGIN UNCHANGED V2 RISK HELPERS", "// END UNCHANGED V2 RISK HELPERS")
    assert _section(v5, "// BEGIN REFERENCE STATE", "// END REFERENCE STATE") == _section(v4, "// BEGIN REFERENCE STATE", "// END REFERENCE STATE")
    assert _section(v5, "// BEGIN V2 RISK BOX DISPLAY", "// END V2 RISK BOX DISPLAY") == _section(v4, "// BEGIN V2 RISK BOX DISPLAY", "// END V2 RISK BOX DISPLAY")
    visible = _section(v5, "// BEGIN CONFIRMED DISPLAY", "// END CONFIRMED DISPLAY")
    assert "plotshape(confirmedSignal," in visible and "offset=" not in v5
    assert "legacyConfirmedSignal" not in visible
    alerts = [line for line in v5.splitlines() if line.startswith("alertcondition(")]
    assert len(alerts) == 2 and all("legacy" not in line.lower() for line in alerts)
    for color in ("#008F82", "#D34B66", "#AD7B29", "#4679C9", "#CB882A", "#00CBB1"):
        assert color in v5
    assert "hline(0, \"零轴\"" in v5 and "plot.style_histogram" not in v5


def fixture(n=24):
    """One warmup, twelve quiet bars, then a positive release at bar 13."""
    f = pd.DataFrame({
        "open": 99.5, "high": 101.1, "low": 99., "close": 100.,
        "md": 0., "sb": 0., "atr": 1., "ropeHigh": 100.,
        "legacy_confirmed": False, "legacy_parent_high": 100.5,
        "ready": True, "data_gap": False, "confirmed": True,
    }, index=pd.RangeIndex(n))
    f.loc[1:12, ["open", "high", "low", "close"]] = [101., 101.1, 99., 101.]
    f.loc[12, "legacy_confirmed"] = True
    f.loc[13, ["open", "high", "low", "close", "md", "sb"]] = [101.5, 102.2, 100.8, 102., .3, .1]
    return f


def test_body_before_legacy_can_confirm_on_release_without_current_full_body():
    f = fixture()
    # The release opens above the rope but is not a full body above it.
    f.loc[13, ["open", "low"]] = [99.8, 99.]
    out = detect(f)
    assert out.confirmed[out.confirmed].index.tolist() == [13]
    assert out.body_support_i.iloc[13] == 12
    assert out.legacy_i.iloc[13] == 12


def test_body_after_legacy_delays_event_until_its_actual_closed_bar():
    f = fixture()
    f.loc[1:12, "open"] = 99.8  # no body support before V4 provenance at 12
    f.loc[13, ["open", "close", "high", "low", "md", "sb"]] = [99.8, 102., 102.2, 99., .3, .1]
    f.loc[14, ["open", "close", "high", "md", "sb"]] = [101.2, 103., 103.2, .4, .1]
    out = detect(f)
    assert not out.confirmed.iloc[13] and out.confirmed.iloc[14]
    assert out.body_support_i.iloc[14] == 14


def test_legacy_before_maturity_and_wrong_stage_body_do_not_borrow_into_episode():
    f = fixture()
    f.loc[5, "legacy_confirmed"] = True
    f.loc[12, "legacy_confirmed"] = False
    f.loc[1:12, "open"] = 99.8
    out = detect(f)
    assert not out.confirmed.any()
    assert pd.isna(out.legacy_i.iloc[13])
    assert out.why_pending.iloc[13] == "await_legacy"


def test_no_flat_or_down_release_and_full_quiet_range_breakout_are_required():
    f = fixture()
    f.loc[13, ["md", "sb"]] = [0., 0.]
    assert not detect(f).confirmed.any()
    f = fixture(); f.loc[13, "md"] = -.3
    assert detect(f).why_pending.iloc[13] == "down_release"
    f = fixture(); f.loc[13, "close"] = 101.1  # equals/inside full quiet high
    assert not detect(f).confirmed.any()


def test_close_under_rope_invalidates_old_body_then_a_new_body_can_restore_it():
    f = fixture()
    f.loc[13, ["open", "close", "high", "low", "md", "sb"]] = [99., 100., 101.2, 99., .2, .1]
    # close at rope clears the quiet body support and cannot be confirmation.
    f.loc[14, ["open", "close", "high", "md", "sb"]] = [101., 103., 103.2, .3, .1]
    out = detect(f)
    assert pd.isna(out.body_support_i.iloc[13])
    assert out.confirmed.iloc[14]


def test_expiry_gap_unknown_and_new_quiet_episode_reset_provenance():
    f = fixture()
    f.loc[12:13, "legacy_confirmed"] = False
    f.loc[14:19, ["open", "close", "high", "md", "sb"]] = [99.8, 102., 102.2, .3, .1]
    out = detect(f)
    assert out.why_pending.iloc[19] == "expired"  # release ages 0..5 only
    f = fixture(); f.loc[13, "legacy_confirmed"] = False; f.loc[14, "data_gap"] = True
    out = detect(f)
    assert out.why_pending.iloc[14] == "gap" and pd.isna(out.legacy_i.iloc[14])
    f = fixture(); f.loc[13, "legacy_confirmed"] = False; f.loc[14, "atr"] = np.nan
    assert detect(f).why_pending.iloc[14] == "unknown"
    f = fixture(); f.loc[13, "legacy_confirmed"] = False; f.loc[14, ["md", "sb"]] = [0., 0.]
    out = detect(f)
    assert out.structure_id.iloc[14] == 2 and pd.isna(out.legacy_i.iloc[14])


def test_one_event_per_episode_same_bar_association_and_prefix_future_invariance():
    f = fixture()
    # Body and legacy may both become known on the release close.
    f.loc[1:12, "open"] = 99.8
    f.loc[12, "legacy_confirmed"] = False
    f.loc[13, ["open", "legacy_confirmed"]] = [101.5, True]
    out = detect(f)
    assert out.confirmed.iloc[13] and out.confirmed.sum() == 1
    f.loc[14, ["open", "close", "high", "md", "sb"]] = [101.5, 104., 104.2, .5, .1]
    assert detect(f).confirmed.sum() == 1
    baseline = detect(f)
    for end in (1, 12, 13, 14, 18):
        pd.testing.assert_frame_equal(detect(f.iloc[:end]), baseline.iloc[:end])
    changed = f.copy()
    changed.loc[15:, ["open", "high", "low", "close", "md", "sb"]] = [200., 201., 199., 200., 10., 1.]
    pd.testing.assert_frame_equal(detect(changed).iloc[:15], baseline.iloc[:15])


def test_pending_keeps_frozen_band_and_unknown_boolean_or_provenance_fails_closed():
    f = fixture()
    f.loc[12, "legacy_confirmed"] = False
    f.loc[13, ["md", "sb"]] = [.11, .30]  # outside frozen .10, inside a recomputed .20
    f.loc[13, "atr"] = 2.
    out = detect(f)
    assert out.pending.iloc[13] and out.why_pending.iloc[13] == "await_legacy"
    f = fixture(); f.loc[12, "legacy_parent_high"] = np.nan
    assert not detect(f).confirmed.any()
    f = fixture(); f["ready"] = f["ready"].astype(object); f.loc[12, "ready"] = np.nan
    assert not detect(f).confirmed.any() and detect(f).why_pending.iloc[12] == "unknown"
    f = fixture(); f["data_gap"] = f["data_gap"].astype(object); f.loc[12, "data_gap"] = np.nan
    assert not detect(f).confirmed.any() and detect(f).why_pending.iloc[12] == "unknown"
    f = fixture(); f["legacy_confirmed"] = f["legacy_confirmed"].astype(object); f.loc[12, "legacy_confirmed"] = np.nan
    assert not detect(f).confirmed.any()
    f = fixture(); f.loc[5, "confirmed"] = False
    with pytest.raises(ValueError, match="final tip"):
        detect(f)
