"""Narrow the single binding five-minute peak and trough using native one-minute marks."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_drawdown_bounds as five
from yoyo.evaluation import cloud_close_reward_account as wallet


def main():
    equity = pd.read_csv(five.PARENT/'hourly_OKX.csv.gz',index_col='hour_end_utc',parse_dates=True)
    five_hours = pd.read_csv(five.EXP/'hourly_refinement.csv',index_col='hour_end',parse_dates=True)
    equity.loc[five_hours.index,['lower_nav','upper_nav']] = five_hours[['refined_lower','refined_upper']].to_numpy()
    prior = five.max_drawdown(equity)
    if not np.isclose(prior,35.03936646567354):
        raise ValueError('Parent 5m conservative bound differs')
    trough = pd.Timestamp('2025-11-12T14:00Z')
    violated = equity.index[equity.lower_nav/equity.upper_nav.cummax().clip(lower=1000) < .65]
    if list(violated)!=[trough+pd.Timedelta(hours=1)]:
        raise ValueError('The frozen peak/trough windows no longer isolate the 35% ambiguity')
    raw = wallet.ROOT/'data/research/cloud_close_drawdown_bounds_1m_20261109/quotes.csv'
    quotes = pd.read_csv(raw,dtype={'symbol':str},keep_default_na=False)
    legs = five.funded_legs()
    # A close-stage fee can be paid while another contract is at its adverse
    # extreme in the same hour. Allow that fee on every unrefined hourly floor.
    # At a simultaneous open, also include equity immediately before entry fees.
    late = legs[~legs.exit_phase.eq('open')]
    exit_clock = pd.to_datetime(late.exit_bar_ms+wallet.HOUR,unit='ms',utc=True)
    exit_fees = (abs(late.signed_size)*late.exit_price*late.exit_fee_rate).groupby(exit_clock).sum()
    coarse_hours = equity.index.difference(five_hours.index)
    equity.loc[coarse_hours,'lower_nav'] -= exit_fees.reindex(coarse_hours,fill_value=0)
    open_clock = pd.to_datetime(legs.entry_ms+wallet.HOUR,unit='ms',utc=True)
    entry_fees = (abs(legs.signed_size)*legs.entry_price*wallet.TAKER).groupby(open_clock).sum()
    pre_entry = equity.open_nav + entry_fees.reindex(equity.index,fill_value=0)
    equity['upper_nav'] = np.maximum(equity.upper_nav,pre_entry)
    rows = []
    for stamp in [five.PEAK,trough]:
        end = stamp+pd.Timedelta(hours=1)
        fine = five.hour_envelope(stamp,equity.loc[end],legs,quotes,60_000)
        if (fine['observed_symbols'] != fine['active_symbols'] or fine['min_observed_close'] is None
                or fine['refined_lower'] < fine['previous_lower']-1e-4
                or fine['refined_upper'] > fine['previous_upper']+1e-4):
            raise ValueError('Minute source incomplete or inconsistent with five-minute marks')
        equity.loc[end,['lower_nav','upper_nav']] = [fine['refined_lower'],max(fine['refined_upper'],pre_entry.loc[end])]
        rows.append(fine)
    records = pd.DataFrame(rows)
    records.to_csv(five.EXP/'minute_refinement.csv',index=False)
    seen = np.r_[1000.,equity[['open_nav','open_exit_nav','nav']].to_numpy().ravel()]
    observed = max(100*(1-(seen/np.maximum.accumulate(seen)).min()),
                   100*(1-rows[1]['min_observed_close']/rows[0]['max_observed_close']))
    bound = five.max_drawdown(equity)
    if observed>bound+1e-7:
        raise ValueError('Timestamped drawdown exceeds the conservative interval')
    result = dict(start_usdt=1000.,final_usdt=equity.nav.iloc[-1],previous_5m_upper_pct=prior,
                  refined_upper_pct=bound,observed_lower_pct=observed,
                  passes_35pct_drawdown_upper=bool(bound<=35),
                  method='Two binding hours valued on synchronized 1m OHLC; other five-minute and hourly envelopes retained. Fills unchanged.')
    (five.EXP/'minute_summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(result)


if __name__ == '__main__':
    main()
