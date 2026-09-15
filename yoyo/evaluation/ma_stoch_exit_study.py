"""Pre-May exit experiment with development-only selection and frozen validation.

Protocol: exp-ma-stoch-exit-optimization-20260915-v1/PROJECT_PLAN.md. Calls the
restricted timestamp-first reader, never reads holdout prices. Same-day random
controls use prior SMA20 true-range/close, with fixed bins and one hashed draw.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess
import numpy as np
import pandas as pd
from yoyo.data.spike_fanshen_prefix import read_prefix
from yoyo.evaluation.ma_stoch_exit_engine import POLICIES, prepare, simulate, BAR
from yoyo.evaluation.ma_shift_stoch_study import dump, sha, metrics, control_stats
from yoyo.evaluation.ma_shift_stoch import run_backtest

ROOT=Path(__file__).resolve().parents[2]
EXP=ROOT/'experiments/active/exp-ma-stoch-exit-optimization-20260915-v1'
BUILDERS=['yoyo/evaluation/ma_stoch_exit_study.py','yoyo/evaluation/ma_stoch_exit_engine.py',
          'yoyo/evaluation/ma_shift_stoch.py','yoyo/evaluation/ma_shift_stoch_study.py',
          'yoyo/evaluation/spike_fanshen_exit.py','yoyo/data/spike_fanshen_prefix.py',
          str((EXP/'config.json').relative_to(ROOT)),str((EXP/'PROJECT_PLAN.md').relative_to(ROOT)),
          'tests/test_ma_stoch_exit_engine.py','tests/test_ma_stoch_exit_study.py']


def committed_identity(paths):
    receipt={}
    for path in paths:
        raw=(ROOT/path).read_bytes()
        if subprocess.check_output(['git','show','HEAD:'+path],cwd=ROOT)!=raw:
            raise ValueError('uncommitted required file: '+path)
        receipt[path]=hashlib.sha256(raw).hexdigest()
    return receipt


def select(arms,minimum=30):
    eligible=[k for k,v in arms.items() if v['n']>=minimum]
    if not eligible:raise ValueError('no candidate has enough development trades')
    return sorted(eligible,key=lambda k:(-arms[k]['final_equity'],arms[k]['mtm_max_drawdown_pct'],arms[k]['n'],k))[0]


def validation_arms(selection):
    name=selection['selected']
    if name not in POLICIES:raise ValueError('unregistered selection')
    return list(dict.fromkeys(['baseline','stop_2',name]))


def matched_controls(ctx,targets,start,end,policy,cfg):
    close_clock=ctx['index']+BAR
    vol=ctx['vol'];buckets=np.searchsorted(cfg['volatility_bins'],vol,side='left')
    days=close_clock.strftime('%Y-%m-%d')
    allowed=(close_clock>=start)&(close_clock<end)&np.isfinite(vol)
    allowed[-1]=False
    strata={}
    for i in np.flatnonzero(allowed):strata.setdefault((days[i],int(buckets[i])),[]).append(int(i))
    rows=[]
    for target in targets.itertuples(index=False):
        i=int(target.signal_i);side=int(target.side)
        choices=[j for j in strata.get((days[i],int(buckets[i])),[]) if j!=i]
        event=f'{pd.Timestamp(target.entry_time).isoformat()}|{side}'
        row=dict(target_trade_id=int(target.trade_id),event=event,day=days[i],vol_bin=int(buckets[i]),side=side,
                 signal_i=i,target_net_return=float(target.net_return),target_gross_return=float(target.gross_return),
                 choices=len(choices),matched=False)
        if choices:
            chosen=choices[int(hashlib.sha256(f'{cfg["control_seed"]}|{event}'.encode()).hexdigest(),16)%len(choices)]
            replay=simulate(ctx,start,end,policy,forced=(chosen,side),tick=cfg['tick'],keep_curve=False)
            if len(replay['trades'])>1:raise AssertionError('multiple control trades')
            row.update(control_signal_i=chosen,control_entry_time=close_clock[chosen],reason='censored')
            if len(replay['trades'])==1:
                r=replay['trades'].iloc[0]
                row.update(matched=True,reason='matched',control_net_return=float(r.net_return),
                           control_gross_return=float(r.gross_return),control_exit_time=r.exit_time,
                           excess_net_return=float(target.net_return-r.net_return))
        else:row['reason']='empty_stratum'
        rows.append(row)
    return pd.DataFrame(rows)


def summarise(r):
    tr=r['trades'];curve=r['curve'].equity.to_numpy(float)
    row=dict(**metrics(tr),entries=len(tr)+len(r['open_positions']),open_count=len(r['open_positions']),
             final_equity=r['final_equity'],mtm_return_pct=(r['final_equity']/1000-1)*100,
             mtm_max_drawdown_pct=float(-(curve/np.maximum.accumulate(curve)-1).min()*100),
             ambiguous_bars=r['ambiguous_bars'],gross_r_mean=None if tr.empty else tr.gross_r.mean(),
             net_r_mean=None if tr.empty else tr.net_r.mean(),
             exit_reasons=tr.exit_reason.value_counts().to_dict())
    row['sides']={label:metrics(tr[tr.side==side]) for label,side in [('long',1),('short',-1)]}
    return row


def run(phase):
    cfg=json.loads((EXP/'config.json').read_text())
    if cfg['round_trip_cost']!=.002 or cfg['initial_equity']!=1000:raise ValueError('fixed engine cost/equity mismatch')
    code=committed_identity(BUILDERS)
    if phase=='dev':
        start,end=pd.Timestamp(cfg['dev_start']),pd.Timestamp(cfg['dev_end']);names=list(POLICIES)
    elif phase=='validate':
        frozen=committed_identity([str((EXP/'selection.json').relative_to(ROOT)),str((EXP/'dev/summary.json').relative_to(ROOT))])
        selection=json.loads((EXP/'selection.json').read_text())
        if selection['code']!=code or selection['dev_summary_sha256']!=sha(EXP/'dev/summary.json'):
            raise ValueError('selection and builder/source identity changed')
        start,end=pd.Timestamp(cfg['val_start']),pd.Timestamp(cfg['val_end']);names=validation_arms(selection)
    else:raise ValueError('unknown phase')
    out=EXP/phase
    if out.exists():raise ValueError('phase directory exists: frozen runs cannot overwrite')
    frame,source=read_prefix(ROOT/cfg['source'],5,end)
    ctx=prepare(frame);out.mkdir()
    summary=dict(phase=phase,start=start,end=end,config=cfg,code=code,source=source,
                 source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                 generated_at=pd.Timestamp.now(tz='UTC'),holdout_consumed=False,
                 evaluated_bars=int(((frame.index>=start)&(frame.index<end)).sum()),
                 policies={n:asdict(POLICIES[n]) for n in names},arms={})
    if phase=='validate':summary['selection_identity']=frozen
    for name in names:
        r=simulate(ctx,start,end,name,initial_equity=cfg['initial_equity'],tick=cfg['tick'])
        for key in ('trades','fills','open_positions','curve'):r[key].to_csv(out/f'{name}_{key}.csv',index=False)
        stats=summarise(r)
        controls=matched_controls(ctx,r['trades'],start,end,name,cfg)
        controls.to_csv(out/f'{name}_controls.csv',index=False)
        stats['control']=control_stats(controls,cfg)
        if name=='baseline':
            old=run_backtest(frame,start,end)
            for key in ('entry_time','exit_time','side'):
                if old.trades[key].tolist()!=r['trades'][key].tolist():raise AssertionError('baseline ledger parity: '+key)
            np.testing.assert_allclose(old.trades.net_return,r['trades'].net_return,rtol=0,atol=1e-12)
            np.testing.assert_allclose(old.equity_curve.iloc[-1].equity,r['final_equity'],rtol=1e-12)
            stats['baseline_parity']=True
        summary['arms'][name]=stats
        print(name,json.dumps(stats,ensure_ascii=False,default=str),flush=True)
    dump(out/'summary.json',summary)
    if phase=='dev':
        chosen=select(summary['arms'],cfg['minimum_dev_closed_trades'])
        dump(EXP/'selection.json',dict(selected=chosen,selected_at=pd.Timestamp.now(tz='UTC'),
             selection_rule=cfg['selection'],code=code,dev_summary_sha256=sha(out/'summary.json'),
             dev_source_prefix_sha256=source['prefix_sha256'],holdout_consumed=False,
             development_profitable=summary['arms'][chosen]['mtm_return_pct']>0))
        print('FROZEN DEVELOPMENT SELECTION:',chosen,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--phase',required=True,choices=['dev','validate'])
    run(p.parse_args().phase)
