"""No-stop limit entry, next-candle reclaim, and native streaming regressions."""
import unittest
import numpy as np
import pandas as pd

from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native


def replay(bars, *, minutes=1, side=1, target=150., batch_bars=None, start='2026-01-06', index=None):
    frame = pd.DataFrame(bars, columns=['open','high','low','close'],
        index=pd.date_range(start, periods=len(bars), freq='min', tz='UTC') if index is None else index)
    entries = pd.DataFrame([dict(trade_id='reclaim', entry_i=0, expire_i=1,
        end_i=len(frame)-1, entry=100., side=side, stop=np.nan, risk=100.,
        target_price=target, reclaim_minutes=minutes)])
    policy = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False,
        round_trip_cost=0., target_from_entry=True, intrabar_path='OHLC',
        fill_maker_fee=.0002, fill_taker_fee=.0005,
        reclaim_next_bar=True, breakeven_includes_cost=False)
    return run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)),
        entries, policy, tick=.1, symbol='TEST', execution_minutes=1,
        entry_order_mode='limit', batch_bars=batch_bars)


class LimitReclaimTests(unittest.TestCase):
    def test_no_initial_stop_and_no_retroactive_stop_on_confirmation_wick(self):
        bars=[[102]*4,[102,103,30,101],[101,103,20,102],[102,103,99,101],[101]*4]
        rows,fills,_=replay(bars)
        r=rows.iloc[0]
        self.assertEqual(r.reclaim_i,2)
        self.assertEqual(r.reason,5)
        self.assertEqual(r.exit_i,3)
        self.assertEqual(r.exit_price,100.)
        self.assertEqual(r.reclaim_status,'reclaimed')
        self.assertAlmostEqual(r.net_notional_bp,-7.)
        self.assertEqual(list(fills.phase),['entry','stop'])

    def test_failed_reclaim_exits_exact_following_close(self):
        rows,_,_=replay([[102]*4,[102,103,30,101],[101,102,20,98],[98]*4])
        self.assertEqual(rows.iloc[0].reason,11)
        self.assertEqual(rows.iloc[0].exit_i,2)
        self.assertEqual(rows.iloc[0].exit_price,98.)
        self.assertEqual(rows.iloc[0].reclaim_status,'failed')

    def test_close_equal_entry_is_not_reclaim(self):
        rows,_,_=replay([[102]*4,[102,103,99,101],[101,102,99,100],[101]*4])
        self.assertEqual(rows.iloc[0].reason,11)

    def test_short_mirror(self):
        rows,_,_=replay([[98]*4,[98,180,97,99],[99,190,97,98],[98,101,97,99],[99]*4],
                        side=-1,target=50.)
        self.assertEqual(rows.iloc[0].reason,5)
        self.assertEqual(rows.iloc[0].exit_i,3)
        self.assertEqual(rows.iloc[0].exit_price,100.)

    def test_target_can_fill_before_confirmation(self):
        rows,_,_=replay([[102]*4,[102,103,99,101],[101,111,95,98],[98]*4],target=110.)
        self.assertEqual(rows.iloc[0].reason,4)
        self.assertEqual(rows.iloc[0].reclaim_status,'pending')
        self.assertAlmostEqual(rows.iloc[0].net_notional_bp,995.8)

    def test_five_minute_clock_is_candle_boundary_not_elapsed_five_minutes(self):
        # Fill in minute[00:00,00:01), so 5m touch bar closes00:05,
        # and its successor closes00:10. No protection through00:09.
        bars=[[102]*4,[102,103,99,101]]+[[101,103,20,101]]*8+[[101,103,20,102],[102,103,99,101],[101]*4]
        rows,_,_=replay(bars,minutes=5)
        self.assertEqual(rows.iloc[0].reclaim_i,10)
        self.assertEqual(rows.iloc[0].exit_i,11)

    def test_streaming_preserves_pending_fill_and_active_stop_across_batches(self):
        bars=[[102]*4,[102,103,30,101],[101,103,20,102],[102,103,99,101],[101]*4]
        a,fa,_=replay(bars)
        b,fb,_=replay(bars,batch_bars=2)
        for c in ['entry_i','exit_i','exit_price','reason','reclaim_i','net_notional_bp']:
            self.assertEqual(a.iloc[0][c],b.iloc[0][c])
        pd.testing.assert_frame_equal(fa.drop(columns=['client_order_id']),fb.drop(columns=['client_order_id']))

    def test_missing_preconfirmation_minute_censors_before_gap(self):
        index=pd.to_datetime(['2026-01-06T00:00Z','2026-01-06T00:01Z',
                              '2026-01-06T00:03Z','2026-01-06T00:04Z'])
        rows,_,_=replay([[102]*4,[102,103,20,98],[98,105,97,104],[104]*4],
                        minutes=5,index=index)
        self.assertEqual(rows.iloc[0].reason,0)
        self.assertEqual(rows.iloc[0].exit_i,1)
        self.assertEqual(rows.iloc[0].censor_reason,'missing_preconfirmation_interval')
        self.assertEqual(rows.iloc[0].reclaim_status,'pending')

    def test_data_end_before_confirmation_is_censored(self):
        rows,_,_=replay([[102]*4,[102,103,20,98]],minutes=5)
        self.assertEqual(rows.iloc[0].reason,0)
        self.assertEqual(rows.iloc[0].reclaim_status,'pending')


if __name__=='__main__':unittest.main()
