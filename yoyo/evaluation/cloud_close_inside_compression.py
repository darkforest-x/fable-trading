"""Compare 12H cloud-trend thrusts from ordinary and seven-bar narrow inside candles."""
import gc
import json
from functools import lru_cache

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_weak_rally_branch as market
from yoyo.evaluation import cloud_close_waves_wallet as repair

wallet = market.wallet
EXP = market.EXP.parent / 'exp-cloud-close-inside-compression-20261109-v1'
CACHE = market.CACHE.parent / 'cloud_close_inside_compression'


def signals(bars,features):
    """Use high/low of the prior seven contiguous bars, the current 12H close and its EMA20/50."""
    spans = bars.high-bars.low
    inside = bars.high.le(bars.high.shift()) & bars.low.ge(bars.low.shift()) & spans.lt(spans.shift())
    narrow = spans.eq(spans.rolling(7).min()) & bars.index.to_series().diff(6).eq(6*market.STEP)
    ema = np.sign(features['ema20']-features['ema50'])
    long = bars.close.gt(bars.high.shift()) & (ema>0)
    short = bars.close.lt(bars.low.shift()) & (ema<0)
    approved = (long|short) & features['ema_ready'] & inside.shift(fill_value=False)
    approved &= bars.index.to_series().diff(2).eq(2*market.STEP)
    seen = {k:set() for k in ('ordinary','narrow')}
    rows = []
    for i in np.flatnonzero(approved.to_numpy()):
        key = int(bars.index[i])
        t = key + market.STEP
        week = market.breadth.ny(key)
        if not (wallet.START<=t<wallet.END and week['monday_end']<=key-market.STEP and t<=week['expires']):
            continue
        arms = []
        for label,condition in [('ordinary',True),('narrow',narrow.iat[i-1])]:
            if condition and week['week'] not in seen[label]:
                seen[label].add(week['week'])
                arms.append(label)
        if arms:
            side = 1 if long.iat[i] else -1
            stop = bars.low.iat[i-1] if side==1 else bars.high.iat[i-1]
            rows.append((key,week['week'],side,stop,arms))
    return rows


def price(symbol,source):
    h,bars = market.frames(source)
    if len(bars)<50:
        return pd.DataFrame()
    q,clock,metadata = market.engine._prepare_symbol(bars,h)
    plans = signals(bars,metadata['features'])
    events,labels = [],{}
    for key,week,side,stop,arms in plans:
        t = key+market.STEP
        i = int(clock.searchsorted(t))
        if i==len(clock) or clock[i]!=t:
            continue
        px = q[i,0]
        risk = side*(px-stop)
        if risk<=0:
            continue
        event_id = f'{symbol}|{key}|{side}'
        last,why = market.engine._safe_path_end(i,side,q,clock)
        events.append(dict(event_id=event_id,parent_id=event_id,symbol=symbol,week=week,side=side,
            is_control=False,source_row_number=len(events),entry_i=i,entry_ms=t,confirmation_ms=t,
            key_open_ms=key,entry_price=px,stop_price=stop,risk_distance=risk,risk_fraction=risk/px,
            end_i=last,path_upper_bound_reason=why))
        labels[event_id] = arms
    if not events:
        return pd.DataFrame()
    rows,_ = market.engine._simulate_symbol(events,q,clock,(0.,),96)
    frame = pd.DataFrame(rows)
    if not frame.entry_status.eq('executed').all():
        raise ValueError(f'Unexpected native order status for {symbol}')
    for name in ('narrow','ordinary'):
        frame[name] = [name in labels[i] for i in frame.event_id]
    return frame


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed inside-candle compression study')
    src = market.sources()
    src['WAVES'] = (repair.WAVES,None,src['WAVES'][2])
    CACHE.mkdir(parents=True,exist_ok=True)
    tape = []
    for n,(sym,source) in enumerate(sorted(src.items()),1):
        dest = CACHE/(sym+'.csv.gz')
        empty = CACHE/(sym+'.empty')
        if not (dest.exists() or empty.exists()):
            paths = price(sym,source)
            if paths.empty:
                empty.touch()
            else:
                temp = dest.with_suffix('.tmp.gz')
                paths.to_csv(temp,index=False)
                temp.replace(dest)
        if dest.exists():
            tape.append(pd.read_csv(dest,dtype={'symbol':str},float_precision='round_trip'))
        if n%60==0:
            print('priced markets',n,flush=True)
    book = pd.concat(tape,ignore_index=True)
    if not book.event_id.is_unique:
        raise ValueError('Duplicate source event in inside-breakout paths')

    @lru_cache(maxsize=None)
    def quotes(symbol):
        path,_,end = src[symbol]
        h = pd.read_csv(path,usecols=['time',*market.breadth.archive.FIELDS],float_precision='round_trip')
        h = h[h.time.lt(end)]
        return h.time.to_numpy(),h[market.breadth.archive.FIELDS].to_numpy()

    result = {}
    for arm in ('narrow','ordinary'):
        events = book[book[arm]]
        if events.duplicated(['symbol','week']).any():
            raise ValueError('Duplicate symbol-week admission')
        data = wallet.prepare(events,9,quote_loader=quotes)
        for slip in [0.,.0005]:
            key = arm+('_stress' if slip else '')
            funds,eq = wallet.account(data,slip=slip)
            if not np.isclose(1000+funds.cash_pnl.sum(),eq.nav.iat[-1],rtol=1e-9,atol=.005):
                raise ValueError('Inside-bar wallet cash does not reconcile')
            abnormal = funds[funds.gap_flag | funds.exit_reason.eq('stale_12h_markout') |
                             (funds.exit_reason.eq('series_tail_close') & funds.exit_bar_ms.lt(wallet.END-wallet.HOUR))]
            funds.to_csv(EXP/f'funded_{key}.csv.gz',index=False)
            eq.to_csv(EXP/f'hourly_{key}.csv.gz',index_label='hour_end_utc')
            result[key] = dict(metrics={p:wallet.summarize(funds,eq,*bounds)
                              for p,bounds in market.breadth.pair.periods.PERIODS.items()},
                              opportunities=len(events),gap_hours=data['gap_marks'],
                              uncertain_exits=abnormal.exit_reason.value_counts().to_dict())
            print(key,result[key]['metrics']['all'],flush=True)
        del data
        gc.collect()
    (EXP/'summary.json').write_text(json.dumps(dict(contracts=len(src),cases=result),indent=2,allow_nan=False)+'\n')


if __name__=='__main__':
    main()
