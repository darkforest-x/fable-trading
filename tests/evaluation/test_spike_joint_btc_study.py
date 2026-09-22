"""Runner-level contracts that do not require a market replay."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_joint_btc_gate import CONTROL_COLUMNS, DECISION_COLUMNS, STATUS_COLUMNS, TRADE_COLUMNS, _control_parity, _empty, matched_controls


def test_empty_symbol_tables_keep_the_reporter_schema():
    tables = _empty()
    assert tables["trades"].columns.tolist() == TRADE_COLUMNS
    assert tables["controls"].columns.tolist() == CONTROL_COLUMNS
    assert tables["statuses"].columns.tolist() == STATUS_COLUMNS
    assert tables["decisions"].columns.tolist() == DECISION_COLUMNS


def test_controls_use_the_same_gate_permission_as_the_trade_arm():
    index = pd.date_range("2025-01-02", periods=20, freq="15min", tz="UTC")
    prepared = SimpleNamespace(frame=pd.DataFrame(index=index), atr=np.ones(20), close=np.full(20, 100.))
    trade = {"trade_key": "binance_um:TEST:15m:box_any:10", "signal_i": 10, "exit_rule": "price", "gate": "same_sma120",
             "arm": "price__same_sma120", "censored": False}
    def evaluate(exit_rule, i):
        return "closed", {"censored": False, "net_r": .5, "net_return": .01, "exit_time": index[-1]}
    permitted = np.zeros(20, dtype=bool); permitted[[3, 10]] = True
    row = matched_controls(prepared, [trade], np.ones(20, dtype=bool), 15, permitted, evaluate)[0]
    assert row["matched"] and row["control_signal_i"] == 3
    denied = matched_controls(prepared, [trade], np.ones(20, dtype=bool), 15, np.eye(1, 20, 10, dtype=bool)[0], evaluate)[0]
    assert not denied["matched"] and denied["reason"] == "empty_stratum"


def test_thin_parent_empty_control_schema_is_explicit_parity_evidence():
    thin_parent = pd.DataFrame(columns=["timeframe_pair", "trade_key", "arm", "matched", "reason"])
    runner_empty = pd.DataFrame(columns=CONTROL_COLUMNS)
    evidence = _control_parity(thin_parent, runner_empty)
    assert evidence["passed"] and evidence["empty_evidence"] and evidence["reason"] == "both_empty"
    assert not _control_parity(thin_parent, pd.DataFrame([{"trade_key": "unexpected"}]))["passed"]


def test_full_control_parity_treats_both_unknown_exit_times_as_equal():
    from yoyo.evaluation.spike_joint_btc_gate import _control_parity
    old = pd.DataFrame([{'trade_key': 'a', 'matched': False, 'reason': 'invalid_initial',
        'control_signal_i': 3, 'control_signal_bar_open': '2025-01-02T00:45:00Z',
        'control_net_r': np.nan, 'control_net_return': np.nan, 'control_exit_time': np.nan,
        'month': '2025-01', 'vol_bin': 1, 'fold': 'earlier'}])
    new = old.copy()
    new['control_exit_time'] = pd.NaT
    assert _control_parity(old, new)['passed']
