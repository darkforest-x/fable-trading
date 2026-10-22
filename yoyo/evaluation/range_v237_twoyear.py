"""Two-year V2.37 reference and confirmation-close research, no Pine changes.

Sources: delivered V2.37, frozen five-year OKX candles and V2.36 API tail.
Admission features use completed native Stoch5/3/3 on 5m/15m/H1 only. Pair
positions compare candle opens within inclusive +/-25 minutes. A reference
row may depend on a later arrow, explicitly stored as publication_time.
The causal comparison enters at publication close and recomputes the same
five-bar structural stop plus0.05% buffer there; H1 admission is frozen at
the original15m close. Neither mode reads future outcomes for selection.
Execution reuses Nautilus1.231.0 and the independent existing C++ oracle.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT, dump, file_stamp, run
from yoyo.evaluation.pepe_range_exits import aggregate, signals, prepare, replay, inference
from yoyo.evaluation.monday_range_v2_2_reference import round_stop, atr
from yoyo.evaluation.range_morphology import wilson

EXP = ROOT/'experiments/active/exp-range-v237-twoyear-20261022-v1'
CONFIG = EXP/'config.json'
OHLC = ['open','high','low','close']


def read(path):
    return pd.read_csv(path, float_precision='round_trip')


def close_frame(raw):
    frame=raw[OHLC].copy()
    frame.index=pd.DatetimeIndex(pd.to_datetime(raw.ts,unit='ms',utc=True)+pd.Timedelta(minutes=5)).as_unit('ns')
    return frame


def pair_positions(primary_i, primary_side, primary_open, primary_close,
                   secondary_open, secondary_close, secondary_side, window=5):
    """Return earliest confirmed same-side pair, one row per primary arrow.

    All input arrows have actually closed at the supplied dataset cutoff.
    Only their open/close timestamps and sides are used; no OHLC outcome.
    """
    result=[]
    pools={s:np.flatnonzero(secondary_side==s) for s in (-1,1)}
    width=window*300_000_000_000
    for i,side,opened,closed in zip(primary_i,primary_side,primary_open,primary_close):
        pool=pools[int(side)]
        times=secondary_open[pool]
        j=int(np.searchsorted(times,opened-width))
        if j==len(pool) or times[j]>opened+width:
            continue
        other=int(pool[j])
        result.append(dict(original_i=int(i),side=int(side),signal_ns=int(opened),
            original_close_ns=int(closed),matched_ns=int(secondary_open[other]),
            publication_ns=int(max(closed,secondary_close[other]))))
    return result


def events(frame,start,end,tick,window=5):
    frame=frame.loc[frame.index<=pd.Timestamp(end)]
    q=aggregate(frame,15)
    fs,qs=signals(frame).to_numpy(),signals(q).to_numpy()
    fi,qi=np.flatnonzero(fs),np.flatnonzero(qs)
    ft,qt=frame.index.asi8,q.index.as_unit('ns').asi8
    ctx=prepare(frame)
    validq=np.flatnonzero(ctx['signal'])
    result=[]
    for lane,matched in [(5,pair_positions(fi,fs[fi],ft[fi]-300_000_000_000,ft[fi],
            qt[qi]-900_000_000_000,qt[qi],qs[qi],window)),
        (15,pair_positions(validq,ctx['signal'][validq],ft[validq]-900_000_000_000,ft[validq],
            ft[fi]-300_000_000_000,ft[fi],fs[fi],window))]:
        for r in matched:
            if not pd.Timestamp(start).value <= r['original_close_ns'] < pd.Timestamp(end).value:
                continue
            r.update(lane=lane,event_id=f'{lane}-{r["original_i"]}',
                observed_i=int(ctx['observed'][r['original_i']]) if lane==15 else -1,
                retrospective=r['publication_ns']>r['original_close_ns'])
            result.append(r)
    return pd.DataFrame(result)


def project_entries(frame,ev,tick,mode):
    """Freeze stop at original reference close or causal publication close."""
    lo,hi=frame.low.rolling(5).min(),frame.high.rolling(5).max()
    rows=[]
    for r in ev.to_dict('records'):
        i=int(r['original_i']) if mode=='reference' else int(frame.index.get_indexer([pd.Timestamp(r['publication_ns'],tz='UTC')])[0])
        if i<4 or i>=len(frame)-1:
            continue
        side=int(r['side']);entry=float(frame.close.iloc[i])
        stop=round_stop(side,float(lo.iloc[i] if side==1 else hi.iloc[i]),entry*.0005,tick)
        risk=side*(entry-stop)
        if stop<=0 or risk<tick-tick*1e-7:
            continue
        rows.append(dict(r,mode=mode,entry_i=i,entry_time=frame.index[i].isoformat(),entry=entry,
            stop=stop,risk=risk,end_i=len(frame)-1,population='signal'))
    return pd.DataFrame(rows)


def controls(frame,signal,tick,cfg,seed):
    """Same coin/side/year/UTC week/causal ATR bucket/15m clock phase.

    Exclude any native5m/15m arrow close and real entry; sample without
    replacement per cohort. Copy relative initial risk, rounded outward.
    """
    q=aggregate(frame,15)
    vol=(atr(q)/q.close).reindex(frame.index,method='ffill').to_numpy()
    bucket=np.digitize(vol,cfg['volatility_edges'])
    week=(frame.index-pd.to_timedelta(frame.index.dayofweek,unit='D')).normalize().asi8
    segment=(frame.index>=pd.Timestamp(cfg['midpoint'])).astype(int)
    phase=frame.index.minute.to_numpy()%15
    any_arrow=(signals(frame)!=0).to_numpy() | (signals(q).reindex(frame.index,fill_value=0)!=0).to_numpy()
    eligible=(frame.index>=pd.Timestamp(cfg['start']))&(frame.index<pd.Timestamp(cfg['end']))&np.isfinite(vol)&~any_arrow
    eligible[-1]=False;eligible[signal.entry_i.to_numpy(int)]=False
    pools={}
    for i in np.flatnonzero(eligible):
        key=(int(segment[i]),int(week[i]),int(bucket[i]),int(phase[i]))
        pools.setdefault(key,[]).append(int(i))
    rng=np.random.default_rng(seed)
    for pool in pools.values():rng.shuffle(pool)
    rows=[]
    for r in signal.to_dict('records'):
        i=r['entry_i'];key=(int(segment[i]),int(week[i]),int(bucket[i]),int(phase[i]))
        r['segment'],r['week'],r['bucket']=key[:3]
        pool=pools.get(key,[])
        if not pool:
            raise ValueError(f'No matched control for {r["event_id"]}, {key}')
        at=pool.pop();price=float(frame.close.iloc[at]);distance=r['risk']/r['entry']*price
        stop=round_stop(r['side'],price-distance*r['side'],0,tick)
        rows.append(dict(r,entry_i=at,entry_time=frame.index[at].isoformat(),entry=price,stop=stop,
            risk=r['side']*(price-stop),population='random'))
    signal=signal.copy()
    ids=signal.entry_i.to_numpy(int)
    signal['segment']=segment[ids];signal['week']=week[ids];signal['bucket']=bucket[ids]
    return pd.concat([signal,pd.DataFrame(rows)],ignore_index=True)


def build(symbol):
    cfg=json.loads(CONFIG.read_text());spec=next(s for s in cfg['symbols'] if s['symbol']==symbol)
    folder=EXP/symbol;folder.mkdir(exist_ok=True)
    if (folder/'preparation.json').exists():return
    inputs=[ROOT/cfg['history']/symbol/'candles.csv.gz',ROOT/cfg['tail']/symbol/'candles.csv.gz']
    parts=[read(p) for p in inputs]
    # Overlap must agree exactly; no value overwrites or synthetic missing bars.
    merged=pd.concat(parts).drop_duplicates().sort_values('ts')
    assert not merged.ts.duplicated().any(),'Conflicting cached OHLC'
    begin=int(pd.Timestamp(cfg['warmup']).timestamp()*1000);end=int(pd.Timestamp(cfg['end']).timestamp()*1000)
    raw=merged[(merged.ts>=begin)&(merged.ts<end)].reset_index(drop=True)
    np.testing.assert_array_equal(raw.ts,np.arange(begin,end,300000,dtype=np.int64))
    assert np.isfinite(raw[['ts',*OHLC]].to_numpy()).all()
    assert (raw.high>=raw[['open','close','low']].max(axis=1)).all() and (raw.low<=raw[['open','close','high']].min(axis=1)).all()
    frame=close_frame(raw);ev=events(frame,cfg['start'],cfg['end'],spec['tick'])
    ev.to_csv(folder/'events.csv',index=False)
    probes=[len(frame)//2,len(frame)-30,len(frame)-3]
    for stop in probes:
        got=events(frame.iloc[:stop],cfg['start'],frame.index[stop-1],spec['tick'])
        expected=ev[(ev.publication_ns<=frame.index[stop-1].value)&(ev.original_close_ns<frame.index[stop-1].value)]
        pd.testing.assert_frame_equal(got.reset_index(drop=True),expected.reset_index(drop=True))
    # Match the already-delivered recent5m cohort and new native15m ledger.
    old=read(ROOT/cfg['tail']/symbol/'signals.csv')
    overlap=ev[(ev.lane==5)&(ev.signal_ns>=pd.Timestamp('2026-08-22T00:00:00+08:00').value)]
    assert len(overlap)==len(old)
    np.testing.assert_array_equal(overlap.original_close_ns,pd.to_datetime(old.entry_time,utc=True).dt.as_unit('ns').astype('int64'))
    np.testing.assert_array_equal(overlap.publication_ns,pd.to_datetime(old.publication_time,utc=True).dt.as_unit('ns').astype('int64'))
    if symbol=='BTC':
        qrecent=ev[(ev.lane==15)&(ev.original_close_ns>=pd.Timestamp('2026-08-22T00:00:00+08:00').value)]
        assert len(qrecent)==47
    cohorts=[]
    for mode in ['reference','causal']:
        projected=project_entries(frame,ev,spec['tick'],mode)
        for lane in [5,15]:
            rows=projected[projected.lane==lane].copy()
            cohorts.append(controls(frame,rows,spec['tick'],cfg,cfg['seed']+spec['seed_offset']+lane+(0 if mode=='reference' else 100)))
    occurrences=pd.concat(cohorts,ignore_index=True)
    keys=['population','entry_i','side','entry','stop','end_i']
    canonical=occurrences.drop_duplicates(keys).copy().reset_index(drop=True)
    canonical['pair_id']=np.arange(len(canonical))
    occurrences=occurrences.merge(canonical[keys+['pair_id']],on=keys,validate='many_to_one')
    occurrences.to_csv(folder/'occurrences.csv',index=False)
    canonical.to_csv(folder/'candidates.csv',index=False)
    raw.to_csv(folder/'candles.csv.gz',index=False)
    native=dict(schema=1,experiment_id=cfg['experiment_id'],engine='nautilus',symbol=symbol,
        tick=spec['tick'],market_tick=spec['tick'],entry_mode='provided',
        data=str((folder/'candles.csv.gz').relative_to(ROOT)),entry_candidates=str((folder/'candidates.csv').relative_to(ROOT)),
        stop_window_5m=5,buffer_percent=.05,maximum_risk_percent=0,round_trip_cost=.002,
        policies=[dict(name='baseline',trail_arm_r=5,trail_atr_multiple=4,opposite_hour_exit=True)],
        output=str((folder/'native').relative_to(ROOT)))
    dump(folder/'native.json',native)
    receipt=dict(symbol=symbol,bars=len(raw),gaps=0,sources=[file_stamp(p) for p in inputs],
        event_counts=ev.groupby('lane').size().to_dict(),late_counts=ev.groupby('lane').retrospective.sum().to_dict(),
        unique_native_positions=len(canonical),occurrences=len(occurrences),prefix_checks=len(probes),
        recent5m_parity=len(old),recent15m_btc_parity=47 if symbol=='BTC' else None)
    dump(folder/'preparation.json',receipt);print(json.dumps(receipt),flush=True)


def metrics(rows):
    """Natural-exit win rates; boundary marks excluded, reported separately."""
    natural=rows[rows.reason!=0];n=len(natural);wins=int(natural.net_r.gt(1e-9).sum())
    initial=natural.reason.eq(1);stop=natural.reason.isin([1,2])
    loss_stop=stop&natural.net_r.lt(-1e-9)
    return dict(n=len(rows),closed=n,open=int(rows.reason.eq(0).sum()),net_wins=wins,
        net_win_rate=wins/n if n else None,gross_wins=int(natural.gross_r.gt(1e-9).sum()),
        gross_win_rate=float(natural.gross_r.gt(1e-9).mean()) if n else None,
        wilson95=wilson(wins,n) if n else None,net_r=float(natural.net_r.sum()),gross_r=float(natural.gross_r.sum()),
        boundary_net_r=float(rows.loc[rows.reason.eq(0),'net_r'].sum()),mean_bp=float(natural.net_notional_bp.mean()) if n else None,
        initial_sl=int(initial.sum()),trail_stop=int(natural.reason.eq(2).sum()),h1_exit=int(natural.reason.eq(3).sum()),
        positive_then_initial_sl=int((initial&natural.peak_r.gt(1e-9)).sum()),
        positive_then_losing_stop=int((loss_stop&natural.peak_r.gt(1e-9)).sum()),
        mfe1_then_losing_stop=int((loss_stop&natural.peak_r.ge(1-1e-7)).sum()),
        mfe2_then_losing_stop=int((loss_stop&natural.peak_r.ge(2-1e-7)).sum()),
        mfe3_then_losing_stop=int((loss_stop&natural.peak_r.ge(3-1e-7)).sum()),
        cost_covered_then_losing_stop=int((loss_stop&(natural.peak_r>natural.fee_r+1e-9)).sum()),
        median_mfe_r=float(natural.peak_r.median()) if n else None,
        median_hold_minutes=float(((natural.exit_i-natural.entry_i)*5).median()) if n else None)


def analyze(symbol):
    cfg=json.loads(CONFIG.read_text());spec=next(s for s in cfg['symbols'] if s['symbol']==symbol)
    folder=EXP/symbol
    native=read(folder/'native/baseline/trades.csv');base=read(folder/'candidates.csv')
    frame=close_frame(read(folder/'candles.csv.gz'));ctx=prepare(frame)
    ordered=base.sort_values('pair_id');actual=native.sort_values('pair_id')
    assert actual.pair_id.tolist()==ordered.pair_id.tolist()
    z=replay(ctx,ordered[['entry_i','side','entry','stop','end_i']].to_numpy(float),tick=spec['tick'])
    for field,col in [('exit_i',0),('reason',2),('arm_i',4),('steps',7)]:
        np.testing.assert_array_equal(actual[field],z[:,col],err_msg=field)
    for field,col in [('exit_price',1),('protection',5)]:
        np.testing.assert_allclose(actual[field],z[:,col],atol=spec['tick']*1e-7,rtol=0,err_msg=field)
    np.testing.assert_allclose(actual.peak_r,z[:,3],atol=1e-7,rtol=0)
    np.testing.assert_allclose(actual.net_r,actual.gross_r-.002*actual.entry/actual.risk,atol=1e-7,rtol=0)
    occ=read(folder/'occurrences.csv')
    outcomes=['exit_i','exit_price','reason','peak_r','arm_i','protection','gross_r','fee_r','net_r','net_notional_bp','exit_time']
    rows=occ.merge(native[['pair_id',*outcomes]],on='pair_id',validate='many_to_one')
    local=pd.to_datetime(rows.entry_time,utc=True).dt.tz_convert('Asia/Shanghai')
    rows['hour']=local.dt.hour;rows['hour2']=local.dt.hour//2*2;rows['weekday']=local.dt.dayofweek
    rows['before_publication_exit']=pd.to_datetime(rows.exit_time,utc=True).dt.as_unit('ns').astype('int64')<=rows.publication_ns
    rows.to_csv(folder/'results.csv.gz',index=False)
    result=[];timegroups=[];stability=[]
    for (lane,mode),group in rows.groupby(['lane','mode']):
        s=group[group.population=='signal'];r=group[group.population=='random']
        sm,rm=metrics(s),metrics(r)
        paired=s[['event_id','net_r','week']].merge(r[['event_id','net_r']],on='event_id',suffixes=('','_random'),validate='one_to_one')
        result.append(dict(symbol=symbol,lane=int(lane),mode=mode,**sm,random=rm,
            inference_boundary_marked=inference(paired,cfg['seed']),
            retrospective=int(s.retrospective.sum()),
            reference_closed_by_publication=int((s.before_publication_exit&s.reason.ne(0)).sum()),
            reference_stopped_by_publication=int((s.before_publication_exit&s.reason.isin([1,2])).sum())))
        for dimension in ['hour','hour2','weekday']:
            for value,g in s.groupby(dimension):
                rc=r[r.event_id.isin(g.event_id)]
                timegroups.append(dict(symbol=symbol,lane=int(lane),mode=mode,dimension=dimension,value=int(value),
                    **metrics(g),random=metrics(rc)))
            # Pick the first year's top net-win bucket, then show the second year.
            first=s[s.segment==0];later=s[s.segment==1]
            choices=[(v,metrics(g)) for v,g in first.groupby(dimension)]
            choices=[(v,m) for v,m in choices if m['closed']>=cfg['minimum_time_group']]
            if choices:
                chosen,score=max(choices,key=lambda vm:(vm[1]['net_win_rate'],vm[1]['closed'],-vm[0]))
                stability.append(dict(symbol=symbol,lane=int(lane),mode=mode,dimension=dimension,
                    selected=int(chosen),first=score,later=metrics(later[later[dimension]==chosen]),later_all=metrics(later)))
        for period,g in s.groupby('segment'):
            result.append(dict(symbol=symbol,lane=int(lane),mode=mode,period=int(period),**metrics(g),
                random=metrics(r[r.segment==period])))
    dump(folder/'summary.json',dict(rows=result,timegroups=timegroups,stability=stability,
        oracle_paths=len(native),native_positions=len(native),native_fills=2*len(native)))
    print(json.dumps([r for r in result if 'period' not in r]),flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['prepare','run','analyze','summary']);p.add_argument('--symbol',choices=['BTC','ETH']);a=p.parse_args()
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__,str(CONFIG)],cwd=ROOT).returncode:
        raise RuntimeError('Commit builder/config before generation')
    if a.stage=='summary':
        cfg=json.loads(CONFIG.read_text());parts=[json.loads((EXP/s/'summary.json').read_text()) for s in ['BTC','ETH']]
        result=dict(config=cfg,rows=sum([p['rows'] for p in parts],[]),timegroups=sum([p['timegroups'] for p in parts],[]),
            stability=sum([p['stability'] for p in parts],[]),oracle_paths=sum(p['oracle_paths'] for p in parts))
        dump(EXP/'summary.json',result);return
    assert a.symbol
    if a.stage=='prepare':build(a.symbol)
    elif a.stage=='run':run(EXP/a.symbol/'native.json')
    else:analyze(a.symbol)


if __name__=='__main__':main()
