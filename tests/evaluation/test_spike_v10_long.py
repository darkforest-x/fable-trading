"""Guards for the second V10: long only, V9 long within 7 bars after a break.

What must hold: V10 never admits an entry V9 refused, never opens a short,
admits only break ages 0..7, still lets a raw short confirmation end a long
(the exit is V9's, unchanged), and its V9 control arm reproduces the published
V9 ledger on a real truncated stream.
"""
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation import spike_v1_v8_be05 as engine
from yoyo.evaluation.spike_v8_six_filters import assert_baseline_parity
from yoyo.evaluation.spike_v9 import replay_v9
from yoyo.evaluation.spike_v10_full_replay import CUT, V9_LEDGER, truncate, v9_parity
from yoyo.evaluation.spike_v10_long import ARMS, MAX_BREAK_AGE, arm_masks, gate_reason, long_break_ages
from yoyo.evaluation.spike_v10_long_replay import replay_stream


# The two fixtures below are copied from test_spike_v10_full_replay.py rather
# than imported: a site-packages `tests` package shadows this repo's, so a
# `from tests.evaluation...` import fails at collection.
def fixture(periods=900, seed=4, start="2025-01-04"):
    """A long enough synthetic stream for a 48-bar trendline span to exist."""
    index = pd.date_range(start, periods=periods, freq="h", tz="UTC")
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, .7, len(index)))
    bars = pd.DataFrame({"open": np.r_[100, close[:-1]], "close": close, "atr": 1., "rv": 2.,
                         "s20": 100., "e20": 100., "md": 1., "sb": 0., "ready": True,
                         "ropeHigh": close, "ropeLow": close}, index=index)
    bars["high"] = bars[["open", "close"]].max(axis=1) + .3
    bars["low"] = bars[["open", "close"]].min(axis=1) - .3
    signals = pd.DataFrame({"long_signal": False, "short_signal": False}, index=index)
    for j, i in enumerate(range(120, periods - 20, 11)):
        signals.loc[index[i], "long_signal" if j % 2 == 0 else "short_signal"] = True
    selected = np.flatnonzero(signals.any(axis=1))
    ledger = pd.DataFrame({"signal_bar_open": index[selected], "signal_i": selected})
    cache = {"bars": bars, "signals": signals, "v1_signals": signals.copy(),
             "bb": pd.DataFrame({"v7_ready": True, "prior_squeeze_run3": True}, index=index),
             "bb_ready": pd.Series(True, index=index), "data_gap": pd.Series(False, index=index), "tick": .01}
    return base.StreamContext(Path("."), "synthetic", {"cache_sha256": "synthetic"}, cache, ledger, 60,
                              {"asset": "ETH", "symbol": "ETHUSDT", "venue": "synthetic", "timeframe_min": 60})


def trending_fixture(periods=1800, seed=3):
    """A stream with real descending resistance lines and real upward breaks.

    Each 300-bar cycle declines with lower pivot highs and then rallies, which
    is the shape the owner's indicator was built to find. The random-walk
    fixture above almost never produces a line clean enough to be born, so the
    subset and monotonicity checks would otherwise pass on empty sets.
    """
    index = pd.date_range("2025-01-04", periods=periods, freq="h", tz="UTC")
    rng = np.random.default_rng(seed)
    phase = np.arange(periods) % 300
    close = 110 - .05 * phase + np.where(phase > 250, (phase - 250) * .30, 0.) + rng.normal(0, .08, periods)
    bars = pd.DataFrame({"open": np.r_[close[0], close[:-1]], "close": close, "atr": .5, "rv": 2.,
                         "s20": 100., "e20": 100., "md": 1., "sb": 0., "ready": True,
                         "ropeHigh": close, "ropeLow": close}, index=index)
    bars["high"] = close + .3
    bars["low"] = close - .3
    bars.loc[(phase % 70) == 20, "high"] = close[(phase % 70) == 20] + 2.
    signals = pd.DataFrame({"long_signal": False, "short_signal": False}, index=index)
    signals.iloc[120::11, signals.columns.get_loc("long_signal")] = True
    selected = np.flatnonzero(signals.any(axis=1))
    ledger = pd.DataFrame({"signal_bar_open": index[selected], "signal_i": selected})
    cache = {"bars": bars, "signals": signals, "v1_signals": signals.copy(),
             "bb": pd.DataFrame({"v7_ready": True, "prior_squeeze_run3": True}, index=index),
             "bb_ready": pd.Series(True, index=index), "data_gap": pd.Series(False, index=index), "tick": .01}
    return base.StreamContext(Path("."), "synthetic-trend", {"cache_sha256": "synthetic"}, cache, ledger, 60,
                              {"asset": "ETH", "symbol": "ETHUSDT", "venue": "synthetic", "timeframe_min": 60})


def mixed_fixture(seed):
    """The trending stream, with short confirmations interleaved between longs.

    The trending fixture has real descending lines and real upward breaks but
    only long signals; a long-only rule needs shorts present to prove it drops
    them and to prove they still end open longs.
    """
    context = trending_fixture(seed=seed)
    signals = context.cache["signals"].copy()
    index = signals.index
    shorts = np.arange(125, len(index) - 20, 11)
    signals.iloc[shorts, signals.columns.get_loc("short_signal")] = True
    selected = np.flatnonzero(signals.any(axis=1))
    ledger = pd.DataFrame({"signal_bar_open": index[selected], "signal_i": selected})
    cache = dict(context.cache, signals=signals, v1_signals=signals.copy())
    return replace(context, cache=cache, signals_ledger=ledger)


def test_arm_masks_follow_the_owner_rule():
    v9 = np.array([True, True, True, True, True, False, True])
    side = np.array([1, 1, 1, 1, -1, 1, 1])
    age = np.array([-1, 0, 7, 8, 3, 2, 5])
    masks = arm_masks(v9, side, age)
    assert list(masks) == list(ARMS)
    assert masks["v9"].tolist() == v9.tolist()
    assert masks["v9_long"].tolist() == [True, True, True, True, False, False, True]
    assert masks["v10"].tolist() == [False, True, True, False, False, False, True]
    assert MAX_BREAK_AGE == 7
    with pytest.raises(ValueError):
        arm_masks(v9, side, age, max_break_age=-1)


def test_gate_reason_names_every_refusal():
    assert gate_reason(-1, True, 0) == "short_not_traded"
    assert gate_reason(1, False, 0) == "v9_refused"
    assert gate_reason(1, True, -1) == "no_trendline_break_in_segment"
    assert gate_reason(1, True, 8) == "break_age_8_gt_7"
    assert gate_reason(1, True, 0) == "break_on_this_bar"
    assert gate_reason(1, True, 7) == "break_age_7"


def test_break_age_never_reads_later_bars():
    context = trending_fixture(seed=1)
    bars = context.cache["bars"]
    gap = context.cache["data_gap"].to_numpy(bool)
    full = long_break_ages(bars, gap, .01)
    assert full.long_break.any(), "fixture must contain breaks"
    for cut in (700, 1100, 1500):
        prefix = long_break_ages(bars.iloc[:cut], gap[:cut], .01)
        pd.testing.assert_frame_equal(prefix, full.iloc[:cut])


@pytest.mark.parametrize("seed", range(4))
def test_v10_is_long_only_inside_the_window_and_inside_v9(seed):
    context = mixed_fixture(seed)
    outputs, decisions, _ = replay_stream(context)
    assert decisions.side.eq(-1).any() and decisions.v10.astype(bool).any(), "fixture must exercise both sides and the gate"
    v10 = decisions.loc[decisions.v10.astype(bool)]
    assert v10.side.eq(1).all()
    assert v10.trendline_break_age.between(0, MAX_BREAK_AGE).all()
    assert (decisions.v10.astype(bool) <= decisions.v9_long.astype(bool)).all()
    assert (decisions.v9_long.astype(bool) <= decisions.v9.astype(bool)).all()
    for arm in ("v9_long", "v10"):
        assert outputs[arm][0].side.eq(1).all(), f"{arm} opened a short"
    entered = set(outputs["v10"][0].signal_bar_open)
    assert entered <= set(v10.signal_bar_open)


@pytest.mark.parametrize("seed", range(4))
def test_raw_short_confirmation_still_ends_a_long(seed):
    context = mixed_fixture(seed)
    outputs, _, _ = replay_stream(context)
    trades = outputs["v9_long"][0]
    reversed_ = trades.loc[trades.exit_reason.eq("opposite_v6_next_open")]
    assert len(reversed_), "no long was ended by a raw short confirmation"
    shorts = context.cache["signals"].short_signal
    index = shorts.index
    for exit_time in pd.to_datetime(reversed_.exit_time, utc=True):
        assert shorts.iloc[index.get_loc(exit_time) - 1], "reverse exit must follow a raw short close"


@pytest.mark.parametrize("seed", range(3))
def test_control_arm_still_reproduces_the_released_v9_adapter(seed):
    context = fixture(seed=seed)
    outputs, _, _ = replay_stream(context)
    original, _, _, _ = replay_v9(context)
    assert_baseline_parity(outputs["v9"][0], original)


@pytest.mark.skipif(not (engine.RAW / "streams").is_dir() or not (V9_LEDGER / "streams").is_dir(),
                    reason="frozen pool or published V9 ledger unavailable")
def test_one_real_stream_reproduces_the_published_v9_ledger_before_the_cut():
    keys = sorted(p.name for p in (engine.RAW / "streams").iterdir() if (p / "completion.json").is_file())
    context = base.load_verified_stream(engine.RAW / "streams" / keys[0])
    context, kept, _ = truncate(context, CUT)
    assert kept > 0
    last_open = context.cache["bars"].index[-1]
    assert last_open + pd.Timedelta(minutes=context.minutes) <= CUT
    outputs, decisions, _ = replay_stream(context)
    parity = v9_parity(keys[0], outputs["v9"][0].copy(), last_open)
    assert parity["compared_trades"] > 0
    assert parity["max_relative_float_drift"] < 1e-9
    assert outputs["v10"][0].side.eq(1).all()
    assert decisions.loc[decisions.v10.astype(bool)].trendline_break_age.between(0, MAX_BREAK_AGE).all()


def test_age_buckets_put_the_v10_window_in_exactly_three_buckets():
    from yoyo.evaluation.spike_v10_long_report import age_buckets
    ages = pd.Series([-1, 0, 1, 3, 4, 7, 8, 12, 13, 48, 49, 900])
    assert age_buckets(ages).astype(str).tolist() == [
        "no_break", "age_0", "age_1_3", "age_1_3", "age_4_7", "age_4_7", "age_8_12", "age_8_12",
        "age_13_48", "age_13_48", "age_gt_48", "age_gt_48"]


def test_attribution_refuses_a_step_that_changed_a_shared_exit():
    from yoyo.evaluation.spike_v10_long_report import attribution
    left = pd.DataFrame({"arm": "v9_long", "event_key": ["a", "b", "c"], "net_r": [1., -1., 12.],
                         "censored": False})
    right = pd.DataFrame({"arm": "v10", "event_key": ["a", "d"], "net_r": [1., .5], "censored": False})
    detail = attribution(pd.concat([left, right], ignore_index=True), "v9_long", "v10")
    assert detail["removed_events"] == 2 and detail["added_events"] == 1
    assert detail["avoided_loss_r"] == 1. and detail["foregone_profit_r"] == 12.
    assert detail["serial_delta_r"] == -11. + .5
    assert detail["left_10r"] == 1 and detail["retained_10r"] == 0
    right.loc[0, "net_r"] = 2.
    with pytest.raises(ValueError):
        attribution(pd.concat([left, right], ignore_index=True), "v9_long", "v10")
