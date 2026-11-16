"""Focused synthetic contracts for the Monday weekly-boundary trail."""
import unittest

import numpy as np

from yoyo.evaluation.monday_weekly_boundary_trailing import trail_fill


def bars(*rows):
    """Build [ts, open, high, low, close] hourly test bars."""
    return np.asarray([[i, *row] for i, row in enumerate(rows)], dtype=float)


class MondayWeeklyBoundaryTrailingTests(unittest.TestCase):
    def test_long_same_hour_stop_and_target_is_hard_stop(self):
        path = bars((100, 110, 90, 100))
        result = trail_fill(path, 100, 1, 105, 95, 0.01)
        self.assertEqual(result[0], 0)
        self.assertAlmostEqual(result[1], 95)
        self.assertEqual((result[2], result[3]), ("initial_stop", -1))

    def test_short_same_hour_stop_and_target_is_hard_stop(self):
        path = bars((100, 110, 90, 100))
        result = trail_fill(path, 100, -1, 95, 105, 0.01)
        self.assertEqual(result[0], 0)
        self.assertAlmostEqual(result[1], 105)
        self.assertEqual((result[2], result[3]), ("initial_stop", -1))

    def test_open_beyond_target_does_not_mask_later_stop_same_hour(self):
        # The first hour gaps through the long target at its open, then trades
        # through the still-active hard stop. The stop must win that hour.
        path = bars((106, 108, 94, 100))
        result = trail_fill(path, 100, 1, 105, 95, 0.01)
        self.assertEqual(result[0], 0)
        self.assertAlmostEqual(result[1], 95)
        self.assertEqual((result[2], result[3]), ("initial_stop", -1))

    def test_long_trail_uses_touch_close_and_only_applies_next_hour(self):
        # The touch hour's low is below its prospective 1% trail, but above
        # the hard stop. The trail becomes active only on the following bar.
        path = bars(
            (100, 103, 99, 101),
            (105.5, 106, 104, 105.5),
            (106, 108, 105, 107),
        )
        result = trail_fill(path, 100, 1, 105, 97, 0.01)
        self.assertEqual(result[0], -1)
        self.assertTrue(np.isnan(result[1]))
        self.assertEqual((result[2], result[3]), ("censored", 1))

    def test_long_trailing_stop_and_gap_fill(self):
        path = bars(
            (100, 103, 99, 101),
            (101, 111, 100, 110),
            (107, 112, 106, 111),
        )
        result = trail_fill(path, 100, 1, 110, 97, 0.01)
        self.assertEqual(result[0], 2)
        self.assertAlmostEqual(result[1], 107)
        self.assertEqual((result[2], result[3]), ("trailing_stop", 1))

    def test_short_trailing_stop_and_gap_fill(self):
        path = bars(
            (100, 101, 97, 99),
            (99, 100, 89, 90),
            (93, 94, 88, 89),
        )
        result = trail_fill(path, 100, -1, 90, 103, 0.01)
        self.assertEqual(result[0], 2)
        self.assertAlmostEqual(result[1], 93)
        self.assertEqual((result[2], result[3]), ("trailing_stop", 1))

    def test_initial_stop_is_active_on_first_hour_and_gaps_fill_at_open(self):
        path = bars((94, 98, 92, 96), (96, 99, 95, 98))
        result = trail_fill(path, 100, 1, 110, 95, 0.01)
        self.assertEqual(result[0], 0)
        self.assertAlmostEqual(result[1], 94)
        self.assertEqual((result[2], result[3]), ("initial_stop", -1))

    def test_inclusive_final_bar_can_exit(self):
        path = bars(
            (100, 103, 99, 101),
            (101, 111, 100, 110),
            (110, 112, 109, 111),
            (111, 112, 108, 109),
        )
        result = trail_fill(path, 100, 1, 110, 97, 0.01)
        self.assertEqual(result[0], 3)
        self.assertAlmostEqual(result[1], 109.89)
        self.assertEqual((result[2], result[3]), ("trailing_stop", 1))

    def test_no_touch_no_exit_is_censored(self):
        result = trail_fill(bars((100, 103, 99, 101)), 100, 1, 110, 97, 0.01)
        self.assertEqual(result[0], -1)
        self.assertTrue(np.isnan(result[1]))
        self.assertEqual((result[2], result[3]), ("censored", -1))


if __name__ == "__main__":
    unittest.main()
