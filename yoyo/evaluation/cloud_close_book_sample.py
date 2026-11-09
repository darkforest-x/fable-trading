"""Stratified ordinary-trade check of actually published 400-level order books."""
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

from yoyo.evaluation import cloud_close_native_book_impact as native

EXP = native.EXP.parent/'exp-cloud-close-book-sample-20261109-v1'


def run():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed first-second opening book sample')
    source = native.audit.source
    funds = pd.read_csv(source.EXP/'funded_contrarian_half.csv.gz')
    prices = pd.read_csv(source.repair.EXP/'account/selected_paths.csv.gz',usecols=['event_id','tp_r','entry_price'])
    funds['entry_price'] = funds.event_id.map(prices[prices.tp_r.eq(0)].set_index('event_id').entry_price)
    dt = pd.to_datetime(funds.entry_ms,unit='ms',utc=True)
    extremes = {r['event_id'] for r in json.loads((native.EXP/'summary.json').read_text())['cases'].values()}
    pool = funds[funds.entry_ms.mod(native.DAY).eq(0) & dt.ge('2023-03-01') & ~funds.event_id.isin(extremes)].copy()
    pool['year'] = dt.loc[pool.index].dt.year
    pool['key'] = [hashlib.sha256(('cloud-book-73|'+s).encode()).hexdigest() for s in pool.event_id]
    pool['stratum_size'] = pool.groupby(['year','side']).event_id.transform('size')
    sample = pool.sort_values('key').groupby(['year','side']).head(3).sort_values(['year','side','key'])
    sample.to_csv(EXP/'selection.csv',index=False)
    quotes,_ = source.repair.checked_quotes()
    def inspect(row):
        path = EXP/f'case_{row.entry_ms}_{row.symbol}.json'
        if path.exists():
            return json.loads(path.read_text())
        for attempt in range(3):
            try:
                obs = native.evaluate(row,quotes,EXP)
                result = dict(status='observed',**obs)
                break
            except (requests.RequestException,ValueError,KeyError,IndexError) as exc:
                if attempt<2 and isinstance(exc,requests.RequestException):
                    time.sleep(2**attempt)
                    continue
                result = dict(status='unverified',event_id=row.event_id,symbol=row.symbol,reason=str(exc))
                break
        source.repair.archive.prices.safe_json(path,result)
        return result
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(inspect,sample.itertuples()))
    by_id = {r['event_id']:r for r in results}
    table = []
    for r in sample.itertuples():
        case = by_id[r.event_id]
        record = dict(event_id=r.event_id,symbol=r.symbol,year=r.year,side=r.side,weight=r.stratum_size/3,
                      nominal=r.notional,status=case['status'])
        if 'observations' in case:
            for i in ['first','one_second']:
                m = case['observations'][i]
                record[i+'_bp'] = m['adverse_bp_from_trade_open'] if m['all_filled'] else None
                record[i+'_levels'] = m['levels']
        table.append(record)
    fills = pd.DataFrame(table)
    fills.to_csv(EXP/'book_prices.csv',index=False)
    out = {}
    for stage in ['first','one_second']:
        col = stage+'_bp'
        g = fills.dropna(subset=[col]) if col in fills else fills.iloc[0:0]
        if g.empty:
            out[stage] = dict(observed=0)
            continue
        out[stage] = dict(observed=len(g),exceeding_5bp=int(g[col].gt(5).sum()),median_bp=float(g[col].median()),
                     min_bp=float(g[col].min()),max_bp=float(g[col].max()),
                     weighted_exceedance=float(np.average(g[col].gt(5),weights=g.weight)) if len(g)==len(fills) else None)
    source.repair.archive.prices.safe_json(EXP/'summary.json',dict(eligible=len(pool),selected=len(sample),stages=out,
        statuses=fills.status.value_counts().to_dict(),scope='Regular 00 UTC positions only, three cases per year and side.'))


if __name__=='__main__':
    run()
