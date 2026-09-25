"""All archived/current Binance USDT perpetuals, SPIKE two-month code replay.

Source: owner clarified all symbols on 2026-09-25. Only universe and observation
window expand. Five-minute facts reuse the existing completed-M15 EMA120 port;
15m/60m and all fills/stops/MFE/random controls reuse the frozen parent engines.
Features consume chart closes and already completed HTF values. Official REST
tails extend immutable research input; no live cache, production or orders.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import subprocess
import time
import urllib.request

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v128_recent as p
from yoyo.evaluation import spike_lowtf_v1_v126 as low
from yoyo.evaluation import spike_v128_recent_data as data
from yoyo.evaluation.spike_v128_long_replay import merge_history
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path('experiments/active/exp-spike-market-two-months-20260925-v1')
CONFIG = EXP/'config.json'
TABLES = ('trades','statuses','candidates','controls')
HTF = {5:15,15:60,60:240}


def sources():
    paths = tuple(dict.fromkeys([CONFIG,Path(__file__),p.PINE,p.PINE_V128,
        *_local_transitive_python((Path(__file__),))]))
    if not _committed(paths):
        raise ValueError('Commit builders/config/imported sources before construction')
    return {str(x):p.digest(x) for x in paths}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()


def fetch_one(args):
    symbol, old, meta, cfg = args
    root=Path(cfg['data_dir']);dest=root/'audits'/f'{symbol}.json'
    if dest.exists():
        saved=json.loads(dest.read_text())
        if saved['config_hash']!=p.digest(CONFIG) or p.digest(Path(saved['path']))!=saved['sha256']:
            raise ValueError('Data resume identity mismatch')
        return saved
    cols=['ts','open','high','low','close','volume']
    if old:
        if p.digest(Path(old['path']))!=old['sha256']:raise ValueError('Parent OHLCV changed')
        frame=pd.read_csv(old['path'],usecols=cols)
    else:
        frame=pd.DataFrame(columns=cols)
    begin=(pd.Timestamp(int(frame.ts.iloc[-1]),unit='ms',tz='UTC') if len(frame)
        else pd.Timestamp(cfg['warmup_start']))
    tail=data.fetch_rest(symbol,begin,pd.Timestamp(cfg['end']))
    unavailable=tail is None
    if tail is None:tail=pd.DataFrame(columns=cols)
    tail_path=root/'tails'/f'{symbol}.csv.gz'
    tail.to_csv(tail_path,index=False,compression={'method':'gzip','mtime':0})
    if len(frame) and len(tail):
        joined,overlap=merge_history(frame,tail,cfg)
    else:
        joined=data.merge_rows([f for f in [frame,tail] if len(f)],pd.Timestamp(cfg['warmup_start']),pd.Timestamp(cfg['end']))
        overlap=0
    if len(joined):
        prices=joined[['open','high','low','close','volume']].astype(float)
        if not (np.isfinite(prices).all().all() and prices.low.gt(0).all() and prices.volume.ge(0).all()
            and prices.high.ge(prices[['open','low','close']].max(axis=1)).all()
            and prices.low.le(prices[['open','high','close']].min(axis=1)).all()):raise ValueError('Invalid OHLCV')
    path=root/'series'/f'{symbol}.csv.gz'
    joined.to_csv(path,index=False,compression={'method':'gzip','mtime':0})
    recent=joined[joined.ts.ge(pd.Timestamp(cfg['start']).value//10**6)]
    expected=int((pd.Timestamp(cfg['end'])-pd.Timestamp(cfg['start'])).total_seconds()/300)
    result={'symbol':symbol,'path':str(path),'sha256':p.digest(path),'meta':meta,
        'config_hash':p.digest(CONFIG),'rows':len(joined),'recent_rows':len(recent),
        'window_expected_5m':expected,'window_missing_5m':expected-len(recent),
        'recent_positive_volume_rows':int(recent.volume.gt(0).sum()),
        'first_ms':None if joined.empty else int(joined.ts.iloc[0]),
        'last_ms':None if joined.empty else int(joined.ts.iloc[-1]),
        'gap_count':int(joined.ts.diff().dropna().ne(300000).sum()),
        'tail_status':'unknown_or_delisted' if unavailable else 'ok',
        'tail_rows':len(tail),'overlap_verified_rows':overlap,
        'parent':old,'tail':{'path':str(tail_path),'sha256':p.digest(tail_path)},
        'status':'complete_input_audit','fetched_at':pd.Timestamp.now(tz='UTC').isoformat()}
    p.dump(dest,result);return result


def prepare(workers):
    cfg=json.loads(CONFIG.read_text());code=sources();root=Path(cfg['data_dir'])
    for name in ['audits','series','tails']: (root/name).mkdir(parents=True,exist_ok=True)
    snap=root/'exchange_info.json'
    if not snap.exists():
        request=urllib.request.Request('https://fapi.binance.com/fapi/v1/exchangeInfo',headers=data.prior.archives.REQUEST_HEADERS)
        with urllib.request.urlopen(request,timeout=30) as response: info=json.load(response)
        p.dump(snap,info)
    info=json.loads(snap.read_text())
    parent=json.loads(Path(cfg['source_manifest']).read_text())
    if parent.get('failed'):raise ValueError('Prior manifest records failures')
    old={r['symbol']:r for r in parent['streams']};old_meta=p.source.symbol_meta()
    current={r['symbol']:r for r in info['symbols'] if r.get('quoteAsset')=='USDT' and r.get('contractType')=='PERPETUAL'}
    universe=sorted(set(old)|set(current))
    metadata={}
    for s in universe:
        c=current.get(s,{})
        if s in old: meta=old_meta[s].copy()
        else:meta={'asset':c.get('baseAsset',''),'tick':next(float(f['tickSize']) for f in c['filters'] if f['filterType']=='PRICE_FILTER')}
        meta.update(current_status=c.get('status','absent'),onboard_ms=c.get('onboardDate'),delivery_ms=c.get('deliveryDate'),inherited=s in old)
        metadata[s]=meta
    p.dump(root/'universe.json',{'symbols':universe,'archived_count':len(old),'current_count':len(current),
        'additions':sorted(set(current)-set(old)),'archived_absent_current':sorted(set(old)-set(current)),
        'snapshot':str(snap),'snapshot_sha256':p.digest(snap),'metadata':metadata})
    done,failed=[],[]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs={pool.submit(fetch_one,(s,old.get(s),metadata[s],cfg)):s for s in universe}
        for n,f in enumerate(as_completed(jobs),1):
            try:done.append(f.result())
            except Exception as exc:failed.append({'symbol':jobs[f],'error':repr(exc)})
            if n==1 or n%25==0 or n==len(jobs):print(json.dumps({'stage':'data','done':n,'total':len(jobs),'errors':len(failed)}),flush=True)
    p.dump(root/'manifest.json',{'complete':not failed,'failed':failed,'requested':universe,
        'streams':sorted(done,key=lambda r:r['symbol']),'code':code,'config':cfg,
        'source_manifest_sha256':p.digest(Path(cfg['source_manifest'])),'universe_sha256':p.digest(root/'universe.json')})
    if failed:raise RuntimeError('Some symbols unavailable; see manifest, do not silently omit')


def stream(base,symbol,meta,minutes,cfg):
    tick=float(meta['tick']);bars,partial=p.complete_bars(base,minutes)
    higher,hpartial=p.complete_bars(base,HTF[minutes])
    expected=int((pd.Timestamp(cfg['end'])-pd.Timestamp(cfg['start'])).total_seconds()/(60*minutes))
    summary={'symbol':symbol,'minutes':minutes,'chart_bars_total':len(bars),'higher_bars':len(higher),
        'window_bars_expected':expected,'partial_chart_buckets':partial,'partial_higher_buckets':hpartial,
        'first':None if bars.empty else str(bars.index[0]),'last':None if bars.empty else str(bars.index[-1])}
    if len(bars)<2 or not bars.volume.gt(0).any():
        actual=int(((bars.index>=pd.Timestamp(cfg['start']))&(bars.index<pd.Timestamp(cfg['end']))).sum())
        summary.update(window_bars_actual=actual,window_gap_count=expected-actual,valid_ready_window_bars=0,candidates=0,v9_candidates=0,joint_candidates=0,chart_gaps=0,zero_activity=True)
        return {name:pd.DataFrame() for name in TABLES},summary
    facts=low.pine_facts(bars,base,meta['asset'],tick,5) if minutes==5 else p.facts_for(bars,base,meta['asset'],tick,minutes)
    frame=facts['frame'];in_window=p._window(frame.index,minutes,cfg)
    local=p.line_events(frame.open,frame.high,frame.low,frame.close,frame.atr,can_run=facts['can_run'],gap=facts['gap'],tick=tick,
        confirmed_long=facts['v9_long'],parent_high=facts['parent_high'],parent_low=facts['parent_low'],
        raw_side=facts['side'],long_alive=facts['long_alive'],ref_long_exit=facts['ref_long_exit'])
    hf=p.features(higher);hg=p._data_gap(hf,HTF[minutes]).to_numpy(bool)
    hl=p.line_events(hf.open,hf.high,hf.low,hf.close,hf.atr,can_run=~hg&hf.atr.gt(0).to_numpy(),gap=hg,tick=tick,htf=True)
    mapped=p._map_htf(hf,frame,hl.winner_events,minutes,HTF[minutes])
    joints=p.pair_events(frame.close.to_numpy(),bar_times=frame.index.asi8//60000000000,
        chart_breaks=local.events,htf_breaks=mapped,box_id=facts['box']['box_entry'],confirmed_long=facts['v9_long'],gap=facts['gap'])
    identity={'venue':'binance_um','symbol':symbol,'asset':meta['asset'],'timeframe_min':minutes}
    prepared=p.source.prepared_arm(frame,facts['gap'],facts['side'],f'binance_um:{symbol}:{minutes}m',identity,minutes,tick)
    events={'v9_both':[],'joint':[]}
    for arm,items in [('v9_both',[(int(i),int(facts['side'][i])) for i in np.flatnonzero(facts['v9'])]),
        ('joint',[(int(e['joint_i']),1) for e in joints])]:
        for i,side in items:
            close=frame.index[i]+pd.Timedelta(minutes=minutes)
            if close>=pd.Timestamp(cfg['end']):continue
            events[arm].append({**identity,'arm':arm,'signal_i':i,'side':side,'signal_bar_open':frame.index[i],
                'signal_close':close,'in_window':bool(in_window[i])})
    trades,statuses=[],[]
    for arm,evs in events.items():
        t,s=p.serial(prepared,evs,arm,f'binance_um:{symbol}:{minutes}m');trades.extend(t);statuses.extend(s)
    controls=p.matched_controls(prepared,trades,cfg) if trades else pd.DataFrame()
    candidates=[e for evs in events.values() for e in evs if e['in_window']]
    table=pd.DataFrame(trades)
    if len(table):
        closed=table[~table.censored.astype(bool)]
        np.testing.assert_allclose(closed.gross_return-closed.net_return,.002,atol=1e-12)
        np.testing.assert_allclose(closed.net_r,closed.net_return/closed.initial_risk_frac,atol=1e-10)
    # Candle coverage uses candle opens; event windows use close/next-open clocks.
    actual=int(((frame.index>=pd.Timestamp(cfg['start']))&(frame.index<pd.Timestamp(cfg['end']))).sum())
    summary.update(window_bars_actual=actual,window_gap_count=expected-actual,
        valid_ready_window_bars=int((facts['ready']&~facts['gap']&in_window).sum()),
        candidates=len(candidates),v9_candidates=sum(e['in_window'] for e in events['v9_both']),
        joint_candidates=sum(e['in_window'] for e in events['joint']),chart_gaps=int(facts['gap'].sum()),zero_activity=False)
    return {'trades':table,'statuses':pd.DataFrame(statuses),'candidates':pd.DataFrame(candidates),'controls':controls},summary


def worker(args):
    row,minutes,cfg,rid,out=args;folder=Path(out)/'streams'/f"{row['symbol']}_{minutes}m"
    receipt=folder/'receipt.json'
    if receipt.exists():
        old=json.loads(receipt.read_text())
        if old['run_identity']!=rid:raise ValueError('Replay resume identity changed')
        for name,digest in old['files'].items():
            if p.digest(folder/name)!=digest:raise ValueError('Replay output changed')
        return old
    if p.digest(Path(row['path']))!=row['sha256']:raise ValueError('Replay data changed')
    started=time.monotonic();raw=pd.read_csv(row['path'])
    base=raw.set_index(pd.DatetimeIndex(pd.to_datetime(raw.ts,unit='ms',utc=True)))[['open','high','low','close','volume']]
    tables,summary=stream(base,row['symbol'],row['meta'],minutes,cfg)
    folder.mkdir(parents=True,exist_ok=True)
    for name,frame in tables.items():frame.to_csv(folder/f'{name}.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    result={'symbol':row['symbol'],'minutes':minutes,'summary':summary,'input_sha256':row['sha256'],
        'run_identity':rid,'files':{f'{name}.csv.gz':p.digest(folder/f'{name}.csv.gz') for name in TABLES},
        'wall_seconds':time.monotonic()-started}
    p.dump(receipt,result);return result


def replay(workers,symbols=None,output=None):
    cfg=json.loads(CONFIG.read_text());code=sources();data_root=Path(cfg['data_dir'])
    manifest=json.loads((data_root/'manifest.json').read_text())
    if manifest['failed'] or not manifest['complete']:raise ValueError('Data stage incomplete')
    selected=[r for r in manifest['streams'] if symbols is None or r['symbol'] in symbols]
    out=Path(output) if output else EXP/'run_v1';out.mkdir(parents=True,exist_ok=True)
    identity={'config':cfg,'code':code,'input_manifest_sha256':p.digest(data_root/'manifest.json'),
        'symbols':[r['symbol'] for r in selected],'timeframes':cfg['timeframes'],'subset':symbols is not None}
    rid=fingerprint(identity);p.dump(out/'identity.json',identity)
    results,errors=[],[]
    tasks=[(r,m,cfg,rid,str(out)) for r in selected for m in cfg['timeframes']]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(worker,t):(t[0]['symbol'],t[1]) for t in tasks}
        for n,f in enumerate(as_completed(futures),1):
            try:results.append(f.result())
            except Exception as exc:errors.append({'stream':futures[f],'error':repr(exc)})
            if n==1 or n%25==0 or n==len(tasks):
                print(json.dumps({'stage':'replay','done':n,'total':len(tasks),'errors':len(errors)}),flush=True)
                p.dump(out/'progress.json',{'done':n,'total':len(tasks),'errors':errors})
    p.dump(out/'manifest.json',{'complete':not errors,'subset':symbols is not None,'run_identity':rid,
        'errors':errors,'receipts':sorted(results,key=lambda r:(r['symbol'],r['minutes']))})
    if errors:raise RuntimeError('Replay has errors; see manifest')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=['data','replay']);parser.add_argument('--workers',type=int,default=6)
    parser.add_argument('--symbols',nargs='+');parser.add_argument('--output')
    args=parser.parse_args()
    if args.stage=='data':prepare(args.workers)
    else:replay(args.workers,args.symbols,args.output)
