"""Tests for the frozen P1 discovery-gate selector."""
from scripts.summarize_local_signal_v2_p1 import select_candidate, select_gate_point


GATE = {
    "event_recall_min": 0.5,
    "event_precision_min": 0.5,
    "fp_per_1000_bars_max": 250.0,
}


def row(threshold: float, precision: float, recall: float, fp: float) -> dict:
    return {
        "threshold": threshold,
        "event_precision": precision,
        "event_recall": recall,
        "fp_per_1000_bars": fp,
    }


def test_gate_rejects_when_any_frozen_floor_fails():
    rows = [
        row(0.2, 0.49, 0.8, 100),
        row(0.3, 0.8, 0.49, 100),
        row(0.4, 0.8, 0.8, 251),
    ]
    assert select_gate_point(rows, GATE) is None


def test_gate_selects_lowest_fp_then_higher_precision():
    rows = [
        row(0.2, 0.70, 0.80, 120),
        row(0.3, 0.65, 0.60, 80),
        row(0.4, 0.75, 0.55, 80),
    ]
    assert select_gate_point(rows, GATE)["threshold"] == 0.4


def test_candidate_selector_uses_quietest_passing_arm():
    arms = [
        {"arm": "B1", "discovery_gate_pass": False, "gate_operating_point": None},
        {
            "arm": "B2",
            "discovery_gate_pass": True,
            "gate_operating_point": row(0.35, 0.82, 0.73, 81),
        },
        {
            "arm": "C3",
            "discovery_gate_pass": True,
            "gate_operating_point": row(0.45, 0.75, 0.71, 120),
        },
    ]
    assert select_candidate(arms)["arm"] == "B2"


def test_candidate_selector_rejects_when_nothing_passes():
    arms = [{"arm": "B1", "discovery_gate_pass": False, "gate_operating_point": None}]
    assert select_candidate(arms) is None
