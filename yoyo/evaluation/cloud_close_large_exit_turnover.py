"""Compare the account's actual large exit quantities to official minute and hour turnover."""
import time
from zipfile import BadZipFile

import numpy as np
import pandas as pd
import requests

from yoyo.evaluation import cloud_close_cash_giveback as cash
from yoyo.evaluation import cloud_close_entry_turnover as volume

EXP=cash.ROOT/'exp-cloud-close-large-exit-turnover-20261109-v1'


def select(data):
    fills=pd.read_csv(cash.EXP/'orders_median.csv.gz')
    funds=pd.read_csv(cash.SOURCE/'aligned/funded_median.csv.gz',index_col='event_id')
    rows=fills.loc[fills.net_units.lt(0)].rename(columns={'underlying_event_id':'event_id'})
    legs=data['selected'][['event_id','slot','symbol','exit_reason','exit_bar_ms']].rename(columns={'slot':'col'})
    rows=rows.merge(legs,on=['event_id','col'],how='left',validate='many_to_one')
    if rows.symbol.isna().any():
        raise ValueError('Native exit lost parent identity')
    admin=rows.exit_reason.str.contains('settle|delivery',case=False)&rows.hour.eq(rows.exit_bar_ms)&rows.stage.ne(2)
    excluded=int(admin.sum())
    rows=rows[~admin].copy()
    rows['exit_notional']=(rows.underlying_cash+rows.fees).abs()
    rows['opening_notional']=rows.exit_notional.where(rows.stage.isin([1,2]),0.)
    rows['later_notional']=rows.exit_notional.where(rows.stage.eq(3),0.)
    agg=rows.groupby(['symbol','hour']).agg(total=('exit_notional','sum'),opening=('opening_notional','sum'),
                                 later=('later_notional','sum'),fills=('exit_notional','size')).reset_index()
    if not np.allclose(agg.total,agg.opening+agg.later):
        raise ValueError('Unrecognized native execution phase')
    agg['top_open']=agg.index.isin(agg[agg.opening>0].nlargest(20,'opening').index)
    agg['top_later']=agg.index.isin(agg[agg.later>0].nlargest(20,'total').index)
    best=funds.nlargest(10,'cash_pnl')
    endings=rows[rows.event_id.isin(best.index)].groupby('event_id').hour.max()
    keys=set(zip(best.loc[endings.index,'symbol'],endings))
    agg['winner_final']=[(s,t) in keys for s,t in zip(agg.symbol,agg.hour)]
    chosen=agg[agg[['top_open','top_later','winner_final']].any(axis=1)]
    return chosen,excluded,best.cash_pnl.sum()/funds.cash_pnl.sum()


def inspect(row,quotes):
    bars,url=volume.minute.minute_quote(row.symbol,int(row.hour),quotes)
    if bars is None:
        return dict(status=url)
    amounts,source=volume.volume_hour(row.symbol,int(row.hour),bars,url)
    if amounts is None:
        return dict(status=source,price_source=url)
    quote,base=amounts.quote,amounts.base
    unit=quote.div(base.where(base>0))
    if (not np.isfinite(amounts[['quote','base']]).all().all() or (amounts[['quote','base']]<0).any().any()
            or (base.eq(0)&quote.ne(0)).any() or ((unit<bars.low*(1-1e-5))|(unit>bars.high*(1+1e-5))).any()):
        return dict(status='invalid_or_unreconciled_traded_volume',price_source=url)
    minute=float(quote.iat[0])
    whole=float(quote.sum())
    return dict(status='observed',first_minute_usdt=minute,hour_usdt=whole,price_source=url,volume_source=source,
                opening_exceeds_first_minute=bool(row.opening>minute+1e-6),
                exits_exceed_whole_hour=bool(row.total>whole+1e-6))


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed sampled exit liquidity audit')
    data,_,quotes,*_=cash.account()
    sample,admin,profit_share=select(data)
    records=[]
    for row in sample.itertuples(index=False):
        for i in range(3):
            try:
                evidence=inspect(row,quotes)
                break
            except (requests.RequestException,BadZipFile,TimeoutError):
                if i==2:
                    raise
                time.sleep(1+i)
        records.append(dict(row._asdict(),**evidence))
    audit=pd.DataFrame(records)
    audit.to_csv(EXP/'observed_exit_windows.csv.gz',index=False)
    complete=audit[audit.status.eq('observed')]
    cash.queue.repair.archive.prices.safe_json(EXP/'summary.json',dict(selected=len(audit),observed=len(complete),
        states=audit.status.value_counts().to_dict(),administrative_child_fills_excluded=admin,
        top10_parent_share_of_total_cash_profit=float(profit_share),
        open_exceeds_minute=int(complete.opening_exceeds_first_minute.sum()),
        total_exceeds_hour=int(complete.exits_exceed_whole_hour.sum()),
        highest_finite_open_fraction=float((complete.opening/complete.first_minute_usdt.where(complete.first_minute_usdt>0)).max()),
        highest_finite_hour_fraction=float((complete.total/complete.hour_usdt.where(complete.hour_usdt>0)).max())))


if __name__=='__main__':
    main()
