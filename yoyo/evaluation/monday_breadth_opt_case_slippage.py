"""Reprice the actual BTC example after delay or adverse market fills; recalculate funding-aware limit orders."""
import pandas as pd
from yoyo.evaluation import monday_breadth_opt_exit_study as study
from yoyo.evaluation import monday_breadth_opt_exits as sim
from yoyo.evaluation import monday_breadth_opt_funding as funding


def run():
    delays=[0,5,10,15,30,60]
    times=[study.CASE_ENTRY+pd.Timedelta(minutes=x) for x in delays]
    frame,hist=funding.btc_frame(times)
    pol=[sim.Policy('time',144,0),sim.Policy('btc_funded_limit',144,6,take_fraction=1),
         sim.Policy('btc_funded_partial',144,6,width=4,take_fraction=.75),
         sim.Policy('btc_funded_partial',144,6,width=4,take_fraction=.5)]
    rows=[]
    for slip in (0,1,2,5,10):
        fills=sim.simulate(frame,times,pol,entry_slip=slip/10000,exit_slip=slip/10000)
        fills['funding_points']=funding.charged(fills,hist,pol)
        fills['funded_points']=fills.net_points-fills.funding_points
        fills['delay_min']=(fills.entry_time-study.CASE_ENTRY).dt.total_seconds()/60
        fills['adverse_bps_each_market_fill']=slip
        fills['policy']=[pol[i].key for i in fills.policy_id]
        rows.append(fills)
    pd.concat(rows,ignore_index=True).to_csv(study.OUT/'sept16_delay_slippage_sensitivity.csv',index=False)


if __name__=='__main__':run()
