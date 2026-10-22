"""Owner-image Monday Fib first-touch study on Binance BTC perpetual 1m bars.

Source levels match monday_range_v2_37.pine fibPrices: both range endpoints.
Monday high/low use ONLY that completed NY day; pending orders begin Tuesday.
Controls use completed15m ATR14/price, never future outcomes. No fitting.
Nautilus1.231.0 executes resting entries, stops and two independent target legs;
0.8/0.2 accounting is equivalent to fixed partial exits without stop movement.
An independent path audit validates native fills, never supplies native exits.
Public missing-tail endpoint: https://fapi.binance.com/fapi/v1/klines .
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import subprocess
import time
import urllib.parse
import urllib.request
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump, file_stamp
from yoyo.evaluation.monday_range_v2_2_reference import atr
from yoyo.evaluation.pepe_range_exits import inference

EXP = ROOT/'experiments/active/exp-btc-monday-fib1618-20261022-v1'
OHLC = ['open','high','low','close']
CODE = ['yoyo/evaluation/monday_fib_limits.py',
        'yoyo/layers/l3_backtest/nautilus_independent.py',
        'tests/evaluation/test_monday_fib_limits.py']


def aligned(raw):
    f=raw.copy()
    f.index=pd.DatetimeIndex(pd.to_datetime(f.ts,unit='ms',utc=True)
                           +pd.Timedelta(minutes=1)).as_unit('ns')
    return f[OHLC]


def load_data(cfg):
    cache=EXP/'candles.csv.gz'
    if cache.exists():
        return aligned(pd.read_csv(cache,float_precision='round_trip'))
    if cfg.get('venue') == 'OKX USDT perpetual':
        return load_okx_data(cfg,cache)
    source=ROOT/cfg['archive']
    raw=pd.read_csv(source,usecols=['ts',*OHLC],float_precision='round_trip')
    warm=int(pd.Timestamp(cfg['warmup']).value//10**6)
    end=int(pd.Timestamp(cfg['end']).value//10**6)
    raw=raw[(raw.ts>=warm)&(raw.ts<end)]
    # Retain a full hour of overlap for cross-source equality, no content hashes.
    begin=int(raw.ts.max())-59*60000
    cursor=begin
    folder=EXP/'inputs';folder.mkdir(exist_ok=True)
    batches=[];urls=[]
    while cursor<end:
        p=folder/f'binance_BTCUSDT_1m_{cursor}.json'
        query=urllib.parse.urlencode(dict(symbol='BTCUSDT',interval='1m',
            startTime=cursor,endTime=end-1,limit=1500))
        url='https://fapi.binance.com/fapi/v1/klines?'+query
        if p.exists():
            records=json.loads(p.read_text())
        else:
            with urllib.request.urlopen(url,timeout=30) as response:
                records=json.load(response)
            if not isinstance(records,list) or not records:
                raise RuntimeError(f'Unexpected or empty Binance response at {cursor}')
            dump(p,records)
        if int(records[0][0]) != cursor:
            raise ValueError('Missing beginning of API tail')
        block=pd.DataFrame([[int(x[0]),*[float(v) for v in x[1:5]]] for x in records],
                           columns=['ts',*OHLC])
        if not (block.ts.diff().dropna()==60000).all():
            raise ValueError('Missing minute in API page')
        batches.append(block);urls.append(url)
        cursor=int(block.ts.iloc[-1])+60000
        print('tail',str(pd.Timestamp(cursor,unit='ms',tz='UTC')),flush=True)
    tail=pd.concat(batches,ignore_index=True)
    overlap=raw.merge(tail,on='ts',suffixes=('_old','_new'))
    assert len(overlap)==60
    for col in OHLC:
        np.testing.assert_allclose(overlap[col+'_old'],overlap[col+'_new'],rtol=0,atol=1e-8)
    merged=pd.concat([raw,tail[tail.ts>raw.ts.max()]],ignore_index=True)
    assert merged.ts.is_unique and (merged.ts.diff().dropna()==60000).all()
    assert merged.ts.iloc[-1]+60000==end
    assert (merged.high>=merged[OHLC].max(axis=1)).all()
    assert (merged.low<=merged[OHLC].min(axis=1)).all()
    merged.to_csv(cache,index=False)
    dump(EXP/'data_receipt.json',dict(archive=file_stamp(source),api_urls=urls,
        archive_rows=len(raw),new_rows=len(merged)-len(raw),overlap_rows=len(overlap),
        total_rows=len(merged),first_open=str(pd.Timestamp(merged.ts.iloc[0],unit='ms',tz='UTC')),
        last_close=str(pd.Timestamp(end,unit='ms',tz='UTC')),gaps=0,no_content_hash=True))
    return aligned(merged)


def load_okx_data(cfg,cache):
    """Reuse named OKX archives; only fetch a missing confirmed1m tail."""
    from src.data.fetch_okx import _request, API
    warm=int(pd.Timestamp(cfg['warmup']).value//10**6)
    end=int(pd.Timestamp(cfg['end']).value//10**6)
    parts=[]
    for filename in cfg['inputs']:
        raw=pd.read_csv(ROOT/filename,usecols=['ts',*OHLC],float_precision='round_trip')
        parts.append(raw[(raw.ts>=warm)&(raw.ts<end)])
    old=pd.concat(parts,ignore_index=True).sort_values('ts')
    duplicates=old[old.ts.duplicated(keep=False)]
    if len(duplicates) and (duplicates.groupby('ts')[OHLC].nunique().to_numpy()>1).any():
        raise ValueError('Historical OKX sources disagree on overlapping OHLC')
    old=old.drop_duplicates('ts').reset_index(drop=True)
    begin=int(old.ts.max())-59*60000;cursor=end;pages=[];urls=[]
    folder=EXP/'inputs';folder.mkdir(exist_ok=True)
    while cursor>begin:
        p=folder/f'okx_BTC_USDT_SWAP_1m_{cursor}.json'
        url=API+'?'+urllib.parse.urlencode(dict(instId='BTC-USDT-SWAP',bar='1m',limit=100,after=cursor))
        if p.exists():
            result=json.loads(p.read_text())
        else:
            result=_request(url);dump(p,result)
        if result.get('code')!='0' or not result.get('data'):
            raise ValueError('OKX returned no usable confirmed tail')
        data=result['data'];urls.append(url)
        pages.extend([int(x[0]),*map(float,x[1:5])] for x in data
                     if x[8]=='1' and begin<=int(x[0])<end)
        nxt=min(int(x[0]) for x in data)
        if nxt>=cursor:
            raise ValueError('OKX pagination stalled')
        cursor=nxt
    tail=pd.DataFrame(pages,columns=['ts',*OHLC]).sort_values('ts').drop_duplicates('ts')
    overlap=old.merge(tail,on='ts',suffixes=('_old','_new'))
    assert len(overlap)==60
    for c in OHLC:
        np.testing.assert_allclose(overlap[c+'_old'],overlap[c+'_new'],rtol=0,atol=1e-8)
    merged=pd.concat([old,tail[tail.ts>old.ts.max()]],ignore_index=True)
    assert merged.ts.is_unique and (merged.ts.diff().dropna()==60000).all()
    assert merged.ts.iloc[0]==warm and merged.ts.iloc[-1]+60000==end
    assert (merged.high>=merged[OHLC].max(axis=1)).all()
    assert (merged.low<=merged[OHLC].min(axis=1)).all()
    merged.to_csv(cache,index=False)
    dump(EXP/'data_receipt.json',dict(sources=[file_stamp(ROOT/p) for p in cfg['inputs']],
        api_urls=urls,archive_rows=len(old),new_rows=len(merged)-len(old),overlap_rows=len(overlap),
        total_rows=len(merged),first_open=str(pd.Timestamp(warm,unit='ms',tz='UTC')),
        last_close=str(pd.Timestamp(end,unit='ms',tz='UTC')),gaps=0,no_content_hash=True))
    return aligned(merged)


def rounded(value,tick,up):
    return (math.ceil(value/tick-1e-8) if up else math.floor(value/tick+1e-8))*tick


def geometry(hi,lo,side,tick,stop_fraction):
    """Only the fully completed Monday's high and low enter these prices."""
    d=hi-lo
    entry=lo-.618*d if side==1 else hi+.618*d
    tp80=lo+.618*d if side==1 else hi-.618*d
    tp20=hi if side==1 else lo
    entry=rounded(entry,tick,side==-1)
    stop=rounded(entry*(1-side*stop_fraction),tick,side==-1)
    return entry,stop,rounded(tp80,tick,side==1),rounded(tp20,tick,side==1)


def weekly_candidates(frame,cfg):
    """Read completed Monday OHLC only; no touch, winner or future extrema."""
    opened=(frame.index-pd.Timedelta(minutes=1)).tz_convert(cfg['timezone'])
    dates=opened.normalize()
    start=pd.Timestamp(cfg['start']).tz_convert('UTC')
    rows=[]
    for day in dates[opened.dayofweek==0].unique():
        mask=dates==day
        monday=frame.loc[mask]
        freeze=day+pd.DateOffset(days=1)
        if len(monday)!=1440 or freeze.tz_convert('UTC')<start:
            continue
        i=frame.index.searchsorted(freeze.tz_convert('UTC'))
        if i>=len(frame)-2 or frame.index[i]!=freeze.tz_convert('UTC'):
            continue
        expiry=min(int(frame.index.searchsorted((day+pd.DateOffset(days=7)).tz_convert('UTC'),side='right'))-1,len(frame)-2)
        if expiry<=i:
            continue
        hi,lo=float(monday.high.max()),float(monday.low.min())
        for side in (1,-1):
            entry,stop,a,b=geometry(hi,lo,side,cfg['tick'],cfg['stop_fraction'])
            if side*(frame.close.iloc[i]-entry)<cfg['tick']-1e-8:
                raise ValueError('Monday-close entry is not a resting favorable limit')
            rows.append(dict(trade_id=f'{day.date()}-{side}',pair_id=f'{day.date()}-{side}',
                week=str(day.date()),side=side,entry_i=int(i),expire_i=expiry,end_i=len(frame)-1,
                entry=entry,stop=stop,risk=side*(entry-stop),tp80=a,tp20=b,
                monday_high=hi,monday_low=lo,formation_end=str(freeze),population='signal'))
    return pd.DataFrame(rows)


def legs(entries):
    records=[]
    for r in entries.to_dict('records'):
        for name,weight in [('tp80',.8),('tp20',.2)]:
            records.append(dict(r, parent_id=r['trade_id'],trade_id=r['trade_id']+'-'+name,
                                target_price=r[name],leg=name,weight=weight))
    return pd.DataFrame(records)


def native(frame,entries,mode,folder,cfg):
    """Reuse completed native rows; metadata changes invalidate rather than overwrite."""
    from yoyo.layers.l3_backtest.nautilus_independent import run_native,ExitPolicy
    folder.mkdir(exist_ok=True)
    identity=dict(inputs=[file_stamp(EXP/'candles.csv.gz')],config=cfg,
        entries=entries.to_dict('records'),mode=mode,code=[file_stamp(ROOT/p) for p in CODE])
    receipt=folder/'identity.json'
    if receipt.exists():
        if json.loads(receipt.read_text())!=identity:
            raise ValueError(f'Changed replay identity at {folder}; preserve prior run')
        return pd.read_csv(folder/'trades.csv',float_precision='round_trip'),json.loads((folder/'audit.json').read_text())
    rows,fills,audit=run_native(frame,np.full(len(frame),np.nan),np.zeros(len(frame)),
        entries,ExitPolicy(target_from_entry=True,opposite_hour_exit=False,round_trip_cost=0),
        tick=cfg['tick'],symbol='BTC',execution_minutes=1,entry_order_mode=mode)
    rows.to_csv(folder/'trades.csv',index=False);fills.to_csv(folder/'fills.csv',index=False)
    dump(folder/'audit.json',audit);dump(receipt,identity)
    return rows,audit


def path_oracle(bars,r,mode,tick,path='OHLC'):
    """Audit native prices/times with integer-tick monotone segments.

    Entered limits see only the remainder of their fill segment. Stop-first
    sensitivity is applied separately, not used to override primary native fills.
    """
    entry,stop,target=[round(getattr(r,k)/tick) for k in ('entry','stop','target_price')]
    side=int(r.side);entered=mode=='market';fill=int(r.entry_i) if entered else -1
    for i in range(int(r.entry_i)+1,int(r.end_i)+1):
        if not entered and i>r.expire_i:
            return (-1,-1,0.,-1)
        o,h,l,c=bars[i]
        points=[o,h,l,c] if path=='OHLC' else [o,l,h,c]
        if not entered and side*(o-entry)<=0:
            entered=True;fill=i
        if entered:
            if side*(o-stop)<=0:
                return fill,i,float(o*tick),1
            if side*(o-target)>=0:
                return fill,i,float(target*tick),4
        for a,b in zip(points[:-1],points[1:]):
            if not entered and min(a,b)<=entry<=max(a,b):
                entered=True;fill=i;a=entry
            if not entered:
                continue
            crossings=[(abs(v-a),v,reason) for v,reason in [(stop,1),(target,4)]
                       if min(a,b)<=v<=max(a,b)]
            if crossings:
                _,price,reason=min(crossings)
                return fill,i,float(price*tick),reason
        if entered and i==r.end_i:
            return fill,i,float(c*tick),0
    return (-1,-1,0.,-1)


def audit_paths(frame,candidates,rows,mode,cfg):
    bars=np.rint(frame[OHLC].to_numpy()/cfg['tick']).astype(np.int64)
    actual=rows.set_index('trade_id');sensitivity=[]
    checked=0
    for r in candidates.itertuples():
        expected=path_oracle(bars,r,mode,cfg['tick'])
        if expected[0]<0:
            assert r.trade_id not in actual.index
            continue
        a=actual.loc[r.trade_id]
        got=(int(a.entry_i),int(a.exit_i),float(a.exit_price),int(a.reason))
        assert got[:2]==expected[:2] and got[3]==expected[3] and abs(got[2]-expected[2])<1e-7,(r.trade_id,got,expected)
        reverse=path_oracle(bars,r,mode,cfg['tick'],'OLHC')
        stopfirst=expected
        # Conservative path: when native target and stop coexist in the final
        # candle, prefer stop even if OHLC orders the target first.
        if a.reason==4 and bars[int(a.exit_i),2]<=round(a.stop/cfg['tick'])<=bars[int(a.exit_i),1]:
            opened=bars[int(a.exit_i),0]*cfg['tick']
            price=min(opened,a.stop) if a.side==1 else max(opened,a.stop)
            stopfirst=(int(a.entry_i),int(a.exit_i),float(price),1)
        sensitivity.append(dict(trade_id=r.trade_id,
            olhc_exit_i=reverse[1],olhc_reason=reverse[3],
            olhc_r=r.side*(reverse[2]-r.entry)/r.risk,
            stopfirst_r=r.side*(stopfirst[2]-r.entry)/r.risk,
            stopfirst_reason=stopfirst[3]))
        checked+=1
    return pd.DataFrame(sensitivity),dict(native_paths_checked=checked,unfilled_checked=len(candidates)-checked)


def aggregate(rows,cfg):
    result=[]
    for parent,g in rows.groupby('parent_id'):
        assert len(g)==2 and g.entry_i.nunique()==1 and g.entry.nunique()==1
        a=g[g.leg=='tp80'].iloc[0];b=g[g.leg=='tp20'].iloc[0]
        result.append(dict(parent_id=parent,pair_id=a.pair_id,population=a.population,
            week=a.week,side=int(a.side),entry_i=int(a.entry_i),entry_time=a.entry_time,
            entry=a.entry,stop=a.stop,tp80=a.target_price,tp20=b.target_price,
            risk=a.risk,gross_r=float((g.weight*g.gross_r).sum()),
            olhc_r=float((g.weight*g.olhc_r).sum()),stopfirst_r=float((g.weight*g.stopfirst_r).sum()),
            tp80_hit=bool(a.reason==4),tp20_hit=bool(b.reason==4),
            closed=bool((g.reason!=0).all()),olhc_closed=bool((g.olhc_reason!=0).all()),
            exit_i=int(g.exit_i.max()),holding_minutes=int(g.exit_i.max()-a.entry_i),
            segment=int(pd.Timestamp(a.entry_time)>=pd.Timestamp(cfg['split']))))
    return pd.DataFrame(result)


def controls(frame,signal,cfg):
    """Causal closed15m ATR only; matching reads fill time/geometry, no payoff."""
    quarter=frame.resample('15min',closed='right',label='right').agg(
        {'open':'first','high':'max','low':'min','close':'last'})
    vol=(atr(quarter,14)/quarter.close).reindex(frame.index,method='ffill')
    bucket=np.digitize(vol.to_numpy(),cfg['volatility_edges'])
    local=frame.index.tz_convert(cfg['timezone']);days=local.tz_localize(None).normalize()
    week=(days-pd.to_timedelta(local.dayofweek,unit='D')).strftime('%Y-%m-%d').to_numpy()
    segments=np.asarray(frame.index>=pd.Timestamp(cfg['split']))
    good=np.asarray((local.dayofweek>=1)&(frame.index>=pd.Timestamp(cfg['start']))&(frame.index<pd.Timestamp(cfg['end'])-pd.Timedelta(minutes=1)))
    good&=vol.notna().to_numpy()
    for i in signal.entry_i:
        good[max(0,i-5):i+6]=False
    rng=np.random.default_rng(cfg['seed']);used={1:set(),-1:set()};output=[];matched=[]
    for r in signal.sort_values(['entry_i','side']).itertuples():
        target_bucket=int(bucket[r.entry_i-1])
        pool=np.flatnonzero(good&(week==r.week)&(bucket==target_bucket)&(segments==bool(r.segment)))
        pool=np.array([int(i) for i in pool if int(i) not in used[r.side]],dtype=int)
        n=min(cfg['controls_per_signal'],len(pool))
        choice=rng.choice(pool,n,replace=False)
        matched.append(dict(pair_id=r.pair_id,bucket=target_bucket,controls=n))
        for k,i in enumerate(choice):
            used[r.side].add(int(i));entry=float(frame.close.iloc[i])
            stop=rounded(entry*(1-r.side*cfg['stop_fraction']),cfg['tick'],r.side==-1)
            a=rounded(entry*r.tp80/r.entry,cfg['tick'],r.side==1)
            b=rounded(entry*r.tp20/r.entry,cfg['tick'],r.side==1)
            output.append(dict(trade_id=f'random-{r.pair_id}-{k}',pair_id=r.pair_id,
                population='random',entry_i=int(i),side=r.side,entry=entry,stop=stop,
                risk=r.side*(entry-stop),tp80=a,tp20=b,end_i=len(frame)-1,week=r.week,
                bucket=target_bucket,entry_time=str(frame.index[i])))
    return pd.DataFrame(output),pd.DataFrame(matched)


def metric(rows):
    closed=rows[rows.closed];r=closed.gross_r.to_numpy()
    n=len(r);wins=int((r>1e-9).sum())
    z=1.95996398454
    if n:
        p=wins/n;den=1+z*z/n;center=(p+z*z/(2*n))/den
        width=z*np.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
        ci=[100*(center-width),100*(center+width)]
    else:
        ci=[None,None]
    return dict(n=len(rows),closed=n,open=len(rows)-n,wins=wins,losses=int((r < -1e-9).sum()),
        win_pct=100*wins/n if n else None,wilson95_pct=ci,
        tp80=int(rows.tp80_hit.sum()),tp20=int(rows.tp20_hit.sum()),
        tp80_pct=100*rows.tp80_hit.mean() if len(rows) else None,
        tp20_pct=100*rows.tp20_hit.mean() if len(rows) else None,
        gross_r=float(r.sum()),mean_r=float(r.mean()) if n else None,
        pf=float(r[r>0].sum()/-r[r<0].sum()) if (r<0).any() else None,
        terminal_mark_r=float(rows.loc[~rows.closed,'gross_r'].sum()),
        stopfirst_win_pct=float(100*(closed.stopfirst_r>1e-9).mean()) if n else None,
        stopfirst_r=float(closed.stopfirst_r.sum()),
        olhc_r=float(rows.loc[rows.olhc_closed,'olhc_r'].sum()),
        olhc_win_pct=float(100*(rows.loc[rows.olhc_closed,'olhc_r']>1e-9).mean()),
        max_holding_days=float(rows.holding_minutes.max()/1440) if len(rows) else None)


def run():
    cfg=json.loads((EXP/'config.json').read_text())
    if subprocess.run(['git','diff','--quiet','HEAD','--',*CODE],cwd=ROOT).returncode:
        raise RuntimeError('Commit builder before generating artifacts')
    frame=load_data(cfg)
    candidates=weekly_candidates(frame,cfg);candidates.to_csv(EXP/'weekly_orders.csv',index=False)
    prepared=legs(candidates)
    rows,audit=native(frame,prepared,'limit',EXP/'native_signal',cfg)
    sensitivity,checks=audit_paths(frame,prepared,rows,'limit',cfg)
    rows=rows.merge(sensitivity,on='trade_id',validate='one_to_one')
    signal=aggregate(rows,cfg);signal.to_csv(EXP/'signal_trades.csv',index=False)
    print('signal',json.dumps(metric(signal)),flush=True)
    random,matching=controls(frame,signal,cfg)
    random.to_csv(EXP/'random_entries.csv',index=False);matching.to_csv(EXP/'matching.csv',index=False)
    randomlegs=legs(random)
    rrows,raudit=native(frame,randomlegs,'market',EXP/'native_random',cfg)
    rsensitivity,rchecks=audit_paths(frame,randomlegs,rrows,'market',cfg)
    rrows=rrows.merge(rsensitivity,on='trade_id',validate='one_to_one')
    randomtrades=aggregate(rrows,cfg);randomtrades.to_csv(EXP/'random_trades.csv',index=False)
    comparisons=[]
    for part,side,segment in [('all',0,-1),('long',1,-1),('short',-1,-1),('earlier',0,0),('later',0,1)]:
        s=signal.copy();r=randomtrades.copy()
        if side:
            s=s[s.side==side];r=r[r.side==side]
        if segment>=0:
            s=s[s.segment==segment];r=r[r.segment==segment]
        # Pair tests use only fully closed complete control sets.
        complete=r.groupby('pair_id').closed.all()
        ids=complete[complete].index
        paired=s[s.closed & s.pair_id.isin(ids)][['pair_id','week','gross_r']].rename(columns={'gross_r':'net_r'})
        avg=r.groupby('pair_id').gross_r.mean().rename('net_r_random')
        paired=paired.join(avg,on='pair_id')
        comparisons.append(dict(part=part,signal=metric(s),random=metric(r),
            matched_pairs=len(paired),comparison=inference(paired,cfg['seed'])))
    # Validate high/low formation from independent local-day aggregation and
    # prefix stability with future bars removed after each sampled Tuesday.
    prefixes=0
    for i in np.linspace(0,len(candidates)-1,8,dtype=int):
        row=candidates.iloc[i];cut=min(len(frame),int(row.expire_i)+2)
        prefix=weekly_candidates(frame.iloc[:cut],cfg)
        got=prefix.set_index('trade_id').loc[row.trade_id]
        for key in ['entry','stop','tp80','tp20','monday_high','monday_low','formation_end']:
            assert got[key]==row[key],key
        prefixes+=1
    combined=pd.concat([rows,rrows])
    np.testing.assert_allclose(combined.gross_r,combined.net_r,rtol=0,atol=1e-12)
    assert combined.framework_commission.eq(0).all()
    dump(EXP/'summary.json',dict(config=cfg,comparisons=comparisons,
        orders=len(candidates),weeks=int(candidates.week.nunique()),filled=len(signal),
        unmatched=int((matching.controls==0).sum()),
        checks=dict(signal=checks,random=rchecks,prefixes=prefixes,
            native_positions=audit['positions']+raudit['positions'],native_fills=audit['fills']+raudit['fills']),
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        training_eligible=False,production_eligible=False))
    print(json.dumps(comparisons,indent=2),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',type=Path,default=EXP/'config.json')
    args=parser.parse_args();EXP=args.config.resolve().parent;run()
