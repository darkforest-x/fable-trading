"""Checks for within-minute uncertainty and closed higher-timeframe pivots."""
from itertools import product

import numpy as np
import pytest

from yoyo.evaluation import monday_flow_divergence as flow


def test_interval_labels_cover_all_point_assignments():
    tol = 1
    for price_lo, price_hi in [(-2, -1), (-1, 2), (0, 0), (0, 1), (1, 2), (2, 2)]:
        for flow_lo, flow_hi in [(-3, -1), (-3, 0), (-3, 3), (0, 0), (1, 3)]:
            actual = set()
            for p, d in product(range(price_lo, price_hi + 1), range(flow_lo, flow_hi + 1)):
                if p < 0 and d > 0:
                    actual.add("regular")
                elif 0 <= p <= tol and d < 0:
                    actual.add("absorption")
                else:
                    actual.add("none")
            assert actual == set(flow.categories(price_lo, price_hi, flow_lo, flow_hi, tol))


@pytest.mark.parametrize("width", [15, 60])
@pytest.mark.parametrize("side", [1, -1])
def test_first_pivot_extreme_and_touch_prefix_are_time_bounded(width, side):
    n = 24 * width
    rows = np.tile([0, 100, 101, 99, 100, 2, 2, 2], (n, 1)).astype(float)
    rows[:, flow.TS] = np.arange(n) * flow.MINUTE
    prior, hit = 17 * width + 3, 22 * width + 6
    col = flow.LOW if side == 1 else flow.HIGH
    pivot, level = 100 - side * 4, 100 - side * 5
    rows[prior:prior+2, col] = pivot
    rows[hit, col] = level
    if side == -1:
        rows[:, flow.BUY] = 0
    out = flow.timeline(rows, hit, width, side, level, (level, level), (0, 0))
    assert out["pivot_minute_ms"] == prior * flow.MINUTE
    assert out["label"] == "regular"
    assert out["label_pre"] == "regular"
    delta = 2 * (hit - prior - 1)
    assert sorted([side * out["cvd_diff_min"], side * out["cvd_diff_max"]]) == [delta, delta + 2]
    rows[hit+1:, :] = np.nan
    assert flow.timeline(rows, hit, width, side, level, (level, level), (0, 0)) == out


def test_gap_and_unconfirmed_pivot_do_not_enter_control():
    width = 15
    n = 23 * width
    rows = np.tile([0, 100, 101, 99, 100, 2, 2, 1], (n, 1)).astype(float)
    rows[:, flow.TS] = np.arange(n) * flow.MINUTE
    hit = 22 * width
    rows[21 * width, flow.LOW] = 90
    args = (width, 1, 89, (89, 89), (-1, 1))
    assert flow.timeline(rows, hit, *args) == {"label": "no_confirmed_pivot"}
    rows = np.delete(rows, 13 * width, axis=0)
    assert flow.timeline(rows, hit - 1, *args)["label"] == "history_gap"
