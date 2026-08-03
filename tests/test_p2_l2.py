"""P2-L2 economic protocol tests; synthetic fixtures only."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.judgment.p2_l2 import (
    economic_metrics,
    exact_block_signflip_pvalue,
    exact_top_fraction_weights,
    matched_candidate_pairs,
    prepare_walkforward_fold,
)


def _economic_frame(n: int) -> pd.DataFrame:
    gross = np.linspace(-0.02, 0.03, n)
    return pd.DataFrame(
        {
            "candidate_id": [f"c{i}" for i in range(n)],
            "event_group_id": [f"g{i}" for i in range(n)],
            "symbol": ["BTC_USDT_SWAP"] * n,
            "signal_time": pd.date_range("2026-02-01", periods=n, freq="15min", tz="UTC"),
            "atr_pct": np.linspace(0.001, 0.01, n),
            "gross_ret": gross,
            "net_ret_swap_taker": gross - 0.001,
            "label_tp_before_sl": (gross > 0).astype(int),
        }
    )


def test_exact_top_decile_fractionally_weights_all_boundary_ties() -> None:
    scores = np.array([0.0] * 16 + [1.0] * 4)
    result = exact_top_fraction_weights(scores)
    assert result["target_n"] == 2
    assert result["n_equal_boundary"] == 4
    assert result["equality_weight"] == pytest.approx(0.5)
    assert result["boundary_tied"] is True
    weights = result["weights"]
    assert weights.sum() == pytest.approx(2.0)
    assert np.unique(weights[scores == 1.0]).tolist() == [0.5]


def test_exact_top_decile_is_invariant_to_tied_row_order() -> None:
    frame = _economic_frame(20)
    scores = np.array([0.0] * 16 + [1.0] * 4)
    first = economic_metrics(frame, scores, exact_top_fraction=True)
    order = np.array(list(range(16)) + [19, 17, 16, 18])
    second = economic_metrics(
        frame.iloc[order].reset_index(drop=True), scores[order], exact_top_fraction=True
    )
    assert first["mean_pressure_net"] == pytest.approx(second["mean_pressure_net"])


def test_pressure_cost_subtracts_only_incremental_five_bp() -> None:
    frame = _economic_frame(10)
    scores = np.arange(10, dtype=float)
    metrics = economic_metrics(frame, scores)
    assert metrics["mean_net_taker"] == pytest.approx(frame.net_ret_swap_taker.mean())
    assert metrics["mean_pressure_net"] == pytest.approx(
        frame.net_ret_swap_taker.mean() - 0.0005
    )
    assert metrics["approved_total_cost"] == pytest.approx(0.0015)
    assert metrics["additional_slippage_deducted"] == pytest.approx(0.0005)


def test_matched_control_is_exact_stratum_no_reuse_and_excludes_selected_groups() -> None:
    frame = _economic_frame(12)
    frame.loc[:, "signal_time"] = pd.date_range(
        "2026-02-02", periods=12, freq="12h", tz="UTC"
    )
    # Row 1 shares row 0's selected event group and must never be a control.
    frame.loc[1, "event_group_id"] = frame.loc[0, "event_group_id"]
    selected = np.zeros(len(frame), dtype=bool)
    selected[[0, 6]] = True
    pairs = matched_candidate_pairs(frame, selected)
    assert pairs.control_candidate_id.is_unique
    assert not set(pairs.control_event_group_id) & set(pairs.selected_event_group_id)
    assert (pairs.symbol == "BTC_USDT_SWAP").all()
    assert pairs.utc_week.str.startswith("2026-W").all()


def test_exact_week_block_signflip_uses_economic_lift() -> None:
    pairs = pd.DataFrame(
        {
            "utc_week": [f"2026-W{i:02d}" for i in range(1, 9)],
            "selected_pressure_net": [0.01] * 8,
            "control_pressure_net": [0.0] * 8,
        }
    )
    result = exact_block_signflip_pvalue(pairs)
    assert result["observed_lift"] == pytest.approx(0.01)
    assert result["permutations"] == 256
    assert result["hits_ge_observed"] == 1
    assert result["p_value"] == pytest.approx(1 / 256)


def _walkforward_fixture() -> pd.DataFrame:
    rows = []
    times = pd.date_range("2026-02-01", "2026-03-05", freq="6h", tz="UTC")
    for index, signal in enumerate(times):
        rows.append(
            {
                "candidate_id": f"c{index}",
                "event_group_id": f"g{index}",
                "signal_time": signal,
                "interval_start": signal + pd.Timedelta(minutes=15),
                "interval_end": signal + pd.Timedelta(hours=2),
            }
        )
    # This interval crosses test_start; its entire shared group must be purged.
    rows.append(
        {
            "candidate_id": "bridge",
            "event_group_id": "shared",
            "signal_time": pd.Timestamp("2026-02-15T12:00:00Z"),
            "interval_start": pd.Timestamp("2026-02-15T12:15:00Z"),
            "interval_end": pd.Timestamp("2026-02-15T14:00:00Z"),
        }
    )
    rows.append(
        {
            "candidate_id": "shared-test",
            "event_group_id": "shared",
            "signal_time": pd.Timestamp("2026-02-15T13:15:00Z"),
            "interval_start": pd.Timestamp("2026-02-15T13:30:00Z"),
            "interval_end": pd.Timestamp("2026-02-15T15:00:00Z"),
        }
    )
    return pd.DataFrame(rows)


def test_walkforward_fold_has_no_event_or_interval_leak() -> None:
    fold = prepare_walkforward_fold(
        _walkforward_fixture(),
        fold=1,
        test_start=pd.Timestamp("2026-02-15T13:00:00Z"),
        test_end=pd.Timestamp("2026-03-01T17:15:00Z"),
    )
    parts = [fold.train, fold.early_stop, fold.calibration, fold.test]
    groups = [set(part.event_group_id) for part in parts]
    for left in range(len(groups)):
        for right in range(left + 1, len(groups)):
            assert not groups[left] & groups[right]
    assert "shared" not in set().union(*groups)
    assert pd.to_datetime(fold.calibration.interval_end, utc=True).max() < fold.test_start
    assert pd.to_datetime(fold.test.interval_end, utc=True).max() < fold.test_end
