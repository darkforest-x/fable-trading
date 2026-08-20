"""Tests for the complete raw-candidate judgment feature surface."""
from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.prepare_pine_eth_15m_gate_surface import compare_executed_coverage


def test_executed_coverage_exposes_raw_candidates_missing_from_baseline() -> None:
    surface = pd.DataFrame(
        {
            "side": ["long", "short", "long"],
            "signal_i": np.asarray([10, 20, 30]),
        }
    )
    executed = pd.DataFrame(
        {"side": ["long", "short"], "signal_i": np.asarray([10, 20])}
    )
    result = compare_executed_coverage(surface, executed)
    assert result["raw_guarded_candidates"] == 3
    assert result["baseline_executed_candidates"] == 2
    assert result["raw_candidates_not_in_baseline_ledger"] == 1
    assert result["baseline_coverage_of_raw_surface"] == 2 / 3


def test_executed_coverage_rejects_non_surface_baseline_signal() -> None:
    surface = pd.DataFrame({"side": ["long"], "signal_i": [10]})
    executed = pd.DataFrame({"side": ["short"], "signal_i": [10]})
    try:
        compare_executed_coverage(surface, executed)
    except RuntimeError as exc:
        assert "non-surface" in str(exc)
    else:
        raise AssertionError("expected non-surface baseline signal to fail")
