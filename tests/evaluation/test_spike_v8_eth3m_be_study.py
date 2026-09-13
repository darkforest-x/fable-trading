"""Focused contracts for the one-bar-delayed ETH 3m price-BE state machine."""
from __future__ import annotations

import pandas as pd
import pytest

from yoyo.evaluation import spike_v8_eth3m_be_study as study


def _bars(side: int = 1) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    ix = pd.date_range("2026-05-04", periods=10, freq="3min", tz="UTC")
    bars = pd.DataFrame({"open": 100., "high": 100.5, "low": 99., "close": 100., "atr": 1.}, index=ix)
    signals = pd.DataFrame({"long_signal": False, "short_signal": False}, index=ix)
    signals.iloc[4, 0 if side == 1 else 1] = True
    if side == -1:
        bars.loc[:, ["open", "high", "low", "close"]] = [[100., 101., 99.5, 100.]] * len(bars)
    bars.attrs["minutes"] = 3
    return bars, signals, pd.Series(True, index=ix)


@pytest.mark.parametrize("side", [1, -1])
def test_wick_touch_activates_price_be_only_on_the_next_bar(side: int) -> None:
    bars, signals, admission = _bars(side)
    if side == 1:
        bars.iloc[5] = [100., 102.1, 99., 100.4, 1.]
        bars.iloc[6] = [100.2, 100.4, 99.8, 100., 1.]
    else:
        bars.iloc[5] = [100., 101., 97.9, 99.6, 1.]
        bars.iloc[6] = [99.8, 100.2, 99.6, 100., 1.]
    trades, _ = study.replay_serial(bars, signals, admission, enable_be=True)
    trade = trades.loc[~trades.censored].iloc[0]
    assert trade.exit_i == 6
    assert trade.exit_reason == "trailing_stop"
    # Price BE is gross zero; the frozen round-trip cost remains a loss.
    assert trade.net_return == pytest.approx(-.002)


def test_same_bar_wick_and_retest_does_not_retroactively_install_be() -> None:
    bars, signals, admission = _bars()
    bars.iloc[5] = [100., 102.1, 99.5, 100.1, 1.]
    bars.iloc[6] = [100., 100.1, 99.9, 100., 1.]
    trades, diagnostic = study.replay_serial(bars, signals, admission, enable_be=True)
    assert trades.loc[~trades.censored].iloc[0].exit_i == 6
    assert diagnostic["same_bar_1r_entry_retest_ambiguous"] == 1


def test_same_bar_initial_stop_and_one_r_wick_keeps_old_stop_priority() -> None:
    bars, signals, admission = _bars()
    # With OHLC alone the high/low order is unknowable.  The original initial
    # stop is already live; it wins, and BE is never retroactively installed.
    bars.iloc[5] = [100., 102.1, 97.9, 100., 1.]
    trades, diagnostic = study.replay_serial(bars, signals, admission, enable_be=True)
    trade = trades.loc[~trades.censored].iloc[0]
    assert trade.exit_i == 5
    assert trade.exit_reason == "initial_stop"
    assert trade.be_trigger_count == 0
    assert diagnostic["same_bar_1r_entry_retest_ambiguous"] == 0


def test_gap_through_next_bar_be_uses_open_and_keeps_cost() -> None:
    bars, signals, admission = _bars()
    bars.iloc[5] = [100., 102.1, 99., 100.5, 1.]
    bars.iloc[6] = [99.5, 100., 99.4, 99.7, 1.]
    trades, _ = study.replay_serial(bars, signals, admission, enable_be=True)
    trade = trades.loc[~trades.censored].iloc[0]
    assert trade.exit_reason == "trailing_stop_gap"
    assert trade.exit_price == pytest.approx(99.5)
    assert trade.net_return == pytest.approx(-.007)


def test_trail_never_loosens_when_be_is_added() -> None:
    bars, signals, admission = _bars()
    bars.iloc[5] = [100., 105., 99., 104., .1]  # trail becomes 103.6 after BE.
    bars.iloc[6] = [104., 104.2, 103.7, 103.8, .1]
    bars.iloc[7] = [103.8, 104., 103.5, 103.7, .1]
    trades, _ = study.replay_serial(bars, signals, admission, enable_be=True)
    trade = trades.loc[~trades.censored].iloc[0]
    assert trade.exit_i == 7
    assert trade.exit_price == pytest.approx(103.6)


def test_prefix_does_not_change_a_preexisting_next_bar_be_fill() -> None:
    bars, signals, admission = _bars()
    bars.iloc[5] = [100., 102.1, 99., 100.5, 1.]
    bars.iloc[6] = [100.2, 100.4, 99.8, 100., 1.]
    full, _ = study.replay_serial(bars, signals, admission, enable_be=True)
    prefix, _ = study.replay_serial(bars.iloc[:7].copy().set_axis(bars.index[:7]), signals.iloc[:7], admission.iloc[:7], enable_be=True)
    got = full.loc[~full.censored, study.TRADE_KEY].reset_index(drop=True)
    want = prefix.loc[~prefix.censored, study.TRADE_KEY].reset_index(drop=True)
    pd.testing.assert_frame_equal(got, want, check_dtype=False)


def test_paired_be_stop_count_excludes_a_better_original_trail() -> None:
    post = pd.DataFrame({"be_armed": [True, True, False], "protection_be": [100., 103., 100.],
                         "entry_price_be": [100., 100., 100.], "exit_price_be": [100., 103., 100.],
                         "exit_reason_be": ["trailing_stop", "trailing_stop", "trailing_stop"]})
    be_stop, price_fill = study._be_stop_masks(post)
    assert be_stop.tolist() == [True, False, False]
    assert price_fill.tolist() == [True, False, False]


def test_paired_outcomes_ignore_near_zero_float_deltas() -> None:
    post = pd.DataFrame({"net_r_difference": [2., 5e-10, -3., -5e-10], "net_r_baseline": [-1., -1., 2., 11.],
                         "net_r_be": [1., -1., -1., 9.], "be_trigger_count": [1, 0, 1, 1], "be_armed": [True] * 4,
                         "protection_be": [100.] * 4, "entry_price_be": [100.] * 4, "exit_price_be": [100.] * 4,
                         "exit_reason_be": ["trailing_stop"] * 4,
                         "entry_time_baseline": ["2026-05-01T00:00Z"] * 4})
    summary = study._paired_outcome_summary(post)
    assert (summary["improved_pairs"], summary["worsened_pairs"], summary["unchanged_pairs"]) == (1, 1, 2)
    assert summary["rescued_original_losers"] == 1
    assert summary["lost_original_winners"] == 1
