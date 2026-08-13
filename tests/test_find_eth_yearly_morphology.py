"""Unit tests for the frozen ETH yearly morphology gate helpers."""

from __future__ import annotations

from scripts.find_eth_yearly_morphology import build_gate, cluster_candidate_events, passes_gate


def reference_row() -> dict[str, object]:
    return {
        "ma_span_pct": 0.061386,
        "core_range_pct": 0.370104,
        "core_net_pct": -0.113116,
        "core_before_last_pct": 0.071,
        "pre_range_pct": 0.435,
        "pre_net_pct": 0.040,
        "core_range_atr": 2.306,
        "core_intersects_ma_bundle": True,
        "confirm_d3_close_pct": -0.410185,
        "confirm_d5_close_pct": -0.817238,
        "confirm_low5_pct": -0.974841,
        "confirm_high5_pct": 0.0,
        "confirm_red5": 4,
        "core_width": 4,
    }


def test_reference_passes_its_frozen_gate() -> None:
    reference = reference_row()
    assert passes_gate(reference, build_gate(reference))


def test_future_drop_alone_does_not_pass_core_gate() -> None:
    reference = reference_row()
    candidate = dict(reference)
    candidate["ma_span_pct"] = 0.50
    candidate["confirm_d5_close_pct"] = -5.0
    assert not passes_gate(candidate, build_gate(reference))


def test_cluster_is_transitive_and_keeps_best_width_per_endpoint() -> None:
    rows = [
        {"core_end_i": 10, "distance": 0.4, "core_end_time": "a"},
        {"core_end_i": 10, "distance": 0.2, "core_end_time": "a"},
        {"core_end_i": 15, "distance": 0.3, "core_end_time": "b"},
        {"core_end_i": 21, "distance": 0.1, "core_end_time": "c"},
        {"core_end_i": 40, "distance": 0.5, "core_end_time": "d"},
    ]
    events = cluster_candidate_events(rows, gap_bars=6)
    assert len(events) == 2
    assert events[0]["core_end_i"] == 21
    assert events[0]["endpoint_count"] == 3
    assert events[1]["core_end_i"] == 40
