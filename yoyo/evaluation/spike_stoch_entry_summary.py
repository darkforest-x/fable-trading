"""Blocked, denominator-explicit Stoch/WVF and Spike pullback comparisons.

Queues are created before analysis-window filtering; each policy owns its
occupancy. Net opportunity outcomes retain nonfills/skips as zero. MAE on
common fills is explicitly conditional/descriptive, never a selection-free
causal estimate. Costs, timing, coverage and missed baseline winners accompany
headline returns. No fitted thresholds or post-result policy selection.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_stoch_entry import SPIKE_POLICIES, STOCH_POLICIES
from yoyo.evaluation.spike_stoch_entry_study import CONFIG
from yoyo.evaluation.spike_v131_rule_summary import read, epoch, effective, infer, four_week_blocks
from yoyo.evaluation.spike_v131_entry_stages import dump
from yoyo.evaluation.spike_v131_confirmation_cost import stats

CONTRASTS = {'standalone': [('stoch_wvf','stoch')],
             'spike': [('pullback','rebreak'),('pullback_stoch','pullback'),('pullback_wvf','pullback_stoch')]}


def metric(f):
    out=stats(f.net_r)
    if not len(f):return out
    z=f.sort_values(['exit_time','trade_key']);v=z.net_r.to_numpy();eq=v.cumsum()
    out.update(gross_r=float(f.gross_r.sum()),cost_r=float(f.cost_r.sum()),
        realized_r_drawdown=float((np.maximum.accumulate(np.r_[0.,eq])[1:]-eq).max()),
        large_wins_5r=int((v>=5).sum()),large_wins_r=float(v[v>=5].sum()),
        total_bar_minutes=float((f.held_bars*f.timeframe_min).sum()),
        ever_adverse_fraction=float((f.mae_r>1e-9).mean()),
        mae_at_least_half_r=float((f.mae_r>=.5-1e-9).mean()),
        mean_delay_bars=float(f.delay_bars.mean()),mean_chase_anchor_r=float(f.chase_r.mean()))
    for k in ('mae_r','mae_bp','mae_anchor_r','mfe_lower_r','mfe_upper_r','first_bar_mae_r','risk_fraction'):
        out[k]=dict(mean=float(f[k].mean()),median=float(f[k].median()),p90=float(f[k].quantile(.9)))
    return out


def pairs(f,policies,scope):
    gate='state_pair' if scope=='state' else 'pair_eligible'
    flag='serial_state' if scope=='state' else 'serial_pair'
    z=f[f[gate]].copy().reset_index(drop=True)
    if not len(z):return pd.DataFrame()
    value,known,mature=effective(z,z.eligible.to_numpy(bool),z[flag].to_numpy(bool))
    z['value']=value;z['known']=known;z['mature']=mature;z['first']=epoch(z.anchor_time)
    complete=z.groupby('trade_key').agg(n=('policy','size'),known=('known','all'),first=('first','min'),last=('mature','max'))
    complete=complete[(complete.n==2*len(policies)) & complete.known]
    z=z[z.trade_key.isin(complete.index)]
    out=z.pivot(index='trade_key',columns=['cohort','policy'],values='value')
    out.columns=['_'.join(x) for x in out.columns]
    out=out.join(complete[['first','last']])
    signal=z[z.cohort.eq('signal') & z.policy.eq(policies[0])].set_index('trade_key')
    return out.join(signal[['anchor_time','symbol','timeframe_min']])


def inference(values,weeks,cfg):
    x=pd.DataFrame(dict(value=np.asarray(values,float),week=np.asarray(weeks)))
    b=x.groupby('week').value.agg(['sum','size'])
    blocks={str(w):dict(value=float(r['sum']),n=int(r['size'])) for w,r in b.iterrows()}
    return dict(weekly=infer(blocks,'value',cfg),four_week=infer(four_week_blocks(blocks),'value',cfg))


def period_filter(frame,period):
    return frame if period=='all' else frame[frame.period.eq(period)]


def run(root,name='analysis_v1'):
    root=Path(root);out=root/name;out.mkdir(exist_ok=False)
    cfg=json.loads(CONFIG.read_text());receipt=json.loads((root/'receipt.json').read_text())
    assert receipt['complete'] and not receipt['errors']
    start=pd.Timestamp(cfg['analysis_start']);cut=pd.Timestamp(cfg['split'])
    startsec=int(start.timestamp());cutsec=int(cut.timestamp())
    trades=[];allpairs=[];coverage=[];missed=[];commond=[]
    for m in cfg['timeframes']:
        fs=[read(root/'streams'/r['symbol']/f'{m}m.csv.gz') for r in receipt['receipts']]
        f=pd.concat([x for x in fs if len(x)],ignore_index=True)
        for k in ('anchor_time','decision_time','mature_time','entry_time','exit_time'):f[k]=pd.to_datetime(f[k],utc=True)
        t=f[f.cohort.eq('signal') & f.serial_all & f.status.eq('closed') & (f.entry_time>=start)].copy()
        t['period']=np.where(t.entry_time>=cut,'later',np.where(t.mature_time<cut,'early','crossing'))
        trades.append(t)
        for group,policies in [('standalone',STOCH_POLICIES),('spike',SPIKE_POLICIES)]:
            g=f[f.group.eq(group)]
            for scope in (['primary','state'] if group=='standalone' else ['primary']):
                q=pairs(g,policies,scope)
                if not len(q):continue
                q=q[q['first']>=startsec].copy()
                q['period']=np.where(q['first']>=cutsec,'later',np.where(q['last']<cutsec,'early','crossing'))
                q['group']=group;q['scope']=scope
                q.to_csv(out/f'pairs_{m}_{group}_{scope}.csv.gz')
                allpairs.append(q)
                flag='serial_state' if scope=='state' else 'serial_pair'
                for period in ('all','early','later'):
                    ids=period_filter(q,period).index
                    for policy in policies:
                        for cohort in ('signal','control'):
                            rows=g[g.trade_key.isin(ids) & g.policy.eq(policy) & g.cohort.eq(cohort)]
                            used=rows[rows[flag] & rows.status.eq('closed')]
                            coverage.append(dict(timeframe_min=m,group=group,scope=scope,period=period,policy=policy,cohort=cohort,
                                matched_opportunities=len(ids),eligible=int(rows.eligible.sum()),filled=int((rows.eligible & rows.filled).sum()),
                                executed=len(used),metrics=metric(used)))
            # Same original opportunities, each strategy's independently executed trades.
            baselines=['stoch'] if group=='standalone' else ['pullback','rebreak']
            for baseline in baselines:
                b=t[t.policy.eq(baseline)].set_index('trade_key')
                candidates=['stoch_wvf'] if group=='standalone' else ['pullback_stoch','pullback_wvf']
                for candidate in candidates:
                    v=t[t.policy.eq(candidate)].set_index('trade_key')
                    for period in ('all','early','later'):
                        bp=period_filter(b,period);vp=period_filter(v,period)
                        shared=bp.index.intersection(vp.index);lost=bp.index.difference(vp.index)
                        missed.append(dict(timeframe_min=m,period=period,baseline=baseline,policy=candidate,
                            baseline_n=len(bp),variant_n=len(vp),shared_n=len(shared),not_executed_baseline_n=len(lost),
                            not_executed_baseline_net_r=float(bp.loc[lost,'net_r'].sum()),
                            missed_baseline_5r=int((bp.loc[lost,'net_r']>=5).sum()),
                            total_baseline_5r=int((bp.net_r>=5).sum())))
                        if len(shared):
                            a=bp.loc[shared];z=vp.loc[shared];weeks=a.anchor_time.dt.strftime('%G-W%V')
                            for field in ('net_r','mae_r','mae_bp','mae_anchor_r','entry_price_bp'):
                                av=a[field] if field!='entry_price_bp' else pd.Series(0.,index=shared)
                                zv=z[field] if field!='entry_price_bp' else (z.entry_price/a.entry_price-1)*10000
                                delta=zv-av
                                commond.append(dict(timeframe_min=m,period=period,baseline=baseline,policy=candidate,field=field,
                                    n=len(shared),baseline_mean=float(av.mean()),variant_mean=float(zv.mean()),
                                    delta=stats(delta),uncertainty=inference(delta,weeks,cfg),
                                    interpretation='Conditional on both policies executing and closing this original opportunity; descriptive selection, not causal.'))
        print(json.dumps(dict(timeframe_min=m,rows=len(f),closed_serial=len(t))),flush=True)
    t=pd.concat(trades,ignore_index=True);t.to_csv(out/'trades.csv.gz',index=False)
    serial=[];comp=[]
    for m in [0,*cfg['timeframes']]:
        tm=t if m==0 else t[t.timeframe_min.eq(m)]
        for period in ('all','early','later'):
            z=period_filter(tm,period)
            for policy in (*STOCH_POLICIES,*SPIKE_POLICIES):
                serial.append(dict(timeframe_min=m,period=period,policy=policy)|metric(z[z.policy.eq(policy)]))
        for group,policies in [('standalone',STOCH_POLICIES),('spike',SPIKE_POLICIES)]:
            for scope in (['primary','state'] if group=='standalone' else ['primary']):
                frames=[x for x in allpairs if len(x) and x.group.iloc[0]==group and x.scope.iloc[0]==scope]
                if not frames:continue
                q=pd.concat(frames);q=q if m==0 else q[q.timeframe_min.eq(m)]
                for period in ('all','early','later'):
                    z=period_filter(q,period);weeks=z.anchor_time.dt.strftime('%G-W%V')
                    contrasts=[(p,None) for p in policies]+(CONTRASTS[group] if scope=='primary' else [])
                    # Strict same-state sensitivity has only the WVF arm as a hypothesis.
                    if scope=='state':contrasts=[('stoch_wvf',None)]
                    for policy,baseline in contrasts:
                        a=z['signal_'+policy];b=z['control_'+policy]
                        if baseline:a=a-z['signal_'+baseline];b=b-z['control_'+baseline]
                        comp.append(dict(timeframe_min=m,group=group,scope=scope,period=period,policy=policy,baseline=baseline,
                            n=len(z),target=stats(a),random=stats(b),excess=stats(a-b),
                            target_uncertainty=inference(a,weeks,cfg),excess_uncertainty=inference(a-b,weeks,cfg)))
    family=[x for x in comp if x['timeframe_min'] and x['period']=='later']
    def test_p(row):
        p=row['excess_uncertainty']['weekly']['p']
        return 1. if p is None else p
    adjusted=0.
    for i,r in enumerate(sorted(family,key=test_p)):
        adjusted=max(adjusted,min(1.,test_p(r)*(len(family)-i)))
        r['later_family_holm_p']=adjusted
    result=dict(status='complete',config=cfg,symbols=len(receipt['receipts']),serial=serial,comparisons=comp,
        coverage=coverage,missed=missed,common_fill_diagnostics=commond,holm_family_n=len(family),
        limitations=['Long-only; standalone refers to entry admission with frozen Spike risk/raw-opposite exits.',
        'History reused; chronological partitions are not untouched or forward validation.',
        'Historical identity/tick universe not point-in-time; no funding/extra slippage/shared-capital account.',
        'Full and matched results differ in population; only compare target/random within a matched row.',
        'Conditional common-fill MAE cannot alone establish reduced adverse excursion across opportunities.',
        'R drawdown is closed-trade accumulation; holding exposure is whole-bar approximation.',
        'Python closed-bar implementation is not TradingView native tick-by-tick parity.'],
        training_eligible=False,production_eligible=False)
    dump(out/'result.json',result)
    print(json.dumps(dict(output=str(out),rows=len(t),holm_family_n=len(family))),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--name',default='analysis_v1')
    a=p.parse_args();run(a.root,a.name)
