from __future__ import annotations

import numpy as np
import pandas as pd

import yoyo.datasets.ma_launch_t3_hard_val as hard_val
from scripts.verify_15m_ma_launch_t3_hard_val import intervals_disjoint, overlaps_any


def _base() -> dict:
    return {
        "protocol": "base",
        "sources": {
            "bar_minutes": 15,
            "holdout_start": "2026-05-04T00:00:00Z",
        },
        "split": {
            "cutoff": "2026-03-01T00:00:00Z",
            "purge_bars": 0,
        },
        "positive_geometry": {
            "core_length_choices": [4, 5, 6, 7],
            "core_end_offset_from_t_bars": -3,
            "maximum_window_end_offset_from_t_bars": 2,
        },
        "negative_sampling": {
            "positive_guard": {
                "before_core_bars": 12,
                "after_latest_possible_window_end_bars": 12,
            }
        },
    }


def _contract() -> dict:
    return {
        "protocol": "hard-val",
        "sources": {"holdout_start": "2026-05-04T00:00:00Z"},
    }


def _frame() -> pd.DataFrame:
    count = 140
    return pd.DataFrame(
        {
            "open_time": pd.date_range(
                "2026-03-03T00:00:00Z", periods=count, freq="15min"
            ),
            "_segment_id": np.zeros(count, dtype=int),
        }
    )


def test_hard_val_matching_reuses_candidate_that_did_not_fit_prior_template(
    monkeypatch,
) -> None:
    count = len(_frame())
    monkeypatch.setattr(
        hard_val,
        "negative_feature_arrays",
        lambda enriched, base: {
            "bandwidth_pct": np.full(count, 0.5),
            "close_abs_atr": np.full(count, 0.2),
            "two_sided_favorable_abs_atr": np.full(count, 0.3),
        },
    )
    # The first candidate cannot fit W22 at the left edge but remains available
    # for the later W14 template; a single forward cursor would discard it.
    monkeypatch.setattr(
        hard_val,
        "_negative_pool",
        lambda *args, **kwargs: np.asarray([15, 60], dtype=int),
    )
    templates = [
        {
            "sample_id": "long",
            "geometry": {"window_len": 22, "confirmation_bars": 3},
        },
        {
            "sample_id": "short",
            "geometry": {"window_len": 14, "confirmation_bars": 3},
        },
    ]
    selected, missing = hard_val.select_source_hard_val(
        _frame(),
        source_path="data/test.csv",
        symbol="TEST",
        source_candidates=[],
        templates=templates,
        existing_negative_intervals=[],
        contract=_contract(),
        base=_base(),
    )
    assert not missing
    assert {plan.template_positive_sample_id for plan in selected} == {"long", "short"}
    assert intervals_disjoint(
        (plan.window_start_i, plan.window_end_i) for plan in selected
    )


def test_hard_val_capacity_shortfall_is_recorded_not_weakened(monkeypatch) -> None:
    count = len(_frame())
    monkeypatch.setattr(
        hard_val,
        "negative_feature_arrays",
        lambda enriched, base: {
            "bandwidth_pct": np.full(count, 0.5),
            "close_abs_atr": np.full(count, 0.2),
            "two_sided_favorable_abs_atr": np.full(count, 0.3),
        },
    )
    monkeypatch.setattr(
        hard_val,
        "_negative_pool",
        lambda *args, **kwargs: np.asarray([40], dtype=int),
    )
    templates = [
        {"sample_id": "a", "geometry": {"window_len": 14, "confirmation_bars": 3}},
        {"sample_id": "b", "geometry": {"window_len": 14, "confirmation_bars": 3}},
    ]
    selected, missing = hard_val.select_source_hard_val(
        _frame(),
        source_path="data/test.csv",
        symbol="TEST",
        source_candidates=[],
        templates=templates,
        existing_negative_intervals=[],
        contract=_contract(),
        base=_base(),
    )
    assert len(selected) == 1
    assert len(missing) == 1
    assert missing[0]["reason"] == "safe_same_source_capacity_exhausted"


def test_closed_interval_helpers() -> None:
    assert intervals_disjoint([(0, 3), (4, 7)])
    assert not intervals_disjoint([(0, 3), (3, 7)])
    intervals = [(10, 20), (40, 50)]
    starts = [10, 40]
    assert overlaps_any(20, 30, intervals, starts)
    assert not overlaps_any(21, 39, intervals, starts)
