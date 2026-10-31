"""Pre-touch Pyharmonics confluence, original Monday1618 execution unchanged.

Owner 2026-10-31 authorizes only a harmonic admission overlay. Read OHLCV from
at most 1000 completed configured-timeframe bars before the original touch; never
read the touch candle or its outcome for this new feature. XABC pivots require
10 observations on both sides. D may be a provisional prefix endpoint, so
this is a known candidate zone, not a claim of a confirmed future reversal.
Pyharmonics owns peak/pattern search. The adapter intersects its tolerance-
adjusted XD and BCD rules at the fixed Monday level and expires stale zones.
The package's bearish completion-price helper uses max(A,C); the search uses
the appropriate trough. Mirror-symmetric projection below follows the search.

All signal fills, original fill-bar scores, targets, child orders and account
functions are reused. Only absent matched-control fills may run in Nautilus.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
import gzip
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT, dump, file_stamp
from yoyo.evaluation.monday_limit_backstop import CODEX
from yoyo.evaluation.monday_limit_be_scalein_compare import clean
from yoyo.evaluation.monday_limit_paper_accounts import committed
from yoyo.evaluation import monday_order_only as original
from yoyo.evaluation.v3_causal_close_entry import read_csv

EXP = ROOT/'experiments/active/exp-monday-harmonic-confluence-20261031-v1'
STEP = pd.Timedelta(minutes=5)
CODE = ['yoyo/evaluation/monday_harmonic.py', 'yoyo/evaluation/monday_order_only.py']


def interval_step(hc):
    return {'5m':STEP,'15m':pd.Timedelta(minutes=15),'1h':pd.Timedelta(hours=1)}[hc['interval']]


def aggregate_bars(frame, hc):
    """UTC left-labelled bars; a missing 5m constituent invalidates the whole bar.

    Precomputing the complete series is safe only with scan_at's close boundary.
    No forward fill, interpolation, partial candle or external data is used.
    """
    step=interval_step(hc)
    if step==STEP:return frame
    assert frame.index.is_unique and frame.index.is_monotonic_increasing
    assert (frame.index==frame.index.floor(STEP)).all()
    res=frame[['open','high','low','close','volume']].resample(step,origin='epoch',label='left',closed='left')
    out=res.agg(dict(open='first',high='max',low='min',close='last',volume='sum'))
    counts=res.count();complete=counts.eq(int(step/STEP)).all(axis=1)
    out.loc[~complete,:]=np.nan
    return out


def scan_raw(frame, cutoff, symbol, side, level, hc):
    """Convenience entry point for raw 5m prefix/future-mutation validation."""
    return scan_at(aggregate_bars(frame,hc),cutoff,symbol,side,level,hc,
                   source_frame=frame if interval_step(hc)>STEP else None)


def baseline(cfg):
    return read_csv(ROOT/cfg['baseline_ledger'])


def events(cfg):
    b = baseline(cfg)
    parts = [original.select_track(b, t, cfg, True) for t in original.TRACKS]
    x = pd.concat(parts, ignore_index=True)
    x = x[~x.variant.str.endswith('_add')].copy()
    for col in ('side', 'entry', 'entry_i', 'entry_time'):
        if x.groupby('event')[col].nunique().gt(1).any():
            raise ValueError(f'Inconsistent parent touch: {col}')
    x = x.drop_duplicates('event')[['event','side','entry','entry_i','entry_time']]
    x['symbol'] = x.event.str.split(':').str[0]
    x['cutoff'] = pd.to_datetime(x.entry_time, utc=True)-STEP
    return x.sort_values(['symbol','cutoff','event']).reset_index(drop=True)


def projection_band(y, bullish, definitions, name):
    """Intersect library XD and BCD ranges; tolerance is relative to leg ratios.

    This is a projection from known XABC, not a fabricated D observation.
    Both directions follow HarmonicSearch's extrema-based Fibonacci matrix.
    """
    from pyharmonics import constants as c
    X,A,B,C = map(float, y[:4])
    side = 1 if bullish else -1
    peak = max(A,C) if bullish else min(A,C)
    bands = []
    for origin, amplitude, stage in [(peak,abs(peak-X),c.XABCD), (C,abs(C-B),c.BCD)]:
        if amplitude <= 0:
            return None
        ratios = definitions[stage][name]
        prices = [origin-side*amplitude*ratios[k] for k in (c.MIN,c.MAX)]
        bands.append(sorted(prices))
    lower,upper = max(b[0] for b in bands),min(b[1] for b in bands)
    return (lower,upper) if 0 < lower <= upper else None


def scan_at(frame, cutoff, symbol, side, level, hc, *, all_endpoints=False, source_frame=None):
    """Causal feature boundary: only rows whose close time is <= cutoff.

    Uses OHLCV from the last <=1000 completed configured-timeframe rows.
    Raw completed 5m bars after the last higher close only invalidate old zones;
    they never form or confirm a higher-timeframe point.
    Future mutation and prefix truncation must leave every returned field equal.
    """
    from pyharmonics import OHLCTechnicals
    from pyharmonics.search import HarmonicSearch
    from pyharmonics import constants as c
    cutoff = pd.Timestamp(cutoff);step=interval_step(hc);boundary=cutoff.floor(step)
    if step>STEP and source_frame is None:raise ValueError('Higher timeframe needs raw pre-touch invalidation data')
    n = int(frame.index.searchsorted(boundary, side='left'))
    w = frame.iloc[max(0,n-hc['lookback_bars']):n][['open','high','low','close','volume']].copy()
    w.index = w.index.as_unit('ns')
    meta = dict(observable=False,admitted=False,bars=len(w),cutoff=str(cutoff),
                last_input_close=str(w.index[-1]+step) if len(w) else None,
                candidate_count=0,confluent_count=0,reason='insufficient_history')
    if len(w) < hc['min_bars']:
        return meta,[]
    if w.index[-1]+step != boundary or not np.all(np.diff(w.index.asi8)==step.value):
        meta['reason']='noncontiguous_history';return meta,[]
    if not np.isfinite(w.to_numpy(float)).all():
        meta['reason']='nonfinite_history';return meta,[]
    tail=w.iloc[:0]
    if step>STEP:
        lo=source_frame.index.searchsorted(boundary);hi=source_frame.index.searchsorted(cutoff)
        tail=source_frame.iloc[lo:hi][['open','high','low','close','volume']]
        expected=pd.date_range(boundary,cutoff,freq=STEP,inclusive='left')
        if not tail.index.as_unit('ns').equals(expected.as_unit('ns')) or not np.isfinite(tail.to_numpy(float)).all():
            meta['reason']='noncontiguous_partial_tail';return meta,[]
        meta.update(harmonic_interval=hc['interval'],invalidation_last_close=str(cutoff))
    td = OHLCTechnicals(w,symbol,hc['interval'],peak_spacing=hc['peak_spacing'])
    hs = HarmonicSearch(td,fib_tolerance=hc['fib_tolerance'],check_anchor=True)
    desired_type = 0 if side==1 else 1
    positions = [i for i,p in enumerate(td.peak_data) if p[2]==desired_type]
    latest = td.peak_data[positions[-1]][0] if positions else -1
    # Older D endpoints were unconditionally discarded below. Limit only the
    # search, retaining the full pivot matrix and all XABC anchor choices.
    limit = -1 if all_endpoints else len(td.peak_data)-positions[-1] if positions else 1
    hs.search(limit_to=limit);hs.forming(limit_to=limit,percent_c_to_d=hc['percent_c_to_d'])
    found = hs.get_patterns(family=hs.XABCD)[hs.XABCD] + hs.get_patterns(family=hs.XABCD,formed=False)[hs.XABCD]
    records=[];seen=set();rejected=Counter()
    for p in found:
        if p.bullish != (side==1):
            continue
        ix = w.index.get_indexer(pd.DatetimeIndex(p.x))
        if np.any(ix<0) or np.any(np.diff(ix)<=0):
            rejected['unordered_points']+=1;continue
        if ix[-1] != latest:
            rejected['stale_endpoint']+=1;continue
        spacing=hc['peak_spacing']
        if np.any(ix[:4]<spacing) or np.any(ix[:4]+spacing>=len(w)):
            rejected['unconfirmed_xabc']+=1;continue
        band=projection_band(p.y,p.bullish,hs.PATTERNS,p.name)
        if band is None:
            rejected['disjoint_projection']+=1;continue
        lo,hi=band; C=float(p.y[3])
        after_c=w.iloc[ix[3]+1:];after_d=w.iloc[ix[4]+1:]
        if side==1:
            invalid=(after_c.low.min()<lo) or (len(after_d)>0 and after_d.high.max()>C)
            invalid=invalid or (len(tail)>0 and (tail.low.min()<lo or tail.high.max()>C))
        else:
            invalid=(after_c.high.max()>hi) or (len(after_d)>0 and after_d.low.min()<C)
            invalid=invalid or (len(tail)>0 and (tail.high.max()>hi or tail.low.min()<C))
        if invalid:
            rejected['invalidated_before_touch']+=1;continue
        key=(p.name,tuple(map(str,p.x)))
        if key in seen:continue
        seen.add(key)
        records.append(dict(name=p.name,bullish=bool(p.bullish),formed=bool(p.formed),
            points=[str(v) for v in p.x],prices=[float(v) for v in p.y],
            xabc_confirmed_at=str(w.index[ix[3]+spacing]+step),
            d_confirmed=bool(ix[4]+spacing<len(w)),prz_low=float(lo),prz_high=float(hi),
            confluence=bool(lo<=level<=hi),retraces={str(k):float(v) for k,v in p.retraces.items()},
            library_raw_completion=[float(p.completion_min_price),float(p.completion_max_price)]))
    records.sort(key=lambda p:(p['name'],p['points'],p['formed']))
    meta.update(observable=True,admitted=any(p['confluence'] for p in records),
                candidate_count=len(records),confluent_count=sum(p['confluence'] for p in records),
                reason='ok',rejected=dict(rejected))
    return meta,records


def scan_symbol(symbol, config, rows):
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    cfg=json.loads(Path(config).read_text());out=Path(config).parent/'scan'/symbol
    out.mkdir(parents=True,exist_ok=True)
    if (out/'done.json').exists():return json.loads((out/'done.json').read_text())
    started=time.perf_counter();job=json.loads((CODEX/'symbols'/symbol/'identity.json').read_text())['job']
    stamps=[file_stamp(p) for p in job['paths']]
    f,_=load_job(job);old=json.loads((CODEX/'symbols'/symbol/'done.json').read_text())
    assert (len(f),str(f.index[0]),str(f.index[-1]))==(old['rows'],old['first'],old['last'])
    gates=[];patterns=[];checks=[];higher=interval_step(cfg['harmonic'])>STEP
    prepared=aggregate_bars(f,cfg['harmonic'])
    for r in rows:
        cutoff=pd.Timestamp(r['cutoff']);k=int(r['entry_i'])
        assert f.index[k]==cutoff,(symbol,k,cutoff,f.index[k])
        gate,candidates=scan_at(prepared,cutoff,symbol,int(r['side']),float(r['entry']),cfg['harmonic'],
                                source_frame=f if higher else None)
        gates.append(dict(r,**gate))
        patterns.extend(dict(event=r['event'],cutoff=str(cutoff),**p) for p in candidates)
        # One actual prefix per coin; mutate every future candle, not only one.
        if not checks and gate['observable']:
            n=f.index.searchsorted(cutoff)
            factor=int(interval_step(cfg['harmonic'])/STEP)
            start=max(0,n-(cfg['harmonic']['lookback_bars']+2)*factor) if higher else max(0,n-cfg['harmonic']['lookback_bars'])
            tail=f.iloc[start:min(len(f),n+40)].copy()
            tail.loc[tail.index>=cutoff,['open','high','low','close','volume']]*=100
            other=scan_raw(tail,cutoff,symbol,int(r['side']),float(r['entry']),cfg['harmonic'])
            prefix=scan_raw(f.iloc[:n],cutoff,symbol,int(r['side']),float(r['entry']),cfg['harmonic'])
            assert (gate,candidates)==other==prefix
            checks.append(dict(event=r['event'],prefix_equal=True,future_mutation_equal=True))
    g=pd.DataFrame(gates);g.drop(columns=['rejected'],errors='ignore').to_csv(out/'gates.csv.gz',index=False)
    with gzip.open(out/'patterns.jsonl.gz','wt') as handle:
        for row in patterns:handle.write(json.dumps(row)+'\n')
    result=dict(symbol=symbol,events=len(g),observable=int(g.observable.sum()),admitted=int(g.admitted.sum()),
                patterns=len(patterns),sources=stamps,causality_checks=checks,seconds=time.perf_counter()-started)
    dump(out/'done.json',result);return result


def run_scan(config,workers):
    cfg=json.loads(config.read_text());root=config.parent
    committed(*CODE,str(config.relative_to(ROOT)))
    assert version('pyharmonics')==cfg['harmonic']['version']
    ev=events(cfg);ev['cutoff']=ev.cutoff.astype(str)
    ev.to_csv(root/'events.csv.gz',index=False)
    with ProcessPoolExecutor(workers) as pool:
        jobs={pool.submit(scan_symbol,s,str(config),x.to_dict('records')):s for s,x in ev.groupby('symbol',sort=True)}
        for i,fu in enumerate(as_completed(jobs),1):
            s=jobs[fu]
            try:r=fu.result()
            except Exception as error:
                r=dict(symbol=s,error=repr(error));dump(root/'scan'/s/'error.json',r)
            print(json.dumps(dict(progress=i,total=len(jobs),**r)),flush=True)
    receipts=[];tabs=[]
    for s in sorted(ev.symbol.unique()):
        d=root/'scan'/s
        if not (d/'done.json').exists():raise RuntimeError(f'Incomplete harmonic scan: {s}')
        receipts.append(json.loads((d/'done.json').read_text()));tabs.append(read_csv(d/'gates.csv.gz'))
    gates=pd.concat(tabs,ignore_index=True)
    assert gates.event.is_unique and set(gates.event)==set(ev.event)
    gates.to_csv(root/'gates.csv.gz',index=False)
    dump(root/'scan.json',dict(events=len(gates),symbols=len(receipts),observable=int(gates.observable.sum()),
        admitted=int(gates.admitted.sum()),causality_checks=sum(len(r['causality_checks']) for r in receipts),
        unavailable=gates.loc[~gates.observable,'reason'].value_counts().to_dict(),
        package_versions={p:version(p) for p in ('pyharmonics','numpy','pandas','ta')},
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        interval=cfg['harmonic']['interval'],complete_higher_bars_only=True,
        no_touch_bar=True,no_model_fit=True,parameter_search=False))


def native_controls(config,workers):
    """Only selected events; existing runner reuses exact signal/control fills."""
    cfg=json.loads(config.read_text());committed(*CODE,str(config.relative_to(ROOT)))
    admitted=original.admitted_population(cfg);syms=sorted(set(admitted.index.str.split(':').str[0]))
    with ProcessPoolExecutor(workers) as pool:
        jobs={pool.submit(original.process,s,str(config)):s for s in syms}
        for i,fu in enumerate(as_completed(jobs),1):
            try:r=fu.result()
            except Exception as error:r=dict(symbol=jobs[fu],error=repr(error))
            print(json.dumps(dict(progress=i,total=len(jobs),**r)),flush=True)


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['scan','controls'])
    ap.add_argument('--config',type=Path,default=EXP/'config.json');ap.add_argument('--workers',type=int,default=4)
    args=ap.parse_args();config=args.config.resolve()
    if args.action=='scan':run_scan(config,args.workers)
    else:native_controls(config,args.workers)
