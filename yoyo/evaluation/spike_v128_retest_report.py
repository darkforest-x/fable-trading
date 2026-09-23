"""Audit and summarize frozen delayed-entry ledgers without selecting parameters.

UTC anchor weeks are common blocks across symbols and policies. Conditional
closed-fill matching and request-level zero-for-no-fill comparisons are separate;
neither is an account return. Earlier results require both sides fully resolved.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation import spike_v128_retest_entry as study
from yoyo.evaluation import spike_v128_expansion_report as stats
from yoyo.evaluation.spike_v128_recent_report import read_csv
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP=study.EXP
UNRESOLVED={'pending_boundary','no_next_bar','censored_boundary','censored_gap'}


def load(run):
    cfg=json.loads(study.CONFIG.read_text());ident=json.loads((run/'identity.json').read_text())
    manifest=json.loads((run/'manifest.json').read_text());pr=Path(cfg['parent_run'])
    pi=json.loads((pr/'identity.json').read_text());pm=json.loads((pr/'manifest.json').read_text())
    expected={f'{s}_{m}m' for s in pi['symbols'] for m in cfg['timeframes']}
    rid=hashlib.sha256(json.dumps(ident,sort_keys=True).encode()).hexdigest()
    if (not manifest['complete'] or manifest['errors'] or ident['subset'] or manifest['run_identity']!=rid or
        set(manifest['receipts'])!=expected or ident['inputs']!=pi['inputs'] or ident['config']!=cfg or
        ident['config_sha256']!=study.parent.digest(study.CONFIG) or ident['parent_identity_sha256']!=study.parent.digest(pr/'identity.json') or
        ident['parent_manifest_sha256']!=study.parent.digest(pr/'manifest.json')): raise ValueError('invalid run identity')
    for p,sha in (pi['code']|ident['code']).items():
        if study.parent.digest(Path(p))!=sha: raise ValueError(f'code changed: {p}')
    tables={k:[] for k in study.TABLES};receipts=[]
    for key,sha in manifest['receipts'].items():
        folder=run/'streams'/key
        if study.parent.digest(folder/'receipt.json')!=sha: raise ValueError('receipt changed')
        r=json.loads((folder/'receipt.json').read_text())
        study.validate_receipt(folder,rid,ident['inputs'][r['symbol']],r['symbol'],r['minutes'],pm['receipts'][key])
        if not r['summary']['parent_parity']: raise ValueError('missing parent parity')
        receipts.append(r)
        for name in tables:
            f=read_csv(folder/(name+'.csv.gz'))
            if len(f): tables[name].append(f)
    split=pd.Timestamp(cfg['split'])
    tables={n:stats.normalize(pd.concat(parts,ignore_index=True),split) for n,parts in tables.items()}
    for name,f in tables.items():
        for col in ('anchor_close','decision_time','control_decision_time'):
            if col in f: f[col]=pd.to_datetime(f[col],utc=True)
        if 'anchor_close' in f:
            dates=f.anchor_close.dt.normalize()-pd.to_timedelta(f.anchor_close.dt.dayofweek,unit='D')
            f['week']=dates.dt.strftime('%Y-%m-%d')
        subset=['trade_key','control_pool'] if name=='controls' else ['trade_key','policy']
        if f.duplicated(subset).any(): raise ValueError('duplicate ledger event')
    return cfg,tables,receipts


def cohort(f, period, split, closed=False):
    if closed: f=f[f.status=='closed']
    if period=='all': return f
    if period=='later': return f[f.anchor_close>=split]
    before=f.anchor_close<split
    resolved=f.decision_time<split
    if 'exit_time' in f: resolved &= f.exit_time.isna() | (f.exit_time<split)
    return f[before & (resolved if period=='earlier' else ~resolved)]


def random_result(trades, controls, policy, period, split, cfg):
    target=cohort(trades,period,split,closed=True)
    c=controls[(controls.control_pool==policy)&controls.matched]
    out=target.merge(c[['trade_key','control_net_return','control_net_r','control_exit_time','control_decision_time']],on='trade_key',validate='one_to_one')
    if period=='earlier': out=out[(out.control_exit_time<split)&(out.control_decision_time<split)]
    r=stats.inference((out.net_return-out.control_net_return)*1e4,out.week,cfg['statistics_seed'],cfg['bootstrap'])
    r.update(random_coverage=len(out)/len(target) if len(target) else np.nan,
             random_mean_net_bp=float(out.control_net_return.mean()*1e4),paired_target_mean_net_bp=float(out.net_return.mean()*1e4),
             random_win_rate=float((out.control_net_return>0).mean()) if len(out) else np.nan,
             random_mean_net_r=float(out.control_net_r.mean()),random_ge5r=int((out.control_net_r>=5).sum()))
    return r


def request_result(outcomes,controls,policy,period,split,cfg):
    target=cohort(outcomes,period,split)
    c=controls[controls.control_pool==policy]
    pair=target.merge(c[['trade_key','control_status','control_net_return','control_exit_time','control_decision_time']],on='trade_key',validate='one_to_one')
    pair=pair[~pair.status.isin(UNRESOLVED)&~pair.control_status.isin(UNRESOLVED)]
    # An empty stratum is unavailable, not a zero-return cash request.
    pair=pair[pair.control_status!='empty_stratum']
    if period=='earlier': pair=pair[(pair.control_decision_time<split)&(pair.control_exit_time.isna()|(pair.control_exit_time<split))]
    a=pair.net_return.where(pair.status=='closed',0.)
    b=pair.control_net_return.where(pair.control_status=='closed',0.)
    r=stats.inference((a-b)*1e4,pair.week,cfg['statistics_seed'],cfg['bootstrap'])
    return r|{'target_request_mean_bp':float(a.mean()*1e4),'random_request_mean_bp':float(b.mean()*1e4),
              'target_filled':int(pair.status.eq('closed').sum()),'random_filled':int(pair.control_status.eq('closed').sum()),
              'requests_before_unresolved_filter':len(target)}


def tail_counts(base,treated,level):
    """Re-entering an old winner does not count as retaining its high-R outcome."""
    old=base[base.net_r>=level];new=treated[treated.net_r>=level]
    entered=old.trade_key.isin(treated.trade_key);still=old.trade_key.isin(new.trade_key)
    return {'threshold_net_r':level,'baseline_high_r':len(old),'reentered':int(entered.sum()),
            'still_high_r':int(still.sum()),'reentered_lost_high_r':int((entered&~still).sum()),
            'not_reentered':int((~entered).sum()),'new_high_r':int((~new.trade_key.isin(old.trade_key)).sum()),
            'treated_high_r':len(new)}


def build(run,output):
    declared=[Path(__file__),Path('tests/evaluation/test_spike_v128_retest_report.py'),Path(stats.__file__)]
    if not _committed(declared): raise ValueError('commit report builder before construction')
    cfg,tables,receipts=load(run);split=pd.Timestamp(cfg['split'])
    cand,trades,statuses,controls=[tables[n] for n in study.TABLES]
    summaries=[];requests=[];comparisons=[];tails=[];prices=[];groups=[];funnels=[]
    for minutes in cfg['timeframes']:
        for arm in study.parent.ARMS:
            key={'timeframe_min':minutes,'arm':arm}
            select=lambda f:f[(f.timeframe_min==minutes)&(f.arm==arm)]
            ca,tr,st,co=map(select,(cand,trades,statuses,controls))
            for policy in study.POLICIES:
                pc=ca[ca.policy==policy];pt=tr[tr.policy==policy];ps=st[st.policy==policy]
                for period in ('all','earlier','later','cross_split'):
                    t=cohort(pt,period,split);s=cohort(ps,period,split);cl=t[t.status=='closed']
                    summaries.append(key|{'policy':policy,'period':period,'candidates':len(s),'taken':len(t),
                        'censored':len(t)-len(cl)}|stats.extended_metrics(cl)|random_result(pt,co,policy,period,split,cfg))
                    requests.append(key|{'policy':policy,'period':period}|request_result(pc,co,policy,period,split,cfg))
                for reason,n in pc.status.value_counts().items(): funnels.append(key|{'policy':policy,'stage':'outcome','reason':reason,'n':int(n)})
                for reason,n in ps.status.value_counts().items(): funnels.append(key|{'policy':policy,'stage':'serial','reason':reason,'n':int(n)})
                for field in ('breakout_i','retest_i','confirmation_i'):
                    if field in pc: funnels.append(key|{'policy':policy,'stage':'reached','reason':field,'n':int(pc[field].notna().sum())})
                for name,subset in [('long',pt[pt.side==1]),('short',pt[pt.side==-1]),('BTCUSDT',pt[pt.symbol=='BTCUSDT']),('ETHUSDT',pt[pt.symbol=='ETHUSDT'])]:
                    closed=cohort(subset,'all',split,closed=True)
                    groups.append(key|{'policy':policy,'group':name,'taken':len(subset)}|stats.extended_metrics(closed)|random_result(subset,co,policy,'all',split,cfg))
                for week in sorted(pt.week.unique()):
                    subset=pt[pt.week==week];cl=subset[subset.status=='closed']
                    groups.append(key|{'policy':policy,'group':'week:'+week,'taken':len(subset)}|stats.extended_metrics(cl)|random_result(subset,co,policy,'all',split,cfg))
            for period in ('all','earlier','later','cross_split'):
                b=cohort(tr[tr.policy=='baseline'],period,split,closed=True)
                for policy in ('wait3','retest'):
                    r=cohort(tr[tr.policy==policy],period,split,closed=True)
                    comp=stats.comparison(b,r,cfg['statistics_seed'],cfg['bootstrap'])
                    comp['treated_closed']=comp.pop('high20_closed')
                    comparisons.append(key|{'policy':policy,'period':period}|comp)
                    for level in (3,5,10): tails.append(key|{'policy':policy,'period':period}|tail_counts(b,r,level))
                    old=ca[(ca.policy=='baseline')&(ca.status=='closed')]
                    pairs=r.merge(old[['trade_key','net_return','net_r']],on='trade_key',suffixes=('','_immediate'),validate='one_to_one')
                    price=key|{'policy':policy,'period':period,'pairs':len(pairs),
                        'delayed_net_bp':float(pairs.net_return.mean()*1e4),'immediate_same_anchor_net_bp':float(pairs.net_return_immediate.mean()*1e4),
                        'delayed_net_r':float(pairs.net_r.mean()),'immediate_same_anchor_net_r':float(pairs.net_r_immediate.mean()),
                        'delayed_anchor_r':float(pairs.net_r_anchor.mean()),
                        'median_delay_bars':float(pairs.delay_bars.median()),'median_chase_anchor_r':float(pairs.chase_anchor_r.median()),
                        'worse_entry_fraction':float((pairs.chase_anchor_r>0).mean()) if len(pairs) else np.nan,
                        'median_risk_ratio':float(pairs.risk_ratio.median())}
                    prices.append(price)
    frames={'summary':pd.DataFrame(summaries),'request_comparison':pd.DataFrame(requests),
        'policy_comparison':pd.DataFrame(comparisons),'tail_retention':pd.DataFrame(tails),'price_cost':pd.DataFrame(prices),
        'subgroups':pd.DataFrame(groups),'funnel':pd.DataFrame(funnels)}
    summary=frames['summary'];summary['holm_p_four_cells']=np.nan
    for period in ('all','earlier','later','cross_split'):
        idx=summary.index[(summary.policy=='retest')&(summary.period==period)]
        summary.loc[idx,'holm_p_four_cells']=stats.holm(summary.loc[idx,'p_one_sided'].tolist())
    output.mkdir(parents=True,exist_ok=False)
    for name,f in frames.items(): f.to_csv(output/(name+'.csv'),index=False)
    for name,f in tables.items(): f.to_csv(output/(name+'.csv.gz'),index=False,compression={'method':'gzip','mtime':0})
    receipt={'run':str(run),'run_manifest_sha256':study.parent.digest(run/'manifest.json'),
        'source':{str(p):study.parent.digest(p) for p in declared},'config':cfg,'streams':len(receipts),
        'parent_parity_streams':sum(r['summary']['parent_parity'] for r in receipts),
        'files':{p.name:study.parent.digest(p) for p in sorted(output.iterdir())}}
    study.parent.dump(output/'summary_receipt.json',receipt)
    print(summary[(summary.period=='all')][['timeframe_min','arm','policy','taken','closed','win_rate','mean_net_r','mean_net_bp','net_ge5r','matched','mean_excess_bp','holm_p_four_cells']].to_string(index=False))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();build(a.run,a.output)
