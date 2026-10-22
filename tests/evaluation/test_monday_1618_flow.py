"""Flow features read only metrics/funding/taker data available before the touch."""
import unittest
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_1618_validity import touches
from yoyo.evaluation.monday_1618_flow import flow_features, FLOW
from test_monday_1618_validity import walk


class FlowTests(unittest.TestCase):
    def test_flow_features_ignore_data_from_touch_onward(self):
        f = walk();f['taker_buy'] = f.volume*.5
        rows = pd.DataFrame(touches(f, 'T', dict(horizon_days=14)))
        idx5 = pd.date_range(f.index[0], f.index[-1], freq='5min')
        rng = np.random.default_rng(2)
        m = pd.DataFrame(dict(sum_open_interest=100+np.cumsum(rng.normal(0, 1, len(idx5))).clip(1),
            sum_toptrader_long_short_ratio=rng.uniform(.5, 2, len(idx5)),
            count_long_short_ratio=rng.uniform(.5, 2, len(idx5))), index=idx5)
        fr = pd.Series(rng.normal(0, 1e-4, len(f.index[::32])), index=f.index[::32])
        base = flow_features(rows, f, m, fr)
        self.assertTrue(base[FLOW].notna().all(axis=None) or base[FLOW].notna().sum().sum() > 0)
        for i, r in rows.iterrows():
            t = pd.Timestamp(r.touch_time)
            g = f.copy();g.loc[g.index >= t, 'taker_buy'] = 0.;g.loc[g.index >= t, 'volume'] *= 9
            mm = m.copy();mm.loc[mm.index > t-pd.Timedelta(minutes=5)] *= 3
            ff = fr.copy();ff.loc[ff.index > t] = 1.
            again = flow_features(rows.loc[[i]], g, mm, ff)
            pd.testing.assert_series_equal(again.loc[i], base.loc[i])


if __name__ == '__main__':unittest.main()
