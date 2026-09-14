"""Synthetic contracts for the standalone multi-timeframe release replay."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yoyo.layers.l3_backtest.pine_allin_eth4h import Policy as OldPolicy
from yoyo.layers.l3_backtest.pine_allin_eth4h import Replay as OldReplay
from yoyo.evaluation.release_eth_metrics import settle
from yoyo.layers.l3_backtest.release_eth_multitf import Policy, Replay


def frame(n: int = 8, *, start: str = "2026-01-01", minutes: int = 240) -> pd.DataFrame:
    time = pd.date_range(start, periods=n, freq=f"{minutes}min", tz="UTC")
    close = np.full(n, 100.0)
    return pd.DataFrame({
        "open_time": time, "open": close, "high": close + .5, "low": close - .5, "close": close,
        "atr": np.ones(n), "entry_allowed": np.ones(n, dtype=bool), "osc": np.ones(n),
        "hk_dayofweek": np.zeros(n, dtype=int), "hk_hour": np.zeros(n, dtype=int),
        "v7_long": np.zeros(n, dtype=bool), "v7_short": np.zeros(n, dtype=bool),
        "cross_long": np.zeros(n, dtype=bool), "cross_short": np.zeros(n, dtype=bool),
        "slow_ma": np.arange(n, dtype=float),
    })


@pytest.mark.parametrize("minutes", [15, 60, 240])
def test_equity_timestamp_uses_parent_duration(minutes: int) -> None:
    f = frame(3, minutes=minutes)
    out = Replay(f, Policy(), minutes).run(0, len(f))
    assert out["equity"].time.iloc[0] == f.open_time.iloc[0] + pd.Timedelta(minutes=minutes)
    assert out["trades"].empty


def test_default_4h_trade_accounting_matches_historical_replay() -> None:
    f = frame(7)
    f.loc[0, "v7_long"] = True
    f.loc[2, "v7_short"] = True
    f.loc[3, ["open", "high", "low", "close"]] = [104., 104.5, 103.5, 104.]
    old = OldReplay(f.drop(columns="slow_ma"), OldPolicy("old", fee=.001)).run(0, len(f))
    new = Replay(f, Policy("new", fee=.001), 240).run(0, len(f))
    cols = ["direction", "qty", "signal_i", "entry_i", "entry_price", "exit_i", "exit_price",
            "exit_reason", "gross_pnl", "net_pnl", "fees", "holding_bars"]
    pd.testing.assert_frame_equal(new["trades"][cols].reset_index(drop=True), old["trades"][cols].reset_index(drop=True))
    pd.testing.assert_frame_equal(new["equity"].reset_index(drop=True), old["equity"].reset_index(drop=True))
    assert new["cash"] == pytest.approx(old["cash"])
    assert new["fees"] == pytest.approx(old["fees"])
    assert new["max_drawdown_path"] == pytest.approx(old["max_drawdown_path"])


def test_immediate_stop_protects_fill_bar_but_delayed_stop_does_not() -> None:
    f = frame(4)
    f.loc[0, "v7_long"] = True
    f.loc[1, ["high", "low"]] = [100.5, 96.0]
    f.loc[2, ["open", "high", "low", "close"]] = [96.0, 96.5, 95.5, 96.0]
    delayed = Replay(f, Policy(immediate_stop=False), 240).run(0, len(f))["trades"].iloc[0]
    immediate = Replay(f, Policy(immediate_stop=True), 240).run(0, len(f))["trades"].iloc[0]
    assert delayed.exit_i == 2 and delayed.exit_reason == "stop_gap"
    assert immediate.exit_i == 1 and immediate.exit_reason == "stop"
    assert bool(delayed.firstbar_unprotected_touch)


def test_break_even_update_is_not_retroactive_on_the_trigger_bar() -> None:
    f = frame(4)
    f.loc[0, "v7_long"] = True
    # The low precedes the high in the deterministic path; an immediate BE fill
    # would be a forbidden retroactive use of the close-confirmed trigger.
    f.loc[1, ["high", "low"]] = [101.6, 99.0]
    f.loc[2, ["open", "high", "low", "close"]] = [100.2, 100.3, 99.5, 100.2]
    trade = Replay(f, Policy(immediate_stop=True), 240).run(0, len(f))["trades"].iloc[0]
    assert trade.exit_i == 2
    assert trade.exit_price == pytest.approx(100.1)


def test_fees_apply_to_every_fill_and_trade_evidence_is_causal() -> None:
    f = frame(4)
    f.loc[0, "v7_long"] = True
    # Low is reached before the later extreme high, so exit path must not claim it as MFE.
    f.loc[1, ["high", "low"]] = [111.0, 90.0]
    out = Replay(f, Policy(immediate_stop=True, fee=.001), 240).run(0, len(f))
    trade = out["trades"].iloc[0]
    expected = trade.qty * trade.entry_price * .001 + trade.qty * trade.exit_price * .001
    assert trade.fees == pytest.approx(expected)
    assert out["fees"] == pytest.approx(expected)
    assert trade.mfe_return == pytest.approx(0.0)
    assert trade.mae_r <= -1.0
    assert out["max_leverage"] >= trade.leverage


def test_fresh_cooldown_and_stop_policy_switches_are_individual() -> None:
    f = frame(6)
    f.loc[0, "v7_long"] = True
    f.loc[1, "v7_short"] = True
    f.loc[2, ["open", "high", "low", "close"]] = [104., 104.5, 103.5, 104.]
    f.loc[2, "v7_long"] = True
    old = Replay(f, Policy(fresh_cooldown=False), 240).run(0, len(f))
    fresh = Replay(f, Policy(fresh_cooldown=True), 240).run(0, len(f))
    assert "entry_signal" in set(old["events"].event)
    assert (fresh["events"].event == "cooldown_skip").any()

    f2 = frame(5)
    f2.loc[[0, 1, 2], "v7_long"] = True
    f2.loc[2, ["high", "low", "close"]] = [100., 98., 99.]
    loose = Replay(f2, Policy(ratchet_only=False), 240).run(0, len(f2))["events"]
    tight = Replay(f2, Policy(ratchet_only=True), 240).run(0, len(f2))["events"]
    assert loose[loose.event == "same_side_stop_reset"].stop.iloc[-1] < tight[tight.event == "same_side_stop_reset"].stop.iloc[-1]

    f3 = frame(4)
    f3.loc[0, "v7_long"] = True
    f3.loc[1, "v7_short"] = True
    f3.loc[2, ["high", "low"]] = [104., 99.]
    shared = Replay(f3, Policy(immediate_stop=False, isolate_stops=False), 240).run(0, len(f3))
    isolated = Replay(f3, Policy(immediate_stop=True, isolate_stops=True), 240).run(0, len(f3))
    assert len(shared["trades"]) == 1  # only the long reverse exit
    assert len(isolated["trades"]) == 2
    assert isolated["trades"].iloc[-1].exit_reason == "stop"

    # Stop isolation controls reset sharing.  It must not also enable fill-bar
    # protection; E5 keeps both flags explicitly true where both are wanted.
    f4 = frame(4)
    f4.loc[0, "v7_long"] = True
    f4.loc[1, ["high", "low"]] = [100.5, 96.0]
    f4.loc[2, ["high", "low"]] = [100.5, 96.0]
    isolated_only = Replay(f4, Policy(immediate_stop=False, isolate_stops=True), 240).run(0, len(f4))
    assert not bool(isolated_only["events"].query("event == 'entry_fill'").stop_active.iloc[0])
    assert isolated_only["trades"].iloc[0].exit_i == 2


def test_slope_cross_and_injected_control_boundaries() -> None:
    f = frame(6)
    f.loc[1, "v7_long"] = True
    f.loc[1, "slow_ma"] = -1.0
    assert Replay(f, Policy(entry_gate="slope"), 240).run(0, len(f))["trades"].empty
    f.loc[1, "cross_long"] = True
    f.loc[1, "slow_ma"] = 2.0
    assert len(Replay(f, Policy(cross_only=True, entry_gate="slope"), 240).run(0, len(f))["trades"]) == 0  # censored
    # Ordinary short exits the injected long at the next open; control cannot reopen.
    f.loc[2, "v7_short"] = True
    out = Replay(f, Policy(immediate_stop=True), 240).run(0, len(f), injected=(0, 1), record_equity=False)
    assert len(out["trades"]) == 1
    assert out["open_position"] is None
    assert out["equity"].empty


def test_15m_execution_frame_refines_parent_path_and_requires_complete_groups() -> None:
    f = frame(3, minutes=60)
    f.loc[0, "v7_long"] = True
    f.loc[1, "low"] = 96.
    child_times = pd.date_range(f.open_time.iloc[0], periods=12, freq="15min", tz="UTC")
    child = pd.DataFrame({"open_time": child_times, "open": 100., "high": 100.5, "low": 99.5, "close": 100.})
    child.loc[5, "low"] = 96.  # second 15m child of the entry parent bar
    out = Replay(f, Policy(immediate_stop=True), 60, child).run(0, 3)
    assert out["execution_precision"] == "15m"
    assert out["trades"].iloc[0].exit_time == child.open_time.iloc[5]
    with pytest.raises(ValueError, match="complete 15m coverage"):
        Replay(f, Policy(), 60, child.iloc[:-1])
    invalid = child.copy()
    invalid.loc[0, "high"] = 99.0
    with pytest.raises(ValueError, match="invalid OHLC geometry"):
        Replay(f, Policy(), 60, invalid)


def test_delayed_gap_entry_with_zero_initial_risk_has_no_r_multiple() -> None:
    f = frame(4)
    f.loc[0, "v7_long"] = True  # signal close 100, proposed stop 97
    f.loc[1, ["open", "high", "low", "close"]] = [97.0, 97.5, 96.5, 97.0]
    f.loc[2, ["open", "high", "low", "close"]] = [97.0, 97.5, 96.5, 97.0]
    out = Replay(f, Policy(immediate_stop=False), 240).run(0, len(f))
    trade = out["trades"].iloc[0]
    assert trade.entry_price == pytest.approx(97.0)
    assert trade.initial_stop == pytest.approx(97.0)
    assert trade.initial_risk_fraction == 0.0
    assert np.isnan(trade.mfe_r) and np.isnan(trade.mae_r)
    assert not np.isinf(out["trades"][["mfe_r", "mae_r"]].to_numpy(float)).any()
    assert int((out["trades"].mfe_r >= 1).sum()) == 0


def test_flat_exit_fee_is_observed_without_equity_records() -> None:
    f = frame(4)
    f.loc[0, "v7_long"] = True
    f.loc[1, ["high", "low"]] = [100.5, 96.0]
    out = Replay(f, Policy(immediate_stop=True, fee=.001), 240).run(0, len(f), record_equity=False)
    trade = out["trades"].iloc[0]
    assert out["equity"].empty
    assert out["path_peak"] == pytest.approx(508.0)
    assert out["close_peak"] == pytest.approx(500.0)
    assert out["cash"] == pytest.approx(500.0 + trade.net_pnl)
    assert out["max_drawdown_path"] >= (500.0 - out["cash"]) / 500.0


def test_no_equity_replay_peaks_settle_administrative_fee() -> None:
    f = frame(3)
    f.loc[0, "v7_long"] = True
    policy = Policy(fee=.001)
    out = Replay(f, policy, 240).run(0, len(f), record_equity=False)
    assert out["equity"].empty
    assert out["open_position"] is not None
    assert out["path_peak"] == pytest.approx(500.0)
    assert out["close_peak"] == pytest.approx(500.0)
    settled = settle(out, f, len(f), policy.fee, 240)
    assert settled["final_equity"] == pytest.approx(496.0)
    assert settled["max_drawdown_path"] == pytest.approx(.008)
    assert settled["max_drawdown_close"] == pytest.approx(.008)
