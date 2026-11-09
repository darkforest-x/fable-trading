"""Trade a benchmark basket on market-wide 12H capitulation and recovery."""
import json
from functools import lru_cache

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_okx_retired_account as archive
from yoyo.evaluation import cloud_close_pair_branch as pair

wallet = pair.wallet
EXP = pair.EXP.parent/'exp-cloud-close-breadth-branch-20261109-v1'
UNIVERSE = archive.EXP.parent/'exp-cloud-close-okx-directory-account-20261109-v1'
STEP = pair.STEP


@lru_cache(maxsize=None)
def ny(key):
    return pair.calendar._ny_week_window(int(key))


def classify(symbol,hour,bars):
    """Use only the fully observed NY Monday H1 highs/lows and each subsequently closed 12H close."""
    keys = bars.index.to_numpy(dtype='int64')
    weeks = {}
    values = []
    for key,close in zip(keys,bars.close):
        w = ny(key)
        if not w['monday_end']<=key<key+STEP<=w['expires']:
            continue
        week = w['week']
        if week not in weeks:
            actual = hour.loc[w['start']:w['monday_end']-1]
            if np.array_equal(actual.index,np.arange(w['start'],w['monday_end'],wallet.HOUR)):
                high,low = actual.high.max(),actual.low.min()
                if high>low>0:
                    width = high-low
                    weeks[week] = (low-.618*width,high+.618*width)
            weeks.setdefault(week,None)
        if weeks[week] is not None:
            lo,hi = weeks[week]
            values.append((int(key)+STEP,-1 if close<lo else 1 if close>hi else 0))
    return pd.Series(dict(values),name=symbol,dtype='float64')


def panel():
    orig = pd.read_csv(wallet.SOURCE/'input_coverage.csv')
    rows = []
    for e in orig.itertuples():
        root = wallet.TOP if e.cohort=='top20_reused' else wallet.SOURCE
        p = root/'inputs'/e.symbol
        h = pd.read_csv(p/'bars1h.csv.gz',index_col='time',usecols=['time','high','low'])
        k = pd.read_csv(p/'bars12h.csv.gz',index_col='time',usecols=['time','close'])
        rows.append(classify(e.symbol,h,k))
    cfg = json.loads((UNIVERSE/'config.json').read_text())
    ends = {s:pd.Timestamp(n['delivery_from_utc']).value//1_000_000 for n in cfg['notices'] for s in n['symbols']}
    for s,end in ends.items():
        h = pd.read_csv(archive.prices.CACHE/'series'/(s+'-USDT-SWAP')/'bars1h.csv.gz',index_col='time')
        h = h[h.index<end]
        k = archive.native.twelve_hour(h)
        rows.append(classify(s,h,k))
    x = pd.concat(rows,axis=1).sort_index()
    if x.shape[1]!=360 or not x.columns.is_unique:
        raise ValueError('Broad market tape does not contain the prescribed 360 distinct contracts')
    return x


def triggers(x):
    records = []
    seen = set()
    for t,v in x.iterrows():
        prior = t-STEP
        if prior not in x.index:
            continue
        w = ny(t-STEP)
        if w['week'] in seen or ny(t-2*STEP)['week']!=w['week']:
            continue
        previous = x.loc[prior]
        n = int(previous.notna().sum())
        if n<30:
            continue
        chosen = []
        for extreme in [-1,1]:
            impacted = previous[previous.eq(extreme)].index
            if len(impacted)*3<n:
                continue
            recovered = int(v.loc[impacted].eq(0).sum())
            observed = int(v.loc[impacted].notna().sum())
            if 2*recovered>=len(impacted):
                chosen.append(dict(week=w['week'],t=int(t),side=-extreme,universe=n,shocked=len(impacted),
                    next_observed=observed,recovered=recovered,tradable_same_week_delay=t+STEP<=w['expires']))
        if len(chosen)==1:
            records.append(chosen[0])
            seen.add(w['week'])
    return pd.DataFrame(records)


def trades(events,delay):
    names = ['BTC','ETH']
    series = {}
    for s in names:
        t,v = wallet.quotes(s)
        series[s] = pd.DataFrame(v,index=t,columns=archive.FIELDS)
    union = series['BTC'].index.intersection(series['ETH'].index)
    grid = union[(union>=wallet.START)&(union<wallet.END)].to_numpy()
    close = {}
    for s in names:
        # Use completed 12H close only after the bar's full 12 constituents exist.
        own = archive.native.twelve_hour(series[s].rename_axis('time')).close
        close[s] = own.set_axis(own.index+STEP)
    closed = pd.DataFrame(close).dropna()
    busy = -1
    rows = []
    missing = blocked = 0
    for key in events.itertuples():
        t = key.t+delay
        if t<=busy:
            blocked += 1
            continue
        at = grid.searchsorted(t)
        if at==len(grid) or grid[at]!=t:
            missing += 1
            continue
        basis = pd.Series({s:series[s].at[t,'open'] for s in names})
        deadline = t+6*24*wallet.HOUR
        observed = closed.loc[t+1:deadline]
        returns = (observed/basis-1).mean(axis=1)*key.side
        stopped = returns.index[returns<=-.05]
        exit_t = int(stopped[0]) if len(stopped) else deadline
        p = int(grid.searchsorted(exit_t))
        phase,reason = 'open','spread_stop' if len(stopped) else 'six_day'
        if p==len(grid):
            p = len(grid)-1
            phase,reason = 'close','sample_end_close'
        gaps = np.flatnonzero(np.diff(grid[at:p+1])>wallet.HOUR)
        if len(gaps):
            p = at+int(gaps[0])+1
            phase,reason = 'open','first_common_open_after_gap'
        end = int(grid[p])
        if end<t:
            raise ValueError('Exit precedes the observed benchmark entry')
        for slot,s in enumerate(names):
            rows.append(dict(event_id=key.week,parent_id=key.week,symbol=s,slot=slot,side=key.side,
                    entry_price=basis[s],entry_ms=t,exit_bar_ms=end,exit_price=series[s].at[end,phase],
                    exit_phase=phase,exit_reason=reason,exit_fee_rate=wallet.TAKER))
        busy = end
    return pd.DataFrame(rows),dict(busy=blocked,missing=missing)


def scenario(rows,label,slip=0.):
    data = wallet.prepare(None,2,assigned=(rows,{}))
    legs,eq = wallet.account(data,slip=slip,weights=(.5,.5))
    g = legs.groupby('event_id')
    funds = g.agg(entry_ms=('entry_ms','first'),exit_bar_ms=('exit_bar_ms','max'),
                  cash_pnl=('cash_pnl','sum'),notional=('notional','sum'),side=('side','first'))
    funds['net_return'] = funds.cash_pnl/funds.notional
    notionals = (legs.quantity*legs.entry_price).groupby(legs.event_id)
    if (not np.allclose(notionals.min(),notionals.max(),rtol=1.e-8) or
        not np.isclose(1000+funds.cash_pnl.sum(),eq.nav.iat[-1],atol=.001)):
        raise ValueError('Portfolio cash or equal benchmark sizing does not reconcile')
    funds.to_csv(EXP/f'funded_{label}.csv.gz')
    if label in ('full','stress'):
        eq.to_csv(EXP/f'hourly_{label}.csv.gz',index_label='hour_end_utc')
    return {p:wallet.summarize(funds,eq,*bounds) for p,bounds in pair.periods.PERIODS.items()}


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Completed breadth mechanism study')
    x = panel()
    signals = triggers(x)
    signals.to_csv(EXP/'signals.csv',index=False)
    paired = signals[signals.tradable_same_week_delay]
    results = {}
    for label,events,delay in [('full',signals,0),('paired',paired,0),('later',paired,STEP)]:
        rows,skips = trades(events,delay)
        results[label] = dict(metrics=scenario(rows,label),skips=skips)
        if label=='full':
            results['stress'] = dict(metrics=scenario(rows,'stress',.0005),skips=skips)
        print(label,results[label]['metrics']['all'],flush=True)
    (EXP/'summary.json').write_text(json.dumps(dict(contracts=x.shape[1],recovery_events=len(signals),
           paired_candidate_weeks=len(paired),results=results,scope='Historical 360-contract panel, prior-week anchor causal; benchmark cash before funding.'),
           indent=2,allow_nan=False)+'\n')


if __name__=='__main__':
    main()
