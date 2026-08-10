"""Unit tests for deterministic P2 visual-audit geometry."""
from __future__ import annotations

from scripts.audit_local_signal_v2_p2_visuals import (
    deterministic_sample,
    xywhn_to_xyxy,
)


def test_xywhn_to_xyxy_converts_and_bounds():
    assert xywhn_to_xyxy([0.5, 0.5, 0.2, 0.4], 100, 50) == (40, 15, 60, 35)
    assert xywhn_to_xyxy([0.0, 0.0, 0.4, 0.4], 100, 50) == (0, 0, 20, 10)


def test_deterministic_sample_is_stable_and_keeps_source_order():
    rows = [{"id": value} for value in range(20)]
    first = deterministic_sample(rows, 5, seed=7)
    second = deterministic_sample(rows, 5, seed=7)
    assert first == second
    assert [row["id"] for row in first] == sorted(row["id"] for row in first)


def test_deterministic_sample_returns_all_when_small():
    rows = [{"id": 1}, {"id": 2}]
    assert deterministic_sample(rows, 5, seed=7) == rows
