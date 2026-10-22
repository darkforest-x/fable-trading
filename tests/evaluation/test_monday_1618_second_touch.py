"""Second-touch detection keeps failed retests and needs a real retrace."""
import unittest
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_1618_second_touch import second_touches


def frame(rows):
    idx = pd.date_range('2026-01-06T05:00Z', periods=len(rows), freq='15min')
    h = np.array([r[0] for r in rows], float);l = np.array([r[1] for r in rows], float)
    return pd.DataFrame(dict(open=(h+l)/2, high=h, low=l, close=(h+l)/2, volume=1.), index=idx)


ROW = pd.DataFrame([dict(symbol='T', week='2026-01-05', side=-1, segment=0, touch_i=0, level=116.18,
                         stop=120., edge=110., R=10., valid=False)])


class SecondTouchTests(unittest.TestCase):
    def test_retest_that_runs_to_stop_is_kept_as_failure(self):
        f = frame([(116.2, 115), (115, 113.5), (120.5, 115)]+[(115, 114)]*5)
        got = second_touches(f, ROW, .236, 14)
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0]['second_i'], got[0]['valid'], got[0]['r']), (2, False, -1.0))

    def test_shallow_pullback_is_not_a_retest(self):
        f = frame([(116.2, 115), (116, 114.5), (116.5, 115)]+[(115, 114)]*5)
        self.assertEqual(second_touches(f, ROW, .236, 14), [])

    def test_valid_retest_reaches_edge_later(self):
        f = frame([(116.2, 115), (115, 113.5), (116.3, 115), (114, 112), (112, 109.5)]+[(110, 109)]*3)
        got = second_touches(f, ROW, .236, 14)
        self.assertTrue(got[0]['valid'])
        self.assertAlmostEqual(got[0]['r'], 6.18/3.82)


if __name__ == '__main__':unittest.main()
