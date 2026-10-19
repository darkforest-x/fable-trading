"""Attribute frozen confirmation outcomes without treating hindsight selection as a rule.

Owner 2026-10-19: measure selection, entry-price, exit-price, fee and risk-sizing
contributions separately. Independent 15m/1H anchors and their original matched
random draws are the unit; archived serial flags remain separate. The current
21,547-trade cohort supplies four-timeframe execution diagnostics. Confirmation
features use anchor next-open, OHLC through confirmation close, and known stop;
actual next-open price is separately named. No fitted filter or production change.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_replay import _smma
from yoyo.evaluation.spike_v126_htf_recheck import complete_bars
from yoyo.evaluation.spike_v131_excursions import MANIFEST, PARENT, EXTRA, provenance, trace
from yoyo.evaluation.spike_v6_wvf_study import ExecutionSpec
from yoyo.evaluation.spike_v7_fast import _initial_position_fast

EXP = Path('experiments/active/exp-spike-v131-confirmation-cost-20261019-v1')
CONFIG = EXP/'config.json'
PATHS = Path('experiments/active/exp-spike-v131-staged-entry-search-20261019-v1/results_v2/leg_paths.csv.gz')
EXCURSIONS = Path('experiments/active/exp-spike-v131-excursions-20261019-v1/results_v1/trades.csv.gz')
FIELDS = ['trade_key','arm','policy','symbol','timeframe_min','side','anchor_i','anchor_close',
          'status','confirmation_status','decision_i','entry_i','exit_i','entry_time','exit_time',
          'entry_price','initial_stop','initial_risk','exit_price','exit_reason','net_r','tick',
          'signal_i','confirmation_i','breakout_i','retest_i','chase_anchor_r','risk_ratio','delay_bars']
COMPONENTS = ['selection_effect_r','entry_price_effect_r','exit_price_effect_r','fee_change_r','risk_sizing_effect_r']


def dump(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False))


def read(path, columns=None):
    try:
        return pd.read_csv(path, usecols=None if columns is None else lambda c:c in columns, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=columns or [])


def known_status(status):
    return status == 'closed' or status == 'expired' or str(status).startswith('cancel_') or status in ('risk_invalid','delayed_risk_invalid')


def attribution(side, anchor_entry, stop, parent_exit, delayed_entry=None, delayed_exit=None, cost=.002):
    """Exact money bridge in anchor-risk units, then equal-risk delayed position sizing."""
    risk = side*(anchor_entry-stop)
    if risk <= 0: raise ValueError('nonpositive anchor risk')
    base = (side*(parent_exit-anchor_entry)-cost*anchor_entry)/risk
    out = dict(parent_r=base, parent_gross_r=side*(parent_exit-anchor_entry)/risk,
               parent_fee_r=cost*anchor_entry/risk, confirmed=delayed_entry is not None,
               selection_effect_r=0., entry_price_effect_r=0., exit_price_effect_r=0.,
               fee_change_r=0., risk_sizing_effect_r=0., delayed_r=0., delayed_anchor_r=0.,
               delayed_fee_r=0., delayed_gross_r=0., selected_parent_r=0., chase_fill_r=None, risk_ratio=None)
    if delayed_entry is None:
        out['selection_effect_r']=-base
    else:
        delayed_risk=side*(delayed_entry-stop)
        if delayed_risk<=0:raise ValueError('nonpositive delayed risk')
        same_units=(side*(delayed_exit-delayed_entry)-cost*delayed_entry)/risk
        final=same_units*risk/delayed_risk
        out.update(selected_parent_r=base, delayed_r=final, delayed_anchor_r=same_units,
                   delayed_fee_r=cost*delayed_entry/delayed_risk,
                   delayed_gross_r=side*(delayed_exit-delayed_entry)/delayed_risk,
                   entry_price_effect_r=-side*(delayed_entry-anchor_entry)/risk,
                   exit_price_effect_r=side*(delayed_exit-parent_exit)/risk,
                   fee_change_r=-cost*(delayed_entry-anchor_entry)/risk,
                   risk_sizing_effect_r=final-same_units,
                   chase_fill_r=side*(delayed_entry-anchor_entry)/risk,risk_ratio=delayed_risk/risk)
    if not math.isclose(out['delayed_r']-base,sum(out[k] for k in COMPONENTS),abs_tol=1e-9):
        raise ValueError('attribution identity failed')
    return out


def feature_values(side, anchor_entry, stop, confirmation_close, actual_entry, delay):
    """Only confirmation-close features are decision-known; fill metrics are execution facts."""
    ra=side*(anchor_entry-stop); rd=side*(confirmation_close-stop); rf=side*(actual_entry-stop)
    if min(ra,rf)<=0:raise ValueError('invalid feature risk')
    return dict(chase_decision_r=side*(confirmation_close-anchor_entry)/ra,
                chase_fill_r=side*(actual_entry-anchor_entry)/ra,
                risk_ratio=rf/ra,delay_bars=int(delay),fee_fill_r=.002*actual_entry/rf,
                fee_decision_r=.002*confirmation_close/rd if rd>0 else None,
                confirmation_close=confirmation_close,anchor_entry=anchor_entry,anchor_risk=ra)


def temporal_period(anchor, mature, split):
    if anchor>=split:return 'later'
    return 'early' if mature<split else 'crossing'


def worker(args):
    source, targets, current_paths, root, cfg = args
    symbol=source['symbol']; folder=Path(root)/'streams'/symbol
    folder.mkdir(parents=True,exist_ok=False)
    before=provenance(source['path']); inputs=[before]
    raw=pd.read_csv(source['path'],usecols=['ts','open','high','low','close','volume'])
    assert len(raw)==source['rows'] and raw.ts.is_monotonic_increasing and not raw.ts.duplicated().any()
    assert int(raw.ts.iloc[0])==source['first_ms'] and int(raw.ts.iloc[-1])==source['last_ms']
    base=raw.drop(columns='ts').set_axis(pd.to_datetime(raw.ts.to_numpy(),unit='ms',utc=True))
    base=base[(base.index>=pd.Timestamp('2022-11-01',tz='UTC')) &
              (base.index+pd.Timedelta(minutes=5)<=pd.Timestamp(cfg['end']))]
    events=[]; diagnostics=[]; audits=[]
    for minutes in cfg['diagnostic_timeframes']:
        target=targets[targets.timeframe_min.eq(minutes)]
        paths=current_paths[current_paths.timeframe_min.eq(minutes)]
        oldfolder=PARENT/f'{symbol}_{minutes}m' if minutes in (15,60) else EXTRA/'streams'/f'{symbol}_{minutes}m'
        cp=oldfolder/('candidate_outcomes.csv.gz' if minutes in (15,60) else 'candidates.csv.gz')
        inputs.append(provenance(cp)); candidates=read(cp,FIELDS)
        if len(candidates):
            candidates=candidates[candidates.arm.eq('v9_both') & candidates.policy.isin(['baseline','retest'])]
        if candidates.empty and target.empty:continue
        bars,_=complete_bars(base,minutes)
        prev=bars.close.shift(); tr=pd.concat([bars.high-bars.low,(bars.high-prev).abs(),(bars.low-prev).abs()],axis=1).max(axis=1)
        bars['atr']=_smma(tr,14)
        a={k:bars[k].to_numpy(float) for k in ['open','high','low','close','atr']}
        gap=bars.index.to_series().diff().ne(pd.Timedelta(minutes=minutes)).to_numpy();gap[0]=False
        clock=bars.index+pd.Timedelta(minutes=minutes)
        controls=read(oldfolder/'controls.csv.gz');inputs.append(provenance(oldfolder/'controls.csv.gz'))
        ret=candidates[candidates.policy.eq('retest')].set_index('trade_key')
        tick=float(paths.tick.iloc[0]) if len(paths) else (float(candidates.tick.dropna().iloc[0]) if 'tick' in candidates and candidates.tick.notna().any() else None)
        if tick is None:continue
        assert not ret.index.duplicated().any()
        replay_cache={}
        def replay(r):
            e,x=int(r['entry_i']),int(r['exit_i']);side=int(r['side']);entry=float(r['entry_price'])
            stop=round(float(r['initial_stop'])/tick)*tick
            cachekey=(e,x,side,entry,stop,str(r['exit_reason']))
            if cachekey in replay_cache:return replay_cache[cachekey]
            assert 0<=e<=x<len(bars) and not gap[e:x+1].any()
            assert abs(entry-a['open'][e])<=max(tick*1e-6,entry*1e-10)
            risk=side*(entry-stop)
            old_r=(side*(float(r['exit_price'])-entry)-.002*entry)/risk
            assert math.isclose(old_r,float(r['net_r']),abs_tol=1e-8)
            if minutes==60:
                z=trace(side=side,entry=entry,stop0=stop,tick=tick,e=e,x=x,
                    opposite=str(r['exit_reason']).startswith('opposite'),trail=2,
                    **{u:a[v] for u,v in [('o','open'),('h','high'),('l','low'),('c','close'),('atr','atr')]})
                x,px=z['actual_exit_i'],z['actual_exit_price']
            else:px=float(r['exit_price'])
            z=dict(entry=entry,stop=stop,exit=px,e=e,x=x,risk=risk,
                   net_r=(side*(px-entry)-.002*entry)/risk)
            replay_cache[cachekey]=z
            return z
        def control_result(c,policy):
            anchor=int(c['control_anchor_i']);side=int(c['side'])
            initial=_initial_position_fast(bars.index,a['open'],a['high'],a['low'],a['close'],a['atr'],gap,anchor,side,ExecutionSpec(tick=tick))
            assert initial is not None
            e=anchor+1 if policy=='baseline' else int(c['control_confirmation_i'])+1
            entry=a['open'][e];stop=float(initial['initial_stop']);risk=side*(entry-stop)
            px=entry+side*(float(c['control_net_r'])*risk+.002*entry)
            return replay(dict(entry_i=e,exit_i=bars.index.get_loc(pd.Timestamp(c['control_exit_time'])),side=side,
                entry_price=entry,initial_stop=stop,exit_price=px,exit_reason=c['control_exit_reason'],net_r=c['control_net_r']))
        if minutes in (15,60):
            serial_path=oldfolder/'serial_trades.csv.gz';inputs.append(provenance(serial_path))
            serial=read(serial_path,['trade_key','arm','policy','status'])
            serial=serial[serial.arm.eq('v9_both')]
            serialkeys={p:set(serial[serial.policy.eq(p)].trade_key) for p in ['baseline','retest']}
            controls=controls[controls.arm.eq('v9_both') & controls.control_pool.isin(['baseline','retest'])]
            bycontrol={(r['trade_key'],r['control_pool']):r for r in controls.to_dict('records')}
            excluded={};used=0
            for b in candidates[candidates.policy.eq('baseline')].to_dict('records'):
                key=b['trade_key'];r=ret.loc[key].to_dict();anchor=int(b['anchor_i'])
                if clock[anchor]<pd.Timestamp(cfg['analysis_start']):continue
                reason=None if b['status']=='closed' and known_status(r['status']) else str(b['status'])+'|'+str(r['status'])
                if reason:
                    excluded[reason]=excluded.get(reason,0)+1;continue
                bp=replay(b);rp=replay(r) if r['status']=='closed' else None
                decisions=[bp['x'],int(r['decision_i'])]+([] if rp is None else [rp['x']])
                z=dict(trade_key=key,cohort='signal',symbol=symbol,timeframe_min=minutes,side=int(b['side']),
                    anchor_time=str(clock[anchor]),mature_time=str(clock[max(decisions)]),
                    parent_entry=bp['entry'],parent_exit=bp['exit'],stop=bp['stop'],
                    delayed_entry=None if rp is None else rp['entry'],delayed_exit=None if rp is None else rp['exit'],
                    parent_exit_before_delayed_entry=bool(rp and bp['x']<rp['e']),
                    retest_status=r['status'],parent_serial=key in serialkeys['baseline'],delayed_serial=key in serialkeys['retest'])
                z.update(attribution(int(b['side']),bp['entry'],bp['stop'],bp['exit'],
                    None if rp is None else rp['entry'],None if rp is None else rp['exit']))
                events.append(z);used+=1
                bc=bycontrol.get((key,'baseline'));rc=bycontrol.get((key,'retest'))
                if not bc or not rc or bc['control_status']!='closed' or not bool(bc['matched']) or not known_status(rc['control_status']):continue
                cb=control_result(bc,'baseline');cr=control_result(rc,'retest') if rc['control_status']=='closed' else None
                ca=int(bc['control_anchor_i']);ct=pd.Timestamp(rc['control_decision_time'])
                mature=max(clock[cb['x']],ct,clock[cr['x']] if cr else ct)
                cz=dict(trade_key=key,cohort='control',symbol=symbol,timeframe_min=minutes,side=int(b['side']),
                    anchor_time=str(clock[ca]),mature_time=str(mature),parent_entry=cb['entry'],parent_exit=cb['exit'],stop=cb['stop'],
                    delayed_entry=None if cr is None else cr['entry'],delayed_exit=None if cr is None else cr['exit'],
                    parent_exit_before_delayed_entry=bool(cr and cb['x']<cr['e']),retest_status=rc['control_status'],
                    parent_serial=False,delayed_serial=False)
                cz.update(attribution(int(b['side']),cb['entry'],cb['stop'],cb['exit'],None if cr is None else cr['entry'],None if cr is None else cr['exit']))
                events.append(cz)
            audits.append(dict(timeframe_min=minutes,eligible=used,excluded=excluded))
        # Original filled V13.1 cohort: no new candidate admission or selection.
        for p in paths.to_dict('records'):
            key=p['trade_key'];side=int(p['side']);e=bars.index.get_loc(pd.Timestamp(p['entry_time']));confirm=e-1
            if p['cohort']=='signal':
                rr=ret.loc[key];anchor=int(rr['anchor_i'])
                if minutes in (15,60):
                    rec=replay(rr.to_dict())
                    assert math.isclose(rec['net_r'],p['net_r'],abs_tol=1e-8)
                    assert bars.index[rec['x']]==pd.Timestamp(p['exit_time'])
                if 'delay_bars' in rr and pd.notna(rr['delay_bars']):assert confirm-anchor==int(rr['delay_bars'])
            else:
                cc=controls[(controls.trade_key.eq(key)) & (controls.control_pool.eq('retest'))].iloc[0] if minutes in (15,60) else controls[controls.trade_key.eq(key)].iloc[0]
                anchor=int(cc['control_anchor_i'] if minutes in (15,60) else cc['control_sig'])
            assert e>anchor+1 and clock[confirm]==bars.index[e]
            vals=feature_values(side,a['open'][anchor+1],p['initial_stop'],a['close'][confirm],p['entry_price'],confirm-anchor)
            z={k:p[k] for k in ['trade_key','cohort','symbol','timeframe_min','side','entry_time','exit_time','entry_price','initial_stop','net_r']}
            z.update(vals,anchor_time=str(clock[anchor]),gross_r=p['net_r']+vals['fee_fill_r'])
            diagnostics.append(z)
    for name,rows in [('events',events),('diagnostics',diagnostics)]:
        pd.DataFrame(rows).to_csv(folder/(name+'.csv.gz'),index=False)
    assert before==provenance(source['path'])
    receipt=dict(symbol=symbol,inputs=inputs,audits=audits,event_rows=len(events),diagnostic_rows=len(diagnostics))
    dump(folder/'receipt.json',receipt)
    return receipt


def stats(x):
    x=np.asarray(x,float); pos=float(x[x>0].sum());neg=-float(x[x<0].sum())
    return dict(n=len(x),sum=float(x.sum()),mean=float(x.mean()) if len(x) else None,
                win_rate=float((x>0).mean()) if len(x) else None,pf=pos/neg if neg else None)


def interval(values,weeks,cfg):
    f=pd.DataFrame(dict(v=np.asarray(values),w=np.asarray(weeks)))
    g=f.groupby('w').v.agg(['sum','size'])
    if len(g)<2:return None
    rng=np.random.default_rng(cfg['seed'])
    k=rng.integers(0,len(g),(cfg['bootstrap'],len(g)))
    out=g['sum'].to_numpy()[k].sum(axis=1)/g['size'].to_numpy()[k].sum(axis=1)
    return [float(x) for x in np.quantile(out,[.025,.975])]


def paired(frame,control,cfg,columns):
    m=frame.merge(control,on='trade_key',suffixes=('','_control'),validate='one_to_one')
    weeks=pd.to_datetime(m.anchor_time,utc=True).dt.strftime('%G-W%V')
    out=dict(n=len(m),coverage=len(m)/len(frame) if len(frame) else None)
    for k in columns:
        delta=m[k]-m[k+'_control']
        out[k]=dict(target=stats(m[k]),random=stats(m[k+'_control']),excess=stats(delta),
                    weekly_excess_95=interval(delta,weeks,cfg))
    return out


def summarize(events,diagnostics,cfg):
    split=pd.Timestamp(cfg['split'])
    for f in [events,diagnostics]:
        for col in ['anchor_time','mature_time','entry_time','exit_time']:
            if col in f:f[col]=pd.to_datetime(f[col],utc=True)
    events['period']=[temporal_period(a,m,split) for a,m in zip(events.anchor_time,events.mature_time)]
    diagnostics['period']=np.where(diagnostics.entry_time>=split,'later',np.where(diagnostics.exit_time<split,'early','crossing'))
    summaries=[];bins=[];coverage=[]
    for minutes in [15,60]:
        for side in [0,1,-1]:
            both=events[events.timeframe_min.eq(minutes) & (True if side==0 else events.side.eq(side))]
            for period in ['all','early','later']:
                f=both if period=='all' else both[both.period.eq(period)]
                t=f[f.cohort.eq('signal')];c=f[f.cohort.eq('control')]
                take=t[t.confirmed];skip=t[~t.confirmed]
                row=dict(timeframe_min=minutes,side=side,period=period,anchors=len(t),confirmed=len(take),
                    parent=stats(t.parent_r),delayed=stats(t.delayed_r),selected_parent=stats(take.parent_r),
                    selected_delayed=stats(take.delayed_r),skipped_parent=stats(skip.parent_r),
                    avoided_negative_r=-float(skip.loc[skip.parent_r<0,'parent_r'].sum()),
                    missed_positive_r=float(skip.loc[skip.parent_r>0,'parent_r'].sum()),
                    components={k:stats(t[k]) for k in COMPONENTS},
                    parent_exit_before_delayed_entry=int(t.parent_exit_before_delayed_entry.sum()),
                    archived_parent_serial=stats(t.loc[t.parent_serial,'parent_r']),
                    archived_delayed_serial=stats(t.loc[t.delayed_serial,'delayed_r']),
                    matched=paired(t,c,cfg,['parent_r','delayed_r']))
                row['delta_weekly_95']=interval(t.delayed_r-t.parent_r,t.anchor_time.dt.strftime('%G-W%V'),cfg)
                row['archived_serial_delta']=row['archived_delayed_serial']['sum']-row['archived_parent_serial']['sum']
                row['occupancy_delta']=row['archived_serial_delta']-(row['delayed']['sum']-row['parent']['sum'])
                summaries.append(row)
    for minutes in cfg['diagnostic_timeframes']:
        for side in [0,1,-1]:
            f=diagnostics[diagnostics.timeframe_min.eq(minutes) & (True if side==0 else diagnostics.side.eq(side))]
            for period in ['all','early','later']:
                z=f if period=='all' else f[f.period.eq(period)]
                t=z[z.cohort.eq('signal')];c=z[z.cohort.eq('control')]
                coverage.append(dict(timeframe_min=minutes,side=side,period=period,n=len(t),
                    net=stats(t.net_r),gross=stats(t.gross_r),fees=stats(t.fee_fill_r),
                    chase_quantiles={str(q):float(t.chase_fill_r.quantile(q)) for q in [.1,.25,.5,.75,.9]},
                    delay_quantiles={str(q):float(t.delay_bars.quantile(q)) for q in [.25,.5,.75]},
                    gross_positive_net_negative=int(((t.gross_r>0)&(t.net_r<=0)).sum()),
                    matched=paired(t,c,cfg,['net_r'])))
                for feature,edges in [('chase_decision_r',cfg['chase_edges']),('delay_bars',cfg['delay_edges']),('fee_decision_r',cfg['fee_edges'])]:
                    cuts=[-np.inf,*edges,np.inf]; labels=pd.cut(t[feature],cuts,include_lowest=True)
                    for label,g in t.groupby(labels,observed=True):
                        if g.empty:continue
                        bins.append(dict(timeframe_min=minutes,side=side,period=period,feature=feature,bin=str(label),
                            net=stats(g.net_r),gross=stats(g.gross_r),fees=stats(g.fee_fill_r),
                            matched=paired(g,c,cfg,['net_r'])))
    return dict(attribution=summaries,current_trade_diagnostics=coverage,feature_bins=bins,
                status_counts=events.groupby(['cohort','timeframe_min','retest_status']).size().reset_index(name='n').to_dict('records'))


def run(output,workers,symbols,aggregate_only=False):
    cfg=json.loads(CONFIG.read_text());output=Path(output)
    declared=[Path(__file__),CONFIG,EXP/'PROJECT_PLAN.md',Path('tests/evaluation/test_spike_v131_confirmation_cost.py')]
    for p in declared:subprocess.run(['git','ls-files','--error-unmatch',str(p)],check=True,stdout=subprocess.DEVNULL)
    subprocess.run(['git','diff','--exit-code','HEAD','--',*map(str,declared)],check=True)
    manifest=json.loads(MANIFEST.read_text());assert manifest['complete'] and not manifest['failed']
    sources=manifest['streams'];assert len(sources)==638
    if symbols:sources=[s for s in sources if s['symbol'] in symbols];assert len(sources)==len(symbols)
    if not aggregate_only:
        output.mkdir(parents=True,exist_ok=False)
        paths=read(PATHS);targets=read(EXCURSIONS)
        assert len(targets)==21547 and len(paths)==24678
        assert not targets.trade_key.duplicated().any()
        dump(output/'identity.json',dict(config=cfg,subset=bool(symbols),symbols=[s['symbol'] for s in sources],
            builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
            sources=[provenance(p) for p in [MANIFEST,PATHS,EXCURSIONS,*declared]]))
        receipts=[];errors=[]
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures={pool.submit(worker,(s,targets[targets.symbol.eq(s['symbol'])],paths[paths.symbol.eq(s['symbol'])],str(output),cfg)):s['symbol'] for s in sources}
            for i,f in enumerate(as_completed(futures),1):
                try:receipts.append(f.result())
                except Exception as e:errors.append(dict(symbol=futures[f],error=repr(e)))
                if i==1 or i%20==0 or i==len(futures):print(json.dumps(dict(done=i,total=len(futures),errors=len(errors))),flush=True)
        dump(output/'receipt.json',dict(complete=not errors,errors=errors,receipts=receipts))
        if errors:raise RuntimeError(str(errors[:5]))
    events=pd.concat([read(output/'streams'/s['symbol']/'events.csv.gz') for s in sources],ignore_index=True)
    diag=pd.concat([read(output/'streams'/s['symbol']/'diagnostics.csv.gz') for s in sources],ignore_index=True)
    assert not events[['trade_key','cohort']].duplicated().any() and not diag[['trade_key','cohort']].duplicated().any()
    events.to_csv(output/'events.csv.gz',index=False);diag.to_csv(output/'diagnostics.csv.gz',index=False)
    result=summarize(events,diag,cfg)
    result.update(config=cfg,subset=bool(symbols),generated_at=datetime.now(timezone.utc).isoformat(),
        limitations=['Selected-parent immediate outcomes are hindsight attribution, not executable entries.',
        'Independent anchors overlap; archived serial admission is held fixed, including 1H earlier-exit occupancy.',
        'Original matched draws, no redraw; zero-fill opportunity comparisons differ from both-filled-only pairs.',
        'Diagnostics retain the original filled cohort; target-bin controls are not additionally matched on the new feature.',
        'Fixed feature bins and chronological splits reuse historical data; no prospective or optimized-gate claim.',
        'Archived universe/ticks are not point-in-time; no funding, order-book, margin or account simulation.'])
    dump(output/'result.json',result)
    print(json.dumps(dict(events=len(events),diagnostics=len(diag),output=str(output))),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=EXP/'results_v1');p.add_argument('--workers',type=int,default=4)
    p.add_argument('--symbols',nargs='+');p.add_argument('--aggregate-only',action='store_true');a=p.parse_args()
    run(a.output,a.workers,a.symbols,a.aggregate_only)
