"""A neutral pair from relative displacement around completed V1.7 Monday anchors."""
import json
from functools import lru_cache

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_leadership as leadership
from yoyo.evaluation import cloud_close_reward_paths as calendar
from yoyo.evaluation import cloud_close_tenr_exposure as periods

wallet = leadership.wallet
EXP = wallet.ROOT/'experiments/active/exp-cloud-close-pair-branch-20261109-v1'
STEP = 12*wallet.HOUR
CFG = json.loads((EXP/'config.json').read_text())
SYMBOLS = CFG['symbols']


def trend(close):
    """EMA20/EMA50 on observed 12H closes up to that timestamp, re-warmed after any gap."""
    groups = close.index.to_series().diff().ne(STEP).cumsum()
    def spread(part):
        return part.ewm(span=20,adjust=False,min_periods=50).mean()-part.ewm(span=50,adjust=False,min_periods=50).mean()
    return close.groupby(groups).transform(spread)


def asset_inputs(symbol,coverage):
    root = wallet.TOP if coverage.at[symbol,'cohort']=='top20_reused' else wallet.SOURCE
    p = root/'inputs'/symbol/'bars12h.csv.gz'
    bars = pd.read_csv(p,index_col='time',usecols=['time','close'],float_precision='round_trip')
    clock,ohlc = wallet.quotes(symbol)
    strength = trend(bars.close)
    anchors = {}
    rows = []
    for t,close in bars.close.items():
        w = calendar._ny_week_window(int(t))
        if not w['end']<=t<t+STEP<=w['expires'] or not np.isfinite(strength.at[t]):
            continue
        week = w['week']
        if week not in anchors:
            start,stop = clock.searchsorted([w['start'],w['end']])
            observed = clock[start:stop]
            if np.array_equal(observed,np.arange(w['start'],w['end'],wallet.HOUR)):
                high,low = float(ohlc[start:stop,1].max()),float(ohlc[start:stop,2].min())
                if high>low:
                    anchors[week] = (high,low)
            anchors.setdefault(week,None)
        if anchors[week] is None:
            continue
        high,low = anchors[week]
        rows.append(dict(week=week,entry_ms=int(t)+STEP,symbol=symbol,close=close,trend=strength.at[t],
                         distance=(close-(high+low)/2)/(high-low)))
    return pd.DataFrame(rows),bars.close.set_axis(bars.index+STEP)


def first_keys(features):
    """Only the signal bar and completed Monday are eligible to create a weekly trigger."""
    seen = set()
    choices = []
    for t,part in features.groupby('entry_ms',sort=True):
        if len(part)<4 or len(set(part.week))!=1 or part.week.iat[0] in seen:
            continue
        long = part[(part.distance>0)&(part.trend>0)].sort_values(['distance','symbol'],ascending=[False,True])
        short = part[(part.distance<0)&(part.trend<0)].sort_values(['distance','symbol'])
        if long.empty or short.empty or long.distance.iat[0]-short.distance.iat[0]<2.236:
            continue
        week = part.week.iat[0]
        seen.add(week)
        choices.append(dict(t=int(t),week=week,long=long.symbol.tolist(),short=short.symbol.tolist()))
    return choices


def build_events(choices,closes,seed=None):
    rng = np.random.default_rng(seed)
    records,eligible = [],[]
    skips = dict(busy=0,missing_entry=0)
    busy_until = -1
    @lru_cache(maxsize=None)
    def pair_data(a,b):
        close = closes[a].div(closes[b]).dropna()
        ax,ap = wallet.quotes(a)
        bx,bp = wallet.quotes(b)
        both = np.intersect1d(ax,bx,assume_unique=True)
        sub = both[(both>=wallet.START)&(both<wallet.END)]
        hi = tuple(np.searchsorted(q,sub) for q in (ax,bx))
        return close,trend(close),sub,ap[hi[0]],bp[hi[1]]
    for pick in choices:
        a = pick['long'][0] if seed is None else str(rng.choice(pick['long']))
        b = pick['short'][0] if seed is None else str(rng.choice(pick['short']))
        t = pick['t']
        eligible.append(dict(t=t,week=pick['week'],long=a,short=b,original=(a,b)==(pick['long'][0],pick['short'][0])))
        if t<=busy_until:
            skips['busy'] += 1
            continue
        ratios,delta,clock,left,right = pair_data(a,b)
        at = int(clock.searchsorted(t))
        if at==len(clock) or clock[at]!=t:
            skips['missing_entry'] += 1
            continue
        deadline = t+28*24*wallet.HOUR
        following = ratios[(ratios.index>t)&(ratios.index<=deadline)]
        stops = (following<=ratios.at[t]*.94)|(delta.reindex(following.index)<0)
        first = int(following.index[np.flatnonzero(stops)[0]]) if stops.any() else deadline
        reason = 'ratio_or_ema_close' if stops.any() else '28d_timeout'
        endidx = int(clock.searchsorted(first))
        if endidx>=len(clock):
            endidx = len(clock)-1
            reason = 'sample_end_close'
        phase = 'close' if reason=='sample_end_close' else 'open'
        gaps = np.flatnonzero(np.diff(clock[at:endidx+1])>wallet.HOUR)
        if len(gaps):
            endidx = at+int(gaps[0])+1
            phase,reason = 'open','first_common_open_after_gap'
        elif phase=='open' and int(clock[endidx])!=first:
            raise ValueError('Unexplained late exit without a native hourly gap')
        end = int(clock[endidx])
        key = f'{pick["week"]}|{a}|{b}'
        for slot,(name,side,bars) in enumerate([(a,1,left),(b,-1,right)]):
            records.append(dict(event_id=key,parent_id=key,symbol=name,side=side,slot=slot,entry_ms=t,
                        exit_bar_ms=end,entry_price=float(bars[at,0]),exit_price=float(bars[endidx,0 if phase=='open' else 3]),
                        exit_phase=phase,exit_reason=reason,exit_fee_rate=wallet.TAKER))
        busy_until = end
    return pd.DataFrame(records),pd.DataFrame(eligible),skips


def scenario(name,events,slip=0.):
    data = wallet.prepare(None,2,assigned=(events,{}))
    legs,equity = wallet.account(data,slip=slip,weights=(.5,.5))
    allocation = legs.quantity*legs.entry_price
    raw_values = allocation.groupby(legs.event_id)
    if not np.allclose(raw_values.max(),raw_values.min(),rtol=1.e-9,atol=.00001):
        raise ValueError('Pair entries did not reserve the same raw notional')
    group = legs.groupby('event_id',sort=False)
    pairs = group.agg(entry_ms=('entry_ms','first'),exit_bar_ms=('exit_bar_ms','max'),
                      cash_pnl=('cash_pnl','sum'),notional=('notional','sum'),reason=('exit_reason','first'))
    pairs['net_return'] = pairs.cash_pnl/pairs.notional
    if not group.size().eq(2).all() or not np.isclose(1000+pairs.cash_pnl.sum(),equity.nav.iat[-1],atol=.005):
        raise ValueError('Pair/leg cash does not reconcile to the native account')
    if name in ('extremes','extremes_5bp'):
        legs.to_csv(EXP/f'legs_{name}.csv.gz',index=False)
        pairs.to_csv(EXP/f'pairs_{name}.csv.gz')
        equity.to_csv(EXP/f'hourly_{name}.csv.gz',index_label='hour_end_utc')
    return dict(metrics={p:wallet.summarize(pairs,equity,*bounds) for p,bounds in periods.PERIODS.items()},
                exit_types=pairs.reason.value_counts().to_dict(),unquoted_leg_hours=data['gap_marks'])


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Completed cloud pair mechanism screen')
    coverage = pd.read_csv(wallet.SOURCE/'input_coverage.csv').set_index('symbol')
    features,closes = [],{}
    for symbol in SYMBOLS:
        part,close = asset_inputs(symbol,coverage)
        features.append(part)
        closes[symbol] = close
    features = pd.concat(features,ignore_index=True)
    choices = first_keys(features)
    rows = []
    for item in choices:
        rows.append(dict(entry_ms=item['t'],week=item['week'],long_candidates=','.join(item['long']),
                         short_candidates=','.join(item['short'])))
    pd.DataFrame(rows).to_csv(EXP/'weekly_choices.csv',index=False)
    cases = {}
    for seed in [None,1029,1030,1031,1032,1033]:
        name = 'extremes' if seed is None else str(seed)
        events,picks,skips = build_events(choices,closes,seed)
        cases[name] = dict(**scenario(name,events),skips=skips,changed_pair_clocks=int((~picks.original).sum()))
        if seed is None:
            cases['extremes_5bp'] = scenario('extremes_5bp',events,.0005)
        print(name,cases[name]['metrics']['all'],flush=True)
    (EXP/'summary.json').write_text(json.dumps(dict(branch='dollar_neutral_relative_momentum',symbols=SYMBOLS,
        weekly_trigger_clocks=len(choices),cases=cases,scope='Ten surviving majors with observed native OKX histories; costs exclude funding.'),
        indent=2,allow_nan=False)+'\n')


if __name__=='__main__':
    main()
