"""Causal forming candles, asynchronous conditions and native timed protection."""
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_fib_confirmation import forming,aggregate,observe,oracle,targets,legs,prepare_body
from yoyo.evaluation.pepe_range_exits import signals
from yoyo.layers.l3_backtest.nautilus_independent import run_native,ExitPolicy


def frame(n=400):
    rng=np.random.default_rng(714)
    c=100+np.cumsum(rng.normal(0,.4,n));o=np.r_[100,c[:-1]]
    return pd.DataFrame(dict(open=o,high=np.maximum(o,c)+.3,low=np.minimum(o,c)-.3,close=c),
        index=pd.date_range('2026-01-06T00:01Z',periods=n,freq='min'))


class ConfirmationTests(unittest.TestCase):
    def body_fixture(self,late=False,low=101.,high=103.):
        f=pd.DataFrame([[100.]*4]*600,columns=['open','high','low','close'],
            index=pd.date_range('2026-01-06T00:01Z',periods=600,freq='min'))
        if late:f.iloc[:300]=98.
        else:f.iloc[10,f.columns.get_loc('low')]=98.
        touch=1 if late else 10
        weekly=pd.DataFrame([dict(trade_id='a',pair_id='a',week='2026-01-05',side=1,
            entry_i=0,expire_i=598,entry=99.,monday_high=high,monday_low=low)])
        original=pd.DataFrame([dict(parent_id='a',entry_i=touch)])
        cfg=dict(timezone='America/New_York',tick=.1,round_trip_cost=.002,
                 stop_fraction=.007,split='2026-06-01T00:00Z')
        with patch('yoyo.evaluation.monday_fib_confirmation.pd.read_csv',side_effect=[weekly,original]):
            return prepare_body(f,cfg)

    def test_body_only_has_no_arrow_requirement(self):
        entries,_,_=self.body_fixture()
        self.assertEqual(entries.entry_i.tolist(),[14])
        self.assertTrue(entries.arrow_i.eq(-1).all())

    def test_body_only_accepts_after_removed_deadline(self):
        entries,_,_=self.body_fixture(late=True)
        self.assertEqual(entries.entry_i.tolist(),[314])
        self.assertGreater(entries.wait_minutes.iloc[0],285)

    def test_body_only_skips_insufficient_fee_room(self):
        entries,decisions,_=self.body_fixture(low=100.,high=100.2)
        self.assertTrue(entries.empty)
        self.assertEqual(decisions.status.tolist(),['fee_space_insufficient'])

    def test_owner_four_targets_and_original_quarters(self):
        t=targets(83149.8,81166.,-1,.1)
        np.testing.assert_allclose(list(t.values()),[82157.9,81923.8,81751.2,81166.],atol=1e-8)
        long=targets(83149.8,81166.,1,.1)
        np.testing.assert_allclose(list(long.values()),[82157.9,82392.,82564.6,83149.8],atol=.100001)
        e=legs(pd.DataFrame([dict(trade_id='a',**t)]))
        self.assertEqual(len(e),4);self.assertTrue(e.weight.eq(.25).all());self.assertEqual(e.weight.sum(),1.)

    def test_forming_final_matches_confirmed_all_periods(self):
        f=frame(3000)
        for m in (15,30,60):
            actual=forming(f,m);q=aggregate(f,m)
            np.testing.assert_array_equal(actual.state.reindex(q.index),signals(q))

    def test_forming_prefix_no_future_high(self):
        f=frame(3000)
        for m in (15,30,60):
            all_=forming(f,m)
            cut=1721
            before=forming(f.iloc[:cut],m)
            pd.testing.assert_frame_equal(before,all_.iloc[:cut])
            mutated=f.copy();mutated.iloc[cut:,1]=999
            pd.testing.assert_frame_equal(forming(mutated,m).iloc[:cut],before)

    def conditions(self,arrow,body,side=1):
        f=frame();e=np.zeros(len(f),int);e[arrow]=side
        q=pd.DataFrame(np.nan,index=f.index,columns=f.columns)
        q.iloc[body]=101 if side==1 else 99
        return f,{30:e},q

    def test_arrow_before_body_latches_despite_disappearance(self):
        f,e,q=self.conditions(20,44)
        r=observe(f,10,350,1,100,e,q)
        self.assertEqual((r['entry_i'],r['arrow_i'],r['body_i']),(44,20,44))

    def test_body_before_arrow_short(self):
        f,e,q=self.conditions(44,29,-1)
        r=observe(f,10,350,-1,100,e,q)
        self.assertEqual((r['entry_i'],r['arrow_i'],r['body_i']),(44,44,29))

    def test_pre_touch_arrow_not_allowed(self):
        f,e,q=self.conditions(9,29)
        self.assertEqual(observe(f,10,350,1,100,e,q)['status'],'expired')

    def test_deadline_inclusive_then_expired(self):
        f,e,q=self.conditions(295,29)
        self.assertEqual(observe(f,10,350,1,100,e,q)['entry_i'],295)
        self.assertEqual(observe(f,10,350,1,100,e,q,284)['status'],'expired')

    def test_body_touching_level_not_inside(self):
        f,e,q=self.conditions(20,29);q.iloc[29,q.columns.get_loc('open')]=100
        self.assertEqual(observe(f,10,350,1,100,e,q)['status'],'expired')

    def test_no_wait_timeout_but_weekly_reset_still_applies(self):
        f,e,q=self.conditions(320,29)
        self.assertEqual(observe(f,10,350,1,100,e,q,None)['entry_i'],320)
        self.assertEqual(observe(f,10,310,1,100,e,q,None)['status'],'expired')

    def test_no_wait_timeout_last_week_is_censored(self):
        f,e,q=self.conditions(399,29)
        self.assertEqual(observe(f,10,399,1,100,e,q,None)['status'],'data_censored')

    def test_native_timed_cost_be_waits_until_feasible(self):
        f=pd.DataFrame([[100.]*4]*21,columns=['open','high','low','close'],
            index=pd.date_range('2026-01-06T00:01Z',periods=21,freq='min'))
        f.iloc[14]=[100,101,100,100.5]
        f.iloc[15]=[100.5,100.6,100.1,100.2]
        entries=pd.DataFrame([dict(trade_id='leg',entry_i=0,entry=100.,side=1,
            stop=99.,risk=1.,target_price=104.,end_i=20)])
        rows,_,_=run_native(f,np.full(len(f),np.nan),np.zeros(len(f)),entries,
            ExitPolicy(target_from_entry=True,opposite_hour_exit=False,
                time_breakeven_minutes=5,round_trip_cost=.002),tick=.1,symbol='TEST',execution_minutes=1)
        r=rows.iloc[0]
        self.assertEqual((r.reason,r.be_trigger_i,r.be_effective_i,r.exit_i),(5,9,14,15))
        self.assertAlmostEqual(r.net_r,0.)
        got=oracle(np.rint(f.to_numpy()/.1).astype(int),r,5,.1,.002)
        self.assertEqual(got,(15,100.2,5,9,14))


if __name__=='__main__':unittest.main()
