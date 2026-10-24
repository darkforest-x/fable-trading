"""Owner-fixed Monday limit/reclaim comparison, executed only by Nautilus.

Inputs are completed NY Monday OHLC anchors and original 1m/5m candles.
First-touch OHLC is used only to identify an observable first-touch event and
compress the idle order interval: the order is submitted at the preceding
close, and native matching still owns its fill and every subsequent exit.
No future outcome is used for admission. Confirmation clocks are derived from
actual native fills, not a precomputed desired exit. With no initial SL there
is no defined initial risk R: all reported returns use entry notional.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
import subprocess
import time
import zlib

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT, dump, file_stamp
from yoyo.evaluation.monday_ma6_retest import finite

EXP = ROOT/'experiments/active/exp-monday-limit-reclaim-20261024-v1'
OHLC = ['open','high','low','close']
TARGETS = ['near_monday_edge','range_0.5','range_0.618','opposite_monday_edge','opposite_1618']
CODE = ['yoyo/evaluation/monday_limit_reclaim.py',
        'yoyo/evaluation/monday_limit_reclaim_inputs.py',
        'yoyo/layers/l3_backtest/nautilus_independent.py']


def segment_ends(f, job):
    """Data boundaries only, never price-dependent holding deadlines."""
    n=len(f); step=pd.Timedelta(minutes=job['execution_minutes']).value
    discontinuous=np.diff(f.index.as_unit("ns").asi8)!=step
    if job['symbol']=='XAUUSD':
        # Without an audited session calendar no missing minute is assumed safe.
        discontinuous |= np.diff(f.quality_segment.to_numpy())!=0
    cuts=np.flatnonzero(discontinuous)+1
    starts=np.r_[0,cuts]; ends=np.r_[cuts,n]-1
    return np.repeat(ends,np.diff(np.r_[starts,n]))


def prepare(f, job, cfg, quality):
    """Create causal native orders and retain every valid/invalid weekly side.

Control matching reads only previous/current completed execution-bar OHLC:
14-bar TR mean divided by close, quarter-octave log2 buckets, same NY week and
direction. The signal's limit/target percentage geometry is scaled to random
submission closes. Random orders can remain unfilled; never resample them by
outcome. Complete matched groups are a conditional comparison, not a funded
portfolio or an unqualified full-process causal effect.
"""
    from yoyo.evaluation.monday_limit_reclaim_inputs import weekly_context
    contexts=weekly_context(f,job['execution_minutes'],quality.get('blocked_monday_weeks',()))
    decimals=max(2,min(9,int(np.ceil(-np.log10(f[OHLC].to_numpy().min())))+7))
    tick=10.**(-decimals); p=f[OHLC].round(decimals)
    o,h,l,c=(p[k].to_numpy() for k in OHLC)
    tr=pd.concat([p.high-p.low,(p.high-p.close.shift()).abs(),(p.low-p.close.shift()).abs()],axis=1).max(axis=1)
    vol=tr.rolling(14,min_periods=14).mean()/p.close
    bucket=np.floor(np.log2(vol.where(vol>0))*4).fillna(-999).to_numpy(int)
    ends=segment_ends(f,job); orders=[]; opportunities=[]
    rng=np.random.default_rng(cfg['seed']+zlib.crc32(job['symbol'].encode()))
    for w in contexts:
        week=w['week']
        for side in (1,-1):
            event=f"{job['symbol']}:{week}:{side:+d}"
            common=dict(event_id=event,symbol=job['symbol'],asset_group=('XAUUSD' if job['symbol']=='XAUUSD' else job['symbol'].removesuffix('USDT') if job['symbol'] in ('BTCUSDT','ETHUSDT') else 'ALTCOINS'),source_group=job['group'],week=week,
                        side=side,execution_minutes=job['execution_minutes'])
            if not w.get('valid',False):
                opportunities.append(dict(common,status='unobservable_week',detail=(w.get('monday_exclusion_reason') if not w.get('monday_valid') else w.get('monitor_unobservable_reason') or w.get('monitor_start_reason')) or 'invalid_week'));continue
            hi,lo=float(w['monday_high']),float(w['monday_low']);width=hi-lo
            level=lo-.618*width if side==1 else hi+.618*width
            entry=float(np.floor(level/tick+1e-7)*tick if side==1 else np.ceil(level/tick-1e-7)*tick)
            a,b=int(w['monitor_start_i']),int(w['monitor_end_i_exclusive'])
            common.update(monday_high=hi,monday_low=lo,level=level,entry=entry)
            if width<=0 or entry<=0 or b<=a:
                opportunities.append(dict(common,status='invalid_level_or_window'));continue
            hits=np.flatnonzero(l[a:b]<=entry if side==1 else h[a:b]>=entry)
            if not len(hits):
                opportunities.append(dict(common,status='no_observed_touch',detail=w.get('monitor_cutoff_cause','week_end')));continue
            touch=a+int(hits[0]); submit=touch-1
            common.update(touch_i_hint=touch,touch_interval_open=str(f.index[touch]),submission_i=submit)
            if submit<0 or side*(c[submit]-entry)<tick*(1-1e-7) or ends[touch]<=submit:
                opportunities.append(dict(common,status='invalid_submission_geometry'));continue
            targets=([lo,lo+.5*width,lo+.618*width,hi,hi+.618*width] if side==1 else
                     [hi,hi-.5*width,hi-.618*width,lo,lo-.618*width])
            opportunities.append(dict(common,status='admitted',bucket=int(bucket[submit])))
            pool=np.arange(a,max(a,b-1),dtype=int)
            pool=pool[(bucket[pool]==bucket[submit])&(bucket[pool]!=-999)&(ends[pool]>pool)&(pool!=submit)]
            chosen=rng.choice(pool,cfg['controls_per_signal'],replace=False) if len(pool)>=cfg['controls_per_signal'] else []
            attempts=[('signal',0,submit,entry,targets,touch)]
            for k,j in enumerate(chosen,1):
                raw=c[j]*entry/c[submit]
                ce=float(np.floor(raw/tick+1e-7)*tick if side==1 else np.ceil(raw/tick-1e-7)*tick)
                ct=[ce*(t/entry) for t in targets]
                attempts.append(('random',k,int(j),ce,ct,-1))
            for pop,k,j,price,tp,hint in attempts:
                if price<=0 or side*(c[j]-price)<tick*(1-1e-7):
                    opportunities.append(dict(common,event_id=event+f':c{k}',status='invalid_control_geometry',population=pop));continue
                end=int(ends[j+1]);expire=min(b-1,end)
                if expire<=j:continue
                for minutes in job['confirm_minutes']:
                    pair=f'{event}:m{minutes}'
                    for mode,target in zip(TARGETS,tp):
                        rounded=float(np.round(target,decimals))
                        unreachable=rounded<=0
                        if not unreachable and side*(rounded-price)<tick*(1-1e-7):
                            raise ValueError('Nonfavorable frozen target')
                        orders.append(dict(common,trade_id=f'{pair}:{mode}:c{k}',pair_id=f'{pair}:{mode}',
                            population=pop,control_id=k,entry_i=j,expire_i=expire,end_i=end,
                            entry=price,stop=np.nan,risk=price,target_price=np.nan if unreachable else rounded,
                            target_unreachable=unreachable,exit_mode=mode,reclaim_minutes=int(minutes),
                            touch_i_hint=hint,bucket=int(bucket[j]),submission_time=str(f.index[j]+pd.Timedelta(minutes=job['execution_minutes'])),
                            censor_reason='source_end' if end==len(f)-1 else 'unobserved_source_path',
                            intended_entry_time=str(f.index[j+1]),matched_controls_available=len(chosen)))
    return p,pd.DataFrame(orders),pd.DataFrame(opportunities),contexts,tick


def label_candles(f, rows, step):
    """Ex-post candle descriptions only; never passed back into entry decisions.

Uses touch and immediate-successor OHLC through their closes. Labels can be
later than a trade's target exit and are explicitly observational, not filters.
Incomplete timeframe candles remain unknown, never filled or interpolated.
"""
    if rows.empty:return rows
    rows=rows.copy()
    for minutes in sorted(rows.reclaim_minutes.unique()):
        g=f[OHLC].resample(f'{minutes}min',closed='left',label='left')
        bars=g.agg(dict(open='first',high='max',low='min',close='last'))
        bars['complete']=g.close.count().eq(int(minutes)//step)
        sel=rows.reclaim_minutes.eq(minutes); sub=rows.loc[sel]
        touch_open=f.index[sub.entry_i.astype(int)].floor(f'{minutes}min')
        first=bars.reindex(touch_open);second=bars.reindex(touch_open+pd.Timedelta(minutes=int(minutes)))
        side=sub.side.to_numpy();level=sub.entry.to_numpy()
        for label,z in [('touch',first),('next',second)]:
            complete=z.complete.fillna(False).to_numpy(bool)
            close_inside=side*(z.close.to_numpy()-level)>0
            body_inside=np.where(side>0,np.minimum(z.open,z.close)>=level,np.maximum(z.open,z.close)<=level)
            wick=np.where(side>0,z.low.to_numpy()<np.minimum(z.open,z.close),z.high.to_numpy()>np.maximum(z.open,z.close))
            rows.loc[sel,f'{label}_candle_complete']=complete
            rows.loc[sel,f'{label}_close_inside']=np.where(complete,close_inside.astype(float),np.nan)
            if label=='touch':rows.loc[sel,'touch_wick_only_reclaim']=np.where(complete,(body_inside&wick&close_inside).astype(float),np.nan)
    return rows


def process(job,cfg):
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy,run_native
    dest=EXP/'symbols'/job['symbol'];dest.mkdir(parents=True,exist_ok=True)
    identity=dict(config=cfg,job=job,inputs=[file_stamp(Path(p)) for p in job['paths']],
                  code=[file_stamp(ROOT/p) for p in CODE],engine='NautilusTrader1.231.0')
    if (dest/'done.json').exists():
        if json.loads((dest/'identity.json').read_text())!=finite(identity):raise RuntimeError('Frozen identity changed; do not replay')
        return json.loads((dest/'done.json').read_text())
    started=time.perf_counter(); f,quality=load_job(job)
    p,e,op,contexts,tick=prepare(f,job,cfg,quality)
    op.to_csv(dest/'opportunities.csv.gz',index=False);e.to_csv(dest/'orders.csv.gz',index=False)
    dump(dest/'weeks.json',finite(contexts));dump(dest/'quality.json',finite(quality))
    rows=fills=pd.DataFrame(); audit={'positions':0,'fills':0,'opportunities':[]}
    if len(e):
        frame=p.copy();frame.index=frame.index+pd.Timedelta(minutes=job['execution_minutes'])
        policy=ExitPolicy(trail_arm_r=1e12,opposite_hour_exit=False,round_trip_cost=0,
            target_from_entry=True,intrabar_path=cfg['intrabar_path'],reclaim_next_bar=True,
            breakeven_includes_cost=False,fill_maker_fee=0. if job['symbol']=='XAUUSD' else cfg['maker_fee'],
            fill_taker_fee=0. if job['symbol']=='XAUUSD' else cfg['taker_fee'])
        rows,fills,audit=run_native(frame,np.full(len(frame),np.nan),np.zeros(len(frame)),e,policy,
            tick=tick,symbol='XAU' if job['symbol']=='XAUUSD' else job['symbol'].removesuffix('USDT'),
            execution_minutes=job['execution_minutes'],entry_order_mode='limit',batch_bars=cfg['streaming_batch_bars'])
        if len(rows):
            rows['closed']=rows.reason.ne(0)
            rows['gross_bp']=rows.gross_r*10000
            rows['fee_bp']=rows.fee_r*10000
            rows['net_bp']=rows.net_notional_bp
            rows['basis']='gross' if job['symbol']=='XAUUSD' else 'net'
            rows['return_bp']=rows.gross_bp if job['symbol']=='XAUUSD' else rows.net_bp
            if job['symbol']=='XAUUSD':rows.loc[:,['fee_bp','net_bp','fee_r','net_r','net_notional_bp']]=np.nan
            rows['entry_interval_open']=f.index[rows.entry_i.astype(int)].astype(str).to_numpy()
            rows['exit_interval_open']=f.index[rows.exit_i.astype(int)].astype(str).to_numpy()
            rows['holding_minutes_upper']=(pd.to_datetime(rows.framework_exit_time)-pd.to_datetime(rows.entry_interval_open)).dt.total_seconds()/60
            rows=label_candles(f,rows,job['execution_minutes'])
            rows=rows.drop(columns=['gross_r','fee_r','net_r','peak_r','net_notional_bp'],errors='ignore')
            rows.rename(columns={'risk':'notional_normalizer'},inplace=True)
    rows.to_csv(dest/'trades.csv.gz',index=False);fills.to_csv(dest/'fills.csv.gz',index=False)
    dump(dest/'audit.json',finite(audit));dump(dest/'identity.json',finite(identity))
    done=dict(symbol=job['symbol'],execution_minutes=job['execution_minutes'],confirm_minutes=job['confirm_minutes'],
        rows=len(f),first=str(f.index[0]),last=str(f.index[-1]),opportunities=len(op),
        touched=int(op.status.eq('admitted').sum()) if len(op) else 0,
        orders=len(e),positions=audit['positions'],fills=audit['fills'],seconds=time.perf_counter()-started)
    dump(dest/'done.json',finite(done));return done


def run(symbols=None,workers=2):
    from yoyo.evaluation.monday_limit_reclaim_inputs import jobs
    cfg=json.loads((EXP/'config.json').read_text())
    if not cfg['run_enabled']:raise RuntimeError('Configuration not enabled')
    for p in CODE+['experiments/active/exp-monday-limit-reclaim-20261024-v1/config.json']:
        if subprocess.run(['git','diff','--quiet','HEAD','--',p],cwd=ROOT).returncode or subprocess.run(['git','ls-files','--error-unmatch',p],cwd=ROOT,capture_output=True).returncode:
            raise RuntimeError('Commit builders and configuration first')
    work=jobs(cfg)
    if symbols:work=[j for j in work if j['symbol'] in symbols]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending={pool.submit(process,j,cfg):j for j in work}
        for k,future in enumerate(as_completed(pending),1):
            job=pending[future]
            try:result=future.result()
            except Exception as error:
                result=dict(symbol=job['symbol'],error=repr(error));dump(EXP/'symbols'/job['symbol']/'error.json',result)
            print(json.dumps(dict(progress=k,total=len(work),**result)),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--workers',type=int,default=2)
    parser.add_argument('--symbols',nargs='*');args=parser.parse_args();run(args.symbols,args.workers)
