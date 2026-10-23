"""Official event parsing and release-window flags for the macro study."""
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_1618_macro import decision_day, fomc, events, flag

INPUTS = Path(__file__).resolve().parents[2]/'experiments/active/exp-monday-1618-macro-20261023-v1/inputs'


class MacroTests(unittest.TestCase):
    def test_decision_day_handles_cross_month_and_single_day(self):
        cases = {(2024, 'Apr/May', '30-1'): '2024-05-01', (2012, 'July', '31-August 1'): '2012-08-01',
                 (2011, 'March', '15'): '2011-03-15', (2025, 'January', '28-29'): '2025-01-29',
                 (2023, 'Jan/Feb', '31-1'): '2023-02-01', (2025, 'March', '18-19*'): '2025-03-19'}
        for args, want in cases.items():
            self.assertEqual(str(decision_day(*args).date()), want)

    @unittest.skipUnless((INPUTS/'fomccalendars.htm').exists(), 'official pages not present')
    def test_fomc_matches_statement_dates_and_excludes_specials(self):
        days = {str(d.date()) for d in fomc(INPUTS)}
        for d in ('2020-01-29', '2022-03-16', '2023-02-01', '2024-05-01', '2025-12-10', '2012-08-01'):
            self.assertIn(d, days)
        for d in ('2020-03-03', '2020-03-15', '2020-03-18', '2025-08-22', '2011-08-01'):
            self.assertNotIn(d, days)
        self.assertEqual(sum(d.startswith('2022') for d in days), 8)

    @unittest.skipUnless((INPUTS/'bls_release_dates.json').exists(), 'official dates not present')
    def test_release_times_follow_new_york_dst(self):
        ev = events(INPUTS)
        cpi = ev[(ev.kind == 'cpi') & (ev.local.dt.date.astype(str) == '2024-07-11')].utc.iloc[0]
        self.assertEqual(str(cpi), '2024-07-11 12:30:00+00:00')
        nfp = ev[(ev.kind == 'empsit') & (ev.local.dt.date.astype(str) == '2024-01-05')].utc.iloc[0]
        self.assertEqual(str(nfp), '2024-01-05 13:30:00+00:00')

    def test_flag_window_and_side(self):
        ev = pd.DataFrame(dict(kind=['cpi'], utc=[pd.Timestamp('2024-07-11 12:30', tz='UTC')]))
        t = ['2024-07-11 10:30:00+00:00', '2024-07-11 14:30:00+00:00', '2024-07-11 14:45:00+00:00', '2024-07-11 12:15:00+00:00']
        near, side, kind = flag(t, ev, 2)
        self.assertEqual(near.tolist(), [True, True, False, True])
        self.assertEqual(side.tolist()[:2], [-1, 1])


if __name__ == '__main__':unittest.main()
