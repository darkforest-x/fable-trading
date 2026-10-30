"""Keep the owner comparison on one score clock and one episode."""
import unittest
import pandas as pd
from yoyo.evaluation.v3_causal_close_entry import close_order, select_orders, changed_order_ids


class V3CloseTests(unittest.TestCase):
    def setUp(self):
        self.p=pd.DataFrame({'close':[98.,102.,110.]})
        self.order=dict(side=1,entry=100.,risk=100.,stop=70.,target_price=110.,expire_i=2,end_i=2,reclaim_ref=100.,be_offset_bp=17.)
        self.baseline=dict(entry_i=0,exit_i=2)

    def test_outside_close_allowed_and_anchors_preserved(self):
        r,status=close_order(self.order,self.baseline,self.p,.1)
        self.assertEqual(status,'admitted');self.assertEqual(r['entry'],98.)
        self.assertEqual(r['reclaim_ref'],100.);self.assertEqual(r['stop'],70.)

    def test_future_prices_and_future_exit_cannot_change_admission(self):
        a=close_order(self.order,self.baseline,self.p,.1)
        p=self.p.copy();p.iloc[1:]=999.
        b=close_order(self.order,dict(entry_i=0,exit_i=100),p,.1)
        self.assertEqual(a,b)

    def test_native_episode_completed_at_touch_is_not_reopened(self):
        self.assertEqual(close_order(self.order,dict(entry_i=0,exit_i=0),self.p,.1)[1],'episode_completed_in_touch_bar')

    def test_no_touch_and_bounds_are_explicit(self):
        self.assertEqual(close_order(self.order,None,self.p,.1)[1],'no_touch_before_expiry')
        p=self.p.copy();p.iloc[0]=110.
        self.assertEqual(close_order(self.order,self.baseline,p,.1)[1],'close_at_or_past_target')
        p.iloc[0]=70.
        self.assertEqual(close_order(self.order,self.baseline,p,.1)[1],'close_at_or_past_backstop')

    def test_short_mirror(self):
        r,_=close_order(dict(self.order,side=-1,stop=130.,target_price=90.),self.baseline,200-self.p,.1)
        self.assertEqual(r['entry'],102.);self.assertEqual(r['stop'],130.)

    def test_target_band_uses_frozen_score_only(self):
        rows=pd.DataFrame([dict(event_id='a',reclaim_minutes=60,exit_mode=m,stored=.99) for m in ['range_0.618','opposite_1618']])
        cfg=dict(score_cut=.5,far_score=.6)
        self.assertEqual(select_orders(rows,pd.Series({'a':.5678}),cfg).exit_mode.tolist(),['range_0.618'])
        self.assertEqual(select_orders(rows,pd.Series({'a':.6}),cfg).exit_mode.tolist(),['opposite_1618'])
        self.assertTrue(select_orders(rows,pd.Series({'a':.499}),cfg).empty)

    def test_one_tick_stop_change_invalidates_only_that_order(self):
        a=pd.DataFrame([dict(self.order,trade_id=x,entry_i=0,order_mode='market') for x in ['a','b']])
        b=a.copy();b.loc[0,'stop']+=1e-6
        self.assertEqual(changed_order_ids(a,b,1e-6),{'a'})
        self.assertEqual(changed_order_ids(a,a.copy(),1e-6),set())


if __name__=='__main__':unittest.main()
