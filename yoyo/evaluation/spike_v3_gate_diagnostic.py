"""V3 noise diagnosis by frozen single-tag display filters, not a new detector.

All gates use frozen current-bar causal tags; original volume/TR/candle/MD tags
use authenticated features at or before each event. Original parent/cooldown clocks
stay frozen. No declined parent's state is reset and no replacement child is
searched. Only outcomes use future bars, reusing pinned trades and controls.
"""
from pathlib import Path
import argparse
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_burst_early_warning as v3
from yoyo.evaluation import spike_burst_recall_study as old
from yoyo.evaluation.spike_burst_dataset import load_feature

ROOT=old.ROOT
PRIOR=v3.EXPERIMENT/'results'
EXP=ROOT/'experiments/active/exp-spike-v3-gate-diagnostic-20260910-v1'
OUT=EXP/'results'
PREPARED_SHA='519d3bafb7d82a7e46a162ee5b109869bb85baf118ce8480450d5a7fafffbd6d'
VALIDATION_SHA='14c73602956911b66f8adbb5472430f68d99a1117daf69c2542d5228e72794c0'
ORIGINAL_GATES=['tag_above_six','tag_original_volume','tag_original_expansion',
    'tag_original_body','tag_original_close_position','tag_nonnegative_md']
FILTERS={'early':['tag_recent_density']+ORIGINAL_GATES,'confirmed':ORIGINAL_GATES}

CONFIG=dict(schema='v3-single-gate-diagnostic',filters=FILTERS,new_hypotheses=13,
    selection='frozen events; no parent/cooldown regeneration',period='same278OKX1H61days',
    holdout_consumption_per_config=1,control='original event-matched controls, not refitted',
    live_changes=False)


def pins():
    paths=[Path(__file__),EXP/'PROJECT_PLAN.md',ROOT/'yoyo/evaluation/spike_burst_recall_study.py',
           ROOT/'yoyo/evaluation/altseason_research.py']
    out={}
    for p in paths:
        rel=str(p.resolve().relative_to(ROOT))
        if subprocess.check_output(['git','show','HEAD:'+rel],cwd=ROOT)!=p.read_bytes():
            raise ValueError('Commit exact builder/plan first '+rel)
        out[rel]=old.sha(p)
    return out


def authenticate():
    refs={}
    for name,digest in [('prepared_manifest.json',PREPARED_SHA),('validation_manifest.json',VALIDATION_SHA)]:
        p=old.checked(PRIOR/name,digest);d=json.loads(p.read_text());refs[str(p)]=digest
        if d.get('status')!='complete': raise ValueError('Incomplete source')
        for a in d['artifacts']:
            p=old.checked(a['path'],a['sha256']);refs[str(p)]=a['sha256']
    return refs


def select(frame,arm,gate):
    """One original-stage gate at a time; future fields never select events."""
    if arm not in FILTERS or (gate!='baseline' and gate not in FILTERS[arm]):
        raise ValueError('Unregistered filter')
    mask=frame.arm.eq(arm)
    if gate!='baseline': mask &= frame[gate].eq(True)
    return frame.loc[mask].copy()


def recall(labels,signals):
    """Independent fixed denominator; actual retained decision positions only."""
    indices={k:np.sort(v.decision_i.to_numpy(int)) for k,v in signals.groupby('instrument')}
    rows=[]
    for r in labels.itertuples():
        arr=indices.get(r.instrument,np.array([],dtype=int));pos=arr.searchsorted(r.event_i)
        lag=int(arr[pos]-r.event_i) if pos<len(arr) else None
        rows.append(dict(instrument=r.instrument,event_i=r.event_i,decision_time=r.decision_time,
            label=r.label,hit_1=lag is not None and 0<=lag<=1,hit_6=lag is not None and 0<=lag<=6))
    return pd.DataFrame(rows)


def build():
    source=pins();refs=authenticate()
    if OUT.exists() and any(OUT.iterdir()): raise ValueError('Refusing overwrite/rescoring')
    OUT.mkdir(parents=True)
    old.write_json(OUT/'started.json',dict(config=CONFIG,source_pins=source))
    actual=pd.read_csv(PRIOR/'trade_events.csv.gz',float_precision='round_trip')
    controls=pd.read_csv(PRIOR/'trade_controls.csv.gz',float_precision='round_trip')
    labels=pd.read_csv(PRIOR/'labels.csv.gz',float_precision='round_trip')
    for t in (actual,controls,labels): t['decision_time']=pd.to_datetime(t.decision_time,utc=True)
    if actual.event_id.duplicated().any(): raise ValueError('Duplicate event identity')
    matching=json.loads((PRIOR/'matching.json').read_text())
    for col in ORIGINAL_GATES[1:]: actual[col]=False
    # Restore individual original V1 ingredients, never V2 comparison.
    for job in matching['jobs']:
        frame=load_feature(job['features_path'],job['features_sha256'],60,old.END)
        refs[job['features_path']]=job['features_sha256']
        m=actual.instrument.eq(job['instrument']);ii=actual.loc[m,'decision_i'].to_numpy(int)
        c,o,h,l=(frame[k].iloc[ii].to_numpy() for k in ('close','open','high','low'))
        span=h-l
        actual.loc[m,'tag_original_volume']=frame.rv.iloc[ii].to_numpy()>=4.0
        actual.loc[m,'tag_original_expansion']=frame.expansion.iloc[ii].to_numpy()>=3.0
        actual.loc[m,'tag_original_body']=(span>0)&(c-o>=.55*span)
        actual.loc[m,'tag_original_close_position']=(span>0)&(c-l>=.75*span)
        actual.loc[m,'tag_nonnegative_md']=frame.md.iloc[ii].to_numpy()>=0
    summaries,score_rows,gate_counts,kept_rows=[],[],[],[]
    periods={k:v for k,v in old.period_windows().items() if k in ('full','first31','last30','case_night')}
    for arm,gates in FILTERS.items():
        base=select(actual,arm,'baseline')
        for gate in ['baseline']+gates:
            a=select(actual,arm,gate)
            det=recall(labels,a)
            kept_rows.extend(dict(arm=arm,gate=gate,event_id=x) for x in a.event_id)
            gate_counts.append(dict(arm=arm,gate=gate,retained=len(a),dropped=len(base)-len(a),
                missing_rate=1-len(a)/len(base)))
            for period,(start,end) in periods.items():
                sub=a[a.decision_time.ge(start)&a.decision_time.lt(end)]
                c=controls[controls.matched_event_id.isin(sub.event_id)]
                d=det[det.label.eq('positive')&det.decision_time.ge(start)&det.decision_time.lt(end)]
                original=base[base.decision_time.ge(start)&base.decision_time.lt(end)]
                stats,scores=old.trade_summary(sub,c)
                known=sub[sub.forward_known.eq(True)];random_known=c[c.forward_known.eq(True)]
                stats.update(arm=arm,gate=gate,period=period,alerts_per_day=len(sub)/((end-start)/pd.Timedelta(days=1)),
                    original_signals=len(original),drop_fraction=1-len(sub)/len(original) if len(original) else np.nan,
                    positive_events=len(d),hits_1=int(d.hit_1.sum()),hits_6=int(d.hit_6.sum()),
                    recall_1=d.hit_1.mean(),recall_6=d.hit_6.mean(),
                    forward_known=len(known),forward_success=pd.to_numeric(known.forward_success).mean(),
                    random_forward_success=pd.to_numeric(random_known.forward_success).mean())
                for r in (5,10):
                    n=int(original.peak_r.ge(r).sum());retained=int(sub.peak_r.ge(r).sum())
                    stats[f'peak_{r}r_base']=n;stats[f'peak_{r}r_kept']=retained
                    stats[f'peak_{r}r_retention']=retained/n if n else np.nan
                summaries.append(stats)
                if period=='full': score_rows.extend(dict(arm=arm,gate=gate,**s) for s in scores)
            print('diagnosed',arm,gate,flush=True)
    summary=pd.DataFrame(summaries)
    primary=summary.period.eq('full') & ~summary.gate.eq('baseline')
    assert primary.sum()==13
    summary.loc[primary,'holm_p_13']=v3.holm(summary.loc[primary,'permutation_p'].fillna(1).to_numpy())
    baseline=summary[summary.gate.eq('baseline') & summary.period.eq('full')].set_index('arm')
    if baseline.loc['early','hits_1']!=1463 or baseline.loc['confirmed','hits_1']!=488:
        raise ValueError('Original denominator/clock drift')
    original_summary=pd.read_csv(PRIOR/'trade_summary.csv')
    for arm in FILTERS:
        prior=original_summary[original_summary.arm.eq(arm)&original_summary.period.eq('full')].iloc[0]
        for col in ('valid','natural_wins','natural_exits','wins'):
            if baseline.loc[arm,col]!=prior[col]: raise ValueError('Baseline outcome drift '+col)
    cases=actual[actual.asset.isin(['HYPE','NEAR','PEPE']) & actual.decision_time.ge(old.NIGHT[0]-pd.Timedelta(hours=6)) & actual.decision_time.lt(old.NIGHT[1])]
    products={'summary.csv':summary,'gate_counts.csv':pd.DataFrame(gate_counts),
        'score_summary.csv':pd.DataFrame(score_rows),'retained_event_ids.csv.gz':pd.DataFrame(kept_rows),
        'case_tags.csv':cases[['asset','arm','decision_time','signal_close','confirm_age']+list(v3.TAGS)+ORIGINAL_GATES[1:]]}
    for n,t in products.items(): old.write_csv(OUT/n,t)
    if pins()!=source: raise ValueError('Builder changed during diagnosis')
    old._verify_references(refs)
    old.write_json(OUT/'manifest.json',dict(status='complete',config=CONFIG,source_pins=source,
        source_refs=refs,artifacts=[old.artifact(OUT/n) for n in products],
        code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        limitations=['Existing events filtered; no regenerated cooldown/parent/replacement confirmations',
                     'Seen history,13 exploratory fixed gates, no blind OOS','No account NAV or deployment']))


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    build()
