"""Measure the real traded USDT in the 60 completed minutes immediately before each V1.7 entry."""
import json
import time
from zipfile import BadZipFile
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

from yoyo.evaluation import cloud_close_entry_turnover as audit

minute = audit.minute
EXP = audit.EXP.parent/'exp-cloud-close-prior-minute-liquidity-20261109-v1'
CACHE = audit.CACHE.parent/'cloud_close_prior_minute_liquidity'


def prior(row,quotes):
    hour = row.entry_ms-minute.HOUR
    inst = row.symbol+'-USDT-SWAP'
    path = CACHE/f'{row.symbol}-{hour}.json'
    if path.is_file():
        return json.loads(path.read_text())
    bars,url = minute.minute_quote(row.symbol,hour,quotes)
    rec = dict(event_id=row.event_id,symbol=row.symbol,entry_ms=row.entry_ms,source=url,window_start_ms=hour)
    if bars is None:
        rec['status'] = url
    else:
        vols,vol_url = audit.volume_hour(row.symbol,hour,bars,url)
        if vols is None:
            rec['status'] = vol_url
        else:
            if not np.isfinite(vols[['quote','base']]).all().all() or (vols[['quote','base']]<0).any().any():
                raise ValueError(f'Missing or negative prior native volumes {inst} {hour}')
            ratio = vols.quote/vols.base
            inconsistent = ((ratio<bars.low*(1-1e-5)) | (ratio>bars.high*(1+1e-5))) & vols.base.gt(0)
            rec['status'] = 'turnover_price_mismatch' if inconsistent.any() else 'observed'
            if rec['status']=='observed':
                rec.update(median_usdt=float(vols.quote.median()),mean_usdt=float(vols.quote.mean()),
                           zero_minutes=int(vols.quote.eq(0).sum()),last_minute_open_ms=int(bars.index[-1]),volume_source=vol_url)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(rec,allow_nan=False))
    tmp.replace(path)
    return rec


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed pre-entry turnover acquisition')
    quotes,_ = audit.source.repair.checked_quotes()
    roster = pd.read_csv(audit.source.EXP/'funded_contrarian_half.csv.gz')
    CACHE.mkdir(exist_ok=True,parents=True)
    def collect(r):
        for attempt in range(3):
            try:
                return prior(r,quotes)
            except (requests.RequestException,BadZipFile):
                if attempt==2:
                    raise
                time.sleep(2**attempt)
    with ThreadPoolExecutor(max_workers=4) as workers:
        rows = []
        for r in workers.map(collect,roster.sort_values('notional',ascending=False).itertuples()):
            rows.append(r)
            if len(rows)%250==0:
                print('checked complete preceding hours',len(rows),flush=True)
    out = pd.DataFrame(rows)
    observed = out.status.eq('observed')
    if not (out[observed].last_minute_open_ms==out[observed].entry_ms-60000).all():
        raise ValueError('Prior trading volume window reaches an unclosed minute')
    out.to_csv(EXP/'prior_liquidity.csv.gz',index=False)
    audit.source.repair.archive.prices.safe_json(EXP/'summary.json',dict(parents=len(out),
        statuses=out.status.value_counts().to_dict(),zero_median=int(out.median_usdt.eq(0).sum()),
        name='Average and median first-minute size proxy using only the past 60 complete native minutes.'))


if __name__=='__main__':
    main()
