"""Causal age filtering and independent occupancy for execution research only."""
from yoyo.evaluation.spike_v112_execution_study import accepted, serial_candidates


def test_age_gate_uses_original_parent_and_does_not_retry():
    assert accepted("age6", 0) and accepted("age6", 6)
    assert not accepted("age6", 7) and not accepted("age6", -1)
    assert accepted("parent_stop", 383)
    events = [{"signal_i": 10, "box_entry_i": 1, "bars_after_v9": 9},
              {"signal_i": 12, "box_entry_i": 12, "bars_after_v9": 0}]
    calls = []
    def evaluate(arm, i, s):
        calls.append(i)
        return "closed", {"signal_i": i, "exit_i": i + 2}
    _, status = serial_candidates(events, "age6", evaluate, 20)
    assert calls == [12]
    assert [r["status"] for r in status] == ["rejected_age6", "closed"]


def test_each_arm_owns_occupancy_and_released_candidate_is_replayed():
    events = [{"signal_i": i, "box_entry_i": i, "bars_after_v9": 0} for i in (10, 13, 20)]
    def evaluate(arm, i, s):
        return "closed", {"signal_i": i, "exit_i": i + (5 if arm == "baseline" else 2)}
    base, bs = serial_candidates(events, "baseline", evaluate, 30)
    candidate, cs = serial_candidates(events, "wick_arm", evaluate, 30)
    assert [r["signal_i"] for r in base] == [10, 20]
    assert [r["signal_i"] for r in candidate] == [10, 13, 20]
    assert bs[1]["status"] == "skipped_in_position" and cs[1]["status"] == "closed"


def test_control_uses_its_own_original_risk_denominator():
    """A random entry's 10% risk must not borrow the target's 2% risk."""
    from types import SimpleNamespace
    import numpy as np
    import pandas as pd
    from yoyo.evaluation.spike_v112_execution_study import matched_controls

    frame = pd.DataFrame(index=pd.date_range("2025-01-01", periods=20, freq="15min", tz="UTC"))
    prepared = SimpleNamespace(frame=frame, atr=np.full(20, 1.), close=np.full(20, 100.))
    target = {"trade_key": "test_control_risk", "signal_i": 10, "bars_after_v9": 3,
              "arm": "parent_stop", "censored": False, "baseline_risk_frac": .02}

    def evaluate(arm, i, parent):
        return "closed", {"censored": False, "net_r": .5, "net_return": .01,
                           "initial_risk_frac": .1 if arm == "baseline" else .02,
                           "exit_time": frame.index[-1]}

    result = matched_controls(prepared, [target], np.ones(20, dtype=bool), 15, evaluate)[0]
    assert result["matched"]
    assert result["control_baseline_risk_frac"] == .1
    assert np.isclose(result["control_net_r_on_baseline_risk"], .1)
