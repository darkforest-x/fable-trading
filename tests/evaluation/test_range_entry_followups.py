"""Causal hourly admission and fixed-opportunity retest accounting checks."""
import unittest

import numpy as np
import pandas as pd

from yoyo.evaluation.range_entry_followups import (
    hourly_structure, classify_structure, limit_candidates,
    original_opportunities, realize_opportunities, zero_rejected, describe,
)


def hourly_fixture(second_low=87):
    highs = [101,102,105,102,101,103,107,103,102,104,108,104,103]
    lows = [90,89,88,86,88,89,90,89,88,second_low,89,90,91]
    frame = pd.DataFrame({'open':95., 'close':95.,
        'high':np.repeat(highs,12), 'low':np.repeat(lows,12)},
        index=pd.date_range('2026-01-06 00:05',periods=156,freq='5min',tz='UTC'))
    return frame


def ledger_fixture():
    return pd.DataFrame([dict(trade_id=f'{pop}-0',pair_id=0,population=pop,
        entry_i=0,entry_time='2026-01-06T00:00:00Z',entry=100.,stop=98.,risk=2.,
        gross_r=gross,fee_r=.1,net_r=gross-.1,net_notional_bp=(gross-.1)*200,
        week=1,exit_i=3,exit_time='2026-01-06T00:15:00Z',exit_price=100.+gross*2,
        reason=7,peak_r=max(0.,gross)) for pop,gross in [('signal',1.),('random',-1.)]])


class EntryFollowupTests(unittest.TestCase):
    def test_hourly_pivots_wait_for_two_completed_right_bars(self):
        f=hourly_fixture();x=hourly_structure(f)
        self.assertFalse(x.loc['2026-01-06 11:55'].known)
        self.assertTrue(x.loc['2026-01-06 12:00'].known)
        self.assertEqual(x.loc['2026-01-06 12:00'].direction,1)
        self.assertEqual(x.loc['2026-01-06 12:00'].previous_low,86)
        self.assertEqual(x.loc['2026-01-06 12:00'].last_low,87)
        self.assertLessEqual(x.loc['2026-01-06 12:00'].low_confirmed_ns,
                             pd.Timestamp('2026-01-06 12:00',tz='UTC').value)

    def test_hourly_partial_hour_and_future_changes_cannot_leak(self):
        f=hourly_fixture();full=hourly_structure(f)
        for end in [50,95,143,144,150]:
            partial=hourly_structure(f.iloc[:end])
            pd.testing.assert_frame_equal(partial,full.iloc[:end])
        changed=f.copy();changed.iloc[150:,changed.columns.get_loc('high')]=999
        pd.testing.assert_frame_equal(hourly_structure(changed).iloc[:150],full.iloc[:150])

    def test_mixed_structure_and_trade_direction_are_separate(self):
        x=hourly_structure(hourly_fixture())
        entries=pd.DataFrame({'entry_i':[143,143,0], 'side':[1,-1,1]})
        self.assertEqual(classify_structure(x,entries).context.tolist(),['aligned','counter','unknown'])
        self.assertEqual(hourly_structure(hourly_fixture(85)).iloc[143].direction,0)

    def test_limits_are_prospective_relative_and_keep_stops(self):
        f=hourly_fixture()
        b=pd.DataFrame([dict(pair_id=0,population=p,entry_i=0,entry=e,side=1,
            stop=s,end_i=len(f)-1) for p,e,s in [('signal',110,99),('random',220,198)]])
        m=pd.DataFrame([dict(pair_id=0,entry=110,level=100.06,key='test')])
        result=limit_candidates(f,b,m,.1,30,'UTC')
        np.testing.assert_allclose(result.entry,[100.,200.1])
        np.testing.assert_allclose(result.stop,[99.,198.])
        self.assertEqual(result.expire_i.tolist(),[6,6])
        self.assertEqual(result.status.tolist(),['pending','pending'])

    def test_weekend_deadline_uses_known_calendar(self):
        f=hourly_fixture().iloc[:10].copy()
        f.index=pd.date_range('2026-01-09 23:50',periods=10,freq='5min',tz='America/New_York')
        b=pd.DataFrame([dict(pair_id=0,population='signal',entry_i=0,entry=110,
            side=1,stop=99,end_i=9)])
        m=pd.DataFrame([dict(pair_id=0,entry=110,level=100,key='test')])
        self.assertEqual(limit_candidates(f,b,m,.1,30,'America/New_York').iloc[0].expire_i,1)

    def test_unfilled_opportunities_remain_zero_in_denominator(self):
        original=ledger_fixture()
        decisions=original[['trade_id']].assign(status='expired')
        out=realize_opportunities(original,pd.DataFrame(),decisions)
        s=describe(out,[0],42)
        self.assertEqual(s['opportunities'],1)
        self.assertEqual(s['n'],0)
        self.assertEqual(s['per_opportunity_net_bp'],0)
        self.assertEqual(s['skipped_original_winners'],1)
        self.assertTrue(out.actual_entry_time.isna().all())

    def test_partition_preserves_every_original_pair_and_return(self):
        original=ledger_fixture()
        contexts=original[['trade_id']].assign(context=['aligned','counter'])
        pieces=[zero_rejected(original,contexts,c) for c in ['aligned','counter','mixed','unknown']]
        np.testing.assert_allclose(sum(x.net_r.to_numpy() for x in pieces),original.net_r)
        s=describe(pieces[0],[0],42)
        self.assertEqual(s['n'],1)
        self.assertEqual(s['random_n'],0)
        self.assertEqual(s['random_per_opportunity_net_bp'],0)

    def test_actual_fill_time_and_original_risk_are_distinct(self):
        original=ledger_fixture()
        fills=original.iloc[:1].copy()
        fills['entry']=99.;fills['risk']=1.;fills['entry_i']=2
        fills['entry_time']='2026-01-06T00:10:00Z'
        fills['net_notional_bp']=50.
        decisions=original[['trade_id']].assign(status=['filled','expired'])
        out=realize_opportunities(original,fills,decisions)
        self.assertEqual(out.iloc[0].entry_i,0)
        self.assertEqual(out.iloc[0].actual_entry_i,2)
        self.assertAlmostEqual(out.iloc[0].reference_net_r,.005*99/2)


if __name__=='__main__':unittest.main()
