"""Six-day labels on each distinct initial lower-touch constituent; all fixed frames.

Decision uses only first touches through the initial close and its 30m follow-up.
Prior volatility is the last 12 closed 5m log changes; trend is the closed hour
before observation, as in the frozen majors experiment. Six-day highs and fixed
exit open are outcome fields only. Use observed Binance USD-M quotes; no padding.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import zlib

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_six_day_excursions as six
from yoyo.evaluation.monday_breadth_constituent_prices import load_quotes
from yoyo.evaluation.monday_breadth_constituent_summary import write_outputs
from yoyo.evaluation.monday_breadth_constituent_scope import exclusion_reason

fixed, base = six.fixed, six.base
OUT = six.previous.census.ACTIVE/'exp-monday-breadth-constituent-six-day-20261108-v1'
CACHE = base.ROOT/'data/research/breadth_constituent_sixday_20261108/checkpoints-v1'
N = six.HOLD_BARS
STEP = fixed.STEP
FLOATS = ['entry_open','exit_open','peak_price','net_pct','peak_pct','control_net_pct','control_win_pct','control_peak_pct']


def membership():
    saved = pd.read_csv(six.OUT/'events.csv.gz',usecols=['asset','minutes','known','decision','band','continued',
                        'initial','new_coins','period','monitor_bars'],parse_dates=['known','decision'])
    sig = saved[saved.asset.eq('BTC')].drop(columns='asset').sort_values(['minutes','known'])
    _, ledger = six.previous.census.ledger_rows()
    members, clocks = [], {}
    date0 = sig.known.min().normalize().replace(day=1)-pd.Timedelta(days=1)
    grid = pd.date_range(date0,fixed.LEDGER_END+six.HOLD+STEP,freq=STEP)
    for m,g in sig.groupby('minutes'):
        span = pd.Timedelta(minutes=m)
        initial = ledger.assign(known=ledger.touch.dt.floor(span)+span)
        pairs = initial[initial.side.eq(1)][['known','symbol','touch']].sort_values('touch').drop_duplicates(['known','symbol'])
        joined = pairs.merge(g,on='known',validate='many_to_one')
        pd.testing.assert_series_equal(joined.groupby('known').size(),g.set_index('known').initial,
                                       check_names=False,check_dtype=False)
        members.append(joined)
        counts = initial.groupby(['known','side']).symbol.nunique()
        blocked = counts[counts.ge(25)].index.get_level_values(0).unique()+fixed.WAIT
        allowed, phase = fixed.control_clock(grid,m)
        sp = grid.searchsorted(base.SPLIT)
        allowed[max(0,sp-N):sp] = False
        for k in grid.searchsorted(blocked):
            allowed[max(0,k-N-6):k+N+7] = False
        a = grid.get_indexer(g.decision)
        assert (a>=0).all()
        allowed[a] = False
        pos = np.flatnonzero(allowed)
        clocks[m] = (pos,phase[pos])
    return sig,pd.concat(members,ignore_index=True),clocks,grid


def study_symbol(symbol, rows, clocks, grid):
    rows = rows.reset_index(drop=True).copy()
    reason = exclusion_reason(symbol)
    rows['eligible'] = not bool(reason)
    rows['status'] = reason or 'missing_entry'
    for col in FLOATS:
        rows[col] = np.nan
    rows['peak_bar_open'] = pd.Series(pd.NaT,index=rows.index,dtype='datetime64[ns, UTC]')
    rows['control_n'] = 0
    rows['exit_time'] = rows.decision+six.HOLD
    if reason:
        return rows,{'symbol':symbol,'excluded':reason}
    starts = pd.DatetimeIndex(rows.decision.unique()).sort_values()
    start = max(grid[0],starts[0].normalize().replace(day=1)-pd.Timedelta(hours=2))
    end = min(grid[-1],starts[-1].normalize().replace(day=1)+pd.offsets.MonthBegin()+six.HOLD)
    raw,receipt = load_quotes(symbol,start,end,needed_intervals=[(t,t+six.HOLD) for t in starts])
    if raw.empty:
        return rows,receipt
    lo = min(raw.index[0],starts.min())
    hi = max(raw.index[-1],starts.max()+six.HOLD)
    idx = pd.date_range(lo,hi,freq=STEP)
    frame = raw.reindex(idx)
    opens = frame.open.to_numpy(); highs = frame.high.to_numpy()
    peaks = six.six_day_peaks(frame).to_numpy()
    pos = idx.get_indexer(pd.DatetimeIndex(rows.decision))
    assert ((pos>=0)&(pos+N<len(idx))).all()
    entry = opens[pos]; ex = opens[pos+N]
    valid = np.isfinite(entry)&np.isfinite(ex)
    rows['entry_open'] = entry
    rows['exit_open'] = ex
    rows['status'] = np.select([~np.isfinite(entry),~np.isfinite(ex),~np.isfinite(peaks[pos])],
                               ['missing_entry','missing_exit','missing_intermediate_high'],default='complete')
    rows['peak_pct'] = peaks[pos]
    peak_info = {}
    for p in np.unique(pos[np.isfinite(peaks[pos])]):
        q = p+int(np.argmax(highs[p:p+N]))
        if opens[p+N]>highs[q]:
            peak_info[p] = (opens[p+N],idx[p+N])
        else:
            peak_info[p] = (highs[q],idx[q])
    if peak_info:
        r = pd.DataFrame.from_dict(peak_info,orient='index',columns=['price','time']).reindex(pos)
        rows['peak_price'] = r.price.to_numpy()
        rows['peak_bar_open'] = pd.DatetimeIndex(r.time)
    close = frame.close.shift(1)
    v = (np.log(close).diff().pow(2).rolling(12).sum()**.5*100).to_numpy()
    prev = (close.shift(6)/close.shift(18)-1).to_numpy()
    features = np.isfinite(v)&np.isfinite(prev)
    band = np.digitize(v,[.25,.5,1,2,4])
    key = (((idx.year*12+idx.month)*4+idx.hour//6)*6+band)*3+(np.sign(np.nan_to_num(prev)).astype(int)+1)
    key = np.asarray(key)
    pools_per_signal = []
    shift = (idx[0]-grid[0])//STEP
    for m,g in rows.groupby('minutes'):
        p,phase = clocks[m]
        a,b = np.searchsorted(p,[shift,shift+len(idx)])
        loc = p[a:b]-shift
        ok = features[loc]&np.isfinite(peaks[loc])
        eligible = loc[ok]
        pool_rows = pd.DataFrame({'k':key[eligible]*7+phase[a:b][ok],'p':eligible})
        pools = {k:group.p.to_numpy() for k,group in pool_rows.groupby('k')}
        for j in g.index:
            s = pos[j]
            if not valid[j] or not np.isfinite(peaks[s]) or not features[s]:
                continue
            pool = pools.get(key[s]*7+rows.at[j,'monitor_bars'])
            if pool is not None:
                rng = np.random.default_rng([base.SEED,int(rows.at[j,'decision'].timestamp()),zlib.crc32(symbol.encode())])
                pools_per_signal.append((j,rng.choice(pool,size=min(24,len(pool)),replace=False)))
    selected = np.unique(np.concatenate([pos[valid],*[p for _,p in pools_per_signal]]))
    if selected.size:
        ret = pd.concat([base.trade_returns(opens,selected[k:k+16000],hold_bars=N)
                         for k in range(0,len(selected),16000)])
        lookup = dict(ret.items())
        rows.loc[valid,'net_pct'] = [lookup[p] for p in pos[valid]]
        controls = []
        for j,p in pools_per_signal:
            rr = np.array([lookup[q] for q in p])
            controls.append((j,len(p),rr.mean(),100*(rr>0).mean(),peaks[p].mean()))
        if controls:
            cols = ['control_n','control_net_pct','control_win_pct','control_peak_pct']
            c = pd.DataFrame(controls,columns=['j',*cols]).set_index('j')
            for col in cols:
                rows.loc[c.index,col] = c[col]
    assert np.allclose(rows.peak_pct,(rows.peak_price/rows.entry_open-1)*100,equal_nan=True)
    return rows,receipt


def process(symbol,g,clocks,grid):
    data,meta = CACHE/(symbol+'.csv.gz'),CACHE/(symbol+'.json')
    if not(data.is_file() and meta.is_file()):
        r,proof = study_symbol(symbol,g,clocks,grid)
        tmp = data.with_suffix('.tmp')
        r.to_csv(tmp,index=False,compression='gzip'); tmp.replace(data)
        temp = meta.with_suffix('.tmp')
        temp.write_text(json.dumps(proof,ensure_ascii=False,default=str)); temp.replace(meta)
    return data,meta


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument('--workers',type=int,default=4)
    args = cli.parse_args()
    sig,members,clocks,grid = membership()
    CACHE.mkdir(exist_ok=True,parents=True); OUT.mkdir(exist_ok=True)
    groups = list(members.groupby('symbol',sort=True))
    print(f'{len(sig)} signals, {len(members)} memberships, {len(groups)} symbols',flush=True)
    saved = []
    with ThreadPoolExecutor(args.workers) as ex:
        futures = {ex.submit(process,s,g,clocks,grid):s for s,g in groups}
        for i,f in enumerate(as_completed(futures),1):
            try:
                saved.append(f.result())
            except Exception as err:
                print(f'ERROR {futures[f]}: {err!r}',flush=True)
                for t in futures: t.cancel()
                raise
            if i%20==0: print(f'{i}/{len(groups)} done',flush=True)
    allrows = pd.concat([pd.read_csv(data,parse_dates=['touch','known','decision','exit_time'],dtype={'band':str})
                         for data,_ in sorted(saved)],ignore_index=True)
    keys = ['minutes','known','symbol']
    assert len(allrows)==len(members) and not allrows.duplicated(keys).any()
    assert members.merge(allrows[keys],on=keys,how='left',indicator=True)._merge.eq('both').all()
    allrows.sort_values(keys,inplace=True)
    allrows.to_csv(OUT/'members.csv.gz',index=False)
    (OUT/'price_sources.json.gz').write_bytes(__import__('gzip').compress(json.dumps(
        [json.loads(meta.read_text()) for _,meta in sorted(saved)],ensure_ascii=False).encode()))
    write_outputs(allrows,sig,OUT,pd.read_csv(six.OUT/'summary.csv',dtype={'band':str}))


if __name__=='__main__':
    main()
