"""Causal forming candles, asynchronous conditions and native timed protection."""
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_fib_confirmation import forming,aggregate,observe,oracle,targets,legs,prepare_body,body_confirmation,structural_stop_levels,initial_stop,wick_engulf_confirmation
from yoyo.evaluation.pepe_range_exits import signals
from yoyo.layers.l3_backtest.nautilus_independent import run_native,ExitPolicy


def frame(n=400):
    rng=np.random.default_rng(714)
    c=100+np.cumsum(rng.normal(0,.4,n));o=np.r_[100,c[:-1]]
    return pd.DataFrame(dict(open=o,high=np.maximum(o,c)+.3,low=np.minimum(o,c)-.3,close=c),
        index=pd.date_range('2026-01-06T00:01Z',periods=n,freq='min'))


class ConfirmationTests(unittest.TestCase):
    def test_near_monday_target_changes_only_first_quarter(self):
        for side,edge in ((1,80.),(-1,100.)):
            old=targets(100,80,side,.1)
            new=targets(100,80,side,.1,True)
            self.assertEqual(new['tp1'],edge)
            for j in (2,3,4):self.assertEqual(new[f'tp{j}'],old[f'tp{j}'])
            self.assertTrue(all(side*(new[f'tp{j+1}']-new[f'tp{j}'])>0 for j in (1,2,3)))

    def test_near_target_invalidates_cached_admission_and_execution(self):
        from yoyo.evaluation.monday_fib_tp_breakeven import admission_changed,check_projection_policy
        old={};new=dict(first_target_monday_boundary=True)
        self.assertTrue(admission_changed(new,old))
        with self.assertRaisesRegex(ValueError,'first_target_monday_boundary'):
            check_projection_policy(new,old)

    def test_near_target_already_passed_keeps_existing_skip_rule(self):
        old,_,_=self.body_fixture(low=99.,high=103.)
        new,decisions,_=self.body_fixture(low=99.,high=103.,first_boundary=True)
        self.assertEqual(len(old),1)
        self.assertTrue(new.empty)
        self.assertEqual(decisions.status.tolist(),['target_already_passed'])

    def test_structural_stop_exact_five_closed_bars_and_mirror(self):
        f=frame(120);f.loc[:,'high']=110.;f.loc[:,'low']=90.
        f.iloc[0,f.columns.get_loc('low')]=1.
        f.iloc[0,f.columns.get_loc('high')]=999.
        f.iloc[89,f.columns.get_loc('low')]=88.
        f.iloc[89,f.columns.get_loc('high')]=112.
        cfg=dict(stop_15m_lookback=5,tick=.1,stop_fraction=.007)
        levels=structural_stop_levels(f,cfg)
        self.assertEqual(initial_stop(100,1,89,cfg,levels),88.)
        self.assertEqual(initial_stop(100,-1,89,cfg,levels),112.)

    def test_structural_stop_ignores_forming_and_future_bars(self):
        f=frame(120);cfg=dict(stop_15m_lookback=5,tick=.1)
        before=structural_stop_levels(f,cfg)
        f.iloc[90:,f.columns.get_loc('low')]=-1000.
        f.iloc[90:,f.columns.get_loc('high')]=1000.
        after=structural_stop_levels(f,cfg)
        pd.testing.assert_frame_equal(before.iloc[:104],after.iloc[:104])
        prefix=structural_stop_levels(f.iloc[:98],cfg)
        pd.testing.assert_frame_equal(prefix,after.iloc[:98])
        self.assertTrue(after.iloc[:74].isna().all().all())

    def test_structural_stop_configuration_invalidates_frozen_reuse(self):
        from yoyo.evaluation.monday_fib_tp_breakeven import admission_changed,check_projection_policy
        old=dict(stop_fraction=.007);new=dict(old,stop_15m_lookback=5)
        self.assertTrue(admission_changed(new,old))
        with self.assertRaisesRegex(ValueError,'stop_15m_lookback'):
            check_projection_policy(new,old)
        for n in (0,-1,2.5,True):
            with self.assertRaises(ValueError):structural_stop_levels(frame(),dict(stop_15m_lookback=n))

    def test_fixed_stop_default_remains_unchanged(self):
        cfg=dict(stop_fraction=.007,tick=.1)
        self.assertIsNone(structural_stop_levels(frame(),cfg))
        self.assertAlmostEqual(initial_stop(100,1,0,cfg),99.3)
        self.assertAlmostEqual(initial_stop(100,-1,0,cfg),100.7)

    def test_fee_change_invalidates_admission_and_native_projection(self):
        from yoyo.evaluation.monday_fib_tp_breakeven import admission_changed,check_projection_policy
        old=dict(wait_minutes=None,require_arrow=False,adverse_extension_fraction=.02,
            round_trip_cost=.002,tick=.1,stop_fraction=.007,target_breakeven=True,be_minutes=[0])
        new=dict(old,round_trip_cost=0.)
        self.assertTrue(admission_changed(new,old))
        with self.assertRaisesRegex(ValueError,'round_trip_cost'):
            check_projection_policy(new,old)
        self.assertFalse(admission_changed(old,dict(old)))
        check_projection_policy(old,dict(old))

    def test_causal_filter_only_allows_native_projection(self):
        from yoyo.evaluation.monday_fib_tp_breakeven import admission_changed,check_projection_policy
        old=dict(wait_minutes=None,require_arrow=False,round_trip_cost=0.,tick=.1,
            stop_fraction=.007,target_breakeven=True,be_minutes=[0])
        new=dict(old,adverse_extension_fraction=.02)
        self.assertTrue(admission_changed(new,old))
        check_projection_policy(new,old)

    def body_fixture(self,late=False,low=101.,high=103.,extension=None,wick=None,wick_i=10,next_close=False,first_boundary=False):
        f=pd.DataFrame([[100.]*4]*600,columns=['open','high','low','close'],
            index=pd.date_range('2026-01-06T00:01Z',periods=600,freq='min'))
        if late:f.iloc[:300]=98.
        else:f.iloc[10,f.columns.get_loc('low')]=98.
        if wick is not None:f.iloc[wick_i,f.columns.get_loc('low')]=wick
        touch=1 if late else 10
        weekly=pd.DataFrame([dict(trade_id='a',pair_id='a',week='2026-01-05',side=1,
            entry_i=0,expire_i=598,entry=99.,monday_high=high,monday_low=low)])
        original=pd.DataFrame([dict(parent_id='a',entry_i=touch)])
        cfg=dict(timezone='America/New_York',tick=.1,round_trip_cost=.002,
                 stop_fraction=.007,split='2026-06-01T00:00Z',adverse_extension_fraction=extension,
                 next_15m_close_confirmation=next_close,first_target_monday_boundary=first_boundary)
        with patch('yoyo.evaluation.monday_fib_confirmation.pd.read_csv',side_effect=[weekly,original]):
            return prepare_body(f,cfg)

    def test_body_only_has_no_arrow_requirement(self):
        entries,_,_=self.body_fixture()
        self.assertEqual(entries.entry_i.tolist(),[14])
        self.assertTrue(entries.arrow_i.eq(-1).all())

    def test_next_close_delays_actual_entry_and_preserves_first_body(self):
        entries,_,_=self.body_fixture(next_close=True)
        self.assertEqual(entries.entry_i.tolist(),[29])
        self.assertEqual(entries.body_i.tolist(),[14])

    def test_next_close_keeps_adverse_gate_live_through_second_bar(self):
        entries,decisions,_=self.body_fixture(next_close=True,extension=.02,wick=96.,wick_i=20)
        self.assertTrue(entries.empty)
        self.assertEqual(decisions.status.tolist(),['adverse_extension_invalidated'])

    def test_next_close_future_after_delayed_entry_is_ignored(self):
        entries,_,_=self.body_fixture(next_close=True,extension=.02,wick=96.,wick_i=35)
        self.assertEqual(entries.entry_i.tolist(),[29])

    def test_next_close_mirror_open_is_not_required(self):
        for side in (1,-1):
            q=pd.DataFrame(np.nan,index=frame(90).index,columns=['open','close'])
            q.iloc[14]=100+side
            q.iloc[29]=[100-side,100+side]
            self.assertEqual(body_confirmation(q,10,80,side,100,True),(29,14))

    WICK_BARS={14:[101,102,100,101.5],29:[101.5,101.6,100.5,100.8],
               44:[100.8,102.2,100.7,102],59:[101,101.2,99.5,100.6],74:[100.6,101.3,100.5,101.2]}

    def wick_quarter(self,bars,side=1):
        q=pd.DataFrame(np.nan,index=frame(200).index,columns=['open','high','low','close'])
        for k,(o,h,l,c) in bars.items():
            q.iloc[k]=[o,h,l,c] if side==1 else [200-o,200-l,200-h,200-c]
        return q

    def test_wick_engulf_waits_for_second_wick_and_mirrors(self):
        for side in (1,-1):
            q=self.wick_quarter(self.WICK_BARS,side)
            self.assertEqual(wick_engulf_confirmation(q,7,190,side,100),(74,14,59))

    def test_wick_requires_open_and_close_inside(self):
        bars=dict(self.WICK_BARS);bars[14]=[99.5,102,99.5,101.5]
        bars[74]=[100.6,101.3,100.6,101.2]
        self.assertEqual(wick_engulf_confirmation(self.wick_quarter(bars),7,190,1,100),(-1,-1,-1))

    def test_engulf_needs_larger_opposite_previous_body(self):
        bars=dict(self.WICK_BARS);bars[74]=[100.6,101,100.5,100.9]
        bars[89]=[100.9,101,100.6,100.7];bars[104]=[100.7,101.1,100.6,101]
        self.assertEqual(wick_engulf_confirmation(self.wick_quarter(bars),7,190,1,100),(104,14,59))
        self.assertEqual(wick_engulf_confirmation(self.wick_quarter(bars),7,100,1,100),(-1,-1,-1))

    def test_wick_rule_admission_shares_targets_and_stop(self):
        from yoyo.evaluation.monday_fib_tp_breakeven import admission_changed
        self.assertTrue(admission_changed(dict(entry_rule='two_wick_engulf'),{}))
        f=pd.DataFrame([[101.5]*4]*600,columns=['open','high','low','close'],
            index=pd.date_range('2026-01-06T00:01Z',periods=600,freq='min'))
        for k,(o,h,l,c) in self.WICK_BARS.items():
            f.iloc[k-14,0]=o;f.iloc[k,3]=c;f.iloc[k-7,1]=h;f.iloc[k-7,2]=l
        weekly=pd.DataFrame([dict(trade_id='a',pair_id='a',week='2026-01-05',side=1,
            entry_i=0,expire_i=598,entry=100.,monday_high=110.,monday_low=105.)])
        original=pd.DataFrame([dict(parent_id='a',entry_i=7)])
        cfg=dict(timezone='America/New_York',tick=.1,round_trip_cost=0.,stop_fraction=.007,
                 split='2026-06-01T00:00Z',adverse_extension_fraction=None,entry_rule='two_wick_engulf')
        with patch('yoyo.evaluation.monday_fib_confirmation.pd.read_csv',side_effect=[weekly,original]):
            entries,_,checks=prepare_body(f,cfg)
        r=entries.iloc[0]
        self.assertEqual((r.trade_id,r.entry_i,r.wick1_i,r.wick2_i,r.entry),('wick2engulf15m-a',74,14,59,101.2))
        self.assertEqual((r.tp1,r.stop),(107.5,100.4))
        self.assertEqual(checks['entry_rule'],'two_wick_engulf')

    def test_next_close_must_be_immediately_adjacent_not_latched(self):
        q=pd.DataFrame(np.nan,index=frame(90).index,columns=['open','close'])
        q.iloc[14]=101;q.iloc[29]=99;q.iloc[44]=[99,101]
        self.assertEqual(body_confirmation(q,10,50,1,100,True),(-1,-1))
        q.iloc[59]=101;q.iloc[74]=[99,101]
        self.assertEqual(body_confirmation(q,10,80,1,100,True),(74,59))

    def test_next_close_equal_to_level_fails(self):
        q=pd.DataFrame(np.nan,index=frame(90).index,columns=['open','close'])
        q.iloc[14]=101;q.iloc[29]=[101,100]
        self.assertEqual(body_confirmation(q,10,35,1,100,True),(-1,-1))

    def test_next_close_cannot_cross_week_or_data_end(self):
        q=pd.DataFrame(np.nan,index=frame(90).index,columns=['open','close'])
        q.iloc[14]=101;q.iloc[29]=101
        self.assertEqual(body_confirmation(q,10,28,1,100,True),(-1,-1))
        self.assertEqual(body_confirmation(q.iloc[:29],10,28,1,100,True),(-1,-1))

    def test_next_close_prefix_causality(self):
        q=pd.DataFrame(np.nan,index=frame(90).index,columns=['open','close'])
        q.iloc[14]=101;q.iloc[29]=101
        self.assertEqual(body_confirmation(q.iloc[:30],10,29,1,100,True),(29,14))
        q.iloc[30:]=999
        self.assertEqual(body_confirmation(q,10,89,1,100,True),(29,14))

    def test_next_close_invalidates_admission_cache(self):
        from yoyo.evaluation.monday_fib_tp_breakeven import admission_changed
        old=dict(wait_minutes=None,require_arrow=False,round_trip_cost=0.)
        self.assertTrue(admission_changed(dict(old,next_15m_close_confirmation=True),old))

    def test_body_only_accepts_after_removed_deadline(self):
        entries,_,_=self.body_fixture(late=True)
        self.assertEqual(entries.entry_i.tolist(),[314])
        self.assertGreater(entries.wait_minutes.iloc[0],285)

    def test_body_only_skips_insufficient_fee_room(self):
        entries,decisions,_=self.body_fixture(low=100.,high=100.2)
        self.assertTrue(entries.empty)
        self.assertEqual(decisions.status.tolist(),['fee_space_insufficient'])

    def test_adverse_two_percent_wick_invalidates_before_recovery_close(self):
        entries,decisions,_=self.body_fixture(extension=.02,wick=97.)
        self.assertTrue(entries.empty)
        self.assertEqual(decisions.status.tolist(),['adverse_extension_invalidated'])
        self.assertEqual(decisions.invalid_i.tolist(),[10])

    def test_future_extension_after_entry_does_not_change_admission(self):
        entries,_,_=self.body_fixture(extension=.02,wick=96.,wick_i=20)
        self.assertEqual(entries.entry_i.tolist(),[14])

    def test_exact_two_percent_is_not_strictly_greater(self):
        entries,_,_=self.body_fixture(extension=.02,wick=97.02)
        self.assertEqual(entries.entry_i.tolist(),[14])

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
