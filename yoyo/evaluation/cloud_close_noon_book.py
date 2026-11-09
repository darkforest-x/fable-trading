"""Reconstruct an outcome-independent set of midday 12H entry books from daily native history."""
import gzip
import hashlib
import json
import tarfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

from yoyo.evaluation import cloud_close_ioc_book as ioc

native = ioc.native
EXP = native.EXP.parent/'exp-cloud-close-noon-book-20261109-v1'


def before_noon(inst,when,url):
    filename = EXP/f'{inst}_{when}.jsonl.gz'
    if filename.exists():
        with gzip.open(filename,'rt') as pipe:
            return [json.loads(row) for row in pipe]
    book={}
    last=-1
    records=[]
    with requests.get(url,stream=True,timeout=(8,50)) as stream:
        stream.raise_for_status()
        with tarfile.open(fileobj=stream.raw,mode='r|gz') as archive:
            for member in archive:
                if not member.isfile() or not member.name.startswith(inst+'-'):
                    continue
                for line in archive.extractfile(member):
                    m=json.loads(line)
                    clock=int(m['ts'])
                    if m['instId']!=inst or clock<last:
                        raise ValueError('Invalid native symbol or nonmonotone order book')
                    if clock>when and not records:
                        if not book or last<when-60000:
                            raise ValueError('No anchored and recent native book at noon')
                        records.append(dict(instId=inst,action='snapshot',ts=str(last),
                                            asks=list(book['asks'].items()),bids=list(book['bids'].items())))
                    if clock>when:
                        records.append(m)
                        if clock>when+1000:
                            break
                    elif book or m['action']=='snapshot':
                        native.apply_message(book,m)
                    last=clock
                break
    if len(records)<2 or int(records[-1]['ts'])<=when+1000:
        raise ValueError('Incomplete native book tape around noon decision')
    with gzip.open(filename,'wt') as out:
        for r in records:
            out.write(json.dumps(r,separators=(',',':'))+'\n')
    return records


def case(r,quotes):
    symbol=r.symbol
    inst=symbol+'-USDT-SWAP'
    when=int(r.entry_ms)
    ts,ohlc=quotes(symbol)
    idx=ts.searchsorted(when)
    if idx<1 or ts[idx]!=when or ts[idx-1]!=when-native.audit.minute.HOUR:
        raise ValueError('Original actual entry price or confirmed close unavailable')
    close,entry=ohlc[idx-1,3],ohlc[idx,0]
    for lag in range(1,25):
        hour=when-lag*native.audit.minute.HOUR
        bars,url=native.audit.minute.minute_quote(symbol,hour,quotes)
        if bars is not None:
            amounts,_=native.audit.volume_hour(symbol,hour,bars,url)
            if amounts is not None and amounts.vol.gt(0).sum()>=3:
                break
    else:
        raise ValueError('No contemporaneous contract/base volume proof')
    face=(amounts.base/amounts.vol)[amounts.vol.gt(0)].dropna()
    if len(face)<3 or not np.allclose(face,face.median(),rtol=1e-8,atol=1e-12):
        raise ValueError('Historical contract units are not reproducible')
    unit=float(face.median())
    date=when//native.DAY*native.DAY
    res=requests.get(native.API,params=dict(module=4,instType='SWAP',instFamilyList=symbol+'-USDT',
                     dateAggrType='daily',begin=date-1,end=date+1),timeout=(8,20))
    res.raise_for_status()
    body=res.json()
    items=[g for row in body.get('data',[]) for det in row['details'] for g in det['groupDetails']
           if int(g['dateTs'])==date and g['filename'].startswith(inst+'-L2orderbook-400lv-')]
    if len(items)!=1:
        raise ValueError('No unique native 400-level archive for this selected position')
    records=before_noon(inst,when,items[0]['url'])
    book={}
    out={}
    def measure():
        sweep=native.walk(book,r.side,r.notional/entry,unit,entry)
        available=ioc.execution(book,r.side,r.notional/close,unit,close)
        return dict(sweep=sweep,ioc=available)
    for i,m in enumerate(records):
        if int(m['ts'])>when+1000:
            break
        native.apply_message(book,m)
        if i==0:
            out['at_close']=measure()
    out['one_second']=measure()
    return dict(status='measured',prior_update_age_ms=when-int(records[0]['ts']),official_archive=items[0],
                contract_face=unit,face_hour_ms=hour,signal_close=close,entry_open=entry,stages=out)


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed midday liquidity probe')
    source=native.audit.source
    f=pd.read_csv(source.EXP/'funded_contrarian_half.csv.gz')
    dt=pd.to_datetime(f.entry_ms,unit='ms',utc=True)
    f=f[(dt>=pd.Timestamp('2023-03-01',tz='UTC')) & dt.dt.hour.eq(12)].copy()
    f['year']=dt.loc[f.index].dt.year
    f['selection']=f.event_id.map(lambda x:hashlib.sha256(('cloud-book-noon-73|'+x).encode()).hexdigest())
    selected=f.sort_values('selection').groupby(['year','side']).head(1)
    selected.to_csv(EXP/'selection.csv',index=False)
    quotes,_=source.repair.checked_quotes()
    def handle(r):
        path=EXP/f'case_{r.symbol}_{r.entry_ms}.json'
        if path.exists():
            return json.loads(path.read_text())
        try:
            record=case(r,quotes)
        except (ValueError,requests.RequestException) as exc:
            record=dict(status='unverified',reason=str(exc))
        record.update(event_id=r.event_id,symbol=r.symbol,entry_ms=r.entry_ms,side=r.side,notional=r.notional)
        source.repair.archive.prices.safe_json(path,record)
        return record
    with ThreadPoolExecutor(max_workers=2) as workers:
        cases=list(workers.map(handle,selected.itertuples()))
    stats={}
    for stage in ['at_close','one_second']:
        valid=[c['stages'][stage] for c in cases if c['status']=='measured']
        bp=[p['sweep']['adverse_bp_from_trade_open'] for p in valid if p['sweep']['all_filled']]
        frac=[p['ioc']['fill_fraction'] for p in valid]
        stats[stage]=dict(covered=len(valid),complete_sweeps=len(bp),above_5bp=sum(b>5 for b in bp),
              median_bp=float(np.median(bp)) if bp else None,full_ioc=sum(x>=1-1e-9 for x in frac),
              zero_ioc=sum(x==0 for x in frac),median_ioc_fraction=float(np.median(frac)) if frac else None)
    source.repair.archive.prices.safe_json(EXP/'summary.json',dict(eligible=len(f),selected=len(selected),stages=stats,
                        missing={c['event_id']:c['reason'] for c in cases if c['status']!='measured'}))


if __name__=='__main__':
    main()
