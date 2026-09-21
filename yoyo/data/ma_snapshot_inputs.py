"""Freeze historical closed-bar prefixes for an owner-requested rule replay.

Only public Binance/OKX trade-price OHLCV is used. Local CSV readers inspect
only timestamps until the approved warmup start and stop at the close cutoff
before converting boundary OHLCV. Thirty-minute bars aggregate to UTC-aligned
1h/4h only when every constituent exists. Native lower periods are fetched
with an explicit bounded end, never from an unbounded latest-price request.
No production cache, trading endpoint, or credentials are used.
"""
from __future__ import annotations
import argparse, csv, gzip, hashlib, json, threading, time
from collections import deque
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import numpy as np
import pandas as pd
import requests
from yoyo.datasets.ma_launch_followup50 import ensure_committed

ROOT=Path(__file__).resolve().parents[2]
COLS=['ts','open_time','open','high','low','close','volume']

def dump(p,x):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(x,ensure_ascii=False,indent=2,default=str)+'\n')

def validate(f, minutes, cutoff):
    if f.empty:return
    ts=f.ts.to_numpy(dtype='int64'); a=f[['open','high','low','close','volume']].to_numpy(float)
    if np.any(np.diff(ts)<=0) or np.any(ts+minutes*60000>cutoff):raise ValueError('ordering/cutoff violated')
    if not np.isfinite(a).all() or np.any(a[:,:4]<=0):raise ValueError('nonfinite/nonpositive price')
    if np.any(a[:,1]<np.maximum(a[:,0],a[:,3])) or np.any(a[:,2]>np.minimum(a[:,0],a[:,3])):raise ValueError('OHLC geometry')

def bounded_local(path, minutes, cutoff, earliest):
    """Timestamp-first reader; poison after cutoff must never be converted."""
    rows=[]; boundary=0; opener=gzip.open if str(path).endswith('.gz') else open
    with opener(path,'rt',newline='') as h:
        reader=csv.reader(h); head=next(reader); idx={k:i for i,k in enumerate(head)}
        name=next(k for k in ('ts','time','open_time') if k in idx)
        previous=None
        for raw in reader:
            s=raw[idx[name]]
            ms=int(s) if name=='ts' or s.isdigit() else int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()*1000)
            if previous is not None and ms<=previous:raise ValueError('not ascending')
            previous=ms
            if ms+minutes*60000>cutoff:boundary=1;break
            if ms<earliest:continue
            rows.append([ms,*[float(raw[idx[k]]) for k in COLS[2:]]])
    f=canonical(rows);validate(f,minutes,cutoff)
    return f, {'path':str(path),'boundary_timestamps_inspected':boundary,'future_ohlcv_converted':0}

def canonical(rows):
    f=pd.DataFrame(rows,columns=['ts',*COLS[2:]])
    f.insert(1,'open_time',pd.to_datetime(f.ts,unit='ms',utc=True))
    return f

def aggregate(f, source_minutes, target_minutes, cutoff):
    if target_minutes % source_minutes:raise ValueError('noninteger resampling ratio')
    if target_minutes==source_minutes:return f.copy()
    duration=target_minutes*60000; needed=target_minutes//source_minutes
    if f.empty:return f.copy()
    g=f.groupby((f.ts//duration)*duration,sort=True)
    out=g.agg(ts=('ts','first'),last_ts=('ts','last'),count=('ts','size'),
              open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),volume=('volume',lambda x:x.sum()))
    # Unique sorted inputs + exact count and endpoints imply every grid member.
    aligned=(f.ts % (source_minutes*60000)).eq(0).groupby((f.ts//duration)*duration).all()
    valid=(out['count']==needed)&(out.ts==out.index)&(out.last_ts==out.index+duration-source_minutes*60000)&(out.index+duration<=cutoff)&aligned
    out=out.loc[valid,['ts',*COLS[2:]]].reset_index(drop=True)
    out.insert(1,'open_time',pd.to_datetime(out.ts,unit='ms',utc=True))
    return out

class Client:
    def __init__(self,venue,output):
        self.venue=venue;self.output=output;self.lock=threading.Lock();self.next=0.;self.local=threading.local()
    def get(self,params):
        url='https://fapi.binance.com/fapi/v1/klines' if self.venue=='binance' else 'https://www.okx.com/api/v5/market/history-candles'
        if not hasattr(self.local,'session'):self.local.session=requests.Session()
        for attempt in range(3):
            with self.lock:
                pause=max(0,self.next-time.monotonic());time.sleep(pause);self.next=time.monotonic()+(.32 if self.venue=='binance' else .16)
            try:
                response=self.local.session.get(url,params=params,timeout=25)
                if response.status_code in (418,429):time.sleep(float(response.headers.get('Retry-After',15)))
                response.raise_for_status();body=response.json()
                if self.venue=='okx':
                    if body.get('code')!='0':raise ValueError(str(body)[:200])
                    rows=body['data']
                else:
                    if not isinstance(body,list):raise ValueError(str(body)[:200])
                    rows=body
                return rows,{'url':url,'params':params,'response_sha256':hashlib.sha256(response.content).hexdigest(),'rows':len(rows)}
            except Exception:
                if attempt==2:raise
                time.sleep(2**attempt)

def fetch(client,symbol,minutes,start,cutoff):
    step=minutes*60000; end=cutoff-cutoff%step; earliest=start-1200*step
    rows={};receipts=[];cursor=end
    for _ in range(10):
        if cursor<=earliest:break
        if client.venue=='binance':
            params={'symbol':symbol,'interval':f'{minutes}m' if minutes<60 else f'{minutes//60}h','endTime':cursor-1,'startTime':earliest,'limit':1500}
        else:params={'instId':symbol,'bar':f'{minutes}m','after':cursor,'before':earliest-1,'limit':300}
        raw,receipt=client.get(params);receipts.append(receipt)
        if not raw:break
        # Timestamp guard precedes any OHLC conversion; reject out-of-request data.
        stamps=[int(v[0]) for v in raw]
        if max(stamps)+step>end:raise ValueError('API returned future candle')
        for v,ts in zip(raw,stamps):
            if ts<earliest:continue
            if client.venue=='okx' and str(v[8])!='1':raise ValueError('unconfirmed candle')
            rows[ts]=[ts,*[float(x) for x in v[1:6]]]
        oldest=min(stamps)
        if oldest>=cursor:raise ValueError('nonprogressing page')
        cursor=oldest
        if client.venue=='binance' or len(raw)<300:break
    f=canonical([rows[k] for k in sorted(rows)]);validate(f,minutes,cutoff)
    return f,receipts

def run(plan_path):
    plan=json.loads(plan_path.read_text());source_commit=ensure_committed([Path(__file__),plan_path])
    out=ROOT/plan['experiment_dir'];output=out/'inputs';output.mkdir(exist_ok=True)
    cutoff=int(pd.Timestamp(plan['scan_end_utc']).timestamp()*1000);start=int(pd.Timestamp(plan['scan_start_utc']).timestamp()*1000)
    base=ROOT/plan['normalized_root'];inventory=[];choices={};errors=[]
    # One available venue per asset, chosen by coverage then stable Binance priority.
    tasks=[]
    for venue in ('binance','okx'):
        for p in sorted((base/venue).glob('*.csv.gz')):
            market=p.name.removesuffix('_30m.csv.gz'); asset=market.removesuffix('USDT') if venue=='binance' else market.removesuffix('-USDT-SWAP')
            if venue=='okx' and not market.endswith('-USDT-SWAP'):continue
            tasks.append((p,venue,market,asset))
    def local_job(task):
        p,venue,market,asset=task
        f,audit=bounded_local(p,30,cutoff,start-1200*240*60000-240*60000)
        return task,f,audit
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures={pool.submit(local_job,t):t for t in tasks}
        for num,fut in enumerate(as_completed(futures),1):
            t=futures[fut]
            try:
                task,f,audit=fut.result();p,venue,market,asset=task
                recent=not f.empty and int(f.ts.max())+30*60000>=start
                audit.update(venue=venue,market=market,asset=asset,rows=len(f),has_target_period=recent)
                inventory.append(audit)
                if recent:
                    score=(int(f.ts.max()),venue=='binance')
                    if asset not in choices or score>choices[asset][0]:choices[asset]=(score,task,f)
            except Exception as e:errors.append({'source':str(t[0]),'error':str(e)})
            if num%100==0:print('local inspected',num,'/',len(tasks),flush=True)
    dump(out/'local_inventory.json',inventory);dump(out/'source_errors.json',errors)
    specs=[];coverage=[]
    def save(f,asset,venue,market,minutes,origin):
        dest=output/f'{venue}_{market}_{minutes}m.csv'
        if dest.exists():
            # Resume only exact same derived bytes.
            prior=pd.read_csv(dest)
            if len(prior)!=len(f) or not np.array_equal(prior.ts.to_numpy(),f.ts.to_numpy()):raise ValueError('immutable input changed '+str(dest))
            if not np.allclose(prior[COLS[2:]].to_numpy(float),f[COLS[2:]].to_numpy(float),rtol=1e-14,atol=0):raise ValueError('immutable prices changed '+str(dest))
        else:f.to_csv(dest,index=False)
        desc={'path':str(dest.relative_to(ROOT)),'symbol':asset,'market':market,'venue':venue,'bar_minutes':minutes,'prefix_sha256':hashlib.sha256(dest.read_bytes()).hexdigest(),'rows':len(f),'origin':origin}
        specs.append(desc);coverage.append({**desc,'last_close_utc':str(pd.Timestamp(int(f.ts.max())+minutes*60000,unit='ms',tz='UTC')) if len(f) else None})
    for asset,(_,task,f) in sorted(choices.items()):
        p,venue,market,_=task
        for minutes in (30,60,240):
            existing=output/f'{venue}_{market}_{minutes}m.csv'
            if existing.exists():
                g=pd.read_csv(existing);validate(g,minutes,cutoff)
            else:
                g=aggregate(f,30,minutes,cutoff);g=g[g.ts>=start-1200*minutes*60000].reset_index(drop=True)
            save(g,asset,venue,market,minutes,str(p))
    dump(out/'high_timeframe_sources.json',specs)
    dump(out/'universe.json',[{'asset':a,'venue':v[1][1],'market':v[1][2]} for a,v in sorted(choices.items())])
    clients={v:Client(v,out) for v in ('binance','okx')}
    jobs=[(asset,t[1],t[2],m) for asset,(_,t,f) in sorted(choices.items()) for m in (3,5,15)]
    def network_job(job):
        asset,venue,market,m=job;dest=output/f'{venue}_{market}_{m}m.csv';receipt=out/'fetch_receipts'/f'{venue}_{market}_{m}m.json'
        if dest.exists() and receipt.exists():
            f=pd.read_csv(dest);validate(f,m,cutoff)
            if hashlib.sha256(dest.read_bytes()).hexdigest()!=json.loads(receipt.read_text())['sha256']:raise ValueError('resume SHA drift')
            return job,f,json.loads(receipt.read_text())['requests']
        f,r=fetch(clients[venue],market,m,start,cutoff)
        return job,f,r
    with ThreadPoolExecutor(max_workers=10) as pool:
        futs={pool.submit(network_job,j):j for j in jobs}
        for num,fut in enumerate(as_completed(futs),1):
            job=futs[fut];asset,venue,market,m=job
            try:
                _,f,r=fut.result();save(f,asset,venue,market,m,'bounded_public_api')
                dump(out/'fetch_receipts'/f'{venue}_{market}_{m}m.json',{'requests':r,'sha256':specs[-1]['prefix_sha256'],'future_ohlcv_converted':0})
            except Exception as e:
                errors.append({'asset':asset,'venue':venue,'market':market,'minutes':m,'error':str(e)})
            if num%50==0:print('native fetched',num,'/',len(jobs),'errors',len(errors),flush=True);dump(out/'source_errors.json',errors)
    dump(out/'coverage.json',coverage);dump(out/'source_errors.json',errors)
    scan_plan={**plan,'sources':specs,'output_dir':str(out/'results'),'source_builder_commit':source_commit}
    dump(out/'scan_plan.json',scan_plan)
    print(json.dumps({'assets':len(choices),'sources':len(specs),'errors':len(errors),'scan_plan':str(out/'scan_plan.json')}),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--plan',type=Path,required=True);run(p.parse_args().plan)
