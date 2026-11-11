"""Exploratory: measure the penetration of the signal candle, known at entry.

Reuse the 5% stop ledger unchanged. The shallow account was chosen after
reviewing the descriptive buckets and is not independently validated.
"""
import numpy as np
import pandas as pd

import strategy_account as account
from strategy_review import OUT, add_rules, photo_coverage
from strategy_signals import CUTOFF, HOUR


def main():
    signals = add_rules(pd.read_csv(OUT/'signals.csv.gz')).reset_index(names='signal_row')
    ledger = pd.read_csv(OUT/'fixed_trades_sl5.csv.gz')
    d = ledger[ledger.owner_style & ledger.closed_flag].merge(
        signals[['signal_row','tip_outside_pct']],on='signal_row',validate='one_to_one')
    d['penetration_pct'] = pd.cut(d.tip_outside_pct,[-np.inf,.5,1,2,5,np.inf])
    d['period'] = np.where(pd.to_datetime(d.entry_ms,unit='ms',utc=True).dt.year<2025,'before_2025','2025_and_after')
    both = pd.concat([d,d.assign(period='all')],ignore_index=True)
    stats = both.groupby(['penetration_pct','period'],observed=True).net_return.agg(['size','mean'])
    stats['mean_net_pct'] = 100*stats.pop('mean')
    stats.to_csv(OUT/'proximity_sl5_buckets.csv')
    keep = signals.owner_style & signals.tip_outside_pct.le(.5)
    preview = signals.copy()
    preview['owner_style'] = keep
    photos = photo_coverage(preview)
    quotes = account.QuoteBook(account._load_quote_cache(set(signals.loc[keep,'symbol']),CUTOFF))
    end = CUTOFF//HOUR*HOUR
    candidates = account._make_candidates(ledger[ledger.signal_row.isin(signals.loc[keep,'signal_row'])],quotes,end)
    _,_,metrics = account._account_one(candidates,'owner_style_tip_within_0.5pct','sl5',quotes,end)
    metrics['before_move_owner_photos_matched'] = int(photos.owner_style.sum())
    pd.DataFrame([metrics]).to_csv(OUT/'proximity_sl5_account.csv',index=False)
    print(metrics['final_equity_usdt'],metrics['max_drawdown_hourly_close_pct'],metrics['before_move_owner_photos_matched'])


if __name__=='__main__':
    main()
