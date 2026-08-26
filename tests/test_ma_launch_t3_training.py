from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pytest

from yoyo.datasets.ma_launch_t3_training import (
    Geometry,
    T3DatasetError,
    assign_geometry,
    geometry_audit,
    legal_geometries,
    mark_positive_guards,
    negative_feature_arrays,
    pine_rma,
    plan_positives,
    position_bin,
    split_for_interval,
)
from scripts.verify_15m_ma_launch_t3_dataset import (
    intervals_disjoint,
    overlaps_sorted_guards,
)


def prereg() -> dict:
    return {
        "protocol": "test",
        "positive_geometry": {
            "core_end_offset_from_t_bars": -3,
            "core_length_choices": [4, 5, 6, 7],
            "input_window_length_choices": list(range(14, 23)),
            "confirmation_bars_choices": [3, 4, 5],
            "maximum_window_end_offset_from_t_bars": 2,
            "position_gate": {
                "required_nonempty_bins": ["middle", "right"],
                "maximum_single_bin_share": 0.7,
                "minimum_center_fraction_std": 0.07,
                "minimum_unique_center_fractions_rounded_4dp": 20,
            },
        },
        "sources": {
            "bar_minutes": 15,
            "holdout_start": "2026-05-04T00:00:00Z",
        },
        "split": {
            "cutoff": "2026-03-01T00:00:00Z",
            "purge_bars": 150,
        },
        "negative_sampling": {
            "completed_no_launch_condition": {
                "pseudo_t_close_abs_atr_max_over_12_bars": 1.5,
                "pseudo_t_two_sided_favorable_abs_atr_max_over_12_bars": 2.0,
            },
            "hard_definition": {"six_ma_bandwidth_pct_max": 1.243218},
            "positive_guard": {
                "before_core_bars": 12,
                "after_latest_possible_window_end_bars": 12,
            },
        },
    }


def test_legal_geometry_is_t3_small_and_bounded() -> None:
    options = legal_geometries(prereg())
    assert len(options) == 9 * 4 * 3
    assert {option.core_len for option in options} == {4, 5, 6, 7}
    assert {option.window_len for option in options} == set(range(14, 23))
    assert {option.confirmation_bars for option in options} == {3, 4, 5}
    assert {option.core_end_offset for option in options} == {-3}
    assert max(option.window_end_offset for option in options) == 2
    assert min(option.core_start_local for option in options) >= 0


def test_geometry_assignment_is_identity_stable() -> None:
    first = assign_geometry("BTC|LONG|2025-01-01", prereg())
    second = assign_geometry("BTC|LONG|2025-01-01", prereg())
    assert first == second


def test_position_bins_are_not_all_fixed() -> None:
    bins = {option.position_bin for option in legal_geometries(prereg())}
    assert bins == {"middle", "right"}
    assert position_bin(0.2) == "left"
    assert position_bin(0.5) == "middle"
    assert position_bin(0.8) == "right"


def test_time_split_uses_complete_interval_and_symmetric_purge() -> None:
    kwargs = {
        "cutoff": "2026-03-01T00:00:00Z",
        "purge_bars": 150,
        "bar_minutes": 15,
    }
    assert split_for_interval("2026-02-01", "2026-02-20", **kwargs) == "train"
    assert split_for_interval("2026-03-03", "2026-03-04", **kwargs) == "val"
    assert split_for_interval("2026-02-28", "2026-03-02", **kwargs) is None


def test_positive_plan_moves_core_end_exactly_three_bars() -> None:
    row = {
        "event_id": "evt",
        "symbol": "BTC_USDT_SWAP",
        "direction": "LONG",
        "source_path": "data/x.csv",
        "source_anchor_i": 1000,
        "anchor_time": "2025-01-01T00:00:00Z",
    }
    plan = plan_positives([row], prereg())[0]
    assert plan.core_end_i == 997
    assert 4 <= plan.core_end_i - plan.core_start_i + 1 <= 7
    assert 14 <= plan.window_end_i - plan.window_start_i + 1 <= 22
    assert plan.window_end_i <= 1002
    assert plan.selection_label_end_i == 1011


def test_geometry_audit_passes_large_stable_identity_surface() -> None:
    rows = [
        {
            "event_id": f"e{i}",
            "symbol": f"S{i % 50}",
            "direction": "LONG" if i % 2 == 0 else "SHORT",
            "source_path": "data/x.csv",
            "source_anchor_i": 1000 + i * 300,
            "anchor_time": (pd.Timestamp("2024-01-01T00:00:00Z") + pd.Timedelta(hours=i)).isoformat(),
        }
        for i in range(2000)
    ]
    audit = geometry_audit(plan_positives(rows, prereg()), prereg())
    assert audit["passed"] is True
    assert audit["position_bins"]["middle"] > 0
    assert audit["position_bins"]["right"] > 0
    assert audit["maximum_single_bin_share"] <= 0.7


def test_geometry_audit_fails_fixed_position_collapse() -> None:
    p = prereg()
    p["positive_geometry"]["position_gate"]["required_nonempty_bins"] = ["middle", "right"]
    rows = [
        {
            "event_id": f"e{i}",
            "symbol": "S",
            "direction": "LONG",
            "source_path": "data/x.csv",
            "source_anchor_i": 1000 + i * 300,
            "anchor_time": (pd.Timestamp("2024-01-01T00:00:00Z") + pd.Timedelta(hours=i)).isoformat(),
        }
        for i in range(20)
    ]
    plans = plan_positives(rows, p)
    fixed = [
        copy.copy(plan).__class__(
            **{
                **plan.__dict__,
                "geometry": Geometry(14, 7, 5),
            }
        )
        for plan in plans
    ]
    with pytest.raises(T3DatasetError, match="position bins are missing"):
        geometry_audit(fixed, p)


def test_pine_rma_has_sma_seed_then_wilder_updates() -> None:
    values = np.arange(1.0, 7.0)
    result = pine_rma(values, 3)
    assert np.isnan(result[:2]).all()
    assert result[2] == pytest.approx(2.0)
    assert result[3] == pytest.approx((2.0 * 2 + 4.0) / 3)


def test_negative_features_separate_easy_and_dense_no_launch() -> None:
    n = 180
    close = np.full(n, 100.0)
    frame = pd.DataFrame(
        {
            "open": close,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "sma20": close,
            "ema20": close,
            "sma60": close,
            "ema60": close,
            "sma120": close,
            "ema120": close,
        }
    )
    features = negative_feature_arrays(frame, prereg())
    assert features["no_launch"][150]
    assert features["hard"][150]
    assert features["close_abs_atr"][150] == pytest.approx(0.0)


def test_positive_guard_protects_max_core_and_latest_window() -> None:
    occupied = np.zeros(200, dtype=bool)
    mark_positive_guards(
        occupied,
        [{"source_anchor_i": 100}],
        prereg(),
    )
    # Max core starts at t-9; guard adds 12 before. Latest window ends at t+2,
    # guard adds 12 after.
    assert occupied[79:115].all()
    assert not occupied[78]
    assert not occupied[115]


def test_closed_interval_disjoint_audit() -> None:
    assert intervals_disjoint([(0, 4), (5, 9), (20, 25)])
    assert not intervals_disjoint([(0, 4), (4, 9)])


def test_sorted_guard_overlap_lookup() -> None:
    guards = [(10, 20), (40, 50), (70, 80)]
    starts = [start for start, _ in guards]
    assert overlaps_sorted_guards(15, 16, guards, starts)
    assert overlaps_sorted_guards(21, 40, guards, starts)
    assert not overlaps_sorted_guards(21, 39, guards, starts)
