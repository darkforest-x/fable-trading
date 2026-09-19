"""Meaningful report gates: temporal purge and complete control identity."""
import pandas as pd
import pytest

from yoyo.evaluation.spike_v112_execution_report import control_contract, period_rows


def test_same_return_different_control_exit_fails():
    row = {"trade_key": "x", "matched": True, "reason": "matched", "control_signal_i": 5,
           "control_signal_bar_open": "2025-01-01T00:00:00Z", "control_exit_time": "2025-01-01T01:00:00Z",
           "control_net_r": 1., "control_net_return": .01, "month": "2025-01", "vol_bin": 1, "fold": "earlier"}
    original = pd.DataFrame([row])
    changed = pd.DataFrame([{**row, "control_exit_time": "2025-01-01T02:00:00Z"}])
    assert control_contract(original, original)["passed"]
    with pytest.raises(AssertionError, match="control_exit_time"):
        control_contract(original, changed)


def test_earlier_outcome_cannot_cross_split():
    table = pd.DataFrame({"status": ["closed"] * 3,
                          "signal_close": pd.to_datetime(["2025-09-09", "2025-09-09", "2025-09-10"], utc=True),
                          "exit_time": pd.to_datetime(["2025-09-09", "2025-09-10", "2025-09-11"], utc=True)})
    assert period_rows(table, "earlier").index.tolist() == [0]
    assert period_rows(table, "later").index.tolist() == [2]
    assert len(period_rows(table, "full")) == 3
