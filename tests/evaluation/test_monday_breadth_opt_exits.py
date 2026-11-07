"""Synthetic regressions for execution order, exit-window edges and funding on partial fills."""
import importlib.util
import unittest
import pandas as pd

if importlib.util.find_spec('vectorbt') is not None:
    from yoyo.evaluation import monday_breadth_opt_exits as exits
    from yoyo.evaluation import monday_breadth_opt_funding as funding


def quotes():
    index=pd.date_range('2026-01-02',periods=15,freq='5min',tz='UTC')
    frame=pd.DataFrame(100.,index=index,columns=['open','high','low','close'])
    return exits.prepare(frame)


@unittest.skipUnless(importlib.util.find_spec('vectorbt'),'requires isolated VectorBT environment')
class ExitTest(unittest.TestCase):
    def test_ambiguous_entry_candle_stop_first_and_excludes_exit_candle_high(self):
        frame=quotes()
        frame.iloc[0,1:3]=[112.,92.]
        frame.iloc[12,1]=200.
        policy=exits.Policy('time',1,6,take_pct=10,take_fraction=1)
        f=exits.simulate(frame,[frame.index[0]],[policy]).iloc[0]
        self.assertEqual((f.exit_vwap,f.ambiguous),(94,1))
        self.assertAlmostEqual(f.peak_pct,12)

    def test_gap_partial_and_remaining_stop_on_same_bar(self):
        frame=quotes()
        frame.iloc[1,:4]=[112.,113.,92.,99.]
        policy=exits.Policy('time',1,6,take_pct=10,take_fraction=.5)
        f=exits.simulate(frame,[frame.index[0]],[policy]).iloc[0]
        self.assertEqual(f.first_exit_time,frame.index[1])
        self.assertEqual(f.exit_time,frame.index[1])
        self.assertEqual(f.exit_legs,2)
        self.assertAlmostEqual(f.first_exit_price,110)
        self.assertAlmostEqual(f.exit_vwap,102)
        self.assertAlmostEqual(f.net_points,2.-100*.0005-.5*110*.0002-.5*94*.0005)

    def test_trailing_arms_after_high_and_gap_pays_worse_open(self):
        frame=quotes()
        frame.iloc[1,:4]=[100.,120.,95.,105.]
        frame.iloc[2,:4]=[105.,112.,100.,111.]
        policy=exits.Policy('percent_trail',1,12,5,4)
        f=exits.simulate(frame,[frame.index[0]],[policy]).iloc[0]
        self.assertEqual(f.exit_time,frame.index[2])
        self.assertEqual(f.exit_vwap,105)

    def test_funding_charged_to_residual_size_with_adverse_boundary_ordering(self):
        t=pd.date_range('2026-01-01',periods=4,freq='8h',tz='UTC')
        hist=pd.DataFrame(dict(realized_rate=[-.01,.02,.01,.01],mark_price=[100.]*4),index=t)
        fills=pd.DataFrame(dict(entry_time=[t[0]],first_exit_time=[t[1]],exit_time=[t[3]],policy_id=[0],exit_legs=[2]))
        policy=exits.Policy('time',24,6,take_pct=5,take_fraction=.75)
        cost=funding.charged(fills,hist,[policy])
        self.assertAlmostEqual(cost[0],2+.25+.25)


if __name__=='__main__':unittest.main()
