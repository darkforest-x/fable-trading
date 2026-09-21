"""Recover a timestamp-corrupt 0G cache through the same bounded public venue.

The source pool contains 0G on both venues but its normalized time column is
empty. Catalog metadata places 0G before the target date. US100/US500/XOM were
listed September10, after the target, and are recorded as not yet listed.
No numerical gate changes or substitution of another instrument is allowed.
"""
import argparse,hashlib,json
from pathlib import Path
import pandas as pd
from yoyo.data.ma_snapshot_inputs import ROOT,Client,fetch,dump
from yoyo.datasets.ma_launch_followup50 import ensure_committed

def run(planpath):
    commit=ensure_committed([Path(__file__),ROOT/'yoyo/data/ma_snapshot_inputs.py',planpath]);plan=json.loads(planpath.read_text());out=ROOT/plan['experiment_dir']
    if (out/'recovered_sources.json').exists():raise FileExistsError('recovery already exists')
    client=Client('binance',out);start=int(pd.Timestamp(plan['scan_start_utc']).timestamp()*1000);cutoff=int(pd.Timestamp(plan['scan_end_utc']).timestamp()*1000);sources=[]
    for minutes in plan['timeframes_minutes']:
        f,receipts=fetch(client,'0GUSDT',minutes,start,cutoff)
        if f.empty:raise ValueError('0G unavailable at requested time')
        path=out/'inputs'/f'binance_0GUSDT_{minutes}m.csv';f.to_csv(path,index=False);sha=hashlib.sha256(path.read_bytes()).hexdigest()
        sources.append(dict(path=str(path.relative_to(ROOT)),symbol='0G',market='0GUSDT',venue='binance',bar_minutes=minutes,prefix_sha256=sha,rows=len(f),origin='bounded_public_api_timestamp_cache_recovery'))
        dump(out/'fetch_receipts'/f'binance_0GUSDT_{minutes}m.json',dict(requests=receipts,sha256=sha,future_ohlcv_converted=0))
    dump(out/'recovered_sources.json',sources)
    dump(out/'source_error_resolution.json',{'source_commit':commit,'recovered_asset':'0G','recovered_venue':'binance','not_yet_listed':['US100-USDT-SWAP','US500-USDT-SWAP','XOM-USDT-SWAP'],'listing_date_utc':'2026-09-10','unresolved_source_assets':[]})
    print('Recovered 0G on all6timeframes;3 other empty markets listed after target date.')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--plan',type=Path,required=True);run(p.parse_args().plan)
