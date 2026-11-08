"""Synchronized 5m account marks across the original ALT sleeve and BTC half exits."""
import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_opt_funding as funding
from yoyo.evaluation import monday_breadth_opt_payoffs as prices
from yoyo.evaluation.monday_breadth_extension_pairs import EXP


def accumulated_cost(settlements, start, end, times):
    values = settlements.loc[start:end].copy()
    boundary = values.index.isin([start,end])
    values.loc[boundary] = values.loc[boundary].clip(lower=0)
    if values.isna().any():
        raise ValueError('Missing mark for held BTC funding settlement')
    return values.reindex(times,fill_value=0).cumsum().to_numpy()


def run():
    out = EXP/'partial'
    events = pd.read_parquet(out/'events.parquet').query('epoch == 3')
    curves = pd.read_parquet(EXP/'sleeves/equity_5m.parquet')
    frame,_ = prices.load_full_ohlc('BTCUSDT',events.decision.min(),events.exit_time.max(),repair_missing=False)
    history = funding.rates('BTCUSDT', history=True, local_only=True)
    settlements = history.realized_rate * history.mark_price.fillna(frame.open.reindex(history.index))
    originals = pd.read_parquet(funding.FUNDED_CACHE,filters=[('policy_id','in',[4,5])])
    partials = originals.loc[originals.exit_legs.eq(2)].set_index(['entry_time','policy_id'])
    accounts = []
    for name,g in events.groupby('exit_policy'):
        btc = pd.Series(np.nan,index=curves.index)
        btc.iloc[0] = 1.
        wealth = 1.
        for t in g.sort_values('decision').itertuples():
            marks = pd.date_range(t.decision,t.exit_time,freq='5min')
            close = np.r_[t.entry_open,frame.close.reindex(marks[:-1])]
            financing = accumulated_cost(settlements,t.decision,t.exit_time,marks)
            value = close.copy()
            if t.exit_legs==2:
                key = (t.decision,4 if name=='half_then_6d' else 5)
                paid = accumulated_cost(settlements,t.decision,t.first_exit_time,marks)
                financing = .5 * (financing+paid)
                sold = marks>t.first_exit_time
                value[sold] = .5*(value[sold]+partials.at[key,'first_exit_price']*(1-.0002))
            np.testing.assert_allclose(financing[-1], t.funding_points, atol=1e-6)
            btc.loc[marks] = wealth*((value-financing)/t.entry_open-.0005)
            wealth *= 1+t.funded_pct/100
            btc.at[t.exit_time] = wealth
        btc = btc.ffill()
        total = (btc+curves.ALT)/2
        if name=='target_all':
            np.testing.assert_allclose(total,curves.dual_source_specific,atol=1e-8)
        funded_growth = 100*(total.iloc[-1]-1)
        stress_btc = (1+(g.funded_pct-.2)/100).prod()
        base = pd.read_csv(EXP/'sleeves/accounts_2026.csv').set_index('strategy')
        previous = events[events.exit_policy.eq('target_all')]
        stress_alt = 2*(1+base.at['dual_source_specific','stressed_growth']/100)-(1+(previous.funded_pct-.2)/100).prod()
        accounts.append(dict(exit_policy=name,combined_funded_growth=funded_growth,
                             combined_funded_marked_dd=100*(1-total/total.cummax().clip(lower=1)).max(),
                             btc_funded_marked_dd=100*(1-btc/btc.cummax().clip(lower=1)).max(),
                             combined_stressed_growth=100*((stress_btc+stress_alt)/2-1)))
    pd.DataFrame(accounts).to_csv(out/'accounts_2026.csv',index=False)


if __name__=='__main__':
    run()
