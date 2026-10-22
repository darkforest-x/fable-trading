"""Confluence features read only bars that closed before the touch bar."""
import unittest
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_1618_validity import touches
from yoyo.evaluation.monday_1618_confluence import features, profile, value_area, FEATS
from test_monday_1618_validity import walk


class ConfluenceTests(unittest.TestCase):
    def test_features_ignore_touch_bar_and_future(self):
        f = walk(96*7*16)
        rows = pd.DataFrame(touches(f, 'T', dict(horizon_days=14)))
        base = features(f, rows)
        self.assertGreater(base[FEATS].notna().sum().sum(), 0)
        for i, r in rows.iterrows():
            g = f.copy();t = int(r.touch_i)
            g.iloc[t+1:, :4] = g.iloc[t+1:, :4].to_numpy()*1.2
            g.iloc[t:, 4] = 1e6
            again = features(g, rows.loc[[i]])
            pd.testing.assert_series_equal(again.loc[i], base.loc[i])

    def test_value_area_contains_poc(self):
        edges, vol = profile(np.array([2., 3., 3.]), np.array([1., 2., 2.5]), np.array([1., 5., 5.]), 1., 3., 4)
        self.assertAlmostEqual(vol.sum(), 11.)
        poc, vah, val = value_area(edges, vol)
        self.assertTrue(val <= poc <= vah)


if __name__ == '__main__':unittest.main()
