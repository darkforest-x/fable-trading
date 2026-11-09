"""Compare actual V1.7 parent entry notional with venue-reported first-minute turnover."""
import json
import time
from zipfile import BadZipFile
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

from yoyo.evaluation import cloud_close_cvc_regime as source
from yoyo.evaluation import cloud_close_double_minute_risk as minute

EXP = source.EXP.parent/'exp-cloud-close-entry-turnover-20261109-v1'
CACHE = minute.CACHE.parent/'cloud_close_entry_turnover_checks'


def volume_hour(symbol,hour,bars,url):
    """Read native base and USDT turnover; repair older price-only ZIPs with same-timestamp API data."""
    inst = symbol+'-USDT-SWAP'
    folder = minute.CACHE/inst
    archive = None
    if 'api/v5' not in url:
        file = folder/url.rsplit('/',1)[1]
        raw = pd.read_csv(file,usecols=['instrument_name','open_time','vol','vol_quote','vol_ccy','confirm'])
        archive = raw[(raw.instrument_name==inst)&(raw['confirm']==1)].set_index('open_time').reindex(bars.index)
        if archive[['vol_ccy','vol_quote']].notna().all().all():
            return archive[['vol','vol_ccy','vol_quote']].rename(columns={'vol_ccy':'base','vol_quote':'quote'}),url
    cache = folder/f'api-{hour}.json'
    if not cache.exists():
        endpoint = 'https://www.okx.com/api/v5/market/history-candles'
        res = requests.get(endpoint,params=dict(instId=inst,bar='1m',after=hour+minute.HOUR,limit=60),timeout=(8,22))
        res.raise_for_status()
        body = res.json()
        if body.get('code') not in ('0','51001'):
            raise ValueError(f'Unexpected OKX turnover code {body.get("code")} for {inst}')
        minute.write_cache(cache,json.dumps(body).encode())
    raw = json.loads(cache.read_text())['data']
    columns = ['time','open','high','low','close','vol','base','quote','confirm']
    api = pd.DataFrame(raw,columns=columns).apply(pd.to_numeric)
    api = api[api['confirm'].eq(1)].set_index('time').reindex(bars.index)
    if (not np.allclose(api[minute.FIELDS],bars[minute.FIELDS],rtol=1e-9,atol=1e-15) or
            (archive is not None and not np.allclose(api.vol,archive.vol,rtol=1e-9,atol=1e-8))):
        return None,'native_volume_api_unavailable_or_not_same_trades'
    return api[['vol','base','quote']],'https://www.okx.com/api/v5/market/history-candles'


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
        amounts,vol_url = volume_hour(symbol,clock,bars,url)
        first = bars.iloc[0]
        if not np.isclose(first.open,row.entry_price,rtol=1e-8,atol=1e-14):
            raise ValueError(f'First minute price differs from strategy entry {symbol} {clock}')
        if amounts is None:
            record['status'] = vol_url
        else:
            amount,base = float(amounts.quote.iat[0]),float(amounts.base.iat[0])
            if not np.isfinite([amount,base]).all() or min(amount,base)<0:
                raise ValueError(f'Invalid native minute turnover {symbol} {clock}')
            record.update(status='observed',entry_open=float(first.open),minute_low=float(first.low),
                          minute_high=float(first.high),quote_usdt=amount,base_volume=base,volume_source=vol_url)
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
            except (requests.RequestException,TimeoutError,BadZipFile):
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
