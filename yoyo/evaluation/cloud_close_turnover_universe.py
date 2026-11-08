"""Select V1.7 12H keys outside the twelve most-traded eligible contracts at that time."""
import argparse
import gc
import gzip
import json
import sqlite3
from contextlib import closing

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_allmarket_inputs as raw_inputs
from yoyo.evaluation import cloud_close_eligibility_repair as repair
from yoyo.evaluation import cloud_close_leadership as leader
from yoyo.evaluation import cloud_close_second_reclaim as second
from yoyo.evaluation import cloud_close_thirds as thirds
from yoyo.evaluation import cloud_close_volatility_expansion as market

wallet = thirds.wallet
EXP = wallet.ROOT/'experiments/active/exp-cloud-close-turnover-universe-20261109-v1'
COMPLETE = EXP.parent/'exp-cloud-close-turnover-complete-20261109-v1'
LIMIT = 12


def known_at_entry(bars, turnover):
    """Use the 60 settled 12H quote-turnover bars immediately before the signal key opens."""
    data = turnover.reindex(bars.time)
    total = data.rolling(60,min_periods=60).sum()
    # Row t is the open of the last bar BEFORE the signal key. Entry is open(t)+24h.
    total = total.where((bars.time-bars.time.shift(59)).to_numpy()==59*leader.BAR)
    total.index = total.index + 2*leader.BAR
    return total


def shared_quotes(coverage, output):
    """Snapshot only public candles that supplied the archived 12H quote timeline."""
    saved = output/'shared_quote_turnover.csv.gz'
    if saved.is_file():
        return pd.read_csv(saved,dtype={'symbol':str},float_precision='round_trip')
    records = []
    with closing(sqlite3.connect(raw_inputs.CANDLE_CACHE.as_uri()+'?mode=ro',uri=True,timeout=25)) as db:
        for row in coverage.itertuples():
            if row.cohort=='top20_reused':
                continue
            file = wallet.SOURCE/'inputs'/row.symbol/'bars12h.csv.gz'
            timeline = pd.read_csv(file,usecols=['time','source'])
            wanted = set(timeline.loc[timeline.source.eq('shared_OKX_cache_12Hutc'),'time'])
            if not wanted:
                continue
            args = (f'https://www.okx.com|{row.instId}|12Hutc',min(wanted),max(wanted))
            query = 'SELECT t, raw FROM candles WHERE key=? AND t>=? AND t<=? ORDER BY t'
            for t,raw in db.execute(query,args):
                if t not in wanted:
                    continue
                bar = json.loads(raw)
                if len(bar)>=9 and str(bar[8])=='1' and int(bar[0])==t:
                    records.append((row.symbol,t,*map(float,bar[1:5]),float(bar[7])))
    frame = pd.DataFrame(records,columns=['symbol','time','open','high','low','close','quote'])
    if frame.empty or frame.duplicated(['symbol','time']).any():
        raise ValueError('Unexpected shared 12H candle coverage or duplicate rows')
    frame.to_csv(saved,index=False)
    return frame


def okx_values(folder, top, supplement=None):
    bars = pd.read_csv(folder/'bars12h.csv.gz',float_precision='round_trip')
    eligibility = leader.trailing_return(bars).notna()
    source = sorted(folder.glob('12Hutc-*.json.gz') if top else (folder/'raw/12Hutc').glob('*.json.gz'))
    rows = []
    for file in source:
        with gzip.open(file,'rt') as stream:
            rows.extend(row[:9] for row in json.load(stream) if len(row)>=9 and str(row[8])=='1')
    candles = pd.DataFrame(rows,columns=['time','open','high','low','close','contracts','base','quote','confirm'])
    cols = ['open','high','low','close','quote']
    candles = candles[['time',*cols]].apply(pd.to_numeric)
    if supplement is not None:
        candles = pd.concat([candles,supplement[['time',*cols]]],ignore_index=True)
    candles = candles.drop_duplicates()
    if candles.empty:
        return eligibility,pd.Series(dtype=float)
    candles = candles.loc[~candles.time.duplicated(keep=False)].set_index('time').reindex(bars.time)
    same = np.isclose(candles[cols[:4]],bars[cols[:4]],rtol=1e-9,atol=1e-15).all(axis=1)
    valid = same & np.isfinite(candles.quote) & candles.quote.ge(0)
    return eligibility,known_at_entry(bars,candles.quote.where(valid))


def rank_table(eligible, traded):
    """Rank only price-eligible peers at this clock; unknown turnover gives a rank interval."""
    eligible = eligible.fillna(False).astype(bool)
    traded = traded.reindex_like(eligible).where(eligible)
    counts = eligible.sum(axis=1)
    missing = counts-traded.notna().sum(axis=1)
    higher = traded.rank(axis=1,method='min',ascending=False)-1
    possible = higher.add(missing,axis=0)
    # A candidate may trade only when its own value is known and the peer count proves its rank.
    outside = higher.ge(LIMIT)
    inside = possible.lt(LIMIT)
    state = pd.DataFrame(np.select([outside,inside],['outside_top12','inside_top12'],default='unknown'),
                         index=higher.index,columns=higher.columns)
    state.loc[counts.le(LIMIT)] = 'too_few_peers'
    return state, higher, missing


def features(paths,output):
    coverage = pd.read_csv(wallet.SOURCE/'input_coverage.csv',dtype={'symbol':str},keep_default_na=False)
    shared = shared_quotes(coverage,output) if output==COMPLETE else None
    by_symbol = {} if shared is None else dict(tuple(shared.groupby('symbol')))
    eligible, volume = {}, {}
    for row in coverage.itertuples():
        top = row.cohort=='top20_reused'
        folder = (wallet.TOP if top else wallet.SOURCE)/'inputs'/row.symbol
        eligible[row.symbol],volume[row.symbol] = okx_values(folder,top,by_symbol.get(row.symbol))
    if len(eligible)!=291:
        raise ValueError('Frozen OKX census mismatch')
    state,higher,missing = rank_table(pd.DataFrame(eligible),pd.DataFrame(volume))
    events = paths.loc[paths.tp_r.eq(0),['event_id','parent_id','entry_ms','symbol','side','is_control']].copy()
    clock = state.index.get_indexer(events.entry_ms)
    symbol = state.columns.get_indexer(events.symbol)
    if min(clock.min(),symbol.min())<0:
        raise ValueError('Price-eligible event absent from the liquidity clock')
    events['state'] = state.to_numpy()[clock,symbol]
    events['known_strictly_larger'] = higher.to_numpy()[clock,symbol]
    events['unknown_peer_turnovers'] = missing.to_numpy()[clock]
    return events


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--shared',action='store_true',help='Use archived native pages and the shared OKX candle input')
    output = COMPLETE if parser.parse_args().shared else EXP
    if (output/'summary.json').exists():
        raise FileExistsError('Frozen turnover study already evaluated')
    paths = thirds.okx_paths()
    repair.check_parent_sets(paths)
    events = features(paths,output)
    events.to_csv(output/'event_ranks.csv.gz',index=False)
    classified = events[events.state.isin(['inside_top12','outside_top12'])]
    selected = classified[classified.state.eq('outside_top12')]
    real = selected[~selected.is_control]
    controls = selected[selected.is_control & selected.parent_id.isin(real.parent_id)]
    pairs = real[real.parent_id.isin(controls.parent_id)]
    old = pd.read_csv(repair.EXP/'thirds'/'account_metrics.csv')
    rows = old[old.venue.eq('OKX') & old.scenario.eq('actual')].assign(cohort='unfiltered').to_dict('records')
    summary = dict(actual_classes=events[~events.is_control].state.value_counts().to_dict(),pairs=len(pairs))
    outcomes = {}
    for name,chosen,slip in [('rank_available',classified[~classified.is_control],0.),
                             ('outside_top12',real,0.),('paired',pairs,0.),('control',controls,0.),('stress',real,.0005)]:
        sample = paths[paths.event_id.isin(chosen.event_id)]
        metrics,trades,equity = market.fund('OKX',sample,slip)
        rows.extend(dict(cohort=name,period=p,**m) for p,m in metrics.items())
        outcomes[name] = metrics
        if name=='outside_top12':
            trades.to_csv(output/'funded.csv.gz')
            equity.to_csv(output/'hourly.csv.gz',index_label='hour_end_utc')
        del trades,equity
        gc.collect()
    choice = outcomes['outside_top12']
    summary.update(dev_pass=second.meets(choice['dev']),full_pass=second.meets(choice['all']),
                   wealth_improved=choice['all']['final_usdt']>outcomes['rank_available']['all']['final_usdt'])
    table = pd.DataFrame(rows)
    table.to_csv(output/'account_metrics.csv',index=False)
    (output/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')][['cohort','final_usdt','trades','win_pct','payoff_usdt','dd_bound_pct']].to_string(index=False))


if __name__ == '__main__':
    main()
