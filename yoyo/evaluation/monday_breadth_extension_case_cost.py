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
    # An ex-post ceiling. Every member of the original BTC unit can receive the
    # highest available 5m high, a maker exit fee, and the best boundary ordering.
    deadline=CASE+pd.Timedelta(days=6)
    bars=frame.loc[CASE:deadline]
    highs=bars.high.copy()
    highs.at[deadline]=bars.at[deadline,'open']
    payments=(history.realized_rate*history.mark_price).reindex(bars.index,fill_value=0.)
    if payments.isna().any() or highs.isna().any():
        raise ValueError('Incomplete case ceiling inputs')
    payments.at[CASE]=min(0.,payments.at[CASE])
    compulsory=payments.cumsum().shift(1,fill_value=0.)+payments.clip(upper=0.)
    bounds=[]
    for slip in (0.,.001):
        buy=frame.at[CASE,'open']*(1+slip)
        net=highs*(1-.0002)-buy*(1+.0005)-compulsory
        t=net.idxmax()
        bounds.append(dict(market_entry_slip_bp=slip*10000,entry_price=buy,best_bar=t,
                           best_high=highs.at[t],net_points_ceiling=net.at[t],settled_cost_at_high=compulsory.at[t]))
    pd.DataFrame(bounds).to_csv(EXP/'partial/sep16_best_possible_points.csv',index=False)


if __name__=='__main__':
    run()
