"""Clock, boundary and future-mutation checks for the screenshot-derived census."""
import unittest
import numpy as np
import pandas as pd
from yoyo.evaluation.top20_12h_reclaims import aggregate12, census, classify, OHLC


class ReclaimTests(unittest.TestCase):
    def fixture(self, start='2026-09-20'):
        b=pd.DataFrame(dict(open=100.,high=105.,low=95.,close=100.,complete=True),
            index=pd.date_range(start,periods=60,freq='12h',tz='UTC'))
        # Tuesday12UTC is the first post-Monday candle, lower level=88.82.
        b.loc[b.index[5],OHLC]=[91.,96.,87.,92.]
        return b

    def test_complete_grid_and_gap(self):
        r=pd.DataFrame(dict(open=100.,high=101.,low=99.,close=100.),
            index=pd.date_range('2026-09-20',periods=144,freq='15min',tz='UTC'))
        b=aggregate12(r.drop(r.index[50]))
        self.assertEqual(b.complete.tolist(),[True,False,True])
        self.assertTrue(b.iloc[1][OHLC].isna().all())

    def test_boundary_and_mirror(self):
        for side in [1,-1]:
            def check(o,h,l,c):
                if side==-1:o,h,l,c=200-o,200-l,200-h,200-c
                return classify(o,h,l,c,100.,side)
            self.assertEqual(check(101,105,98,102),'pure_wick')
            self.assertEqual(check(99,105,98,102),'body_reclaim')
            self.assertEqual(check(100,105,98,102),'body_on_level')
            self.assertIsNone(check(101,105,100,102))
            self.assertIsNone(check(101,105,98,100))

    def test_clock_summer_and_winter(self):
        for start,week in [('2026-09-20','2026-09-21'),('2026-01-04','2026-01-05')]:
            b=self.fixture(start);o,w=census(b,'TST');row=w[w.week.eq(week)].iloc[0]
            self.assertEqual(pd.Timestamp(row.first_monday).hour,12)
            self.assertEqual(pd.Timestamp(row.ready).hour,12)
            self.assertEqual(pd.Timestamp(row.ready).dayofweek,1)
            self.assertAlmostEqual(row.lower,88.82)
            hit=o[o.is_event];self.assertEqual(len(hit),1)
            self.assertEqual(hit.kind.iloc[0],'pure_wick')

    def test_prefix_and_future_mutation(self):
        b=self.fixture();a,_=census(b,'TST');b.loc[b.index[6]:,OHLC]*=1.5
        z,_=census(b,'TST');cols=['event_id','kind','level','prior_touch_count','tr_prior14']
        pd.testing.assert_frame_equal(a.loc[a.bar_i.eq(5),cols],z.loc[z.bar_i.eq(5),cols])
        p,_=census(self.fixture().iloc[:6],'TST')
        pd.testing.assert_frame_equal(a.loc[a.bar_i.eq(5),cols].reset_index(drop=True),p.loc[p.bar_i.eq(5),cols].reset_index(drop=True))

    def test_missing_future_censors_not_admission(self):
        b=self.fixture();b.loc[b.index[6],OHLC]=np.nan;b.loc[b.index[6],'complete']=False
        o,_=census(b,'TST');e=o[o.is_event].iloc[0]
        self.assertTrue(pd.isna(e.return_pct_1));self.assertEqual(e.kind,'pure_wick')
        self.assertFalse(o[o.bar_i.eq(7)].week_prefix_complete.any())

    def test_missing_monday_invalidates_week(self):
        b=self.fixture();b.loc[b.index[3],OHLC]=np.nan;b.loc[b.index[3],'complete']=False
        o,w=census(b,'TST');self.assertFalse(o.is_event.any())
        self.assertEqual(w[w.week.eq('2026-09-21')].status.iloc[0],'incomplete_monday')

    def test_future_descriptors_exclude_signal_range(self):
        b=self.fixture();b.loc[b.index[6],OHLC]=[92.,96.,86.,90.]
        o,_=census(b,'TST');e=o[o.bar_i.eq(5)&o.side.eq(1)].iloc[0]
        self.assertAlmostEqual(e.return_pct_2,(100/92-1)*100)
        self.assertAlmostEqual(e.favorable_pct_2,13/92*100)
        self.assertAlmostEqual(e.adverse_pct_2,6/92*100)
        self.assertEqual(e.level_lost_2,0);self.assertEqual(e.wick_broken_2,1)
        self.assertEqual(e.near_edge_reached_2,1)


if __name__=='__main__':unittest.main()
