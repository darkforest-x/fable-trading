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
