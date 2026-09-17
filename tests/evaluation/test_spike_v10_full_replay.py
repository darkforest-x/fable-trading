"""Guards for the V10 trendline gate: subset, monotonicity, cut, V9 parity.

The gate may only remove V9 entries. These tests hold that line on synthetic
streams, and -- when the frozen pool is present -- on one real stream where the
truncated V9 arm is compared trade-for-trade with the published V9 ledger.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation import spike_v1_v8_be05 as engine
from yoyo.evaluation.spike_v8_six_filters import assert_baseline_parity
from yoyo.evaluation.spike_v9 import replay_v9
from yoyo.evaluation.spike_v10 import ARMS, break_ages, gate_mask, gate_reason, signal_break_age
from yoyo.evaluation.spike_v10_full_replay import CUT, V9_LEDGER, compare_v9_ledger, replay_stream, truncate, v9_parity


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


def test_truncate_drops_only_bars_closing_after_the_cut():
    context = fixture(start="2026-04-20", periods=600)
    truncated, kept, dropped = truncate(context, CUT)
    index = truncated.cache["bars"].index
    assert kept + dropped == 600 and kept > 0 and dropped > 0
    assert (index + pd.Timedelta(hours=1) <= CUT).all()
    for name in ("signals", "v1_signals", "bb", "data_gap", "bb_ready"):
        assert truncated.cache[name].index.equals(index), name
    assert truncated.cache["tick"] == context.cache["tick"]


def test_gate_mask_and_reason_semantics():
    index = pd.date_range("2025-01-01", periods=5, freq="h", tz="UTC")
    ages = pd.DataFrame({"long_break_age": [-1, 0, 5, 13, 40], "short_break_age": [0, -1, -1, 2, -1],
                         "long_break": False, "short_break": False, "long_line_active": False,
                         "short_line_active": False, "long_line_price": np.nan,
                         "short_line_price": np.nan}, index=index)
    side = np.array([1, 1, 1, 1, -1])
    assert signal_break_age(side, ages).tolist() == [-1, 0, 5, 13, -1]
    assert gate_mask(side, ages, None).tolist() == [True] * 5
    assert gate_mask(side, ages, 0).tolist() == [False, True, False, False, False]
    assert gate_mask(side, ages, 12).tolist() == [False, True, True, False, False]
    assert gate_mask(side, ages, 48).tolist() == [False, True, True, True, False]
    assert gate_reason(-1, 12) == "no_trendline_break_in_segment"
    assert gate_reason(0, 12) == "break_on_this_bar"
    assert gate_reason(13, 12) == "break_age_13_gt_12"
    assert gate_reason(99, None) == "ungated_v9_control"
    with pytest.raises(ValueError):
        gate_mask(side, ages, -3)


def test_break_ages_is_aligned_and_directional():
    context = trending_fixture()
    bars = context.cache["bars"]
    gap = context.cache["data_gap"].to_numpy(bool)
    ages = break_ages(bars, gap, float(context.cache["tick"]))
    assert ages.index.equals(bars.index)
    assert set(ages.columns) >= {"long_break", "short_break", "long_break_age", "short_break_age"}
    assert (ages.long_break_age >= -1).all() and (ages.short_break_age >= -1).all()
    assert ages.long_break.any() or ages.short_break.any(), "a random walk should break some line"


@pytest.mark.parametrize("seed", range(4))
def test_arms_are_nested_and_never_exceed_v9(seed):
    context = trending_fixture(seed=seed)
    outputs, decisions, _ = replay_stream(context)
    assert decisions.trendline_break_age.ge(0).any(), "fixture must contain admitted breaks"
    assert decisions.v10_a200.sum() > decisions.v10_now.sum(), "a wider window must admit more"
    admitted = {arm: set(decisions.loc[decisions[arm].astype(bool), "signal_bar_open"])
                for arm in ARMS if arm != "v9"}
    v9 = set(decisions.loc[decisions.v9.astype(bool), "signal_bar_open"])
    order = ["v10_now", "v10_a12", "v10_a48", "v10_a200"]
    for tighter, looser in zip(order, order[1:]):
        assert admitted[tighter] <= admitted[looser], f"{tighter} must be inside {looser}"
    for arm, chosen in admitted.items():
        assert chosen <= v9, f"{arm} admitted an entry V9 refused"
        assert len(outputs[arm][0]) <= len(outputs["v9"][0])


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
    last_close = context.cache["bars"].index[-1] + pd.Timedelta(minutes=context.minutes)
    assert last_close <= CUT, "no bar may close after the holdout boundary"
    outputs, decisions, _ = replay_stream(context)
    parity = v9_parity(keys[0], outputs["v9"][0].copy(), last_close)
    assert parity["compared_trades"] > 0
    assert parity["max_relative_float_drift"] < 1e-9
    assert (decisions.trendline_break_age >= -1).all()


def ledger_frame(rows):
    """Minimal ledger shaped like the published V9 CSV."""
    columns = ["signal_i", "entry_i", "side", "exit_i", "exit_reason", "entry_price", "exit_price",
               "initial_stop", "initial_risk", "net_return", "net_r", "entry_time", "exit_time", "censored"]
    return pd.DataFrame(rows, columns=columns)


def test_parity_accepts_a_stream_whose_every_trade_opened_after_the_cut():
    """Both sides empty must pass. CSV and replay dtypes differ when empty."""
    cut = pd.Timestamp("2026-05-04T00:00:00Z")
    published = ledger_frame([[7, 8, 1, 20, "initial_stop", 1., .9, .8, .2, -.2, -1., "2026-08-01T00:00:00Z", "2026-08-02T00:00:00Z", False]])
    mine = ledger_frame([]).astype({"signal_i": "float64", "exit_reason": "object"})
    result = compare_v9_ledger("synthetic", published, mine, cut)
    assert result == {"published_trades": 1, "compared_trades": 0, "published_after_cut": 1,
                      "max_relative_float_drift": 0.0}


def test_parity_excludes_a_published_exit_that_landed_on_the_first_dropped_bar():
    """An exit stamped at the cut instant happened on a bar this run refused.

    Exits carry the exit bar's open. The published V9 run had bars past the
    cut, so a trade of its own could exit on the first holdout bar; here that
    position is simply still open and censored. Comparing the two would compare
    against a bar we did not read.
    """
    last_open = pd.Timestamp("2026-05-03T20:00:00Z")
    published = ledger_frame([
        [7, 8, 1, 20, "initial_stop", 1., .9, .8, .2, -.2, -1., "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", False],
        [30, 31, -1, 40, "initial_stop", 1., 1.1, 1.2, .2, -.2, -1.07, "2026-05-02T04:00:00Z", "2026-05-04T00:00:00Z", False]])
    mine = ledger_frame([
        [7, 8, 1, 20, "initial_stop", 1., .9, .8, .2, -.2, -1., "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", False],
        [30, 31, -1, 39, "boundary_mark", 1., np.nan, 1.2, .2, np.nan, np.nan, "2026-05-02T04:00:00Z", "2026-05-03T20:00:00Z", True]])
    result = compare_v9_ledger("synthetic", published, mine, last_open)
    assert result["compared_trades"] == 1 and result["published_after_cut"] == 1


def test_parity_rejects_a_changed_pre_cut_decision():
    cut = pd.Timestamp("2026-05-04T00:00:00Z")
    row = [7, 8, 1, 20, "initial_stop", 1., .9, .8, .2, -.2, -1., "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", False]
    published = ledger_frame([list(row)])
    shifted = list(row)
    shifted[0] = 9
    with pytest.raises(ValueError, match="signal_i"):
        compare_v9_ledger("synthetic", published, ledger_frame([shifted]), cut)
    priced = list(row)
    priced[10] = -1.5
    with pytest.raises(ValueError, match="net_r"):
        compare_v9_ledger("synthetic", ledger_frame([list(row)]), ledger_frame([priced]), cut)
    with pytest.raises(ValueError, match="different trade count"):
        compare_v9_ledger("synthetic", ledger_frame([list(row), list(row)]), ledger_frame([list(row)]), cut)
