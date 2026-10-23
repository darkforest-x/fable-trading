"""Level comparison reuses the1618 ledger semantics for monday_ny x=0.618."""
import unittest
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_1618_validity import touches
from yoyo.evaluation.monday_1618_levels import events
from test_monday_1618_validity import walk


class LevelTests(unittest.TestCase):
    def test_monday_1618_matches_touch_ledger(self):
        f = walk(96*7*14)
        base = pd.DataFrame(touches(f, 'T', dict(horizon_days=14)))
        ev = pd.DataFrame(events(f, 'T', np.random.default_rng(1)))
        mine = ev[(ev.anchor == 'monday_ny') & (ev.level == '0.618')].set_index(['week', 'side'])
        ref = base.set_index(['week', 'side'])
        common = mine.index.intersection(ref.index)
        self.assertGreater(len(common), 3)
        self.assertEqual(list(mine.loc[common, 'touch_time']), list(ref.loc[common, 'touch_time']))
        self.assertEqual(list(mine.loc[common, 'valid']), list(ref.loc[common, 'valid']))
        self.assertEqual(set(ev.level), {'0.272', '0.414', '0.618', '1.0', 'random'})
        self.assertEqual(set(ev.anchor), {'monday_ny', 'monday_utc', 'prev_week'})


if __name__ == '__main__':unittest.main()
