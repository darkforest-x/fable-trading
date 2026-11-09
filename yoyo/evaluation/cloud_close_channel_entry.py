"""Enter completed 12H breakouts of a prior 20-day high/low in the native V1.7 EMA direction."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_notice_tail_risk as source
from yoyo.evaluation import cloud_close_breakout_branch as branch

wallet = source.correction.wallet
engine = branch.engine
STEP = branch.STEP
EXP = source.EXP.parent/'exp-cloud-close-channel-entry-20261109-v1'
CACHE = wallet.ROOT/'data/research/cloud_close_channel_entry'


def signals(bars,*,reclaim=False):
    """Prior 40 contiguous high/low bars exclude the current bar; at least 180 prior contiguous 12H bars."""
    group = bars.index.to_series().diff().ne(STEP).cumsum()
    def channel(values,side):
        if side>0:
            return values.transform(lambda s:s.shift().rolling(40,min_periods=40).max())
        return values.transform(lambda s:s.shift().rolling(40,min_periods=40).min())
    upper = channel(bars.high.groupby(group),1)
    lower = channel(bars.low.groupby(group),-1)
    raw = pd.Series(0,index=bars.index)
    if reclaim:
        inside = bars.close.gt(lower)&bars.close.lt(upper)
        tagged_low = bars.low.lt(lower)
        tagged_high = bars.high.gt(upper)
        raw[inside & tagged_low & ~tagged_high] = 1
        raw[inside & tagged_high & ~tagged_low] = -1
        anchor = lower.where(raw.gt(0),upper)
    else:
        raw[bars.close.gt(upper)] = 1
        raw[bars.close.lt(lower)] = -1
        anchor = upper.where(raw.gt(0),lower)
    first = raw.ne(raw.groupby(group).shift()) & raw.ne(0) & bars.groupby(group).cumcount().ge(180)
    return pd.DataFrame(dict(side=raw.loc[first],anchor=anchor.loc[first]))


def price_symbol(sym,src,*,reclaim=False):
    h,bars = source.correction.parent.repair.market.frames(src)
    if len(bars)<=180:
        return pd.DataFrame()
    q,clock,_ = engine._prepare_symbol(bars,h)
    events = []
    for key,signal in signals(bars,reclaim=reclaim).iterrows():
        side = int(signal.side)
        entry = key+STEP
        i = int(clock.searchsorted(entry))
        if not wallet.START<=entry<wallet.END or i==len(q) or clock[i]!=entry or not (q[i,5]*side>0 and q[i,6]==0):
            continue
        price = float(q[i,0])
        stop,_ = branch.fixed_stop(sym,side,float(signal.anchor))
        risk = side*(price-stop)
        if risk<=0:
            continue
        parent = f'{sym}|{entry}|{side}'
        for lag in [0,1]:
            t = entry+lag*STEP
            j = int(clock.searchsorted(t))
            if j>=len(q) or clock[j]!=t or t>=wallet.END or q[j,6]!=0:
                continue
            ep = float(q[j,0])
            barrier = stop if lag==0 else ep*(1-side*risk/price)
            end,reason = engine._safe_path_end(j,side,q,clock)
            events.append(dict(symbol=sym,week='',parent_id=parent,event_id=parent+'|'+str(lag),side=side,
                  is_control=bool(lag),entry_i=j,entry_price=ep,stop_price=barrier,risk_distance=side*(ep-barrier),
                  risk_fraction=risk/price,source_row_number=0,key_open_ms=key,confirmation_ms=t,entry_ms=t,
                  end_i=end,path_upper_bound_reason=reason))
    rows,_ = engine._simulate_symbol(events,q,clock,(0.,),64)
    return pd.DataFrame(rows)


def main(*,reclaim=False):
    directory = EXP.parent/'exp-cloud-close-channel-reclaim-20261109-v1' if reclaim else EXP
    cache = CACHE.parent/'cloud_close_channel_reclaim' if reclaim else CACHE
    if (directory/'summary.json').exists():
        raise FileExistsError('Completed full 20-day channel study')
    _,quotes,src = source.inputs()
    cache.mkdir(exist_ok=True,parents=True)
    books = []
    for n,(sym,s) in enumerate(sorted(src.items())):
        path = cache/(sym+'.csv.gz')
        if not path.exists() and not path.with_suffix('.empty').exists():
            rows = price_symbol(sym,s,reclaim=reclaim)
            if rows.empty:
                path.with_suffix('.empty').touch()
            else:
                if not rows.entry_status.eq('executed').all():
                    raise ValueError('Unexecuted channel event reached the portfolio')
                tmp = path.with_suffix('.tmp.gz')
                rows.to_csv(tmp,index=False)
                tmp.replace(path)
        if path.exists():
            books.append(pd.read_csv(path,dtype={'symbol':str},float_precision='round_trip'))
        if n%50==0:
            print('priced markets',n,flush=True)
    paths = pd.concat(books,ignore_index=True)
    paths.to_csv(directory/'paths.csv.gz',index=False)
    prime = paths[~paths.is_control]
    late = paths[paths.is_control]
    paired = prime[prime.parent_id.isin(late.parent_id)]
    out = {}
    for name,events in [('full',prime),('matched',paired),('delayed',late)]:
        data = wallet.prepare(events,9,quote_loader=quotes)
        for slip in ([0.,.0005] if name=='full' else [0.]):
            key = name+('_stress' if slip else '')
            funds,curve = wallet.account(data,slip=slip)
            if not np.isclose(1000+funds.cash_pnl.sum(),curve.nav.iat[-1],atol=.001):
                raise ValueError('Native 20-day channel cash failed to reconcile')
            funds.to_csv(directory/f'funded_{key}.csv.gz',index=False)
            if key in ['full','full_stress']:
                curve.to_csv(directory/f'hourly_{key}.csv.gz',index_label='hour_end_utc')
            out[key] = dict(metrics={p:wallet.summarize(funds,curve,*v) for p,v in branch.PERIODS.items()},
                            skips=data['skips'],missing_holding_hours=data['gap_marks'],
                            eligible=len(events),preterminal_tail_exits=int((funds.exit_reason.eq('series_tail_close')&
                                 funds.exit_bar_ms.lt(wallet.END-wallet.HOUR)).sum()))
            print(key,out[key]['metrics']['all'],flush=True)
        del data
        gc.collect()
    source.correction.archive.prices.safe_json(directory/'summary.json',dict(arms=out,universe=len(src),
               scope='Frozen historical panel and H1 terminal/gap exit convention; funding, liquidity and historical margins unmodeled.'))


if __name__=='__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--reclaim',action='store_true')
    main(reclaim=parser.parse_args().reclaim)
