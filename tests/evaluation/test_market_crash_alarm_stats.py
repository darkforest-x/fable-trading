"""Synthetic timing and uncertainty contracts for market crash alarm statistics."""
from __future__ import annotations

import math

import numpy as np
import pytest

from yoyo.evaluation.market_crash_alarm_stats import (
    _draw_blocked_null_positions,
    _draw_null_alarm_positions,
    _valid_blocked_null_sample,
    _valid_null_sample,
    matched_time_null,
    match_one_to_one,
    paired_week_bootstrap,
)


def test_minute_close_availability_and_one_second_latency_change_the_60_second_boundary():
    # Event minute 10 opens at 600s. The preceding bar (row 9) closes at 600s,
    # so its alert arrives at 601s and is late. Row 8 arrives at 541s: only 59s
    # lead. Row 7 arrives at 481s and is the earliest bar meeting >=60s lead.
    result = match_one_to_one([8, 7, 9, 10], [10])

    assert result["alarm_for_event"].tolist() == [1]
    assert result["event_for_alarm"].tolist() == [-1, 0, -1, -1]
    assert result["lead_for_event"].tolist() == [pytest.approx(119.0)]


def test_match_is_one_to_one_and_uses_the_earliest_eligible_alarm():
    shared = match_one_to_one([6], [10, 11])
    assert shared["event_for_alarm"].tolist() == [0]
    assert shared["alarm_for_event"].tolist() == [0, -1]
    assert shared["lead_for_event"][0] == pytest.approx(179.0)
    assert math.isnan(shared["lead_for_event"][1])

    earliest = match_one_to_one([15, 14], [20])
    assert earliest["alarm_for_event"].tolist() == [1]
    assert earliest["event_for_alarm"].tolist() == [-1, 0]
    assert earliest["lead_for_event"][0] == pytest.approx(299.0)


def test_future_and_event_internal_alerts_are_not_timely_hits():
    # Row 9 closes at event open + 1s; rows 10 and 11 close later still.
    result = match_one_to_one([9, 10, 11], [10])

    assert result["alarm_for_event"].tolist() == [-1]
    assert result["event_for_alarm"].tolist() == [-1, -1, -1]
    assert math.isnan(result["lead_for_event"][0])


def test_null_sampler_preserves_each_cell_count_and_global_cooldown():
    eligible = np.arange(0, 800, dtype=np.int64)
    cell_by_position = {int(pos): int(pos // 100) for pos in eligible}
    groups = {
        cell: eligible[(eligible // 100) == cell]
        for cell in range(8)
    }
    counts = {0: 1, 2: 1, 4: 1, 6: 1}
    sample = _draw_null_alarm_positions(np.random.default_rng(7), groups, counts, 15, 10)

    assert sample is not None
    assert len(sample) == len(counts)
    assert _valid_null_sample(sample, cell_by_position, counts, 15)
    assert np.all(np.diff(sample) >= 15)
    assert {cell: sum(cell_by_position[int(p)] == cell for p in sample) for cell in counts} == counts


def test_null_keeps_exact_constraints_and_reports_unfillable_cooldown_without_relaxing():
    eligible = np.arange(12, dtype=np.int64)
    result = matched_time_null(
        eligible_positions=eligible,
        cells=["same"] * len(eligible),
        real_alarm_positions=[0, 1],
        event_positions=[100],
        repeats=3,
        max_restarts=2,
        seed=5,
    )

    assert result["observed_hits"] == 0
    assert result["success_replicates"] == 0
    assert result["failed_replicates"] == 3
    assert result["null_hits"] == []
    assert math.isnan(result["null_recall_mean"])
    assert math.isnan(result["p_ge_observed"])
    assert result["constraints_verified"] is False


def test_public_null_draws_from_event_period_candidates_and_verifies_exact_replicates():
    # The event minute is the only candidate in its cell. Matching the observed cell
    # counts therefore forces every null sample to retain that event-period candidate.
    eligible = np.asarray([0, 500, 1000, 2000, 3000], dtype=np.int64)
    cells = ["far-a", "event-minute", "far-b", "far-c", "far-d"]
    result = matched_time_null(
        eligible_positions=eligible,
        cells=cells,
        real_alarm_positions=eligible,
        event_positions=[500],
        repeats=20,
        seed=19,
    )

    assert result["success_replicates"] == 20
    assert result["failed_replicates"] == 0
    assert len(result["null_hits"]) == 20
    assert result["null_hits"] == [0] * 20
    assert result["constraints_verified"] is True


def test_blocked_null_retries_cross_week_conflict_with_exact_counts_deterministically():
    eligible = np.asarray([10065, 10070, 10080, 10090], dtype=np.int64)
    cells = ["week1-cell", "week1-cell", "week2-cell", "week2-cell"]
    blocks = ["week1", "week1", "week2", "week2"]
    cell_by_position = dict(zip(eligible.tolist(), cells))
    block_by_position = dict(zip(eligible.tolist(), blocks))
    candidates = {
        "week1": {"week1-cell": np.asarray([10065, 10070])},
        "week2": {"week2-cell": np.asarray([10080, 10090])},
    }
    target_counts = {"week1": {"week1-cell": 1}, "week2": {"week2-cell": 1}}

    first, first_meta = _draw_blocked_null_positions(
        np.random.default_rng(2), candidates, target_counts, cooldown_minutes=15, max_restarts=10
    )
    second, second_meta = _draw_blocked_null_positions(
        np.random.default_rng(2), candidates, target_counts, cooldown_minutes=15, max_restarts=10
    )

    assert first is not None and second is not None
    assert first.tolist() == second.tolist() == [10065, 10080]
    assert first_meta == second_meta
    assert first_meta["outer_attempts"] == 2  # first draw conflicts across week boundary
    assert first_meta["block_draw_attempts"] == 4
    assert np.all(np.diff(first) >= 15)
    assert _valid_blocked_null_sample(first, cell_by_position, block_by_position, target_counts, 15)
    assert {cell: sum(cell_by_position[int(p)] == cell for p in first) for cell in set(cells)} == {
        "week1-cell": 1,
        "week2-cell": 1,
    }

    public = matched_time_null(
        eligible_positions=eligible,
        cells=cells,
        real_alarm_positions=[10065, 10080],
        event_positions=[10085],
        block_ids=blocks,
        repeats=1,
        seed=2,
    )
    assert public["success_replicates"] == 1
    assert public["failed_replicates"] == 0
    assert public["null_hits"] == [1]
    assert public["attempts"][0]["outer_attempts"] == 2
    assert public["failure_reasons"] == {
        "within_block_cooldown": 0,
        "cross_week_cooldown": 0,
        "verification_failed": 0,
    }
    assert public["constraints_verified"] is True


def test_paired_week_bootstrap_resamples_week_blocks_and_uses_ratio_of_sums():
    records = [
        {"week": "2025-W01", "event_n": 2, "btc_hits": 1, "six_hits": 2,
         "btc_false": 4, "six_false": 2, "exposure_days": 2},
        # Same week split across two rows; it must remain one resampling block.
        {"week": "2025-W02", "event_n": 1, "btc_hits": 0, "six_hits": 1,
         "btc_false": 1, "six_false": 1, "exposure_days": 0.5},
        {"week": "2025-W02", "event_n": 1, "btc_hits": 0, "six_hits": 0,
         "btc_false": 0, "six_false": 1, "exposure_days": 0.5},
    ]
    first = paired_week_bootstrap(records, repeats=250, seed=23)
    second = paired_week_bootstrap(records, repeats=250, seed=23)

    assert first["weeks"] == 2
    assert first["recall_diff"] == pytest.approx(0.5)
    assert first["recall_diff_ci95"] == pytest.approx(second["recall_diff_ci95"])
    assert first["false_rate_diff"] == pytest.approx(-1.0 / 3.0)
    assert first["false_rate_diff_ci95"] == pytest.approx(second["false_rate_diff_ci95"])
    assert first["valid_recall_replicates"] == 250
    assert first["valid_false_rate_replicates"] == 250


def test_bootstrap_reports_nan_recall_when_no_sampled_week_has_events():
    result = paired_week_bootstrap(
        [{"week": "2026-W01", "event_n": 0, "btc_hits": 0, "six_hits": 0,
          "btc_false": 2, "six_false": 1, "exposure_days": 1}],
        repeats=20,
    )

    assert math.isnan(result["recall_diff"])
    assert all(math.isnan(value) for value in result["recall_diff_ci95"])
    assert result["valid_recall_replicates"] == 0
    assert result["false_rate_diff"] == pytest.approx(-1.0)


def test_bootstrap_rejects_hits_above_the_shared_event_count():
    with pytest.raises(ValueError, match="hits cannot exceed"):
        paired_week_bootstrap(
            [{"week": "2025-W01", "event_n": 1, "btc_hits": 0, "six_hits": 2,
              "btc_false": 0, "six_false": 0, "exposure_days": 1}],
            repeats=2,
        )
