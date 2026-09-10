"""Synthetic causality, V1 equivalence and display contracts; no market scoring."""
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from yoyo.evaluation import spike_burst_progressive as v2
from yoyo.evaluation import spike_burst_replay as v1


def _ohlcv(n=450, seed=4):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, .3, n))
    opening = np.r_[100, close[:-1]]
    return pd.DataFrame(dict(open=opening, high=np.maximum(opening, close) + .2,
        low=np.minimum(opening, close) - .2, close=close, volume=rng.uniform(80, 120, n)),
        index=pd.date_range("2020-01-01", periods=n, freq="h", tz="UTC"))


def _gradual():
    """Hand-worked V1 feature fixture, with no near-zero oscillator episode."""
    n = 60
    f = pd.DataFrame(dict(open=100., high=100.1, low=99.9, close=100., volume=100.,
        atr=1., md=-2., sb=-3., pastWidth=4., pastCrosses=0., ropeHigh=100.2,
        ropeLow=99.8, rv=1., expansion=1., ready=True),
        index=pd.date_range("2020-01-01", periods=n, freq="h", tz="UTC"))
    f["middle"] = 100 + np.arange(n) / 100
    f.loc[f.index[35], ["pastWidth", "pastCrosses"]] = [3., 2.]
    for i, close in ((40, 101.), (41, 102.), (42, 103.)):
        opening = close - .8
        f.loc[f.index[i], ["open", "high", "low", "close", "volume"]] = [opening, close + .1, close - .9, close, 150.]
    f.loc[f.index[43:], ["open", "high", "low", "close"]] = [103., 103.2, 102.8, 103.]
    f["tr"] = pd.concat([f.high-f.low, (f.high-f.close.shift()).abs(),
                         (f.low-f.close.shift()).abs()], axis=1).max(axis=1)
    f["recentLow"] = f.low.rolling(5, min_periods=1).min()
    f["recentHigh"] = f.high.rolling(5, min_periods=1).max()
    return f


def _with_progress(f):
    return f.join(v2.progressive_fields(f))


def _hard_burst():
    f = _gradual()
    f["md"], f["sb"] = 0., 0.
    f["pastWidth"], f["pastCrosses"] = 1., 3.
    f.loc[f.index[30], ["open", "high", "low", "close", "md", "sb", "rv", "expansion", "middle"]] = [100., 105., 99., 104.8, .5, .2, 5., 4., 102.]
    f.loc[f.index[31:], ["open", "high", "low", "close", "md", "sb"]] = [104.8, 107., 104., 106., .6, .3]
    f.loc[f.index[34], ["open", "high", "low", "close"]] = [97., 120., 96., 110.]
    return f


def test_features_append_without_changing_any_v1_field_or_source():
    raw = _ohlcv()
    raw.attrs["minutes"] = 60
    saved = raw.copy(deep=True)
    base = v1.features(raw)
    full = v2.features(raw)
    assert_frame_equal(full.loc[:, base.columns], base)
    assert_frame_equal(raw, saved)
    assert list(full.columns[len(base.columns):]) == list(v2.PROGRESSIVE_COLUMNS)
    assert full.attrs == base.attrs


@pytest.mark.parametrize("seed", [1, 9, 21])
def test_disabled_path_matches_v1_all_columns_and_attrs(seed):
    for frame in (v1.features(_ohlcv(seed=seed)), _hard_burst(), _gradual()):
        old = v1.replay(frame, .0001)
        new = v2.replay(frame, .0001, enhanced=False)
        assert_frame_equal(old, new)
        assert old.attrs == new.attrs
    assert v1.replay(_hard_burst(), .0001).burst.any()
    assert v1.replay(_hard_burst(), .0001).exit.any()


@pytest.mark.parametrize("end", [339, 341, 400, 449])
def test_features_and_state_are_prefix_invariant(end):
    raw = _ohlcv()
    full = v2.features(raw)
    prefix = v2.features(raw.iloc[:end])
    assert_frame_equal(full.iloc[:end], prefix)
    assert_frame_equal(v2.replay(full, .0001).iloc[:end], v2.replay(prefix, .0001))


def test_progressive_confirmed_now_does_not_backfill_prior_two_bars():
    frame = _with_progress(_gradual())
    assert not frame.prog_up.iloc[:42].any()
    assert frame.prog_up.iloc[42]
    assert frame.md.iloc[42] < 0  # independent of a positive MD or near-zero release
    result = v2.replay(frame, .01)
    assert result.index[result.burst].tolist() == [frame.index[42]]
    assert result.route.iloc[42] == "progressive"
    assert result.entry_bar.iloc[42] == 42 and result.entry_ref.iloc[42] == 103.
    assert result.quiet_bars.iloc[42] == 0 and pd.isna(result.release_bar.iloc[42])
    assert result.peak_r.iloc[42] == 0 and np.isnan(result.active_protection.iloc[42])
    for end in (40, 41, 42, 43, 45, 60):
        assert_frame_equal(result.iloc[:end], v2.replay(frame.iloc[:end], .01))


def test_features_match_hand_computed_three_bar_window():
    f = _gradual()
    g = v2.progressive_fields(f)
    i = 42
    assert g.prog_prior_high.iloc[i] == max(f.high.iloc[i-12:i])
    assert g.prog_advance.iloc[i] == pytest.approx((f.close.iloc[i]-f.close.iloc[i-3])/f.atr.iloc[i-3])
    assert g.prog_volume_base.iloc[i] == f.volume.iloc[i-22:i-2].median()
    assert g.prog_volume_ratio.iloc[i] == 1.5
    assert g.prog_efficiency.iloc[i] == pytest.approx((f.close.iloc[i]-f.close.iloc[i-3])/f.tr.iloc[i-2:i+1].sum())
    assert g.prog_dense_hits.iloc[i] == 1


@pytest.mark.parametrize("dense_i,expected", [(29, False), (30, True), (41, True), (42, False)])
def test_density_uses_exact_prior_twelve_not_current(dense_i, expected):
    f = _gradual()
    f["pastWidth"], f["pastCrosses"] = 4., 0.
    f.loc[f.index[dense_i], ["pastWidth", "pastCrosses"]] = [3., 2.]
    g = v2.progressive_fields(f)
    assert bool(g.prog_up.iloc[42]) is expected
    f.loc[f.index[dense_i], "ready"] = False
    assert not v2.progressive_fields(f).prog_up.iloc[42]


@pytest.mark.parametrize("column,index,value", [
    ("volume", 42, 149.999), ("volume", 41, np.nan), ("volume", 40, np.nan),
    ("atr", 39, 2.001), ("tr", 42, 100.), ("high", 42, 110.),
    ("high", 41, 103.), ("ropeHigh", 42, 103.), ("open", 42, 103.),
    ("middle", 42, 100.), ("sb", 42, -1.), ("ready", 42, False),
])
def test_each_gate_has_an_independent_adversarial_failure(column, index, value):
    f = _gradual()
    assert v2.progressive_fields(f).prog_up.iloc[42]
    f.loc[f.index[index], column] = value
    assert not v2.progressive_fields(f).prog_up.iloc[42]


def test_progress_exact_atr_boundary_and_history_volume_na_semantics():
    f = _gradual()
    f.loc[f.index[39], "atr"] = 2.
    f.loc[f.index[25], "volume"] = np.nan
    g = v2.progressive_fields(f)
    assert g.prog_advance.iloc[42] == 1.5 and g.prog_up.iloc[42]
    expected = f.volume.iloc[:40].dropna().iloc[-20:].median()
    assert g.prog_volume_base.iloc[42] == expected
    f.loc[f.index[40:43], "volume"] = 10000.
    f.loc[f.index[42], "high"] = 150.
    changed = v2.progressive_fields(f)
    for col in ("prog_volume_base", "prog_prior_high", "prog_dense_hits", "prog_advance"):
        assert changed[col].iloc[42] == g[col].iloc[42]


def test_v1_same_bar_priority_even_if_both_paths_pass():
    f = _with_progress(_hard_burst())
    old = v1.replay(f, .0001)
    i = int(np.flatnonzero(old.burst)[0])
    f.loc[f.index[i], "prog_up"] = True
    new = v2.replay(f, .0001)
    assert old.route.iloc[i] != "progressive"
    assert new.route.iloc[i] == old.route.iloc[i]
    for col in ("entry_ref", "initial_stop", "risk", "protection", "entry_bar"):
        assert new[col].iloc[i] == old[col].iloc[i]


def test_earlier_progressive_hold_can_suppress_later_v1_arrow():
    f = _with_progress(_hard_burst())
    old = v1.replay(f, .0001)
    assert old.burst.iloc[30]
    f.loc[f.index[25], "prog_up"] = True
    new = v2.replay(f, .0001)
    assert new.burst.iloc[25] and new.route.iloc[25] == "progressive"
    assert not new.burst.iloc[30] and new.trend_side.iloc[30] == 1
    assert not old.burst.iloc[25]  # enhanced signals are not a simple union


def test_invalid_v1_risk_still_has_same_bar_priority():
    f = _with_progress(_hard_burst())
    f.loc[f.index[30], ["atr", "prog_up"]] = [100., True]
    old = v1.replay(f, .0001)
    new = v2.replay(f, .0001)
    assert old.burst.iloc[30] and not old.risk_valid.iloc[30]
    assert new.burst.iloc[30] and new.route.iloc[30] == old.route.iloc[30]
    assert not new.risk_valid.iloc[30] and new.trend_side.iloc[30] == 0


def test_holding_deduplicates_and_exit_bar_cannot_reenter():
    f = _with_progress(_gradual())
    f.loc[f.index[42:47], "prog_up"] = True
    f.loc[f.index[45], ["open", "high", "low", "close"]] = [98., 120., 97., 110.]
    r = v2.replay(f, .01)
    assert r.burst.iloc[42] and not r.burst.iloc[43:46].any()
    assert r.exit.iloc[45] and r.exit_price.iloc[45] == 98.
    assert r.peak_r.iloc[45] == r.peak_r.iloc[44]  # stop-bar high never counted
    assert r.burst.iloc[46] and r.entry_bar.iloc[46] == 46


def test_next_bar_risk_and_protection_use_unmodified_v1_helpers():
    f = _with_progress(_gradual())
    f.loc[f.index[43], ["open", "high", "low", "close"]] = [103., 119., 102.8, 118.]
    f.loc[f.index[44], ["open", "high", "low", "close"]] = [115., 120., 113., 119.]
    r = v2.replay(f, .01)
    ref = v1.risk_reference(1, f.close.iloc[42], f.recentLow.iloc[42], f.atr.iloc[42], tick=.01)
    assert r.initial_stop.iloc[42] == ref.stop and r.risk.iloc[42] == ref.risk
    assert r.active_protection.iloc[43] == ref.stop
    assert not r.exit.iloc[43] and r.protection.iloc[43] == 114.
    assert r.active_protection.iloc[44] == 114. and r.exit.iloc[44]


@pytest.mark.parametrize("scale,tick,valid", [(1e-5, 1e-8, True), (1e-7, 1e-10, False), (1e-7, 1.01e-10, True)])
def test_small_coin_tick_preserves_v1_native_risk_guard(scale, tick, valid):
    f = _gradual()
    for col in ("open", "high", "low", "close", "atr", "md", "sb", "ropeHigh", "ropeLow", "middle", "recentLow", "recentHigh", "tr"):
        f[col] *= scale
    r = v2.replay(_with_progress(f), tick)
    assert r.burst.iloc[42] and r.route.iloc[42] == "progressive"
    assert bool(r.risk_valid.iloc[42]) is valid
    assert r.trend_side.iloc[42] == int(valid)
    if valid:
        expected = v1.risk_reference(1, f.close.iloc[42], f.recentLow.iloc[42], f.atr.iloc[42], tick=tick)
        assert r.initial_stop.iloc[42] == expected.stop


def test_missing_progress_fields_refused_only_when_enhancement_enabled():
    f = _gradual()
    with pytest.raises(ValueError, match="progressive_fields"):
        v2.replay(f, .01)
    assert_frame_equal(v1.replay(f, .01), v2.replay(f, .01, enhanced=False))


def _section(text, start, end):
    return text.split(start, 1)[1].split(end, 1)[0]


def test_pine_keeps_v1_helpers_display_and_visible_plots_unchanged():
    folder = Path(__file__).resolve().parents[1] / "yoyo/evaluation/pine"
    old = (folder / "spike_burst_v1_display.pine").read_text()
    new_path = folder / "spike_burst_v2_progressive.pine"
    new = new_path.read_text()
    assert hashlib.sha256(new_path.read_bytes()).hexdigest() == v2.SOURCE_SHA256
    for name in ("BURST PURE HELPERS", "BURST DISPLAY ONLY"):
        assert _section(new, "// BEGIN " + name, "// END " + name) == _section(old, "// BEGIN " + name, "// END " + name)
    old_plots = [line for line in old.splitlines() if line.startswith(("plot(", "hline(", "pProtect =", "pInitial =", "pEntry =", "fill(", "barcolor("))]
    assert all(line in new.splitlines() for line in old_plots)
    assert 'input.string("增强", "启动模式", options=["原版", "增强"]' in new
    assert "02B · 渐进启动" in new and "最新收盘" in new
    assert 'table.cell(panel, 1, 0, panelState' in new and '"当前未满足启动"' in new
    assert 'bool panelReady = barstate.isconfirmed ? ready : ready[1]' in new
    assert "md >= sb" in new and "md <= sb" in new
    assert "not burstUp and not burstDown and trendSide == 0 and not endedThisBar" in new
    added_plots = [line for line in new.splitlines() if line.startswith("plot(") and line not in old_plots]
    assert len(added_plots) == 9 and all("display=display.data_window)" in line for line in added_plots)
    assert "request.security(" not in new and "varip " not in new


def test_pine_v1_state_is_unchanged_except_one_lower_priority_insertion():
    folder = Path(__file__).resolve().parents[1] / "yoyo/evaluation/pine"
    old = (folder / "spike_burst_v1_display.pine").read_text()
    new = (folder / "spike_burst_v2_progressive.pine").read_text()
    before, after = "// BEGIN BURST STATE MACHINE", "// END BURST STATE MACHINE"
    new_state = _section(new, before, after)
    addition_start = new_state.index("    // V1 has evaluated both hard-burst routes first;")
    addition_end = new_state.index("    if showRisk and trendSide != 0", addition_start)
    assert new_state[:addition_start] + new_state[addition_end:] == _section(old, before, after)
