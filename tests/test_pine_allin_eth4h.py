"""Behavioral probes for original Pine timing and causal 4h preparation."""
import numpy as np
import pandas as pd
import pytest

from yoyo.layers.l3_backtest.pine_allin_eth4h import Policy, Replay, aggregate_4h, features


def frame(n=8):
    out = pd.DataFrame({"open_time": pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC"),
                        "open": 100., "high": 100., "low": 100., "close": 100., "atr": 1.,
                        "v7_long": False, "v7_short": False, "cross_long": False,
                        "cross_short": False, "entry_allowed": True, "osc": 1.,
                        "hk_dayofweek": 0, "hk_hour": 0})
    out.loc[0, "v7_long"] = True
    return out


def test_entry_bar_protection_is_a_real_policy_difference():
    f = frame()
    f.loc[1, "low"] = 96.
    f.loc[2, "low"] = 96.
    delayed = Replay(f, Policy()).run(0, len(f))["trades"].iloc[0]
    immediate = Replay(f, Policy(immediate_stop=True)).run(0, len(f))["trades"].iloc[0]
    assert delayed.exit_i == 2 and immediate.exit_i == 1
    assert delayed.exit_price == immediate.exit_price == 97.


def test_be_update_cannot_retroactively_exit_trigger_bar():
    f = frame()
    f.loc[1, ["high", "close"]] = [102., 101.]
    f.loc[2, "open"] = 99.
    t = Replay(f, Policy()).run(0, len(f))["trades"].iloc[0]
    assert t.exit_i == 2 and t.exit_price == 99. and t.exit_reason == "stop_gap"


def test_be_trigger_is_strictly_greater():
    f = frame()
    f.loc[1, "high"] = 100. * 1.015
    f.loc[2, "low"] = 99.
    assert Replay(f, Policy()).run(0, len(f))["trades"].empty


def test_same_side_signal_resets_the_shared_stop_even_without_pyramiding():
    f = frame()
    f.loc[2, "v7_long"] = True
    f.loc[2, ["open", "high", "low", "close"]] = [100., 100., 98., 98.]
    f.loc[3, ["open", "high", "low", "close"]] = [98., 98., 96., 98.]
    out = Replay(f, Policy()).run(0, len(f))
    assert out["trades"].empty
    assert out["open_position"]["stop"] == pytest.approx(98 * .97)
    assert "same_side_stop_reset" in set(out["events"].event)


def test_stale_cooldown_boolean_allows_same_bar_signal_after_big_profit():
    f = frame(10)
    f.loc[2, ["open", "high", "low", "close"]] = [100., 121., 100., 120.]
    f.loc[2, "v7_short"] = True
    f.loc[3, ["open", "high", "low", "close"]] = [122., 122., 122., 122.]
    f.loc[3, "v7_long"] = True
    out = Replay(f, Policy()).run(0, len(f))
    assert out["trades"].iloc[0].net_return > .2
    assert ((out["events"].i == 3) & (out["events"].event == "entry_signal")).any()


def test_opposite_signal_preserves_shared_stop_mutation_for_new_side():
    f = frame(5)
    f.loc[2, ["open", "high", "low", "close"]] = [100., 102., 100., 101.]
    f.loc[2, "v7_short"] = True
    f.loc[3:, ["open", "high", "low", "close"]] = [101., 101., 101., 101.]
    out = Replay(f, Policy()).run(0, len(f))
    assert out["trades"].iloc[0].exit_reason == "reverse"
    assert out["open_position"]["direction"] == -1
    assert out["open_position"]["stop"] == pytest.approx(104.03)


def test_commission_uses_both_fill_notionals():
    f = frame()
    f.loc[2, "low"] = 96.
    t = Replay(f, Policy(fee=.001)).run(0, len(f))["trades"].iloc[0]
    assert t.fees == pytest.approx(t.qty * (100 + 97) * .001)
    assert t.net_return == pytest.approx(-.03 - .001 * (1 + .97))


def test_control_reverse_closes_without_zero_quantity_position():
    f = frame()
    f.loc[2, "v7_short"] = True
    out = Replay(f, Policy()).run(0, len(f), injected=(0, 1))
    assert len(out["trades"]) == 1 and out["open_position"] is None


def test_gap_and_partial_aggregate_rejected():
    f = frame(32)[["open_time", "open", "high", "low", "close"]].copy()
    f["open_time"] = pd.date_range("2024-01-01", periods=32, freq="15min", tz="UTC")
    f["volume"] = 1.
    assert len(aggregate_4h(f)) == 2
    with pytest.raises(ValueError, match="incomplete"):
        aggregate_4h(f.iloc[:-1])
    with pytest.raises(ValueError, match="gap"):
        aggregate_4h(f.drop(index=4))


def test_indicators_and_bins_are_prefix_causal_and_use_utc_calendar():
    rng = np.random.default_rng(72)
    f = frame(700)[["open_time", "open", "high", "low", "close"]].copy()
    c = 100 * np.exp(np.cumsum(rng.normal(0, .008, len(f))))
    f["close"], f["open"], f["high"], f["low"] = c, c, c * 1.01, c * .99
    all_ = features(f)
    prefix = features(f.iloc[:500])
    for col in ("fast_ma", "slow_ma", "regime_ma", "atr", "osc", "v7_long", "v7_short", "vol_bin"):
        np.testing.assert_allclose(all_[col].iloc[:500], prefix[col], equal_nan=True)
    saturday_20utc = all_.open_time.eq(pd.Timestamp("2024-01-06T20:00Z"))
    assert all_.loc[saturday_20utc, "calendar_allowed"].all()
    assert all_.loc[all_.open_time.dt.dayofweek == 6, "calendar_allowed"].eq(False).all()
