"""Entry-filter and raw-opposite-exit contracts for the selected V8 rule."""
from pathlib import Path

import pandas as pd

from yoyo.evaluation.spike_exit_policy_study import StreamContext
from yoyo.evaluation.spike_v8_replay import replay_mask, v8_admissions


def context():
    index = pd.date_range("2025-01-01", periods=40, freq="h", tz="UTC")
    bars = pd.DataFrame({
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "atr": 1.0,
        "s20": 99.0, "e20": 99.0, "ropeHigh": 99.0, "ropeLow": 101.0,
        "md": 1.0, "sb": 0.0,
    }, index=index)
    signals = pd.DataFrame({"long_signal": False, "short_signal": False}, index=index)
    bb = pd.DataFrame({"v7_ready": True, "prior_squeeze_run3": True}, index=index)
    gaps = pd.Series(False, index=index)
    cache = {"bars": bars, "signals": signals, "v1_signals": signals.copy(), "bb": bb,
             "data_gap": gaps, "tick": 0.01}
    ledger = pd.DataFrame(columns=["signal_bar_open", "signal_i"])
    return StreamContext(Path("."), "test", {}, cache, ledger, 60,
                         {"venue": "test", "symbol": "TEST", "asset": "TEST", "timeframe_min": 60})


def test_exact_three_atr_is_admitted_but_later_confirmation_is_filtered():
    c = context()
    index = c.cache["bars"].index
    c.cache["signals"].loc[index[[15, 20]], "long_signal"] = True
    c.cache["bars"].loc[index[15], ["close", "ropeHigh"]] = [102.0, 99.0]
    c.cache["bars"].loc[index[20], ["close", "ropeHigh"]] = [102.01, 99.0]
    gate = v8_admissions(c)
    assert gate.loc[index[15], "v8"]
    assert not gate.loc[index[20], "v8"]
    assert gate.loc[index[20], "reason"] == "overheated_gt_3atr"


def test_filtered_opposite_entry_still_closes_existing_position():
    c = context()
    index = c.cache["bars"].index
    c.cache["signals"].loc[index[15], "long_signal"] = True
    c.cache["signals"].loc[index[20], "short_signal"] = True
    c.cache["bars"].loc[index[20], ["close", "ropeLow", "open", "high", "low", "md", "sb"]] = [99.0, 103.0, 99.5, 100.0, 98.5, -2.0, -1.0]
    gate = v8_admissions(c)
    assert gate.loc[index[15], "v8"]
    assert not gate.loc[index[20], "v8"]
    trades, _, _ = replay_mask(c, gate.v8)
    assert len(trades) == 1
    assert trades.exit_reason.iloc[0] == "opposite_v6_next_open"
    assert trades.exit_time.iloc[0] == index[21]


def test_future_mutation_cannot_change_past_v8_gate():
    c = context()
    index = c.cache["bars"].index
    c.cache["signals"].loc[index[15], "long_signal"] = True
    before = v8_admissions(c).iloc[:20]
    c.cache["bars"].loc[index[25]:, ["close", "high", "low"]] = [200.0, 201.0, 199.0]
    after = v8_admissions(c).iloc[:20]
    pd.testing.assert_frame_equal(before, after)
