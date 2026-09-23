"""Frozen one-axis SPIKE entry experiment, preserving parent executions.

Only close/ATR through the signal bar enter the feature. Its ratio uses current
plus previous 95 bars; rank thresholds use strictly preceding 30 days of bars.
Both windows restart at chart gaps. Candidate events come from authenticated
parent receipts; every candidate is replayed so occupancy changes are causal.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import time
import numpy as np
import pandas as pd
from yoyo.evaluation import spike_v128_recent as parent
from yoyo.evaluation.spike_v128_recent_report import read_csv
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path('experiments/active/exp-spike-v128-expansion-entry-20260923-v1')
CONFIG = EXP/'config.json'
TEST = Path('tests/evaluation/test_spike_v128_expansion_entry.py')
TABLES = ('candidate_outcomes','serial_trades','serial_statuses','controls')
EVENT_COLUMNS = ['trade_key','arm','symbol','timeframe_min','signal_i','signal_close','side','status',
                 'expansion_ratio','expansion_quintile','high20','censored','exit_i','exit_time','net_return','net_r']
CONTROL_COLUMNS = ['trade_key','arm','symbol','timeframe_min','side','utc_week','fold','vol_bin','matched','reason',
                   'target_net_return','control_sig','control_signal_close','control_exit_time','control_exit_reason',
                   'control_censored','control_net_r','control_net_return','control_pool']


def expansion(frame, gap, minutes, cfg):
    """Causal SPIKE ATR/close expansion and strictly-past quantiles, reset on gaps."""
    segments = pd.Series(np.asarray(gap, int).cumsum(), index=frame.index)
    level = (frame.atr/frame.close).where((frame.close > 0) & (frame.atr > 0))
    w = int(cfg['ratio_window_bars'])
    mean = level.groupby(segments).transform(lambda x: x.rolling(w,min_periods=w).mean())
    ratio = (level/mean).replace([np.inf,-np.inf],np.nan)
    lookback = int(cfg['rank_history_days']*1440/minutes)
    qs = pd.DataFrame({q: ratio.groupby(segments).transform(
        lambda x: x.shift(1).rolling(lookback,min_periods=lookback).quantile(q)) for q in cfg['quantiles']})
    valid = ratio.notna() & qs.notna().all(axis=1)
    quintile = (1 + qs.lt(ratio,axis=0).sum(axis=1)).where(valid,0).astype(int)
    return pd.DataFrame({'expansion_ratio':ratio,'expansion_quintile':quintile,'high20':quintile.eq(5)},index=frame.index)


def select_serial(rows, policy, frame_length, carry=(-1,None)):
    """Activate a gate on the same starting position; freed later events may enter."""
    flat_from, holder = carry
    trades, statuses = [], []
    for row in sorted(rows,key=lambda r:r['signal_i']):
        r = dict(row,policy=policy)
        if policy == 'high20' and int(r['expansion_quintile']) != 5:
            status = 'filtered_unknown' if int(r['expansion_quintile']) == 0 else 'filtered_quintile'
        elif int(r['signal_i']) < flat_from:
            status = 'skipped_in_position'
        else:
            status = r['status']
            if pd.notna(r.get('exit_i')):
                trades.append(r)
                holder = r['trade_key']
                flat_from = frame_length+1 if status == 'censored_boundary' else int(r['exit_i'])
        statuses.append({k:r[k] for k in ('trade_key','arm','symbol','timeframe_min','signal_i','signal_close','side','policy','expansion_quintile','high20')} |
                        {'status':status,'blocking_trade':holder if status == 'skipped_in_position' else None})
    return trades,statuses


def carry_position(prepared, statuses, arm, key, cfg, raw_side):
    """Recover only a pre-window parent position that actually blocks an event."""
    if statuses.empty or 'blocking_trade' not in statuses:
        return -1,None
    prefix = f'{key}:{arm}:'
    holders = set()
    for holder in statuses.loc[statuses.arm.eq(arm),'blocking_trade'].dropna():
        if not holder.startswith(prefix): raise ValueError('invalid parent holder identity')
        timestamp = pd.Timestamp(holder[len(prefix):])
        if timestamp < pd.Timestamp(cfg['start']): holders.add(holder)
    if len(holders) > 1: raise ValueError('multiple affecting carry positions')
    if not holders: return -1,None
    holder = holders.pop(); stamp = pd.Timestamp(holder[len(prefix):])
    signal_open = stamp-pd.Timedelta(minutes=prepared.context.minutes)
    i = int(prepared.frame.index.get_loc(signal_open))
    side = 1 if arm == 'joint' else int(raw_side[i])
    if side not in (-1,1): raise ValueError('invalid carry direction')
    status,result = parent.attempt(prepared,i,side)
    if result is None: raise ValueError('carry position cannot be replayed')
    return (len(prepared.frame)+1 if status == 'censored_boundary' else int(result['exit_i'])),holder


def assert_parity(actual, expected, keys, columns):
    """Fail closed on different entries, statuses, blockers, or execution outcomes."""
    if len(actual) != len(expected): raise ValueError(f'parent count mismatch {len(actual)} != {len(expected)}')
    if not len(expected): return
    a=actual.set_index(keys).sort_index(); e=expected.set_index(keys).sort_index()
    pd.testing.assert_index_equal(a.index,e.index,check_names=False)
    for col in columns:
        if col in ('signal_close','entry_time','exit_time','control_signal_close','control_exit_time'):
            pd.testing.assert_series_equal(pd.to_datetime(a[col],utc=True),pd.to_datetime(e[col],utc=True),check_names=False)
        elif pd.api.types.is_numeric_dtype(e[col]) and not pd.api.types.is_bool_dtype(e[col]):
            np.testing.assert_allclose(pd.to_numeric(a[col]),pd.to_numeric(e[col]),rtol=1e-12,atol=1e-10,equal_nan=True,err_msg=col)
        else:
            pd.testing.assert_series_equal(a[col].fillna('').astype(str),e[col].fillna('').astype(str),check_names=False)


def controls_for(prepared, trades, cfg, gate):
    """One deterministic draw per pool and event; no redraw for unfilled/censored entries."""
    frame, minutes = prepared.frame, prepared.context.minutes
    close = frame.index + pd.Timedelta(minutes=minutes)
    week = np.asarray(close.strftime('%G-W%V')); fold=np.where(close<pd.Timestamp(cfg['split']),'earlier','later')
    bins=np.searchsorted(np.asarray(cfg['vol_bins'],float),prepared.atr/prepared.close,side='left')
    ready=frame.get('ready',pd.Series(False,index=frame.index)).fillna(False).to_numpy(bool)
    valid=(ready & np.isfinite(prepared.atr) & (prepared.atr>0) & np.isfinite(prepared.close) & (prepared.close>0)
           & ~prepared.gap & (close>=pd.Timestamp(cfg['start'])) & (close<pd.Timestamp(cfg['end'])))
    cache={}; pools={}; rows=[]
    for pool in ('all','high20'):
        pool_valid=valid if pool=='all' else valid & np.asarray(gate,bool)
        for trade in trades:
            i,side=int(trade['signal_i']),int(trade['side']); stratum=(pool,side,week[i],fold[i],int(bins[i]))
            if stratum not in pools:
                pools[stratum]=np.flatnonzero(pool_valid & (week==stratum[2]) & (fold==stratum[3]) & (bins==stratum[4]))
            choices=pools[stratum][pools[stratum]!=i]
            chosen=None if not len(choices) else int(choices[int(hashlib.sha256(f"{cfg['control_seed']}|{trade['trade_key']}".encode()).hexdigest(),16)%len(choices)])
            status,control='empty_stratum',None
            if chosen is not None:
                if (chosen,side) not in cache: cache[chosen,side]=parent.attempt(prepared,chosen,side)
                status,control=cache[chosen,side]
            matched=control is not None and status=='closed' and not bool(trade['censored'])
            rows.append({'trade_key':trade['trade_key'],'arm':trade['arm'],'symbol':trade['symbol'],'timeframe_min':minutes,
                         'side':side,'utc_week':week[i],'fold':fold[i],'vol_bin':int(bins[i]),'matched':bool(matched),
                         'reason':'target_censored' if bool(trade['censored']) else ('matched' if matched else status),
                         'target_net_return':trade['net_return'],'control_sig':chosen,
                         'control_signal_close':None if chosen is None else close[chosen],
                         'control_exit_time':None if control is None else control['exit_time'],
                         'control_exit_reason':None if control is None else control['exit_reason'],
                         'control_censored':True if control is None else control['censored'],
                         'control_net_r':np.nan if not matched else control['net_r'],
                         'control_net_return':np.nan if not matched else control['net_return'],'control_pool':pool})
    return pd.DataFrame(rows,columns=CONTROL_COLUMNS)


def authenticated_parent(folder, identity, manifest, symbol, minutes):
    key=f'{symbol}_{minutes}m'
    if parent.digest(folder/'receipt.json') != manifest['receipts'][key]: raise ValueError('parent receipt changed')
    return parent._validate_receipt(folder,manifest['run_identity'],identity['inputs'][symbol])


def validate_receipt(folder, run_identity, input_sha, symbol, minutes, parent_sha):
    r=json.loads((folder/'receipt.json').read_text())
    if (r['run_identity']!=run_identity or r['input_sha256']!=input_sha or r['status']!='complete' or
            r['symbol']!=symbol or r['minutes']!=minutes or r['parent_receipt_sha256']!=parent_sha):
        raise ValueError('study receipt identity mismatch')
    if set(r['files'])!={n+'.csv.gz' for n in TABLES}: raise ValueError('study receipt inventory mismatch')
    for name,sha in r['files'].items():
        if parent.digest(folder/name)!=sha: raise ValueError('study output changed')
    return r


def worker(args):
    symbol,minutes,path,meta,output,cfg,ident,pident,pmanifest,input_sha=args
    final=Path(output)/'streams'/f'{symbol}_{minutes}m'
    folder=Path(cfg['parent_run'])/'streams'/final.name
    pr=authenticated_parent(folder,pident,pmanifest,symbol,minutes)
    if parent.digest(Path(path))!=input_sha: raise ValueError('input changed')
    if (final/'receipt.json').exists():
        return validate_receipt(final,ident,input_sha,symbol,minutes,parent.digest(folder/'receipt.json'))
    staging=final.with_name('.'+final.name+'.staging')
    if staging.exists(): raise ValueError(f'incomplete staging retained: {staging}')
    started=time.perf_counter(); pcfg=pident['config']; merged=pcfg|cfg
    raw=pd.read_csv(path,usecols=['ts','open','high','low','close','volume'])
    index=pd.DatetimeIndex(pd.to_datetime(raw.ts.to_numpy(),unit='ms',utc=True))
    if not index.is_monotonic_increasing or not index.is_unique or (index.asi8%pd.Timedelta(minutes=5).value!=0).any():
        raise ValueError('invalid source clock')
    base=pd.DataFrame(raw[['open','high','low','close','volume']].to_numpy(float),index=index,columns=['open','high','low','close','volume'])
    base=base.loc[(base.index>=pd.Timestamp(pcfg['warmup_start'])) & (base.index+pd.Timedelta(minutes=5)<=pd.Timestamp(cfg['end']))]
    bars,_=parent.complete_bars(base,minutes)
    facts=parent.facts_for(bars,base,meta['asset'],float(meta['tick']),minutes)
    frame=facts['frame']; key=f'binance_um:{symbol}:{minutes}m'
    prepared=parent.source.prepared_arm(frame,facts['gap'],facts['side'],key,
                {'venue':'binance_um','symbol':symbol,'asset':meta['asset'],'timeframe_min':minutes},minutes,float(meta['tick']))
    feature=expansion(frame,facts['gap'],minutes,cfg)
    decisions=read_csv(folder/'decisions.csv.gz'); oldtrades=read_csv(folder/'trades.csv.gz')
    oldstatuses=read_csv(folder/'statuses.csv.gz'); oldcontrols=read_csv(folder/'controls.csv.gz')
    outcomes=[]; cache={}
    for event in decisions.to_dict('records'):
        i,side=int(event['signal_i']),int(event['side']); stamp=frame.index[i]+pd.Timedelta(minutes=minutes)
        if stamp!=pd.Timestamp(event['signal_close']): raise ValueError('candidate clock mismatch')
        if not (pd.Timestamp(cfg['start'])<=stamp<pd.Timestamp(cfg['end'])): raise ValueError('candidate outside frozen window')
        if (i,side) not in cache: cache[i,side]=parent.attempt(prepared,i,side)
        status,result=cache[i,side]
        row=event | {'signal_close':stamp,'trade_key':f"{key}:{event['arm']}:{stamp.isoformat()}",
                     'expansion_ratio':float(feature.expansion_ratio.iloc[i]),
                     'expansion_quintile':int(feature.expansion_quintile.iloc[i]),'high20':bool(feature.high20.iloc[i]),'status':status}
        if result is not None: row |= result | {'trail_armed':bool(result['close_peak_r']>=2.)}
        outcomes.append(row)
    trades=[]; statuses=[]; carries={}
    for arm in parent.ARMS:
        carry=carry_position(prepared,oldstatuses,arm,key,cfg,facts['side']); carries[arm]=carry[1]
        armrows=[r for r in outcomes if r['arm']==arm]
        for policy in ('baseline','high20'):
            t,s=select_serial(armrows,policy,len(frame),carry); trades.extend(t); statuses.extend(s)
    tframe=pd.DataFrame(trades) if trades else pd.DataFrame(columns=EVENT_COLUMNS+['policy'])
    sframe=pd.DataFrame(statuses) if statuses else pd.DataFrame(columns=EVENT_COLUMNS+['policy','blocking_trade'])
    base=tframe[tframe.policy.eq('baseline')]; basestatus=sframe[sframe.policy.eq('baseline')]
    assert_parity(base,oldtrades,['trade_key'],[c for c in oldtrades.columns if c not in decisions.columns and c!='trade_key'])
    assert_parity(basestatus,oldstatuses,['arm','signal_i'],['status','blocking_trade'] if len(oldstatuses) else [])
    controls=controls_for(prepared,[r for r in outcomes if pd.notna(r.get('exit_i'))],merged,feature.high20)
    old_subset=controls[controls.control_pool.eq('all') & controls.trade_key.isin(base.trade_key)]
    assert_parity(old_subset,oldcontrols,['trade_key'],[c for c in oldcontrols.columns if c!='trade_key'])
    table={'candidate_outcomes':pd.DataFrame(outcomes) if outcomes else pd.DataFrame(columns=EVENT_COLUMNS),
           'serial_trades':tframe,'serial_statuses':sframe,'controls':controls}
    staging.mkdir(parents=True)
    for name,f in table.items(): f.to_csv(staging/(name+'.csv.gz'),index=False,compression={'method':'gzip','mtime':0})
    inwindow=parent._window(frame.index,minutes,cfg)
    summary=pr['summary'] | {'parent_parity':True,'feature_ready_window_bars':int(((feature.expansion_quintile>0)&inwindow).sum()),
                             'high20_window_bars':int((feature.high20&inwindow).sum()),
                             'unknown_candidates':sum(r['expansion_quintile']==0 for r in outcomes),
                             'carry_v9_both':carries['v9_both'],'carry_joint':carries['joint']}
    receipt={'status':'complete','symbol':symbol,'minutes':minutes,'run_identity':ident,'input_sha256':input_sha,
             'summary':summary,'files':{n+'.csv.gz':parent.digest(staging/(n+'.csv.gz')) for n in TABLES},
             'parent_receipt_sha256':parent.digest(folder/'receipt.json'),'wall_seconds':time.perf_counter()-started}
    parent.dump(staging/'receipt.json',receipt); staging.replace(final)
    return receipt


def run(output,workers=8,symbols=None):
    cfg=json.loads(CONFIG.read_text()); parent_run=Path(cfg['parent_run'])
    pi=json.loads((parent_run/'identity.json').read_text()); pm=json.loads((parent_run/'manifest.json').read_text())
    if not pm['complete'] or pm['errors'] or pi['subset']: raise ValueError('parent must be full and error-free')
    if hashlib.sha256(json.dumps(pi,sort_keys=True).encode()).hexdigest()!=pm['run_identity']: raise ValueError('parent identity hash mismatch')
    for name,sha in pi['code'].items():
        if parent.digest(Path(name))!=sha: raise ValueError(f'parent code changed: {name}')
    for name in ('start','split','end','timeframes','round_trip_cost','control_seed'):
        if cfg[name]!=pi['config'][name]: raise ValueError(f'parent contract changed: {name}')
    declared=(Path(__file__),TEST,CONFIG,EXP/'PROJECT_PLAN.md',Path('yoyo/evaluation/spike_v128_recent_report.py'))
    if not _committed(declared): raise ValueError('commit builder, tests, config and plan before market construction')
    mpath=Path(pi['input_manifest']); metadata=pi['symbol_meta_source']
    if parent.digest(mpath)!=pi['input_manifest_sha256'] or parent.digest(Path(metadata['path']))!=metadata['sha256']:
        raise ValueError('parent input/metadata manifest changed')
    bysymbol={r['symbol']:r for r in json.loads(mpath.read_text())['streams']}
    requested=sorted(pi['symbols'] if symbols is None else symbols)
    if not requested or not set(requested)<=set(pi['symbols']): raise ValueError('invalid symbol subset')
    identity={'config':cfg,'config_sha256':parent.digest(CONFIG),'parent_identity_sha256':parent.digest(parent_run/'identity.json'),
              'parent_manifest_sha256':parent.digest(parent_run/'manifest.json'),'code':{str(p):parent.digest(p) for p in declared},
              'inputs':{s:pi['inputs'][s] for s in requested},'symbols':requested,'timeframes':cfg['timeframes'],'subset':symbols is not None}
    rid=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    output=Path(output); output.mkdir(parents=True,exist_ok=True)
    if (output/'identity.json').exists() and json.loads((output/'identity.json').read_text())!=identity:
        raise ValueError('identity changed; preserve previous run and use a new output')
    parent.dump(output/'identity.json',identity); (output/'streams').mkdir(exist_ok=True)
    meta=parent.source.symbol_meta()
    tasks=[(s,m,bysymbol[s]['path'],meta[s],str(output),cfg,rid,pi,pm,pi['inputs'][s]) for s in requested for m in cfg['timeframes']]
    receipts=[]; errors=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending={pool.submit(worker,a):(a[0],a[1]) for a in tasks}
        for n,f in enumerate(as_completed(pending),1):
            symbol,minutes=pending[f]
            try: receipts.append(f.result())
            except Exception as exc: errors.append({'symbol':symbol,'minutes':minutes,'error':repr(exc)})
            if n==1 or n%40==0 or n==len(tasks): print(json.dumps({'done':n,'total':len(tasks),'errors':len(errors),'latest':f'{symbol}_{minutes}m'}),flush=True)
    parent.dump(output/'manifest.json',{'complete':not errors and symbols is None and len(receipts)==len(tasks),
                'run_identity':rid,'receipts':{f"{r['symbol']}_{r['minutes']}m":parent.digest(output/'streams'/f"{r['symbol']}_{r['minutes']}m"/'receipt.json') for r in receipts},'errors':errors})
    if errors: raise RuntimeError(f'{len(errors)} stream failures; inspect retained manifest')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--workers',type=int,default=8);p.add_argument('--symbols',nargs='+');a=p.parse_args()
    run(a.output,a.workers,a.symbols)
