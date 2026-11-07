"""Rebound outcomes by same-bar lower first-touch breadth and new names in the next 30 minutes.

The initial breadth is known at the bar close. The six following completed bars determine
whether a different symbol first touched its lower level. Buy at the next open, sell at
the open 24h after the initial close. Only outcome labels use future target prices.
Non-event controls match symbol, month, UTC six-hour block, prior-hour return direction
at the initial close and the trailing 12 closed 5m log-return volatility bin at decision.
"""
import json

import numpy as np
import pandas as pd

from yoyo.data import binance_um_archives as archives
from yoyo.data import ma_snapshot_inputs as api
from yoyo.evaluation import monday_breadth_alarm_census as census
from yoyo.evaluation import monday_breadth_bar_counts as counts
from yoyo.evaluation import monday_breadth_reversal_window as base
from yoyo.evaluation.monday_breadth_reversal_wait import clustered_interval

OUT = census.ACTIVE/'exp-monday-breadth-bar-rebounds-20261107-v1'
INPUT = base.MARKET/'breadth_rebound_targets_20261107'
ASSETS = ('BTC','ETH','SOL')
STEP = pd.Timedelta(minutes=5)
CUTS = [0,1,4,9,24,34,49,99,199,np.inf]
LABELS = ['1','2-4','5-9','10-24','25-34','35-49','50-99','100-199','200+']


def bars_and_followers():
    _, full = census.ledger_rows()
    groups = full[full.side.eq(1)].groupby('touch').symbol.agg(set).sort_index()
    previous = pd.read_csv(counts.OUT/'bars_with_touches.csv.gz',parse_dates=['known_bjt'])
    reference = previous[previous.lower_coins.gt(0)].set_index('known_bjt')
    reference.index = reference.index.tz_convert('UTC')
    rows = []
    times = groups.index
    for bar,symbols in groups.items():
        stop = times.searchsorted(bar+6*STEP,side='right')
        future = groups.iloc[times.searchsorted(bar,side='right'):stop]
        new = set().union(*future) - symbols
        rows.append((bar+STEP,len(symbols),len(new)))
    events = pd.DataFrame(rows,columns=['known','initial','new_coins']).set_index('known')
    assert events.index.equals(reference.index) and events.initial.eq(reference.lower_coins).all()
    # Last complete 5m bar of the interval used to build the first-touch ledger.
    assert events.index.max()+6*STEP <= pd.Timestamp('2026-10-28T00:00:00Z')
    events['alarm'] = reference.lower_alarm
    events['cooldown'] = events.initial.ge(25) & ~events.alarm
    events['band'] = pd.cut(events.initial,CUTS,labels=LABELS)
    events['continued'] = events.new_coins.gt(0)
    excludes = previous[(previous.lower_coins>=25)|(previous.upper_coins>=25)].known_bjt.dt.tz_convert('UTC')
    assert events.cooldown.sum()==33 and events.initial.ge(25).sum()==185
    return events.reset_index(), excludes


def october_prices(asset):
    """Cache official native October OHLCV; cover the missing archive day from the same venue."""
    symbol = asset+'USDT'
    path = INPUT/(symbol+'.csv.gz')
    manifest = INPUT/(symbol+'.json')
    if not (path.is_file() and manifest.is_file()):
        month,receipt = archives._download_month(symbol=symbol,month='2026-10',interval='5m',download_dir=INPUT/'downloads')
        if month is None:
            raise ValueError('Missing Binance UM monthly archive: '+symbol)
        expected = pd.date_range('2026-10-01','2026-11-01',freq=STEP,tz='UTC',inclusive='left')
        month = month.set_index('open_time')
        missing = expected.difference(month.index)
        receipt['rest_gaps'] = []
        if len(missing):
            blocks = pd.Series(missing).groupby(pd.Series(missing).diff().ne(STEP).cumsum())
            client = api.Client('binance',INPUT)
            for _,gap in blocks:
                # Read both adjacent archive bars too, to check feed identity at both joins.
                start,end = gap.iloc[0]-STEP,gap.iloc[-1]+STEP
                stamps = pd.date_range(start,end,freq=STEP)
                if len(stamps)>1500:
                    raise ValueError('REST gap too large')
                raw,proof = client.get(dict(symbol=symbol,interval='5m',startTime=start.value//10**6,
                                            endTime=end.value//10**6,limit=len(stamps)))
                fill = api.canonical([[int(r[0]),*[float(x) for x in r[1:6]]] for r in raw])
                api.validate(fill,5,(end+STEP).value//10**6)
                fill = fill.set_index('open_time')
                if not fill.index.equals(stamps):
                    raise ValueError('Incomplete Binance REST response')
                cols = ['open','high','low','close','volume']
                if not np.allclose(fill.loc[[start,end],cols],month.loc[[start,end],cols],rtol=1e-10,atol=1e-10):
                    raise ValueError('Archive and REST boundary mismatch')
                month = pd.concat([month,fill.loc[gap.to_list()]]).sort_index()
                receipt['rest_gaps'].append(proof)
        if not month.index.equals(expected):
            raise ValueError('October clock coverage incomplete')
        month.reset_index().to_csv(path,index=False)
        manifest.write_text(json.dumps(receipt,indent=2,default=str)+'\n')
    return pd.read_csv(path,usecols=['open_time','open','close'],parse_dates=['open_time']).set_index('open_time')


def prices(asset):
    prefix = base.market_prices(asset)
    missing = prefix.index[prefix.isna().any(axis=1)]
    if len(missing):
        symbol = asset+'USDT'
        blocks = pd.Series(missing).groupby(pd.Series(missing).diff().ne(STEP).cumsum())
        proofs = []
        for _,gap in blocks:
            start,end = gap.iloc[0]-STEP,gap.iloc[-1]+STEP
            times = pd.date_range(start,end,freq=STEP)
            if len(times)>1500:
                raise ValueError('Historical gap exceeds Binance request limit')
            key = f'{symbol}-{start:%Y%m%d}-{end:%Y%m%d}'
            cache = INPUT/(key+'.csv.gz')
            receipt = INPUT/(key+'.json')
            if not (cache.is_file() and receipt.is_file()):
                INPUT.mkdir(parents=True,exist_ok=True)
                raw,proof = api.Client('binance',INPUT).get(dict(symbol=symbol,interval='5m',
                    startTime=start.value//10**6,endTime=end.value//10**6,limit=len(times)))
                quotes = api.canonical([[int(r[0]),*[float(x) for x in r[1:6]]] for r in raw])
                api.validate(quotes,5,(end+STEP).value//10**6)
                quotes.to_csv(cache,index=False)
                receipt.write_text(json.dumps(proof,indent=2)+'\n')
            fill = pd.read_csv(cache,usecols=['open_time','open','close'],parse_dates=['open_time']).set_index('open_time')
            if not fill.index.equals(times) or not np.allclose(prefix.loc[[start,end]],fill.loc[[start,end]],rtol=1e-10):
                raise ValueError('Historical REST quotes failed clock or overlap check')
            prefix.loc[gap.tolist()] = fill.loc[gap.tolist()]
            proofs.append(json.loads(receipt.read_text()))
        (INPUT/(asset+'-historical-gaps.json')).write_text(json.dumps(proofs,indent=2)+'\n')
    minute_file = base.ROOT/'data/research/ma6_knot_1m_20261011/series'/f'binance_um_{asset}USDT_1m_2059200.csv'
    minute = pd.read_csv(minute_file,usecols=['ts','open','close'])
    minute = minute.set_index(pd.to_datetime(minute.ts,unit='ms',utc=True))
    bridge = minute.loc[base.END:].asfreq('1min').resample(STEP)
    bridge = bridge.agg({'open':'first','close':'last'}).where(bridge['close'].count().eq(5),axis=0)
    if not np.allclose(prefix.iloc[-1],bridge.iloc[0],rtol=1e-8):
        raise ValueError('September source overlap mismatch for '+asset)
    frame = pd.concat([prefix,bridge.iloc[1:],october_prices(asset)]).asfreq(STEP)
    assert frame.index.is_unique and frame.notna().all().all()
    return frame


def cached_returns(asset,requested,frame):
    old = pd.read_csv(base.OUT/'events.csv',usecols=['asset','decision','net_pct'],parse_dates=['decision'])
    ctr = pd.read_csv(base.OUT/'controls.csv.gz',usecols=['asset','control_decision','net_pct'],parse_dates=['control_decision'])
    known = pd.concat([old,ctr.rename(columns={'control_decision':'decision'})])
    known = known[known.asset.eq(asset)].groupby('decision').net_pct.first()
    known.index = frame.index.get_indexer(known.index)
    requested = np.unique(requested)
    reuse = known.reindex(known.index.intersection(requested))
    new = np.setdiff1d(requested,reuse.index)
    returns = [reuse]
    opens = frame.open.to_numpy()
    for start in range(0,len(new),16000):
        returns.append(base.trade_returns(opens,new[start:start+16000]))
    return pd.concat(returns)


def evaluate(asset,events,excluded):
    frame = prices(asset)
    idx = frame.index
    close = frame.close.shift(1)
    vol = np.log(close).diff().pow(2).rolling(12).sum().pow(.5)*100
    prior = (close.shift(6)/close.shift(18)-1)*100
    band = np.digitize(vol,[.25,.5,1,2,4])
    key = (((idx.year*12+idx.month)*4+idx.hour//6)*6+band)*3+(np.sign(prior.fillna(0)).to_numpy().astype(int)+1)
    valid = frame.open.notna().iloc[::-1].rolling(base.HOLD_BARS+1).sum().iloc[::-1].eq(base.HOLD_BARS+1).to_numpy()
    eligible = valid & vol.notna().to_numpy() & prior.notna().to_numpy()
    split = idx.searchsorted(base.SPLIT)
    eligible[max(0,split-base.HOLD_BARS):split] = False
    for t in excluded:
        dec = idx.searchsorted(t+6*STEP)
        eligible[max(0,dec-base.EXIT_BARS):dec+base.EXIT_BARS+1] = False
    # A control's initial bar must have no lower touch. Do not screen its following 30m.
    slots = idx.get_indexer(events.known+6*STEP)
    eligible[slots[slots>=0]] = False
    candidates = pd.DataFrame({'key':key[eligible], 'pos':np.flatnonzero(eligible)})
    pools = {k:g.pos.to_numpy() for k,g in candidates.groupby('key')}
    result = events.copy()
    result['asset'] = asset
    result['decision'] = result.known+6*STEP
    result['period'] = np.select([result.known+pd.Timedelta(days=1)<base.SPLIT,result.decision>=base.SPLIT],
                                 ['early','late'],default='crossing')
    available = (slots>=0) & valid[np.maximum(slots,0)]
    selected = slots[available]
    matches = []
    for pos,dec in zip(selected,result.decision[available]):
        pool = pools.get(key[pos],np.empty(0,dtype=int))
        if pd.isna(vol.iloc[pos]) or pd.isna(prior.iloc[pos]):
            pool = pool[:0]
        rng = np.random.default_rng([base.SEED,int(dec.value//10**9),ASSETS.index(asset)])
        matches.append(rng.choice(pool,size=min(24,len(pool)),replace=False))
    returns = cached_returns(asset,np.concatenate([selected,*matches]),frame)
    result['net_pct'] = np.nan
    result.loc[available,'net_pct'] = returns.reindex(selected).to_numpy()
    result['control_n'] = 0
    result.loc[available,'control_n'] = [len(m) for m in matches]
    result['control_net_pct'] = np.nan
    result['control_win_pct'] = np.nan
    # Flatten once to avoid allocating individual pandas objects for thousands of samples.
    n = np.array([len(m) for m in matches])
    rr = pd.DataFrame({'i':np.repeat(np.arange(len(matches)),n),'ret':returns.reindex(np.concatenate(matches)).to_numpy()})
    rr['win'] = 100*rr.ret.gt(0)
    means = rr.groupby('i')[['ret','win']].mean().reindex(range(len(matches)))
    result.loc[available,['control_net_pct','control_win_pct']] = means.to_numpy()
    return result


def summaries(events):
    rows = []
    for period in ('all','early','late'):
        part = events if period=='all' else events[events.period.eq(period)]
        parts = (part,part[part.initial.ge(25)].assign(band='25+'))
        for scope in parts:
            for (asset,band,more),g in scope.groupby(['asset','band','continued'],observed=True):
                outcome = g.net_pct.dropna()
                paired = g.net_pct-g.control_net_pct
                lo,hi = clustered_interval(g,paired)
                rows.append(dict(period=period,asset=asset,band=band,continued=more,observations=len(g),n=len(outcome),
                                 cooldown=int(g.cooldown.sum()),wins=int(outcome.gt(0).sum()),win_pct=100*outcome.gt(0).mean(),
                                 mean_net_pct=outcome.mean(),median_net_pct=outcome.median(),worst_net_pct=outcome.min(),
                                 weeks=g.loc[outcome.index,'known'].dt.tz_localize(None).dt.to_period('W-SUN').nunique(),
                                 paired_n=paired.count(),control_win_pct=g.control_win_pct.mean(),control_net_pct=g.control_net_pct.mean(),
                                 matched_excess_pp=paired.mean(),excess_ci_low=lo,excess_ci_high=hi))
    return pd.DataFrame(rows)


def save_results(result):
    OUT.mkdir(exist_ok=True)
    result.to_csv(OUT/'events.csv.gz',index=False)
    summaries(result).to_csv(OUT/'summary.csv',index=False)
    receipts = {a:json.loads((INPUT/(a+'USDT.json')).read_text()) for a in ASSETS}
    (OUT/'october_sources.json').write_text(json.dumps(receipts,indent=2)+'\n')
    history = {a:json.loads(p.read_text()) for a in ASSETS if (p:=INPUT/(a+'-historical-gaps.json')).exists()}
    (OUT/'historical_gap_sources.json').write_text(json.dumps(history,indent=2)+'\n')
    print(result.groupby('asset').net_pct.agg(['size','count']))


if __name__ == '__main__':
    events, excluded = bars_and_followers()
    save_results(pd.concat([evaluate(a,events,excluded) for a in ASSETS],ignore_index=True))
