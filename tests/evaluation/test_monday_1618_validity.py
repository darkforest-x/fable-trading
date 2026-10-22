"""Monday1618 validity: causal features, touch-bar ordering and label rules."""
import unittest
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_1618_validity import touches, FEATURES

CFG = dict(horizon_days=14)


def walk(n=96*7*12, seed=3):
    rng = np.random.default_rng(seed)
    c = 100*np.exp(np.cumsum(rng.normal(0, .002, n)))
    o = np.r_[100, c[:-1]]
    hi = np.maximum(o, c)*(1+np.abs(rng.normal(0, .001, n)))
    lo = np.minimum(o, c)*(1-np.abs(rng.normal(0, .001, n)))
    idx = pd.date_range('2026-01-05T05:00Z', periods=n, freq='15min')
    return pd.DataFrame(dict(open=o, high=hi, low=lo, close=c, volume=rng.uniform(1, 2, n)), index=idx)


class ValidityTests(unittest.TestCase):
    def test_features_ignore_touch_bar_and_future(self):
        f = walk();rows = pd.DataFrame(touches(f, 'T', CFG))
        self.assertGreater(len(rows), 5)
        checked = 0
        for r in rows.itertuples():
            g = f.copy();t = r.touch_i
            # Touch-bar high/low define the touch; its close/volume and all
            # later bars must not move any feature.
            g.iloc[t+1:, :] = g.iloc[t+1:, :].to_numpy()*1.5
            g.iloc[t, g.columns.get_loc('close')] = g.low.iloc[t]
            g.iloc[t, g.columns.get_loc('volume')] = 1e9
            again = pd.DataFrame(touches(g.iloc[:t+1], 'T', CFG))
            same = again[(again.week == r.week) & (again.side == r.side)]
            self.assertEqual(same.touch_i.iloc[0], t)
            for name in FEATURES:
                a, b = getattr(r, name), same[name].iloc[0]
                self.assertTrue((np.isnan(a) and np.isnan(b)) or abs(a-b) < 1e-9, name)
            checked += 1
        self.assertEqual(checked, len(rows))

    def test_touch_bar_cannot_validate_but_can_fail(self):
        f = walk();rows = pd.DataFrame(touches(f, 'T', CFG))
        r = rows.iloc[0];t = int(r.touch_i)
        g = f.copy()
        # Touch bar spans level and edge: ordering unknown, so not a win by itself.
        if r.side == -1:
            g.iloc[t, g.columns.get_loc('low')] = r.edge-1
            g.iloc[t+1:, g.columns.get_loc('high')] = np.minimum(g.high.iloc[t+1:], r.level)
            g.iloc[t+1:, g.columns.get_loc('low')] = np.maximum(g.low.iloc[t+1:], r.edge+.01)
        else:
            g.iloc[t, g.columns.get_loc('high')] = r.edge+1
            g.iloc[t+1:, g.columns.get_loc('low')] = np.maximum(g.low.iloc[t+1:], r.level)
            g.iloc[t+1:, g.columns.get_loc('high')] = np.minimum(g.high.iloc[t+1:], r.edge-.01)
        again = pd.DataFrame(touches(g, 'T', CFG))
        same = again[(again.week == r.week) & (again.side == r.side)].iloc[0]
        self.assertFalse(same.valid)
        # Stop inside the touch bar fails immediately.
        g = f.copy()
        g.iloc[t, g.columns.get_loc('high' if r.side == -1 else 'low')] = r.stop-r.side*1
        again = pd.DataFrame(touches(g, 'T', CFG))
        same = again[(again.week == r.week) & (again.side == r.side)].iloc[0]
        self.assertFalse(same.valid)
        self.assertEqual(same.r, -1.0)


if __name__ == '__main__':unittest.main()
