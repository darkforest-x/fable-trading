"""Causality, episode identity and protective exit invariants for V7 gates."""
from pathlib import Path

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
import pytest

from yoyo.evaluation.spike_v7_episode_study import episode_admissions, replay_mask
from yoyo.evaluation.spike_exit_policy_study import StreamContext


def inputs(n=70, side=1):
    ix = pd.date_range("2024-09-10", periods=n, freq="h", tz="UTC")
    bars = pd.DataFrame({"open": 100., "high": 101., "low": 99., "close": 100.,
                         "atr": 1., "s20": 99., "e20": 99., "md": 2., "sb": 1.}, index=ix)
    sig = pd.DataFrame({"long_signal": False, "short_signal": False}, index=ix)
    bb = pd.DataFrame({"bb_compressed": False, "prior_squeeze_run3": False, "v7_ready": True}, index=ix)
    bb.iloc[12:18, 0] = True
    gap = pd.Series(False, index=ix)
    return bars, sig, bb, gap


def ready(bb):
    c = bb.bb_compressed.to_numpy(bool)
    q = np.zeros(len(c), bool)
    for i in range(2, len(c)):
        q[i] = c[i-2:i+1].all()
    bb["prior_squeeze_run3"] = [q[max(0, i-10):i].any() for i in range(len(c))]


def test_boundary_uses_previous_close_and_does_not_retroactively_expand():
    b, s, bb, g = inputs()
    s.iloc[17, 0] = True
    b.loc[b.index[17], ["high", "close"]] = [110., 105.]
    ready(bb)
    a = episode_admissions(b, s, bb, g)
    assert a.upper.iloc[17] == 101
    assert a.first_break.iloc[17]
    assert a.upper.iloc[18] == 110


@pytest.mark.parametrize("side", [1, -1])
def test_first_escape_can_wait_for_later_original_signal(side):
    b, s, bb, g = inputs()
    col = "long_signal" if side == 1 else "short_signal"
    s.loc[b.index[[18, 20, 21]], col] = True
    b.loc[b.index[[20, 21]], "close"] = 102 if side == 1 else 98
    ready(bb)
    a = episode_admissions(b, s, bb, g)
    assert a.loc[b.index[[18, 20, 21]], "first"].tolist() == [True, False, False]
    assert a.loc[b.index[[18, 20, 21]], "first_break"].tolist() == [False, True, False]
    assert not a.first_break.iloc[19]


def test_direction_change_does_not_rearm_same_episode():
    b, s, bb, g = inputs()
    s.iloc[18, 0] = True; s.iloc[20, 1] = True
    b.loc[b.index[18], "close"] = 102
    b.loc[b.index[20], "close"] = 98
    ready(bb)
    a = episode_admissions(b, s, bb, g)
    assert a["first"].iloc[18] and a.first_break.iloc[18]
    assert not a["first"].iloc[20] and not a.first_break.iloc[20]


def test_short_interruption_merges_but_full_reset_rearms():
    b, s, bb, g = inputs()
    bb.iloc[21:24, 0] = True
    bb.iloc[40:43, 0] = True
    s.loc[b.index[[18, 24, 43]], "long_signal"] = True
    ready(bb)
    a = episode_admissions(b, s, bb, g)
    assert a.episode.iloc[18] == a.episode.iloc[24]
    assert a.episode.iloc[43] != a.episode.iloc[24]
    assert a.loc[b.index[[18, 24, 43]], "first"].tolist() == [True, False, True]


def test_left_censored_episode_passes_through_until_observed_reset():
    b, s, bb, g = inputs()
    bb.iloc[:18, 0] = True
    bb.iloc[40:43, 0] = True
    s.loc[b.index[[16, 18, 43, 44]], "long_signal"] = True
    ready(bb)
    bb.loc[bb.index[:3], "prior_squeeze_run3"] = True  # full-source pre-cache run
    a = episode_admissions(b, s, bb, g)
    assert a.fallback.iloc[16] and a.fallback.iloc[18]
    assert a["first"].iloc[16] and a["first"].iloc[18]
    assert not a.fallback.iloc[43]
    assert a["first"].iloc[43] and not a["first"].iloc[44]


def test_hidden_prefix_endpoint_can_bridge_ten_bars_to_visible_run():
    b, s, bb, g = inputs()
    bb["bb_compressed"] = False
    bb.loc[bb.index[[0, 1, 9, 10, 11]], "bb_compressed"] = True
    bb.loc[bb.index[40:43], "bb_compressed"] = True
    s.loc[b.index[[12, 13, 43, 44]], "long_signal"] = True
    ready(bb)
    # Pre-cache compressed bars make endpoints 0 and 1 valid. Endpoint 11
    # belongs to the same unknown episode, exactly ten bars after endpoint 1.
    bb.loc[bb.index[:12], "prior_squeeze_run3"] = True
    a = episode_admissions(b, s, bb, g)
    assert a.fallback.iloc[[12, 13]].all()
    assert a["first"].iloc[[12, 13]].all()
    assert a.first_break.iloc[[12, 13]].all()
    assert not a.fallback.iloc[43]
    assert a["first"].iloc[43] and not a["first"].iloc[44]
    assert_frame_equal(a.iloc[:14], episode_admissions(b.iloc[:14], s.iloc[:14], bb.iloc[:14], g.iloc[:14]))


def test_gap_invalidates_boundary_and_requires_new_qualified_episode():
    b, s, bb, g = inputs()
    g.iloc[18] = True
    bb.iloc[30:33, 0] = True
    s.loc[b.index[[18, 33]], "long_signal"] = True
    ready(bb)
    a = episode_admissions(b, s, bb, g)
    assert not a["first"].iloc[18]
    assert a.episode.iloc[33] != a.episode.iloc[17]
    assert a.upper.iloc[33] == 101


@pytest.mark.parametrize("cut", [14, 17, 19, 25, 43, 55])
def test_future_extension_cannot_change_episode_or_entry(cut):
    b, s, bb, g = inputs()
    bb.iloc[21:24, 0] = True; bb.iloc[40:46, 0] = True
    s.iloc[15::3, 0] = True
    b.iloc[16:30, b.columns.get_loc("close")] = 103
    ready(bb)
    full = episode_admissions(b, s, bb, g)
    prefix = episode_admissions(b.iloc[:cut], s.iloc[:cut], bb.iloc[:cut], g.iloc[:cut])
    assert_frame_equal(full.iloc[:cut], prefix)


def test_rejected_opposite_entry_still_exits_original_position():
    b, s, bb, g = inputs()
    s.iloc[18, 0] = True; s.iloc[21, 1] = True
    ready(bb)
    cache = dict(bars=b, signals=s, v1_signals=s.copy(), data_gap=g, bb=bb, tick=.01)
    ledger = pd.DataFrame({"signal_bar_open": b.index[[18, 21]], "signal_i": [18, 21]})
    context = StreamContext(Path("."), "synthetic", {}, cache, ledger, 60,
                            dict(venue="test", symbol="A", asset="A", timeframe_min=60))
    mask = pd.Series(False, index=b.index); mask.iloc[18] = True
    t, f, _ = replay_mask(context, mask)
    assert len(t) == 1
    assert t.exit_reason.iloc[0] == "opposite_v6_next_open"
    assert t.exit_time.iloc[0] == b.index[22]
    assert context.cache["signals"].short_signal.iloc[21]
    assert context.cache["bb"].prior_squeeze_run3.iloc[21]
    assert f.cost_return.sum() == pytest.approx(.002)
