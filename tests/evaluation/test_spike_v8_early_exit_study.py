"""V8 admission and early-exit ordering contracts on a tiny authenticated shape."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation import spike_v8_early_exit_study as study


def _context() -> base.StreamContext:
    index = pd.date_range("2024-09-10", periods=16, freq="h", tz="UTC")
    bars = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "atr": 1.0,
                         "s20": 99.0, "e20": 99.0, "md": 2.0, "sb": 1.0, "ropeHigh": 99.0,
                         "ropeLow": 101.0}, index=index)
    signals = pd.DataFrame({"long_signal": False, "short_signal": False}, index=index)
    signals.loc[index[4], "long_signal"] = True
    bb = pd.DataFrame({"v7_ready": True, "prior_squeeze_run3": True}, index=index)
    cache = {"bars": bars, "signals": signals, "v1_signals": signals.copy(),
             "data_gap": pd.Series(False, index=index), "bb": bb, "tick": .01}
    ledger = pd.DataFrame({"signal_bar_open": [index[4]], "signal_i": [4]})
    return base.StreamContext(Path("."), "synthetic", {}, cache, ledger, 60,
                              {"venue": "test", "symbol": "X", "asset": "X", "timeframe_min": 60})


def test_v8_mask_changes_admission_but_keeps_raw_signal_feed() -> None:
    context = _context()
    context.cache["bars"].loc[:, "close"] = 103.1  # Long rope distance is >3 ATR.
    transformed = study.v8_context(context)
    assert transformed.cache["signals"].equals(context.cache["signals"])
    assert not transformed.cache["bb"].prior_squeeze_run3.iloc[4]
    trades, _, _ = study.replay_policy(context, policy="baseline")
    assert trades.empty


def test_raw_opposite_exit_supersedes_same_close_early_exit_even_if_v8_rejects_reverse() -> None:
    context = _context()
    bars, signals = context.cache["bars"], context.cache["signals"]
    # The long remains a V8 admission.  The short at bar 6 is deliberately
    # outside V8's 3-ATR gate but must still feed the raw opposite close.
    signals.iloc[6, signals.columns.get_loc("short_signal")] = True
    bars.iloc[6, bars.columns.get_loc("close")] = 96.0
    trades, fills, events = study.replay_policy(context, policy="no_new_extreme_2")
    exits = fills.loc[fills.kind.eq("exit")]
    assert exits.reason.iloc[0] == "opposite_v6_next_open"
    assert exits.bar_open.iloc[0] == bars.index[7]
    assert events.loc[events.reason.eq("no_new_extreme_2_next_open")].shape[0] == 1
    assert trades.exit_reason.iloc[0] == "opposite_v6_next_open"


def test_gap_stop_has_priority_over_scheduled_v8_early_exit() -> None:
    context = _context()
    bars = context.cache["bars"]
    bars.iloc[7, bars.columns.get_loc("open")] = 97.0
    trades, fills, _ = study.replay_policy(context, policy="no_new_extreme_2")
    assert fills.loc[fills.kind.eq("exit"), "reason"].iloc[0] == "initial_stop_gap"
    assert trades.exit_reason.iloc[0] == "initial_stop_gap"


def test_v8_baseline_reuses_the_unmodified_execution_engine() -> None:
    context = _context()
    actual = study.replay_policy(context, policy="baseline")
    expected = base.replay_policy(study.v8_context(context), cohort="v7_both", policy="baseline")
    for got, want in zip(actual, expected):
        pd.testing.assert_frame_equal(got, want)


def test_signflip_uses_positive_finite_draw_correction() -> None:
    rows = []
    for policy, values in (("baseline", (1.0, -1.0)), ("no_new_extreme_2", (2.0, -2.0))):
        for signal_i, value in enumerate(values):
            rows.append({"policy": policy, "censored": False, "stream_key": "s", "signal_i": signal_i,
                         "side": 1, "asset": "X", "entry_time": "2024-10-01T00:00:00Z", "net_r": value})
    result = study._paired_null(pd.DataFrame(rows), "no_new_extreme_2", period="development", draws=7)
    assert result["p_formula"] == "(exceedances + 1) / (draws + 1)"
    assert result["period"] == "development"
    assert 0 < result["two_sided_p"] <= 1


def test_drawdown_starts_from_zero_and_accounts_keep_the_fixed_stream_pool() -> None:
    assert study._drawdown_r(pd.Series([-2.0])) == 2.0
    trades = pd.DataFrame([{"policy": "baseline", "censored": False, "stream_key": "s1", "entry_time": "2024-10-01T00:00:00Z",
                            "exit_time": "2024-10-02T00:00:00Z", "net_r": -100.0}])
    universe = pd.DataFrame([{"stream_key": "s1", "timeframe_min": 60, "asset": "A"},
                             {"stream_key": "s2", "timeframe_min": 60, "asset": "B"}])
    accounts = study._stream_accounts(trades, universe)
    bankrupt = accounts.loc[(accounts.policy == "baseline") & (accounts.period == "development") & (accounts.stream_key == "s1")].iloc[0]
    empty = accounts.loc[(accounts.policy == "baseline") & (accounts.period == "development") & (accounts.stream_key == "s2")].iloc[0]
    assert bankrupt.account_return_1pct_risk == -1.0
    assert bankrupt.account_status == "bankrupt_nonpositive_factor"
    assert empty.closed_trades == 0
    assert empty.account_return_1pct_risk == 0.0
    assert empty.closed_trade_max_drawdown_r == 0.0
