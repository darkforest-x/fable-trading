"""Native dual-target accounting and partial allocation without duplicate replay."""
import unittest
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_ma6_retest import normalized_native,describe_exposure,combine_half


class Ma6NativeTests(unittest.TestCase):
    def test_subtick_passive_distance_rejects_only_that_order(self):
        f=pd.DataFrame([[.000001]*4,[.000001,.0000011,.0000009,.000001],
                        [.000001,.0000017,.0000009,.0000015],[.0000015]*4],
                       columns=['open','high','low','close'],
                       index=pd.date_range('2026-01-06',periods=4,freq='5min',tz='UTC'))
        base=dict(pair_id='x',population='signal',symbol='BTCUSDT',side=1,
                  entry_i=0,stop=.0000008,cancel_boundary=.0000012,
                  target_monday=.0000015,target_far=.0000016,
                  expire_i=2,end_i=3,week='2026-01-05')
        e=pd.DataFrame([dict(base,trade_id='subtick',entry=.000001-1e-15),
                        dict(base,trade_id='valid',entry=.00000095)])
        e['risk']=e.entry-e.stop
        rows,fills,audit,rejected=normalized_native(f,e,pd.DataFrame(),'BTCUSDT')
        self.assertEqual(set(rejected.trade_id),{'subtick'})
        self.assertEqual(set(rejected.status),{'invalid_tick_geometry'})
        self.assertEqual(audit['positions'],2)
        self.assertEqual(set(rows.parent_id),{'valid'})

    def test_tp1_then_stop_keeps_half_costs_and_original_risk(self):
        f=pd.DataFrame([[104]*4,[104,105,100,101],[101,111,99,101],[101,102,97,99],[99]*4],
            columns=['open','high','low','close'],index=pd.date_range('2026-01-06',periods=5,freq='5min',tz='UTC'))
        e=pd.DataFrame([dict(trade_id='x',pair_id='x',population='signal',symbol='BTCUSDT',
            side=1,entry_i=0,entry=100.,stop=98.,risk=2.,cancel_boundary=108.,
            target_monday=110.,target_far=120.,expire_i=3,end_i=4,week='2026-01-05')])
        rows,fills,audit,reject=normalized_native(f,e,pd.DataFrame(),'BTCUSDT')
        self.assertEqual(audit['positions'],2)
        self.assertEqual(len(fills),4)
        t=combine_half(describe_exposure(f,rows))
        h=t[t.policy.eq('half_each')].iloc[0]
        self.assertAlmostEqual(h.gross_r,2.)
        self.assertAlmostEqual(h.fee_r,((100+110)*.0002+(100*.0002+98*.0005))/4)
        self.assertEqual(h.tp_legs,1);self.assertEqual(h.sl_legs,1)
        self.assertTrue(h.closed)

    def test_pending_expiry_at_source_end_is_retained(self):
        f=pd.DataFrame([[104]*4,[104,105,101,104]],columns=['open','high','low','close'],
            index=pd.date_range('2026-01-06',periods=2,freq='5min',tz='UTC'))
        e=pd.DataFrame([dict(trade_id='x',pair_id='x',population='signal',symbol='BTCUSDT',
            side=1,entry_i=0,entry=100.,stop=98.,risk=2.,cancel_boundary=108.,
            target_monday=110.,target_far=120.,expire_i=1,end_i=1,week='2026-01-05')])
        rows,fills,audit,_=normalized_native(f,e,pd.DataFrame(),'BTCUSDT')
        self.assertTrue(rows.empty);self.assertEqual(audit['expired'],2)
        f.iloc[1]=[104,105,99,101]
        rows,fills,audit,_=normalized_native(f,e,pd.DataFrame(),'BTCUSDT')
        result=combine_half(describe_exposure(f,rows))
        self.assertEqual(audit['positions'],2)
        self.assertTrue(result.closed.eq(False).all())
        self.assertTrue(rows.reason.eq(0).all())


if __name__=='__main__':unittest.main()
