"""Compare actual V1.7 parent entry notional with venue-reported first-minute turnover."""
import json
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

from yoyo.evaluation import cloud_close_cvc_regime as source
from yoyo.evaluation import cloud_close_double_minute_risk as minute

EXP = source.EXP.parent/'exp-cloud-close-entry-turnover-20261109-v1'
CACHE = minute.CACHE.parent/'cloud_close_entry_turnover_checks'


def observation(row,quotes):
    """Read opening 1m quote volume from the exact source whose 60 candles reproduce this H1."""
    symbol,clock = row.symbol,int(row.entry_ms)
    path = CACHE/f'{symbol}-{clock}.json'
    if path.exists():
        return json.loads(path.read_text())
    bars,url = minute.minute_quote(symbol,clock,quotes)
    record = dict(event_id=row.event_id,symbol=symbol,entry_ms=clock,source=url)
    if bars is None:
        record['status'] = url
    else:
        inst = symbol+'-USDT-SWAP'
        folder = minute.CACHE/inst
        if 'api/v5' in url:
            raw = json.loads((folder/f'api-{clock}.json').read_text())['data']
            hits = [r for r in raw if int(r[0])==clock and str(r[8])=='1']
            if len(hits)!=1:
                raise ValueError(f'Missing unique entry minute {inst} {clock}')
            amount = float(hits[0][7])
            base_volume = float(hits[0][6])
        else:
            file = folder/url.rsplit('/',1)[1]
            raw = pd.read_csv(file, usecols=['instrument_name','open_time','vol_quote','vol_ccy','confirm'],float_precision='round_trip')
            hit = raw[(raw.instrument_name==inst) & (raw.open_time==clock) & (raw['confirm']==1)]
            if len(hit)!=1:
                raise ValueError(f'Missing unique archive entry minute {inst} {clock}')
            amount = float(hit.vol_quote.iat[0])
            base_volume = float(hit.vol_ccy.iat[0])
        first = bars.iloc[0]
        # Quote/base is native traded VWAP, subject to the archive's volume rounding.
        if not np.isfinite([amount,base_volume]).all() or min(amount,base_volume)<0:
            raise ValueError(f'Invalid minute turnover for {inst} {clock}')
        if not np.isclose(first.open,row.entry_price,rtol=1e-8,atol=1e-14):
            raise ValueError(f'Actual first minute price does not match the strategy entry for {inst} {clock}')
        record.update(status='observed',entry_open=float(first.open),minute_low=float(first.low),
                      minute_high=float(first.high),quote_usdt=amount,base_volume=base_volume)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(record,allow_nan=False))
    tmp.replace(path)
    return record


def run():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed full entry turnover observation')
    funds = pd.read_csv(source.EXP/'funded_contrarian_half.csv.gz',float_precision='round_trip')
    native = pd.read_csv(source.repair.EXP/'account/selected_paths.csv.gz',usecols=['event_id','tp_r','entry_price'])
    prices = native[native.tp_r.eq(0)].set_index('event_id').entry_price
    funds['entry_price'] = funds.event_id.map(prices)
    if (funds.duplicated(['entry_ms','symbol']).any() or funds.entry_price.isna().any() or
            not np.isclose(funds.cash_pnl.sum()+1000,119245.009305,atol=.001,rtol=0)):
        raise ValueError('Frozen cash account or entry roster mismatch')
    CACHE.mkdir(exist_ok=True,parents=True)
    quotes,_ = source.repair.checked_quotes()
    def collect(row):
        for attempt in range(3):
            try:
                return observation(row,quotes)
            except (requests.RequestException,TimeoutError) as exc:
                if attempt==2:
                    raise
                time.sleep(2**attempt)
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = []
        for result in pool.map(collect,funds.sort_values('notional',ascending=False).itertuples()):
            rows.append(result)
            if len(rows)%200==0:
                print('observed or source-checked',len(rows),flush=True)
    output = funds.merge(pd.DataFrame(rows),on=['event_id','symbol','entry_ms'],validate='one_to_one')
    observed = output.status.eq('observed')
    output['entry_share'] = output.notional.div(output.quote_usdt)
    output['year'] = pd.to_datetime(output.entry_ms,unit='ms',utc=True).dt.year
    output.to_csv(EXP/'entries.csv.gz',index=False)
    def stats(g):
        finite = g[g.status.eq('observed')]
        return dict(entries=len(g),observed=len(finite),zero=int(finite.quote_usdt.eq(0).sum()),
                    pct_over_one=int(finite.entry_share.gt(.01).sum()),pct_over_ten=int(finite.entry_share.gt(.10).sum()),
                    over_whole_minute=int(finite.entry_share.gt(1).sum()),
                    max_finite_ratio=float(finite.entry_share.replace(np.inf,np.nan).max()) if finite.quote_usdt.gt(0).any() else None)
    source.repair.archive.prices.safe_json(EXP/'summary.json',dict(all=stats(output),
           years={str(k):stats(g) for k,g in output.groupby('year')},statuses=output.status.value_counts().to_dict(),
           measured_entry_notional_fraction=float(output.notional[observed].sum()/output.notional.sum()),
           limitations='Entry only. After-entry observed turnover includes both taker directions and says nothing definitive about available depth at the first print. No ex-post fill filtering.'))


if __name__=='__main__':
    run()
