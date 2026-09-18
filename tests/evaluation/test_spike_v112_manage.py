"""Guards for the V11.2 management rules on V9 longs.

What must hold: with no break on bars s..s+N the trade leaves at open[s+N+1];
a break inside that span, or an exit already at/before s+N+1, leaves the trade
unchanged; the add unit enters at the open after the first break while the
trade is open and exits with it; a break on the exit bar adds nothing.
"""
import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v112_manage_study import add_unit, cut_if_no_break

INDEX = pd.date_range("2025-01-01", periods=40, freq="15min", tz="UTC")
OPEN = np.linspace(100, 104, 40)
GAP = np.zeros(40, bool)


def trade(exit_i=30, exit_price=110.0):
    return {"entry_price": 100.0, "initial_risk": 2.0, "initial_risk_frac": 0.02, "exit_i": exit_i,
            "exit_time": INDEX[exit_i], "exit_price": exit_price, "exit_reason": "trailing_stop",
            "net_return": exit_price / 100.0 - 1 - 0.002, "net_r": (exit_price / 100.0 - 1 - 0.002) / 0.02,
            "censored": False}


def test_no_break_leaves_at_the_open_after_n_bars():
    out = cut_if_no_break(trade(), 10, np.zeros(40, bool), OPEN, GAP, INDEX, 6)
    assert out["exit_i"] == 17 and out["exit_reason"] == "no_break_exit" and out["exit_price"] == OPEN[17]
    assert np.isclose(out["net_r"], (OPEN[17] / 100 - 1 - 0.002) / 0.02)


def test_break_inside_span_or_early_exit_keeps_the_trade():
    brk = np.zeros(40, bool); brk[16] = True
    assert cut_if_no_break(trade(), 10, brk, OPEN, GAP, INDEX, 6)["exit_i"] == 30
    assert cut_if_no_break(trade(exit_i=15), 10, np.zeros(40, bool), OPEN, GAP, INDEX, 6)["exit_i"] == 15
    brk_late = np.zeros(40, bool); brk_late[17] = True       # one bar too late: still cut
    assert cut_if_no_break(trade(), 10, brk_late, OPEN, GAP, INDEX, 6)["exit_i"] == 17


def test_add_unit_enters_after_first_break_and_exits_with_the_trade():
    brk = np.zeros(40, bool); brk[[14, 20]] = True
    add = add_unit(trade(), 10, brk, OPEN)
    assert add["add_break_i"] == 14 and add["add_price"] == OPEN[15]
    assert np.isclose(add["add_net_r"], (110.0 / OPEN[15] - 1 - 0.002) / 0.02)
    only_exit_bar = np.zeros(40, bool); only_exit_bar[30] = True
    assert add_unit(trade(), 10, only_exit_bar, OPEN) is None
