"""Frozen V12.8 ordered confirmation study with actual delayed next-open fills.

The level uses only the original signal high/low; decisions inspect closed bars
through their decision time, at most 24 bars. Stop geometry uses the original
five bars and original ATR. Each entry is repriced, preserving absolute stop,
2R/4ATR exit semantics, raw opposite exits and 20bp costs. Parent identities and
unfiltered serial/control parity are mandatory. Random anchors are drawn once.
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
from yoyo.evaluation.spike_v128_expansion_entry import carry_position, assert_parity, authenticated_parent
from yoyo.evaluation.spike_v128_retest_state import confirmation
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path('experiments/active/exp-spike-v128-retest-entry-20260923-v1')
CONFIG = EXP/'config.json'
TEST = Path('tests/evaluation/test_spike_v128_retest_entry.py')
TABLES = ('candidate_outcomes','serial_trades','serial_statuses','controls')
POLICIES = ('baseline','wait3','retest')
EVENT_COLUMNS = ('trade_key','arm','symbol','timeframe_min','signal_i','signal_close','side','status',
                 'anchor_i','anchor_close','policy','censored','exit_i','exit_time','net_return','net_r',
                 'confirmation_status','breakout_i','retest_i','confirmation_i','decision_i','decision_time')


def delayed_attempt(prepared, i, side, cfg, policy='retest'):
    """Preserve anchor stop, inspect only waiting bars, then reprice actual next open."""
    frame=prepared.frame; minutes=prepared.context.minutes
    trace={'decision_i':i,'confirmation_status':'anchor_risk_invalid'}
    if i+1>=len(frame): return 'no_next_bar',None,trace
    if prepared.gap[i+1]:
        trace['decision_i']=i+1
        return 'next_bar_is_gap',None,trace
    initial=parent._initial_position_fast(frame.index,prepared.open,prepared.high,prepared.low,
        prepared.close,prepared.atr,prepared.gap,i,side,prepared.spec)
    if initial is None: return 'risk_invalid',None,trace
    stop=float(initial['initial_stop']); anchor_price=float(initial['entry_price']); anchor_risk=float(initial['initial_risk'])
    if policy=='retest':
        trace=confirmation(prepared.open,prepared.high,prepared.low,prepared.close,prepared.gap,
                           prepared.raw_side,i,side,stop,int(cfg['max_wait_bars']))
    elif policy=='wait3':
        target=i+int(cfg['diagnostic_wait_bars'])
        trace={'status':'confirmed' if target<len(frame) else 'pending_boundary',
               'decision_i':min(target,len(frame)-1),'confirmation_i':target if target<len(frame) else None}
        for j in range(i+1,min(target,len(frame)-1)+1):
            reason=('cancel_gap' if prepared.gap[j] else 'cancel_opposite' if prepared.raw_side[j]==-side else
                    'cancel_stop' if (prepared.low[j]<=stop if side==1 else prepared.high[j]>=stop) else None)
            if reason:
                trace.update(status=reason,decision_i=j,confirmation_i=None);break
    else: raise ValueError('unknown delayed policy')
    trace=dict(trace);trace['confirmation_status']=trace.pop('status')
    if trace['confirmation_status']=='canceled': trace['confirmation_status']='cancel_'+trace['cancel_reason']
    trace.update(anchor_entry_price=anchor_price,anchor_initial_risk=anchor_risk,anchor_initial_stop=stop)
    if trace['confirmation_status']!='confirmed': return trace['confirmation_status'],None,trace
    confirm=int(trace['confirmation_i']); j=confirm+1
    if j>=len(frame): return 'no_next_bar',None,trace
    if prepared.gap[j]:
        trace['decision_i']=j
        return 'next_bar_is_gap',None,trace
    entry=float(prepared.open[j]);risk=side*(entry-stop)
    if not np.isfinite(entry) or entry<=0 or risk<=0: return 'delayed_risk_invalid',None,trace
    initial.update(signal_i=confirm,signal_bar_open=frame.index[confirm],entry_i=j,entry_time=frame.index[j],
                   entry_price=entry,initial_risk=risk,initial_risk_frac=risk/entry)
    result=parent.fixed.replay_fixed_entry(prepared.context,pd.Series(initial),arm='v8',enable_be=False,prepared=prepared)
    result.update(parent._mfe_fields(result,prepared))
    result.update(net_r_anchor=result['net_return']*entry/anchor_risk,
                  chase_anchor_r=side*(entry-anchor_price)/anchor_risk,risk_ratio=risk/anchor_risk,
                  delay_bars=confirm-i)
    status=('closed' if not result['censored'] else 'censored_boundary' if result['exit_reason']=='boundary_mark' else 'censored_gap')
    return status,result,trace


def attach_execution(event, result):
    """Execution-engine generic identity must not replace experimental event identity."""
    return result | event | {'trail_armed':bool(result['close_peak_r']>=2.)}


def select_serial(rows, policy, frame_length, carry=(-1,None)):
    """Chronological confirmations arbitrate occupancy; an occupied confirmation expires."""
    flat_from,holder=carry;trades=[];statuses=[]
    for original in sorted((r for r in rows if r['policy']==policy),key=lambda r:(r['signal_i'],r['anchor_i'])):
        r=dict(original); status=r['status']
        filled=pd.notna(r.get('exit_i'))
        if filled and int(r['signal_i'])<flat_from: status='skipped_in_position'
        elif filled:
            trades.append(r);holder=r['trade_key']
            flat_from=frame_length+1 if status=='censored_boundary' else int(r['exit_i'])
        statuses.append({k:r.get(k) for k in EVENT_COLUMNS if k not in ('net_return','net_r','exit_i','exit_time','censored')} |
                        {'status':status,'blocking_trade':holder if status=='skipped_in_position' else None})
    return trades,statuses


def controls_for(prepared, outcomes, cfg):
    """One original-stratum random anchor, then the same policy; never redraw failures."""
    baseline=[dict(censored=False,net_return=0.,**{k:v for k,v in r.items() if k not in ('censored','net_return')}) |
              {'censored':r.get('censored',False),'net_return':r.get('net_return',0.)}
              for r in outcomes if r['policy']=='baseline']
    original=parent.matched_controls(prepared,baseline,cfg)
    bykey={(r['trade_key'],r['policy']):r for r in outcomes};result=[];cache={}
    minutes=prepared.context.minutes;close=prepared.frame.index+pd.Timedelta(minutes=minutes)
    for c in original.to_dict('records'):
        c['control_pool']='baseline';c['target_status']=bykey[c['trade_key'],'baseline']['status']
        if c['target_status']!='closed':
            c['matched']=False
            if not c['target_status'].startswith('censored'): c['reason']='target_nofill'
        c['target_decision_time']=bykey[c['trade_key'],'baseline']['decision_time']
        chosen=c['control_sig']
        control_key=(int(chosen),int(c['side']),'baseline') if pd.notna(chosen) else None
        if control_key is not None and control_key not in cache:
            cache[control_key]=(*parent.attempt(prepared,control_key[0],control_key[1]),{})
        c['control_status']='empty_stratum' if control_key is None else cache[control_key][0]
        c['control_decision_time']=c['control_signal_close'];c['control_anchor_i']=c['control_sig']
        result.append(c)
        for policy in ('wait3','retest'):
            target=bykey[c['trade_key'],policy];chosen=c['control_sig'];status='empty_stratum';control=None;trace={}
            if pd.notna(chosen):
                ck=(int(chosen),int(c['side']),policy)
                if ck not in cache: cache[ck]=delayed_attempt(prepared,ck[0],ck[1],cfg,policy)
                status,control,trace=cache[ck]
            matched=target['status']=='closed' and status=='closed'
            r=dict(c);r.update(control_pool=policy,matched=matched,target_status=target['status'],control_status=status,
                reason='matched' if matched else f"target:{target['status']}|control:{status}",
                target_net_return=target.get('net_return',np.nan),target_decision_time=target['decision_time'],
                control_anchor_i=chosen,control_confirmation_i=trace.get('confirmation_i'),
                control_signal_close=None if trace.get('confirmation_i') is None else close[int(trace['confirmation_i'])],
                control_decision_time=None if not trace else close[int(trace['decision_i'])],
                control_exit_time=None if control is None else control['exit_time'],
                control_exit_reason=None if control is None else control['exit_reason'],
                control_censored=False if control is None else control['censored'],
                control_net_r=np.nan if control is None else control['net_r'],
                control_net_return=np.nan if control is None else control['net_return'])
            result.append(r)
    return pd.DataFrame(result) if result else pd.DataFrame(columns=['trade_key','control_pool','matched'])

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
    decisions=read_csv(folder/'decisions.csv.gz'); oldtrades=read_csv(folder/'trades.csv.gz')
    oldstatuses=read_csv(folder/'statuses.csv.gz'); oldcontrols=read_csv(folder/'controls.csv.gz')
    outcomes=[];cache={}
    for event in decisions.to_dict('records'):
        i,side=int(event['signal_i']),int(event['side']); stamp=frame.index[i]+pd.Timedelta(minutes=minutes)
        if stamp!=pd.Timestamp(event['signal_close']): raise ValueError('candidate clock mismatch')
        if not (pd.Timestamp(cfg['start'])<=stamp<pd.Timestamp(cfg['end'])): raise ValueError('candidate outside window')
        for policy in POLICIES:
            ck=(i,side,policy)
            if ck not in cache:
                if policy=='baseline':
                    status,result=parent.attempt(prepared,i,side)
                    cache[ck]=(status,result,{'decision_i':i,'confirmation_status':'immediate'})
                else: cache[ck]=delayed_attempt(prepared,i,side,cfg,policy)
            status,result,trace=cache[ck]
            decision=int(trace['decision_i']); actual_i=int(trace.get('confirmation_i') or decision)
            row=event | {'anchor_i':i,'anchor_close':stamp,'signal_i':actual_i,
                'signal_close':frame.index[actual_i]+pd.Timedelta(minutes=minutes),
                'trade_key':f"{key}:{event['arm']}:{stamp.isoformat()}",'policy':policy,'status':status} | trace
            row['decision_time']=frame.index[decision]+pd.Timedelta(minutes=minutes)
            if result is not None: row=attach_execution(row,result)
            outcomes.append(row)
    trades=[];statuses=[];carries={}
    for arm in parent.ARMS:
        carry=carry_position(prepared,oldstatuses,arm,key,cfg,facts['side']);carries[arm]=carry[1]
        armrows=[r for r in outcomes if r['arm']==arm]
        for policy in POLICIES:
            t,st=select_serial(armrows,policy,len(frame),carry);trades.extend(t);statuses.extend(st)
    tframe=pd.DataFrame(trades) if trades else pd.DataFrame(columns=EVENT_COLUMNS)
    sframe=pd.DataFrame(statuses) if statuses else pd.DataFrame(columns=EVENT_COLUMNS+('blocking_trade',))
    base=tframe[tframe.policy.eq('baseline')];basestatus=sframe[sframe.policy.eq('baseline')]
    assert_parity(base,oldtrades,'trade_key',[c for c in oldtrades if c!='trade_key'])
    assert_parity(basestatus,oldstatuses,['arm','signal_i'],['status','blocking_trade'] if len(oldstatuses) else [])
    controls=controls_for(prepared,outcomes,merged)
    oldsubset=controls[controls.control_pool.eq('baseline') & controls.trade_key.isin(base.trade_key)]
    assert_parity(oldsubset,oldcontrols,'trade_key',[c for c in oldcontrols if c!='trade_key'])
    table={'candidate_outcomes':pd.DataFrame(outcomes) if outcomes else pd.DataFrame(columns=EVENT_COLUMNS),
           'serial_trades':tframe,'serial_statuses':sframe,'controls':controls}
    staging.mkdir(parents=True)
    for name,f in table.items(): f.to_csv(staging/(name+'.csv.gz'),index=False,compression={'method':'gzip','mtime':0})
    inwindow=parent._window(frame.index,minutes,cfg)
    summary=pr['summary'] | {'parent_parity':True,'carry_v9_both':carries['v9_both'],'carry_joint':carries['joint']}
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
    declared=(Path(__file__),TEST,CONFIG,EXP/'PROJECT_PLAN.md',
        Path('yoyo/evaluation/spike_v128_retest_state.py'),Path('tests/evaluation/test_spike_v128_retest_state.py'),
        Path('yoyo/evaluation/spike_v128_expansion_entry.py'),Path('yoyo/evaluation/spike_v128_recent_report.py'))
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
