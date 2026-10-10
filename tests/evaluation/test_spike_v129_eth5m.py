"""V12.9 5m exit semantics, frozen scope, and parent-parity guard for the ETH replay."""
import math
import re

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_lowtf_v1_v126 as low
from yoyo.evaluation import spike_v129_eth5m as study
from yoyo.evaluation.spike_v6_wvf_study import ExecutionSpec


def test_v129_keeps_four_atr_trail_on_5m():
    source = study.PINE_V129.read_text()
    line = re.search(r"^float trailAtr = (.+)$", source, re.M).group(1)
    assert line == "timeframe.isminutes and timeframe.multiplier == 60 ? 2.0 : 4.0"
    spec = ExecutionSpec()
    assert (spec.trail_atr, spec.arm_r, spec.risk_floor_atr, spec.stop_bars, spec.round_trip_cost) == (4.0, 2.0, 2.0, 5, .002)
    assert "useM15Ema120 = input.bool(true" in source


def test_frozen_scope():
    cfg = study.config()
    assert cfg["htf_min"] == low.HTF_OF[5] == 15 and cfg["venue"] == "okx"
    assert pd.Timestamp(cfg["start"]) < pd.Timestamp(cfg["split"]) < study.PARENT_END < pd.Timestamp(cfg["end"])
    assert pd.Timestamp(cfg["tail_start"]) == study.PARENT_END
    assert cfg["training_eligible"] is False and cfg["production_eligible"] is False


def _parent(tmp_path, rows):
    path = tmp_path / "parent.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return {"parent_trades": str(path)}


def _row(i, exit_price=101.0, exit_time="2025-01-01T01:00:00Z"):
    return {"arm": "v126", "censored": False, "signal_i": float(i), "entry_time": "2025-01-01T00:05:00Z",
            "entry_price": 100.0, "initial_stop": 99.0, "exit_time": exit_time, "exit_price": exit_price,
            "exit_reason": "trailing_stop", "net_r": .8}


def test_parent_parity_accepts_exact_and_rejects_drift(tmp_path):
    cfg = _parent(tmp_path, [_row(7)])
    assert study.parent_parity(pd.DataFrame([_row(7)]), cfg)["parent_closed_joint_trades_checked"] == 1
    with pytest.raises(AssertionError):
        study.parent_parity(pd.DataFrame([_row(7, exit_price=102.0)]), cfg)
    with pytest.raises(ValueError):
        study.parent_parity(pd.DataFrame([_row(8)]), cfg)
    # A trade still open at the parent's data end is censored there and excluded on both sides.
    late = _row(9, exit_time="2026-09-25T00:00:00Z")
    assert study.parent_parity(pd.DataFrame([_row(7), late]), cfg)["parent_closed_joint_trades_checked"] == 1


def test_helpers():
    assert study._streaks(np.array([True, True, False, True, True, True])) == 3
    assert study._clean({"a": np.int64(2), "b": [math.nan, np.float64(1.5)]}) == {"a": 2, "b": [None, 1.5]}
