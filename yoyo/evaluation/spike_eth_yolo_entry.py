"""Frozen ETH V9 entry-time YOLO association and admission-only serial replay.

Source: exp-spike-eth-v9-yolo-entry-20260915-v1/PROJECT_PLAN.md. V9 uses
current/prior OHLCV with its existing 720-bar preparation. YOLO uses only
18/19 closed candles ending no later than the scheduled entry, plus causal
close SMA/EMA20/60/120. Future prices occur exclusively in exit evaluation.
This observational study never changes monitor state, weights or thresholds.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import pickle
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_replay import features
from yoyo.evaluation.spike_v8_lowtf_study import v8_mask
from yoyo.evaluation.spike_v9_eth_lowtf import admission, prepared_control, evaluate
from yoyo.evaluation.spike_v7_fast import simulate_v6_variant
from yoyo.evaluation.spike_v6_wvf_study import ExecutionSpec, _data_gap
from yoyo.layers.l1_detection.data import add_mas
from yoyo.monitor.yolo_detector import (
    MODEL_SHA256, PREDICT_PARAMETERS, RenderedWindow, parse_prediction,
)

EXP = Path('experiments/active/exp-spike-eth-v9-yolo-entry-20260915-v1')
TRADE_COLUMNS = ['signal_i','entry_i','side','entry_time','signal_bar_open','exit_i',
                 'exit_time','exit_reason','entry_price','exit_price','initial_stop',
                 'initial_risk','initial_risk_frac','gross_return','net_return','gross_r',
                 'net_r','censored']
ARMS = ('v9','v9_same','v9_lower','v9_both')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str)+'\n')


def frozen_code(paths):
    """Verify actual source bytes were committed before producing artifacts."""
    identity = {}
    for path in paths:
        path = Path(path)
        original = subprocess.check_output(['git','show','HEAD:'+str(path)])
        if original != path.read_bytes():
            raise ValueError('uncommitted research dependency: '+str(path))
        identity[str(path)] = sha(path)
    return identity


def dependencies():
    return [Path(__file__).relative_to(Path.cwd()), EXP/'config.json', EXP/'PROJECT_PLAN.md',
            EXP/'authorization.json', Path('yoyo/data/spike_eth_yolo_sources.py'),
            *[Path('yoyo/evaluation')/name for name in (
                'spike_burst_replay.py','spike_v8_lowtf_study.py','spike_v9.py',
                'spike_v9_eth_lowtf.py','spike_v7_fast.py','spike_v6_wvf_study.py',
                'spike_v1_v8_be05.py','spike_exit_policy_study.py')],
            Path('yoyo/monitor/yolo_detector.py'), Path('yoyo/layers/l1_detection/render.py'),
            Path('yoyo/layers/l1_detection/data.py')]


def read_source(path, minutes):
    raw = pd.read_csv(path)
    index = pd.to_datetime(raw.ts, unit='ms', utc=True) if 'ts' in raw else pd.to_datetime(raw.open_time, utc=True)
    frame = raw[['open','high','low','close','volume']].copy()
    frame.index = pd.DatetimeIndex(index).as_unit('ns')
    frame.index.name = 'open_time'
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError('source clock is invalid')
    delta = pd.Timedelta(minutes=minutes)
    if len(frame)>1 and not (frame.index[1:]-frame.index[:-1] == delta).all():
        raise ValueError('source gap')
    frame.attrs['minutes'] = minutes
    return frame


def build_context(ohlcv, minutes, start, end):
    """Freeze all raw reversals; mask admissions to the explicit study window."""
    frame = features(ohlcv)
    frame.attrs['minutes'] = minutes
    raw, bb, v8 = v8_mask(ohlcv, minutes)
    allowed, decisions = admission(frame, v8, minutes)
    close_time = frame.index + pd.Timedelta(minutes=minutes)
    scope = (close_time >= start) & (close_time < end)
    allowed &= scope
    if (frame.index + pd.Timedelta(minutes=minutes) > end).any():
        raise ValueError('source contains unclosed or post-cutoff OHLC')
    return dict(frame=frame, raw=raw, bb=bb, allowed=allowed,
                decisions=decisions, minutes=minutes)


def render_at(frame, minutes, available_at, length):
    """Render the latest complete native prefix, never the opening trade bar."""
    from yoyo.layers.l1_detection.render import render_chart
    delta = pd.Timedelta(minutes=minutes)
    endpoint = int((frame.index + delta).searchsorted(pd.Timestamp(available_at), side='right'))-1
    if endpoint < length-1 or frame.index[endpoint]+delta > available_at:
        raise ValueError('no complete model input')
    if available_at-(frame.index[endpoint]+delta) >= delta:
        raise ValueError('stale model input')
    window = frame.iloc[endpoint-length+1:endpoint+1]
    columns = [f'{kind}{period}' for period in (20,60,120) for kind in ('sma','ema')]
    if not np.isfinite(window[columns].to_numpy(float)).all():
        raise ValueError('model MA warmup missing')
    pixels, transform = render_chart(window, out_path=None)
    times = tuple(int(t.value//1_000_000) for t in window.index)
    digest = hashlib.sha256(np.ascontiguousarray(pixels).tobytes()).hexdigest()
    return RenderedWindow(pixels,transform,times,digest)


def select_detection(proposals, side):
    """Choose a same-side structural hit without looking at prices or outcomes."""
    eligible = [p for p in proposals if p['structural_pass'] and p['side']==('long' if side==1 else 'short')]
    eligible.sort(key=lambda p:(-p['confidence'],p['window_len'],p['detection_id']))
    return eligible[0] if eligible else None


def replay(context, mask, tick):
    _, trades = simulate_v6_variant(context['frame'],context['raw'],admission=mask,
                                    variant='v9',data_gap=_data_gap(context['frame'],context['minutes']),
                                    spec=ExecutionSpec(tick=tick))
    return trades if len(trades) else pd.DataFrame(columns=TRADE_COLUMNS)


def entry_month_controls(prepared, targets, start, end, *, seed=915151):
    """Match random entry by its available entry month, side and ATR/close.

    Port of the existing V9 control contract: same fixed one-draw hash and
    bins, no replacement of failures. All pool features use signal-bar ATR
    and close; month uses the scheduled next-open clock, including boundaries.
    """
    frame=prepared.frame;delta=pd.Timedelta(minutes=prepared.context.minutes)
    vol=np.searchsorted((.005,.01,.02,.05,.1),prepared.atr/prepared.close,side='left')
    month=(frame.index+delta).strftime('%Y-%m')
    eligible=frame.ready.fillna(False).to_numpy(bool)&np.isfinite(prepared.atr)&(prepared.atr>0)&np.isfinite(prepared.close)&(prepared.close>0)
    eligible&=(frame.index+delta>=start)&(frame.index+delta<end)
    groups,cache,rows={},{},[]
    for target in targets.itertuples(index=False):
        i,side=int(target.signal_i),int(target.side);key=(month[i],int(vol[i]));event=target.event_key
        if key not in groups: groups[key]=np.flatnonzero(eligible&(month==key[0])&(vol==key[1]))
        if event not in cache:
            choices=groups[key][groups[key]!=i]
            if not len(choices): cache[event]=(None,None,'empty_stratum')
            else:
                value=int(hashlib.sha256(f'{seed}|{event}'.encode()).hexdigest(),16)
                chosen=int(choices[value%len(choices)]);result=evaluate(prepared,chosen,side)
                cache[event]=(chosen,result,'invalid_initial' if result is None else 'censored' if result['censored'] else 'matched')
        chosen,result,reason=cache[event]
        matched=result is not None and not bool(result['censored']) and not bool(target.censored)
        rows.append(dict(event_key=event,arm=target.arm,stream=target.stream,fold=target.fold,
             entry_time=target.entry_time,signal_i=i,side=side,matched=matched,
             reason='target_censored' if target.censored else reason,target_net_r=target.net_r,
             target_net_return=target.net_return,control_signal_i=chosen,
             control_signal_time=None if chosen is None else frame.index[chosen],
             control_net_r=np.nan if result is None or result['censored'] else result['net_r'],
             control_net_return=np.nan if result is None or result['censored'] else result['net_return'],
             control_exit_time=None if result is None else result['exit_time'],month=key[0],vol_bin=key[1]))
    return pd.DataFrame(rows)


def prepare():
    cfg = json.loads((EXP/'config.json').read_text())
    identity = frozen_code(dependencies())
    if sha(cfg['model_path']) != cfg['model_sha256'] or cfg['model_sha256'] != MODEL_SHA256:
        raise ValueError('weight identity changed')
    out = EXP/'results'; out.mkdir(exist_ok=True)
    if (out/'prepared.json').exists():
        raise ValueError('preparation exists; do not overwrite frozen evidence')
    source = EXP/'sources'
    source_summary = json.loads((source/'summary.json').read_text())
    for tf in cfg['timeframes']:
        if sha(source/f'{tf}.csv') != source_summary['timeframes'][tf]['csv_sha256']:
            raise ValueError('source CSV differs from acquisition receipt: '+tf)
    dump(out/'evaluation_started.json',dict(started_at=pd.Timestamp.now(tz='UTC'),
         source_identity=identity,source_summary_sha256=sha(source/'summary.json'),
         source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
         exact_study_holdout_consumption=1,prior_exposures_preserved=True,
         environment={x:importlib.metadata.version(x) for x in ('numpy','pandas','torch','ultralytics','scipy')}))
    frames = {tf:read_source(source/f'{tf}.csv',minutes) for tf,minutes in cfg['timeframes'].items()}
    start,end = pd.Timestamp(cfg['start_utc']),pd.Timestamp(cfg['end_utc'])
    contexts, rows = {}, []
    for tf,lower in cfg['lower_timeframes'].items():
        context = build_context(frames[tf],cfg['timeframes'][tf],start,end)
        contexts[tf] = context
        for i in np.flatnonzero(context['allowed'].to_numpy(bool)):
            stamp = context['frame'].index[i]
            side = 1 if context['raw'].long_signal.iloc[i] else -1
            rows.append(dict(event_key=f'ETH_{tf}_{int(stamp.value//1_000_000)}_{side}',
                 timeframe=tf,lower_timeframe=lower,signal_i=int(i),side=side,
                 signal_bar_open=stamp,entry_time=stamp+pd.Timedelta(minutes=cfg['timeframes'][tf]),
                 rv=float(context['frame'].rv.iloc[i]),
                 volatility=float(context['frame'].atr.iloc[i]/context['frame'].close.iloc[i])))
    signals = pd.DataFrame(rows)
    signals.to_csv(out/'signals.csv',index=False)
    with (out/'contexts.pkl').open('wb') as f: pickle.dump(dict(contexts=contexts,frames=frames),f)
    dump(out/'prepared.json',dict(signals=len(signals),per_timeframe=signals.groupby('timeframe').size().to_dict(),
         signals_sha256=sha(out/'signals.csv'),contexts_sha256=sha(out/'contexts.pkl'),
         source_files={tf:sha(source/f'{tf}.csv') for tf in cfg['timeframes']}))
    print(json.dumps(json.loads((out/'prepared.json').read_text())),flush=True)


def infer():
    """Freeze input-only predictions before joining any trade outcome."""
    from PIL import Image
    from ultralytics import YOLO
    import torch
    cfg=json.loads((EXP/'config.json').read_text()); out=EXP/'results'
    frozen_code(dependencies())
    prepared=json.loads((out/'prepared.json').read_text())
    if sha(out/'contexts.pkl')!=prepared['contexts_sha256'] or sha(out/'signals.csv')!=prepared['signals_sha256']:
        raise ValueError('prepared input changed')
    with (out/'contexts.pkl').open('rb') as f: saved=pickle.load(f)
    frames={tf:add_mas(frame) for tf,frame in saved['frames'].items()}
    signals=pd.read_csv(out/'signals.csv',parse_dates=['entry_time','signal_bar_open'])
    requests={}
    for s in signals.itertuples():
        for tf in (s.timeframe,s.lower_timeframe):
            key=f'{tf}_{int(s.entry_time.value//1_000_000)}'
            requests[key]=(tf,s.entry_time)
    if sha(cfg['model_path'])!=cfg['model_sha256']: raise ValueError('weight changed')
    model=YOLO(cfg['model_path'])
    if model.names!={0:'dense_long',1:'dense_short'}: raise ValueError('class identity')
    device='mps' if torch.backends.mps.is_available() else 'cpu'
    cache=out/'inference'; cache.mkdir(exist_ok=True)
    inputs=out/'model_inputs'; inputs.mkdir(exist_ok=True)
    started=time.monotonic()
    for n,(key,(tf,stamp)) in enumerate(sorted(requests.items()),1):
        target=cache/f'{key}.json'
        if target.exists():
            prior=json.loads(target.read_text())
            if prior['model_sha256']!=cfg['model_sha256']: raise ValueError('resume model mismatch')
            for path,digest in prior['input_files'].items():
                if sha(path)!=digest: raise ValueError('resume image mismatch')
            continue
        windows=[render_at(frames[tf],cfg['timeframes'][tf],stamp,length) for length in cfg['windows']]
        predictions=model.predict(source=[w.image for w in windows],batch=2,device=device,**PREDICT_PARAMETERS)
        proposals=[]; input_files={}; evidence=[]
        for w,prediction in zip(windows,predictions):
            name=inputs/f'{key}_w{len(w.times)}.png'
            Image.fromarray(w.image).save(name)
            input_files[str(name)]=sha(name)
            evidence.append(dict(path=str(name),pixel_sha256=w.input_pixel_sha256,
                window_start_ms=w.times[0],window_end_ms=w.times[-1],
                last_close_ms=w.times[-1]+cfg['timeframes'][tf]*60_000,
                decision_ms=int(stamp.value//1_000_000),length=len(w.times)))
            if prediction.boxes is not None and len(prediction.boxes):
                b=prediction.boxes
                proposals.extend(parse_prediction(b.xywhn.cpu().numpy(),b.cls.cpu().numpy(),b.conf.cpu().numpy(),w,cfg['symbol'],tf))
        if len(predictions)!=len(windows): raise ValueError('prediction count mismatch')
        dump(target,dict(key=key,timeframe=tf,decision_at=stamp,model_sha256=cfg['model_sha256'],
             device=device,input_files=input_files,inputs=evidence,proposals=proposals))
        if n%20==0 or n==len(requests):
            print(json.dumps(dict(inferred=n,total=len(requests),seconds=round(time.monotonic()-started,1))),flush=True)
    rows=[]
    for s in signals.to_dict('records'):
        for label,tf in [('same',s['timeframe']),('lower',s['lower_timeframe'])]:
            key=f"{tf}_{int(s['entry_time'].value//1_000_000)}"
            record=json.loads((cache/f'{key}.json').read_text())
            best=select_detection(record['proposals'],s['side'])
            s[label+'_hit']=best is not None
            s[label+'_score']=0. if best is None else best['confidence']
            s[label+'_detection_id']=None if best is None else best['detection_id']
            s[label+'_inference_key']=key
            s[label+'_raw_boxes']=len(record['proposals'])
        rows.append(s)
    pd.DataFrame(rows).to_csv(out/'signals_detected.csv',index=False)
    dump(out/'inference_complete.json',dict(requests=len(requests),images=2*len(requests),signals=len(rows),
         model_sha256=cfg['model_sha256'],decisions_sha256=sha(out/'signals_detected.csv'),
         cache_files={p.name:sha(p) for p in sorted(cache.glob('*.json'))},device=device))


def evaluate_trades(tick):
    """Replay each admission arm and independently verify its fixed exits."""
    cfg=json.loads((EXP/'config.json').read_text()); out=EXP/'results'
    frozen_code(dependencies())
    receipt=json.loads((out/'inference_complete.json').read_text())
    if sha(out/'signals_detected.csv')!=receipt['decisions_sha256']: raise ValueError('detections changed')
    prepared_receipt=json.loads((out/'prepared.json').read_text())
    if sha(out/'contexts.pkl')!=prepared_receipt['contexts_sha256']: raise ValueError('context changed')
    for name,digest in receipt['cache_files'].items():
        if sha(out/'inference'/name)!=digest: raise ValueError('prediction cache changed')
    with (out/'contexts.pkl').open('rb') as f: saved=pickle.load(f)
    signals=pd.read_csv(out/'signals_detected.csv',parse_dates=['entry_time','signal_bar_open'])
    all_trades=[]; all_controls=[]; audit=[]
    for tf,context in saved['contexts'].items():
        group=signals.loc[signals.timeframe.eq(tf)].copy()
        stream=dict(name=tf,minutes=context['minutes'],tick=tick,venue='okx',symbol=cfg['symbol'],asset='ETH')
        prep=prepared_control(context['frame'],context['raw'],stream)
        for arm in ARMS:
            chosen=group
            if arm in ('v9_same','v9_both'): chosen=chosen.loc[chosen.same_hit]
            if arm in ('v9_lower','v9_both'): chosen=chosen.loc[chosen.lower_hit]
            mask=pd.Series(False,index=context['frame'].index)
            mask.iloc[chosen.signal_i.to_numpy(int)]=True
            trades=replay(context,mask,tick)
            trades['timeframe']=tf;trades['stream']=tf;trades['arm']=arm;trades['fold']='full'
            trades=trades.merge(group.drop(columns=['entry_time','signal_bar_open','timeframe']),on=['signal_i','side'],how='left',validate='one_to_one')
            if len(trades) and trades.event_key.isna().any(): raise ValueError('unregistered trade')
            for row in trades.itertuples():
                independent=evaluate(prep,int(row.signal_i),int(row.side))
                if independent is None or bool(independent['censored'])!=bool(row.censored): raise ValueError('fixed exit missing')
                for field in ('entry_price','exit_price','initial_stop','initial_risk','net_return','net_r','exit_i'):
                    if not np.isclose(getattr(row,field),independent[field],rtol=1e-9,atol=1e-9,equal_nan=True):
                        raise ValueError(f'fixed exit mismatch {tf} {arm} {row.event_key} {field}')
                if row.exit_reason!=independent['exit_reason']: raise ValueError('fixed exit reason mismatch')
            if len(trades):
                matches=entry_month_controls(prep,trades,pd.Timestamp(cfg['start_utc']),pd.Timestamp(cfg['end_utc']),seed=cfg['control_seed'])
                matches['timeframe']=tf;all_controls.append(matches)
            all_trades.append(trades)
            audit.append(dict(timeframe=tf,arm=arm,signals=len(chosen),trades=len(trades),
                              fixed_exit_parity=True,censored=int(trades.censored.sum()) if len(trades) else 0))
            print(json.dumps(audit[-1]),flush=True)
    pd.concat(all_trades,ignore_index=True).to_csv(out/'trades.csv',index=False)
    (pd.concat(all_controls,ignore_index=True) if all_controls else
     pd.DataFrame(columns=['event_key','arm','timeframe','matched','target_net_r','control_net_r',
                           'target_net_return','control_net_return'])).to_csv(out/'controls.csv',index=False)
    dump(out/'replay_validation.json',dict(arms=audit,tick=tick,
         trades_sha256=sha(out/'trades.csv'),controls_sha256=sha(out/'controls.csv')))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['prepare','infer','replay'])
    parser.add_argument('--tick',type=float)
    args=parser.parse_args()
    if args.stage=='prepare': prepare()
    elif args.stage=='infer': infer()
    else:
        if args.tick is None or args.tick<=0: raise ValueError('explicit verified tick required')
        evaluate_trades(args.tick)


if __name__=='__main__': main()
