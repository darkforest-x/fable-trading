from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.optimize_btcusdtp_k1k2_independent_timeframes import (
    audit_slice_label,
    binary_auc,
    build_core_pairs,
    select_coordinate,
    with_reference_features,
)


def _base_fixture() -> pd.DataFrame:
    n = 80
    frame = pd.DataFrame(
        {
            "open_time": pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC"),
            "open": np.full(n, 100.0),
            "high": np.full(n, 101.0),
            "low": np.full(n, 99.0),
            "close": np.full(n, 100.0),
            "volume": np.full(n, 10.0),
            "atr": np.full(n, 5.0),
            "segment_id": np.ones(n, dtype=int),
        }
    )
    # K1 genuinely opens below and closes above the selected rolling mean.
    frame.loc[45, ["open", "high", "low", "close"]] = [98.0, 106.0, 97.0, 105.0]
    # K2 keeps its full body above the mean while the lower wick touches it.
    frame.loc[48, ["open", "high", "low", "close"]] = [103.0, 105.0, 99.0, 104.0]
    return frame


def test_core_pair_and_score_are_causal_to_k2() -> None:
    original = with_reference_features(_base_fixture(), 40)
    mutated_base = _base_fixture()
    mutated_base.loc[50:, ["open", "high", "low", "close"]] = [10.0, 200.0, 1.0, 190.0]
    mutated = with_reference_features(mutated_base, 40)
    before = build_core_pairs(original, ma_period=40, maximum_gap_bars=12)
    after = build_core_pairs(mutated, ma_period=40, maximum_gap_bars=12)
    columns = ["direction", "k1_i", "k2_i", "gap_bars", "secondary_score"]
    pd.testing.assert_frame_equal(
        before.loc[before["k2_i"].lt(50), columns].reset_index(drop=True),
        after.loc[after["k2_i"].lt(50), columns].reset_index(drop=True),
    )
    target = before[
        before["direction"].eq(1)
        & before["k1_i"].eq(45)
        & before["k2_i"].eq(48)
    ]
    assert len(target) == 1
    assert 0.0 <= float(target.iloc[0]["secondary_score"]) <= 1.0


def test_k1_that_does_not_start_across_ma_is_rejected() -> None:
    base = _base_fixture()
    base.loc[45, "open"] = 102.0
    featured = with_reference_features(base, 40)
    pairs = build_core_pairs(featured, ma_period=40, maximum_gap_bars=12)
    assert not (
        pairs["direction"].eq(1)
        & pairs["k1_i"].eq(45)
        & pairs["k2_i"].eq(48)
    ).any()


def test_selection_requires_margin_and_sample_eligibility() -> None:
    incumbent = {"robust_score_bp": -10.0, "worst_fold_net_bp": -15.0}
    rows = [
        {
            "eligible": True,
            "robust_score_bp": -8.1,
            "worst_fold_net_bp": -5.0,
            "events": 300,
            "distance_from_initial": 1.0,
            "value_json": "80",
        },
        {
            "eligible": True,
            "robust_score_bp": -7.5,
            "worst_fold_net_bp": -13.0,
            "events": 250,
            "distance_from_initial": 2.0,
            "value_json": "120",
        },
    ]
    selected, reason = select_coordinate(rows, incumbent)
    assert reason == "move_by_preregistered_rule"
    assert selected is not None and selected["value_json"] == "120"


def test_auc_and_partial_audit_label() -> None:
    assert binary_auc([0.1, 0.2, 0.9, 1.0], [False, False, True, True]) == 1.0
    assert audit_slice_label(pd.Timestamp("2026-02-01T00:00:00Z")) == "2026P1"
