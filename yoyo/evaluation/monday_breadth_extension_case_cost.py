"""Sensitivity on the named September event with 10bp adverse market execution per fill."""
import pandas as pd

from yoyo.evaluation import monday_breadth_opt_exits as sim
from yoyo.evaluation import monday_breadth_opt_funding as funding
from yoyo.evaluation.monday_breadth_extension_dual import CASE,EXP
from yoyo.evaluation.monday_breadth_frequency_btc_target import cached_btc_frame


def run():
    times=pd.DatetimeIndex([CASE])
    frame,history,_=cached_btc_frame(times)
    policies=[funding.funded_policies()[i] for i in (0,4)]
    trades=sim.simulate(frame,times,policies,entry_slip=.001,exit_slip=.001)
    trades['funding_points']=funding.charged(trades,history,policies)
    trades['funded_points']=trades.net_points-trades.funding_points
    trades['rule']=trades.policy_id.map({0:'target_all',1:'half_then_6d'})
    trades['slip_market_bp_per_side']=10
    trades['slip_limit_bp']=0
    trades.to_csv(EXP/'partial/sep16_native_cost_sensitivity.csv',index=False)


if __name__=='__main__':
    run()
