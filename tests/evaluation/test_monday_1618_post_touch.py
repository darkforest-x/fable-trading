"""Post-touch checkpoint states read only touch..checkpoint bars."""
import unittest
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_1618_validity import touches
from yoyo.evaluation.monday_1618_post_touch import states, resolve
from test_monday_1618_validity import walk

CFG = dict(horizon_days=14, checkpoints=[4, 8], shallow_R=.15, controls_per_entry=2)


class PostTouchTests(unittest.TestCase):
    def test_states_ignore_bars_after_checkpoint(self):
        f = walk();rows = pd.DataFrame(touches(f, 'T', CFG));rows['segment'] = 0
        base, _ = states(f, rows, CFG, np.random.default_rng(1))
        self.assertGreater(len(base), 3)
        for b in base:
            g = f.copy();g.iloc[b['entry_i']+1:, :] = g.iloc[b['entry_i']+1:, :].to_numpy()*1.3
            again, _ = states(g, rows, CFG, np.random.default_rng(1))
            same = [a for a in again if a['entry_i'] == b['entry_i'] and a['k'] == b['k']]
            self.assertEqual(len(same), 1)
            for key in ('exc', 'inside', 'clean', 'shallow', 'rr'):
                self.assertEqual(same[0][key], b[key])

    def test_resolve_ties_count_as_stop(self):
        h = np.array([10., 12.]);l = np.array([9., 7.]);c = np.array([9.5, 9.])
        self.assertEqual(resolve(h, l, c, 1, 2, -1, 11.5, 7.5), (False, True, 11.5))
        self.assertEqual(resolve(h, l, c, 1, 2, 1, 6.5, 11.5), (True, True, 11.5))


if __name__ == '__main__':unittest.main()
