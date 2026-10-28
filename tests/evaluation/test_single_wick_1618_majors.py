"""Causal wick admission, mirrored levels, and native close-entry contracts."""
import unittest
import numpy as np
import pandas as pd

from yoyo.evaluation.single_wick_1618_majors import prepare, five_minute, execute


class SingleWickTests(unittest.TestCase):
    def fixture(self, side=-1):
        frame = pd.DataFrame(dict(open=100., high=105., low=95., close=100., volume=100.),
            index=pd.date_range('2026-01-05 05:00', periods=400, freq='5min', tz='UTC'))
        frame.iloc[309] = [110., 111., 109., 110., 100.]
        frame.iloc[310] = [110.5, 112., 109., 110., 1000.]
        if side == 1:
            old = frame.copy()
            for dest, source in [('open', 'open'), ('high', 'low'), ('low', 'high'), ('close', 'close')]:
                frame[dest] = 200.-old[source]
        return frame

    def test_mirror_and_future_mutation(self):
        for side in (-1, 1):
            frame = self.fixture(side)
            job = dict(symbol='TSTUSDT', execution_minutes=5)
            cfg = dict(seed=1, controls_per_signal=3)
            _, _, entries, _, _ = prepare(frame, job, cfg)
            sig = entries[entries.population.eq('signal')]
            self.assertEqual(len(sig), 1)
            self.assertEqual(sig.side.iloc[0], side)
            self.assertEqual(sig.entry_i.iloc[0], 310)
            self.assertAlmostEqual(sig.stop.iloc[0], 112 if side == -1 else 88)
            self.assertAlmostEqual(sig.target_price.iloc[0], 98.82 if side == -1 else 101.18)
            frame.iloc[311:, frame.columns.get_indexer(['open','high','low','close'])] *= 1.5
            _, _, changed, _, _ = prepare(frame, job, cfg)
            first = changed[changed.trade_id.isin(entries.trade_id)]
            pd.testing.assert_frame_equal(entries.reset_index(drop=True), first.reset_index(drop=True))

    def test_second_touch_not_filtered_using_future(self):
        f = self.fixture()
        f.iloc[311] = [110., 113., 109., 112., 100.]
        _, _, entries, _, _ = prepare(f, dict(symbol='TSTUSDT', execution_minutes=5), dict(seed=1, controls_per_signal=3))
        self.assertEqual(len(entries[entries.population.eq('signal')]), 1)

    def test_incomplete_minute_candle_is_absent(self):
        f = pd.DataFrame(dict(open=1., high=2., low=.5, close=1., volume=1.),
            index=pd.date_range('2026-01-01', periods=10, freq='min', tz='UTC'))
        self.assertEqual(len(five_minute(f.drop(f.index[3]), 1)), 1)

    def test_native_target_after_close_entry(self):
        frame = pd.DataFrame([[110.5,112,97,110],[110,111,109,110],[110,111,98,99]],
            columns=['open','high','low','close'], index=pd.date_range('2026-01-06', periods=3, freq='5min', tz='UTC'))
        entries = pd.DataFrame([dict(trade_id='one', entry_i=0, end_i=2, side=-1,
            entry=110., stop=112., risk=2., target_price=98.82)])
        rows, fills, audit = execute(frame, entries, dict(symbol='TSTUSDT', execution_minutes=5),
            dict(maker_fee=.0002, taker_fee=.0005), .01)
        self.assertEqual(rows.exit_i.iloc[0], 2)
        self.assertEqual(rows.reason.iloc[0], 4)
        self.assertAlmostEqual(rows.exit_price.iloc[0], 98.82)
        self.assertAlmostEqual(rows.fee_r.iloc[0], (110*.0005+98.82*.0002)/2)
        self.assertEqual(audit['positions'], 1)
        self.assertEqual(len(fills), 2)


if __name__ == '__main__':
    unittest.main()
