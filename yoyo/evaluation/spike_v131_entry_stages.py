"""Compare four causal entry stages with fresh serial occupancy and matched draws.

Owner 2026-10-19 authorized entry-stage ablation. Decisions use original anchor
high/low, original five-bar stop and only OHLC/raw opposite flags through each
stage's closed bar, within 24 bars. Entry uses the following open. Existing
signal facts, absolute stops, actual-entry R and frozen timeframe exits remain.
Random controls retain the original ID-seeded stratum draw, including failures;
all policies share it. No future-confirmation conditioning or content hashing.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v128_recent as parent
from yoyo.evaluation.spike_v128_retest_state import confirmation
from yoyo.evaluation.spike_v131_confirmation_cost import dump, interval, read, stats
from yoyo.evaluation.spike_v131_excursions import PARENT, EXTRA, provenance
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python

EXP = Path('experiments/active/exp-spike-v131-entry-stages-20261019-v1')
CONFIG = EXP/'config.json'
POLICIES = ('immediate', 'breakout', 'pullback', 'rebreak')


def decision(o, h, l, c, gap, raw_side, anchor, side, stop, policy, limit=24):
    """Stop reading at the chosen closed stage; later failure cannot revoke it.

    Inputs: anchor high/low; subsequent OHLC, gap and raw-side columns through
    returned decision_i only. The first-leg extreme excludes the retest bar.
    No same-bar breakout/retest/rebreak transition is allowed.
    """
    if policy not in POLICIES or side not in (-1, 1):
        raise ValueError('invalid entry stage or side')
    if policy == 'immediate':
        return 'confirmed', anchor
    level = h[anchor] if side == 1 else l[anchor]
    stage, extreme = 0, None
    end = min(len(c)-1, anchor+limit)
    for j in range(anchor+1, end+1):
        if gap[j]: return 'cancel_gap', j
        if raw_side[j] == -side: return 'cancel_raw_opposite', j
        if (l[j] <= stop if side == 1 else h[j] >= stop): return 'cancel_stop_touch', j
        if stage and side*(c[j]-level) <= 0: return 'cancel_lost_level', j
        if stage == 0:
            if side*(c[j]-level) > 0:
                if policy == 'breakout': return 'confirmed', j
                stage, extreme = 1, h[j] if side == 1 else l[j]
        elif stage == 1:
            if (l[j] <= level if side == 1 else h[j] >= level):
                if policy == 'pullback': return 'confirmed', j
                stage = 2
            else:
                extreme = max(extreme, h[j]) if side == 1 else min(extreme, l[j])
        elif side*(c[j]-extreme) > 0:
            return 'confirmed', j
    return ('expired' if anchor+limit < len(c) else 'pending_boundary'), end


def attempt(prepared, anchor, side, policy):
    """Use the published exit engine and original stop, repriced at actual entry."""
    p = prepared; n = len(p.frame)
    out = dict(policy=policy, anchor_i=anchor, side=side, decision_i=anchor,
               status='no_next_bar', filled=False, net_r=None)
    if anchor+1 >= n: return out
    if p.gap[anchor+1]:
        return out | dict(status='next_bar_is_gap', decision_i=anchor+1)
    initial = parent._initial_position_fast(p.frame.index, p.open, p.high, p.low,
        p.close, p.atr, p.gap, anchor, side, p.spec)
    if initial is None: return out | dict(status='risk_invalid')
    stop = float(initial['initial_stop']); anchor_price = float(initial['entry_price'])
    status, d = decision(p.open, p.high, p.low, p.close, p.gap, p.raw_side,
                         anchor, side, stop, policy)
    out.update(status=status, decision_i=d, initial_stop=stop, anchor_entry=anchor_price,
               anchor_risk=float(initial['initial_risk']))
    if status != 'confirmed': return out
    if d+1 >= n: return out | dict(status='no_next_bar')
    if p.gap[d+1]: return out | dict(status='next_bar_is_gap', decision_i=d+1)
    entry = float(p.open[d+1]); risk = side*(entry-stop)
    if not math.isfinite(entry) or entry <= 0 or risk <= 0:
        return out | dict(status='delayed_risk_invalid')
    initial.update(signal_i=d, signal_bar_open=p.frame.index[d], entry_i=d+1,
        entry_time=p.frame.index[d+1], entry_price=entry, initial_risk=risk, initial_risk_frac=risk/entry)
    r = parent.fixed.replay_fixed_entry(p.context, pd.Series(initial), arm='v8', enable_be=False, prepared=p)
    status = 'closed' if not r['censored'] else ('censored_boundary' if r['exit_reason']=='boundary_mark' else 'censored_gap')
    out.update({k:r[k] for k in ['entry_i','exit_i','entry_price','initial_risk','exit_price','exit_reason','net_r','gross_return','net_return']})
    out.update(status=status, filled=True, gross_r=r['gross_return']*entry/risk,
        cost_r=.002*entry/risk, delay_bars=d-anchor, chase_r=side*(entry-anchor_price)/out['anchor_risk'])
    return out


def known(status):
    return status == 'closed' or status == 'expired' or status.startswith('cancel_') or status in ('risk_invalid','delayed_risk_invalid','next_bar_is_gap')


def mark_serial(rows, field, frame_length):
    """Warm occupancy for one policy/stream; a close on the exit bar may re-enter."""
    flat = -1
    for r in sorted(rows, key=lambda z:(z['decision_i'], z['anchor_i'], z['trade_key'])):
        r[field] = False
        if not r['filled'] or r['decision_i'] < flat: continue
        r[field] = True
        flat = frame_length+1 if r['status']=='censored_boundary' else int(r['exit_i'])


def draws(prepared, anchors, cfg):
    """Original random-anchor algorithm, without running or selecting outcomes."""
    p = prepared; clock = p.frame.index+pd.Timedelta(minutes=p.context.minutes)
    weeks = np.asarray(clock.strftime('%G-W%V'))
    fold = np.asarray(clock < pd.Timestamp(cfg['split']))
    bins = np.searchsorted(np.asarray(cfg['vol_bins']), p.atr/p.close, side='left')
    valid = (p.frame.ready.fillna(False).to_numpy(bool) & np.isfinite(p.atr) & (p.atr>0)
             & np.isfinite(p.close) & (p.close>0) & ~p.gap
             & (clock>=pd.Timestamp(cfg['start'])) & (clock<pd.Timestamp(cfg['end'])))
    pools = {}; result = {}
    for a in anchors:
        i = a['anchor_i']; stratum = (weeks[i], bool(fold[i]), int(bins[i]))
        if stratum not in pools:
            pools[stratum] = np.flatnonzero(valid & (weeks==stratum[0]) & (fold==stratum[1]) & (bins==stratum[2]))
        choices = pools[stratum][pools[stratum] != i]
        seed = int(hashlib.sha256(f"{cfg['control_seed']}|{a['trade_key']}".encode()).hexdigest(),16)
        result[a['trade_key']] = None if not len(choices) else int(choices[seed % len(choices)])
    return result


def worker(args):
    source, meta, cfg, root = args
    began = time.perf_counter(); symbol = source['symbol']; tick=float(meta['tick'])
    folder = Path(root)/'streams'/symbol; folder.mkdir(parents=True, exist_ok=False)
    before = provenance(source['path']); inputs=[before]
    raw = pd.read_csv(source['path'], usecols=['ts','open','high','low','close','volume'])
    assert len(raw)==source['rows'] and raw.ts.is_monotonic_increasing and not raw.ts.duplicated().any()
    assert int(raw.ts.iloc[0])==source['first_ms'] and int(raw.ts.iloc[-1])==source['last_ms']
    assert np.isfinite(raw.to_numpy(float)).all()
    assert ((raw.low>0) & (raw.low<=raw[['open','close']].min(axis=1)) &
            (raw.high>=raw[['open','close']].max(axis=1))).all()
    base=raw.drop(columns='ts').set_axis(pd.to_datetime(raw.ts.to_numpy(),unit='ms',utc=True))
    base=base[(base.index>=pd.Timestamp(cfg['warmup_start'])) & (base.index+pd.Timedelta(minutes=5)<=pd.Timestamp(cfg['end']))]
    receipts=[]
    for minutes in cfg['timeframes']:
        bars, partial = parent.complete_bars(base, minutes)
        facts = parent.facts_for(bars,base,meta['asset'],tick,minutes) if minutes in (15,60) else parent._generic_facts(bars,meta['asset'],tick,minutes)
        frame=facts['frame']; clock=frame.index+pd.Timedelta(minutes=minutes)
        identity=dict(symbol=symbol,timeframe_min=minutes,asset=meta['asset'],venue='binance_um')
        p=parent.source.prepared_arm(frame,facts['gap'],facts['side'],f'binance_um:{symbol}:{minutes}m',identity,minutes,tick)
        if minutes==60:p=replace(p,spec=replace(p.spec,trail_atr=2.))
        oldfolder=PARENT/f'{symbol}_{minutes}m' if minutes in (15,60) else EXTRA/'streams'/f'{symbol}_{minutes}m'
        archive=oldfolder/('candidate_outcomes.csv.gz' if minutes in (15,60) else 'candidates.csv.gz')
        old=read(archive); inputs.append(provenance(archive))
        old=old[old.arm.eq('v9_both') & old.policy.eq('retest')] if len(old) else old
        anchors=[dict(anchor_i=int(i),side=int(facts['side'][i]),trade_key=f'binance_um:{symbol}:{minutes}m:v9_both:{clock[i].isoformat()}')
                 for i in np.flatnonzero(facts['v9']) if clock[i]<pd.Timestamp(cfg['end'])]
        assert {a['trade_key'] for a in anchors} == set(old.trade_key), (symbol,minutes,'anchor lineage')
        chosen=draws(p,anchors,cfg); oldc=read(oldfolder/'controls.csv.gz'); inputs.append(provenance(oldfolder/'controls.csv.gz'))
        if len(oldc) and 'control_pool' in oldc:
            oldc=oldc[oldc.control_pool.eq('baseline') & oldc.arm.eq('v9_both')]
        draw_checks=0
        for c in oldc.to_dict('records'):
            prior=c['control_sig']; now=chosen[c['trade_key']]
            assert (pd.isna(prior) and now is None) or int(prior)==now, (symbol,minutes,'draw lineage')
            draw_checks+=1
        rows=[]; cache={}; oldmap=old.set_index('trade_key'); checks=0; prefix_checks=0
        for a in anchors:
            key=a['trade_key']; ci=chosen[key]; eligible=ci is not None and clock[ci].dayofweek!=6
            for cohort,i in [('signal',a['anchor_i']),('control',ci)]:
                if i is None: continue
                for policy in POLICIES:
                    ck=(i,a['side'],policy)
                    if ck not in cache: cache[ck]=attempt(p,i,a['side'],policy)
                    r=dict(cache[ck]); d=r['decision_i']; x=int(r.get('exit_i',d))
                    r.update(trade_key=key,cohort=cohort,symbol=symbol,timeframe_min=minutes,
                        pair_eligible=eligible,serial_all=False,serial_pair=False,
                        anchor_time=str(clock[i]),decision_time=str(clock[d]),mature_time=str(clock[x if r['filled'] else d]),
                        entry_time=str(frame.index[int(r['entry_i'])]) if r['filled'] else None,
                        exit_time=str(frame.index[x]) if r['filled'] else None)
                    rows.append(r)
                    if cohort=='signal' and policy=='rebreak':
                        oldr=oldmap.loc[key]
                        assert r['status']==oldr['status'] or minutes==60, (symbol,minutes,key,'status')
                        if pd.notna(oldr.get('entry_i')):
                            for col in ('entry_i','entry_price','initial_stop','initial_risk'):
                                assert math.isclose(r[col],oldr[col],abs_tol=1e-8,rel_tol=1e-10), (symbol,minutes,key,col)
                                checks+=1
                            if minutes!=60 and oldr['status']=='closed':
                                assert r['exit_i']==oldr['exit_i'] and math.isclose(r['net_r'],oldr['net_r'],abs_tol=1e-8), (symbol,minutes,key,'exit')
                                checks+=2
            # One real anchor per stream: compare causal prefixes and old state machine.
            if prefix_checks==0 and cache[a['anchor_i'],a['side'],'immediate'].get('initial_stop'):
                ai=a['anchor_i']; st=cache[ai,a['side'],'immediate']['initial_stop']
                legacy=confirmation(p.open,p.high,p.low,p.close,p.gap,p.raw_side,ai,a['side'],st,24)
                ls='cancel_'+legacy['cancel_reason'] if legacy['status']=='canceled' else legacy['status']
                assert decision(p.open,p.high,p.low,p.close,p.gap,p.raw_side,ai,a['side'],st,'rebreak')==(ls,legacy['decision_i'])
                for policy in POLICIES[1:]:
                    ds,di=decision(p.open,p.high,p.low,p.close,p.gap,p.raw_side,ai,a['side'],st,policy)
                    sliced=[x[:di+1] for x in (p.open,p.high,p.low,p.close,p.gap,p.raw_side)]
                    assert decision(*sliced,ai,a['side'],st,policy)==(ds,di)
                    prefix_checks+=1
        for policy in POLICIES:
            mark_serial([r for r in rows if r['policy']==policy and r['cohort']=='signal'],'serial_all',len(frame))
            for cohort in ('signal','control'):
                mark_serial([r for r in rows if r['policy']==policy and r['cohort']==cohort and r['pair_eligible']],'serial_pair',len(frame))
        ledger=pd.DataFrame(rows)
        serial_checks=0
        if minutes!=60 and len(ledger):
            oldt=read(oldfolder/('serial_trades.csv.gz' if minutes==15 else 'trades.csv.gz'))
            if len(oldt):oldt=oldt[oldt.arm.eq('v9_both') & oldt.policy.eq('retest')]
            oldt=oldt[pd.to_datetime(oldt.entry_time,utc=True)>=pd.Timestamp(cfg['analysis_start'])]
            current=ledger[ledger.cohort.eq('signal') & ledger.policy.eq('rebreak') & ledger.serial_all &
                (pd.to_datetime(ledger.entry_time,utc=True)>=pd.Timestamp(cfg['analysis_start']))]
            assert set(current.trade_key)==set(oldt.trade_key), (symbol,minutes,'serial lineage')
            serial_checks=len(current)
        ledger.to_csv(folder/f'{minutes}m.csv.gz',index=False)
        receipts.append(dict(timeframe_min=minutes,bars=len(frame),partial=partial,anchors=len(anchors),rows=len(rows),
            rebreak_scalar_checks=checks,serial_identity_checks=serial_checks,draw_identity_checks=draw_checks,prefix_checks=prefix_checks))
    assert before==provenance(source['path'])
    receipt=dict(symbol=symbol,seconds=time.perf_counter()-began,inputs=inputs,streams=receipts)
    dump(folder/'receipt.json',receipt)
    return receipt


def paired_frame(f, serial):
    """Common four-policy matched outcomes, preserving zero fills and skipped trades."""
    z=f[f.pair_eligible].copy()
    z['known']=z.status.map(known)
    z['value']=z.net_r.where(z.filled,0.)
    if serial:
        skipped=z.filled & ~z.serial_pair
        z.loc[skipped,'known']=True
        z.loc[~z.serial_pair,'value']=0.
        z.loc[skipped,'mature_time']=z.loc[skipped,'decision_time']
    complete=z.groupby('trade_key').agg(n=('policy','size'),known=('known','all'),
        first=('anchor_time','min'),last=('mature_time','max'))
    complete=complete[(complete.n==8)&complete.known]
    z=z[z.trade_key.isin(complete.index)]
    values=z.pivot(index='trade_key',columns=['cohort','policy'],values='value')
    values.columns=['_'.join(c) for c in values.columns]
    values=values.join(complete[['first','last']])
    sides=z[z.cohort.eq('signal') & z.policy.eq('immediate')].set_index('trade_key')
    values=values.join(sides[['side','anchor_time']])
    return values


def trade_metrics(f):
    s=stats(f.net_r)
    if not len(f):return s
    realized=f.sort_values(['exit_time','trade_key']).net_r.cumsum().to_numpy()
    peaks=np.maximum.accumulate(np.r_[0.,realized])[1:]
    return s | dict(gross_r=float(f.gross_r.sum()),cost_r=float(f.cost_r.sum()),
        realized_r_drawdown=float((peaks-realized).max()),
        delay_median=float(f.delay_bars.median()),chase_mean=float(f.chase_r.mean()),
        large_wins_5r=int((f.net_r>=5).sum()),large_wins_r=float(f.loc[f.net_r>=5,'net_r'].sum()))


def aggregate(root,cfg,receipts):
    root=Path(root); main=[]; paired=[]; slices=[]; audit=[]; pair_audit=[]
    split=pd.Timestamp(cfg['split']); start=pd.Timestamp(cfg['analysis_start'])
    for minutes in cfg['timeframes']:
        frames=[read(root/'streams'/r['symbol']/f'{minutes}m.csv.gz') for r in receipts]
        f=pd.concat([x for x in frames if len(x)],ignore_index=True)
        for col in ('anchor_time','decision_time','mature_time','entry_time','exit_time'):
            f[col]=pd.to_datetime(f[col],utc=True)
        f.to_csv(root/f'events_{minutes}m.csv.gz',index=False)
        t=f[f.cohort.eq('signal') & f.serial_all & (f.entry_time>=start)].copy()
        t['period']=np.where(t.entry_time>=split,'later',np.where(t.mature_time<split,'early','crossing'))
        audit.append(dict(timeframe_min=minutes,rows=len(f),signal_anchors=int((f.cohort.eq('signal')&f.policy.eq('immediate')).sum()),
            statuses=t.groupby(['policy','status']).size().unstack(fill_value=0).to_dict(orient='index')))
        t=t[t.status.eq('closed')]
        for side in (0,1,-1):
            sf=t if side==0 else t[t.side.eq(side)]
            for period in ('all','early','later'):
                g=sf if period=='all' else sf[sf.period.eq(period)]
                for policy in POLICIES:
                    main.append(dict(timeframe_min=minutes,side=side,period=period,policy=policy)|trade_metrics(g[g.policy.eq(policy)]))
        for (policy,year),g in t.groupby(['policy',t.entry_time.dt.year]):
            slices.append(dict(timeframe_min=minutes,policy=policy,year=int(year))|trade_metrics(g))
        for serial in (False,True):
            pairs=paired_frame(f,serial)
            pairs=pairs[pairs['first']>=start]
            pairs['period']=np.where(pairs['first']>=split,'later',np.where(pairs['last']<split,'early','crossing'))
            original=f[f.cohort.eq('signal') & f.policy.eq('immediate') & (f.anchor_time>=start)]
            pair_audit.append(dict(timeframe_min=minutes,mode='paired_serial' if serial else 'independent',
                original_anchors=len(original),eligible_draws=int(original.pair_eligible.sum()),
                common_mature_pairs=len(pairs),common_coverage=len(pairs)/len(original) if len(original) else None,
                temporal_counts={str(k):int(v) for k,v in pairs.period.value_counts().items()}))
            pairs.to_csv(root/f'pairs_{minutes}m_{"serial" if serial else "independent"}.csv.gz')
            for side in (0,1,-1):
                sp=pairs if side==0 else pairs[pairs.side.eq(side)]
                for period in ('all','early','later'):
                    z=sp if period=='all' else sp[sp.period.eq(period)]
                    weeks=z.anchor_time.dt.strftime('%G-W%V')
                    for policy in POLICIES:
                        target=z['signal_'+policy];control=z['control_'+policy]
                        delta=target-z.signal_rebreak;excess=target-control
                        paired.append(dict(timeframe_min=minutes,side=side,period=period,policy=policy,
                            mode='paired_serial' if serial else 'independent',target=stats(target),random=stats(control),
                            excess=stats(excess),excess_week95=interval(excess,weeks,cfg),
                            versus_rebreak=stats(delta),delta_week95=interval(delta,weeks,cfg),
                            incremental_excess=stats(excess-(z.signal_rebreak-z.control_rebreak))))
    m=pd.DataFrame(main); allside=m[m.side.eq(0)]
    choices=[]
    for tf in [None,*cfg['timeframes']]:
        g=allside if tf is None else allside[allside.timeframe_min.eq(tf)]
        early=g[g.period.eq('early')].groupby('policy')['sum'].sum()
        winner=early.idxmax(); later=g[g.period.eq('later')].groupby('policy')['sum'].sum()
        full=g[g.period.eq('all')].groupby('policy')['sum'].sum()
        choices.append(dict(timeframe_min=tf,early_selected=winner,early_net_r=float(early[winner]),
            later_selected_r=float(later[winner]),later_rebreak_r=float(later['rebreak']),
            full_history_best=full.idxmax(),full_history_r={k:float(v) for k,v in full.items()}))
    result=dict(generated_at=datetime.now(timezone.utc).isoformat(),config=cfg,serial=main,paired=paired,
        yearly=slices,selection=choices,audit=audit,pair_audit=pair_audit,
        lineage={k:sum(s[k] for r in receipts for s in r['streams']) for k in
            ['rebreak_scalar_checks','serial_identity_checks','draw_identity_checks','prefix_checks']},
        definitions={'serial':'Actual per-symbol/timeframe both-side occupancy. Equal-risk trade R, not account performance.',
            'paired_serial':'Both queues restricted to ex-ante eligible original non-Sunday draws, then occupancy independently replayed.',
            'independent':'All eight mature outcomes for four stages and paired anchors; no-fill is zero, positions may overlap.',
            'drawdown':'Closed-trade accumulated R only, ignores intratrade marks/shared capital; not account max drawdown.',
            '1H':'Fresh 2ATR occupancy; historical fixed-entry 3357-trade cohort is not the new baseline.',
            'uncertainty':'Descriptive UTC-anchor-week bootstrap; multiple stage/timeframe comparisons are exploratory.'},
        limitations=['Archive universe/ticks are not point-in-time.','No funding, queue, slippage beyond fixed20bp, or shared capital.',
            'Historical data reused; no blind or forward validation.','Matched common-outcome coverage and censoring reported; no replacement draws.',
            'AUC/top-decile metrics do not apply: no prediction model or score.'],training_eligible=False,production_eligible=False)
    dump(root/'result.json',result)
    print(json.dumps(dict(output=str(root),selection=choices,lineage=result['lineage'])),flush=True)


def run(root,workers,symbols):
    cfg=json.loads(CONFIG.read_text()); assert cfg['policies']==list(POLICIES) and cfg['round_trip_cost']==.002
    paths=sorted(set([Path(__file__),CONFIG,*_local_transitive_python((Path(__file__),))]),key=str)
    for p in paths:subprocess.run(['git','ls-files','--error-unmatch',str(p)],check=True,stdout=subprocess.DEVNULL)
    subprocess.run(['git','diff','--exit-code','HEAD','--',*map(str,paths)],check=True)
    manifest=json.loads(Path(cfg['input_manifest']).read_text()); assert manifest['complete'] and not manifest['errors']
    sources=manifest['streams'];assert len(sources)==638
    if symbols:
        sources=[r for r in sources if r['symbol'] in symbols];assert len(sources)==len(symbols)
    root=Path(root);root.mkdir(parents=True,exist_ok=False)
    meta=parent.source.symbol_meta()
    dump(root/'identity.json',dict(config=cfg,subset=bool(symbols),symbols=[r['symbol'] for r in sources],
        builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        code=[provenance(p) for p in paths],manifest=provenance(cfg['input_manifest']),metadata=provenance(parent.source.EXCHANGE_INFO)))
    receipts=[];errors=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        fs={pool.submit(worker,(r,meta[r['symbol']],cfg,str(root))):r['symbol'] for r in sources}
        for n,f in enumerate(as_completed(fs),1):
            try:receipts.append(f.result())
            except Exception as exc:errors.append(dict(symbol=fs[f],error=repr(exc)))
            if n==1 or n%20==0 or n==len(fs):print(json.dumps(dict(done=n,total=len(fs),errors=errors[-3:])),flush=True)
    dump(root/'receipt.json',dict(complete=not errors,errors=errors,receipts=receipts))
    if errors:raise RuntimeError(str(errors[:5]))
    aggregate(root,cfg,receipts)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=EXP/'results_v1')
    parser.add_argument('--workers',type=int,default=4);parser.add_argument('--symbols',nargs='+')
    parser.add_argument('--aggregate',action='store_true')
    a=parser.parse_args()
    if a.aggregate:aggregate(a.output,json.loads(CONFIG.read_text()),json.loads((a.output/'receipt.json').read_text())['receipts'])
    else:run(a.output,a.workers,a.symbols)
