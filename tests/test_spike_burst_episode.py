"""Synthetic V2 episode-token ablation; no market scoring or fetched data."""
import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from yoyo.evaluation import spike_burst_episode as ep
from yoyo.evaluation import spike_burst_progressive as v2


def frame(n=70):
    f = pd.DataFrame(dict(open=100., high=100.1, low=99.9, close=100., volume=100.,
        atr=1., md=-2., sb=-3., pastWidth=4., pastCrosses=0., ropeHigh=100.2,
        ropeLow=99.8, rv=1., expansion=1., ready=True),
        index=pd.date_range("2020-01-01", periods=n, freq="h", tz="UTC"))
    f["middle"] = 100 + np.arange(n) / 100
    f.loc[f.index[35:47], ["pastWidth", "pastCrosses"]] = [3., 2.]
    for i, close in ((40, 101.), (41, 102.), (42, 103.)):
        f.loc[f.index[i], ["open", "high", "low", "close", "volume"]] = [close-.8, close+.1, close-.9, close, 150.]
    f.loc[f.index[43:], ["open", "high", "low", "close"]] = [103., 103.2, 102.8, 103.]
    f["tr"] = pd.concat([f.high-f.low, (f.high-f.close.shift()).abs(),
        (f.low-f.close.shift()).abs()], axis=1).max(axis=1)
    f["recentLow"] = f.low.rolling(5, min_periods=1).min()
    f["recentHigh"] = f.high.rolling(5, min_periods=1).max()
    f.attrs["minutes"] = 60
    return f.join(v2.progressive_fields(f))


def stopped_repeat():
    f = frame()
    f["prog_up"] = False
    f.loc[f.index[[42, 46, 50]], "prog_up"] = True
    f.loc[f.index[45], ["open", "high", "low", "close"]] = [98., 120., 97., 110.]
    return f


@pytest.mark.parametrize("enhanced", [True, False])
def test_disabled_exactly_preserves_frozen_v2_values_columns_dtypes_attrs(enhanced):
    f = stopped_repeat()
    original = v2.replay(f, .01, enhanced=enhanced)
    disabled = ep.replay(f, .01, enhanced=enhanced, episode_gate=False)
    assert_frame_equal(disabled, original)
    assert disabled.attrs == original.attrs


def test_same_episode_stop_does_not_rearm_or_create_new_holding():
    f = stopped_repeat()
    base = v2.replay(f, .01)
    r = ep.replay(f, .01)
    assert base.burst.iloc[42] and base.burst.iloc[46]
    assert r.burst.iloc[42] and r.exit.iloc[45]
    assert not r.burst.iloc[46] and r.episode_gate_blocked.iloc[46]
    assert r.trend_side.iloc[46] == 0 and r.entry_bar.iloc[46] == 42
    assert r.prior_episode_id.iloc[42] == r.prior_episode_id.iloc[46]
    assert r.episode_consumed.iloc[46] and not r.episode_consumed_this_bar.iloc[46]


def test_new_false_to_true_episode_rearms_without_clock_cooldown():
    f = stopped_repeat()
    f.loc[f.index[48:50], ["pastWidth", "pastCrosses"]] = [3., 2.]
    r = ep.replay(f, .01)
    assert r.episode_gate_blocked.iloc[46]
    assert r.burst.iloc[50] and r.entry_bar.iloc[50] == 50
    assert r.prior_episode_id.iloc[50] != r.prior_episode_id.iloc[42]
    assert r.episode_available_before.iloc[50]


def test_prior_twelve_excludes_current_density_and_expires_exactly():
    f = frame()
    f["pastWidth"], f["pastCrosses"] = 4., 0.
    f.loc[f.index[30], ["pastWidth", "pastCrosses"]] = [3., 2.]
    r = ep.density_episodes(f)
    assert pd.isna(r.prior_episode_id.iloc[30])
    assert r.prior_episode_id.iloc[31] == r.prior_episode_id.iloc[42] == 1
    assert pd.isna(r.prior_episode_id.iloc[43])
    assert r.prior_episode_last_dense_bar.iloc[42] == 30


def test_current_bar_new_episode_cannot_unlock_consumed_prior_episode():
    f = stopped_repeat()
    f.loc[f.index[44:46], ["pastWidth", "pastCrosses"]] = [4., 0.]
    f.loc[f.index[46], ["pastWidth", "pastCrosses"]] = [3., 2.]
    r = ep.replay(f, .01)
    assert r.episode_id.iloc[46] != r.prior_episode_id.iloc[46]
    assert r.episode_gate_blocked.iloc[46]
    assert r.episode_available_before.iloc[47]


def test_gap_clears_prior_density_and_requires_full_fresh_summary_window():
    f = frame()
    f["pastWidth"], f["pastCrosses"] = 1., 3.
    f = f.drop(f.index[40])
    r = ep.density_episodes(f)
    assert r.density_gap_reset.iloc[40]
    assert r.prior_episode_id.iloc[40:53].isna().all()
    assert not r.episode_density.iloc[40:52].any()
    assert r.episode_density.iloc[52]
    assert pd.notna(r.prior_episode_id.iloc[53])
    assert r.prior_episode_id.iloc[53] != r.prior_episode_id.iloc[39]


@pytest.mark.parametrize("end", [40, 43, 46, 47, 51, 69])
def test_prefix_causality_and_input_immutable(end):
    f = stopped_repeat()
    saved = f.copy(deep=True)
    full = ep.replay(f, .01)
    assert_frame_equal(ep.replay(f.iloc[:end], .01), full.iloc[:end])
    assert_frame_equal(f, saved)


def test_ordinary_route_priority_consumes_episode_and_preserves_risk():
    f = frame()
    f["md"], f["sb"] = 0., 0.
    f["pastWidth"], f["pastCrosses"] = 1., 3.
    f.loc[f.index[30], ["open", "high", "low", "close", "md", "sb", "rv", "expansion", "middle"]] = [100., 105., 99., 104.8, .5, .2, 5., 4., 102.]
    f.loc[f.index[31:], ["open", "high", "low", "close", "md", "sb"]] = [104.8, 107., 104., 106., .6, .3]
    f.loc[f.index[34], ["open", "high", "low", "close"]] = [97., 120., 96., 110.]
    f["prog_up"] = False
    f.loc[f.index[[30, 35]], "prog_up"] = True
    base = v2.replay(f, .01)
    r = ep.replay(f, .01)
    assert base.burst.iloc[30] and base.route.iloc[30] != "progressive"
    assert r.route.iloc[30] == base.route.iloc[30]
    assert r.episode_consumed_this_bar.iloc[30]
    assert r.exit.iloc[34] and r.episode_gate_blocked.iloc[35]
    assert base.burst.iloc[35] and not r.burst.iloc[35]
    for key in ("entry_ref", "risk", "initial_stop", "protection", "entry_bar"):
        assert r[key].iloc[30] == base[key].iloc[30]


def test_invalid_risk_marker_also_consumes_its_episode():
    f = frame()
    f["prog_up"] = False
    f.loc[f.index[[42, 43]], "prog_up"] = True
    f.loc[f.index[42], "atr"] = 100.
    r = ep.replay(f, .01)
    assert r.burst.iloc[42] and not r.risk_valid.iloc[42]
    assert r.trend_side.iloc[42] == 0 and r.episode_consumed_this_bar.iloc[42]
    assert r.episode_gate_blocked.iloc[43] and not r.burst.iloc[43]
