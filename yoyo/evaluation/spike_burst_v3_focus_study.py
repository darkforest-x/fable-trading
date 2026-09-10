"""Preregistered V3 reference-lock and complete-base interventions.

Prepare authenticates existing closed OHLC/causal features and freezes actual
new parent/child sequences, the ORIGINAL fixed labels and common matched random
schedules. Only evaluate reads forward execution outcomes. Label outcomes never
control detection; reference coverage remains separate from timely new alerts.
Economic evaluation is of parents only: confirmations upgrade, not new trades.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import shutil
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation import spike_burst_early_warning as v3
from yoyo.evaluation import spike_burst_recall_study as old
from yoyo.evaluation import spike_v3_gate_diagnostic as prior
from yoyo.evaluation import spike_burst_v3_reference_gate as reference
from yoyo.evaluation import spike_burst_v3_structural as structure
from yoyo.evaluation.spike_burst_dataset import load_feature, match_controls
from yoyo.evaluation.spike_burst_execution import simulate_trade

ROOT=old.ROOT
EXP=ROOT/'experiments/active/exp-spike-v3-focus-20260910-v1'
OUT=EXP/'results'
ARMS=('v3','reference','near_box')
CONFIG=dict(schema='spike-v3-focus-structural-v1',arms=ARMS,interventions='independent, never stacked',
    primary_economics='parent early only; children are upgrades',holdout_per_new_config=1,
    acceptance=dict(label_drop=.5,large_recall=.8,baseline_positive_retention=.9),
    execution='unchanged actual next-open; detection occupancy is signal-close reference',cost_bp=20)


def pins():
    paths=[Path(__file__),EXP/'PROJECT_PLAN.md']+[ROOT/'yoyo/evaluation'/n for n in (
        'spike_burst_v3_reference_gate.py','spike_burst_v3_structural.py','spike_burst_early_warning.py',
        'spike_burst_replay.py','spike_burst_execution.py','spike_burst_dataset.py',
        'spike_burst_recall_study.py','spike_v3_gate_diagnostic.py','altseason_research.py')]
    result={}
    for p in paths:
        rel=str(p.relative_to(ROOT))
        if subprocess.check_output(['git','show','HEAD:'+rel],cwd=ROOT)!=p.read_bytes():
            raise ValueError('Commit exact builder before running '+rel)
        result[rel]=old.sha(p)
    return result


def frozen_inputs():
    refs=prior.authenticate()
    prepared=json.loads((prior.PRIOR/'prepared_manifest.json').read_text())
    # These parent source pins include the original execution contract.
    for rel,digest in prepared['source_pins'].items():
        p=old.checked(ROOT/rel,digest); refs[str(p)]=digest
    jobs=json.loads((prior.PRIOR/'matching.json').read_text())['jobs']
    labels=pd.read_csv(prior.PRIOR/'labels.csv.gz',float_precision='round_trip')
    labels['decision_time']=pd.to_datetime(labels.decision_time,utc=True)
    labels['bar_open']=pd.to_datetime(labels.bar_open,utc=True)
    in_window=labels[labels.decision_time.ge(old.START)&labels.decision_time.lt(old.END)]
    if len(in_window)!=8046 or in_window.label.eq('positive').sum()!=1660:
        raise ValueError('Frozen label denominator drift')
    return jobs,labels,refs


def prepare():
    source=pins()
    if OUT.exists() and any(OUT.iterdir()): raise ValueError('No overwrite or rescoring')
    jobs,labels,refs=frozen_inputs()
    OUT.mkdir(parents=True);(OUT/'states').mkdir()
    old.write_json(OUT/'started.json',dict(config=CONFIG,source_pins=source))
    prior_events=pd.read_csv(prior.PRIOR/'signals.csv.gz')
    signals,controls,detections,exposure,states=[],[],[],[],[]
    for ji,job in enumerate(jobs):
        frame=load_feature(job['features_path'],job['features_sha256'],60,old.END)
        refs[job['features_path']]=job['features_sha256']
        machines={'v3':v3.detect(frame),'reference':reference.detect(frame,float(job['tick'])),
                  'near_box':structure.detect(frame)}
        context={k:job[k] for k in ('instrument','asset','symbol','venue','minutes')}
        clocks=frame.index+old.HOUR; window=(clocks>=old.START)&(clocks<old.END)
        lab=labels[labels.instrument.eq(job['instrument'])]
        union=sorted(set(i for s in machines.values() for stage in ('early','confirmed')
                         for i in np.flatnonzero(s[stage].to_numpy())))
        parent_union=sorted(set(i for s in machines.values() for i in np.flatnonzero(s.early.to_numpy()) if window[i]))
        matcher=frame.copy();matcher['history_count']=np.where(frame.ready,frame.history_count,0)
        random=match_controls(matcher,union,parent_union,job['instrument'],60,old.START,old.END)
        saved=pd.DataFrame(dict(decision_i=np.arange(len(frame)),decision_time=clocks))
        for arm,s in machines.items():
            saved[arm+'_early']=s.early.to_numpy();saved[arm+'_confirmed']=s.confirmed.to_numpy()
            for col in ('reference_active','reference_before','reference_owner_i','reference_protection',
                        'reference_exit','holding_blocked','setup_id','boxHigh','boxLow','setup_bars','release_age'):
                if col in s: saved[arm+'_'+col]=s[col].to_numpy()
            for stage in ('early','confirmed'):
                ii=np.flatnonzero(s[stage].to_numpy())
                if arm=='v3':
                    prior_ii=prior_events.loc[prior_events.instrument.eq(job['instrument'])&prior_events.arm.eq(stage),'decision_i'].to_numpy(int)
                    if not np.array_equal(ii[window[ii]],prior_ii): raise ValueError('V3 sequence mismatch '+job['instrument'])
                active=s.reference_active if 'reference_active' in s else pd.Series(False,index=s.index)
                dummy=pd.DataFrame(dict(trend_side=active.astype(int),burst=s[stage]),index=s.index)
                matches,det=old.match_signals(lab,ii,frame,dummy)
                det['arm']=arm;det['stage']=stage;det['instrument']=job['instrument']
                detections.append(det)
                mm=matches.set_index('decision_i').to_dict('index')
                for i in ii:
                    if not window[i]: continue
                    eid=old.identity(job['instrument'],arm+'_'+stage,int(i))
                    p=int(s.parent_i.iloc[i]);m=mm[int(i)]
                    signals.append(dict(context,arm=arm,stage=stage,event_id=eid,
                        decision_i=int(i),decision_time=clocks[i],bar_open=frame.index[i],
                        parent_i=p,parent_decision_time=clocks[p],
                        parent_event_id=old.identity(job['instrument'],arm+'_early',p),
                        signal_close=float(frame.close.iloc[i]),signal_atr=float(frame.atr.iloc[i]),
                        relative_volume=float(frame.rv.iloc[i]),tr_expansion=float(frame.expansion.iloc[i]),
                        frozen_parent_high=float(s.frozen_parent_high.iloc[i]),confirm_age=s.confirm_age.iloc[i],
                        reference_active=bool(active.iloc[i]),reference_owner_i=s.reference_owner_i.iloc[i] if 'reference_owner_i' in s else np.nan,
                        match_status=m['match_status'],label_i=m['label_i'],lag=m['lag']))
                    if stage=='early':
                        for k,j in enumerate(random[int(i)]):
                            controls.append(dict(context,arm=arm,stage=stage,event_id=old.identity(eid,'control',k),
                                matched_event_id=eid,decision_i=int(j),decision_time=clocks[j]))
        path=OUT/'states'/f'{ji:03d}_{job["asset"]}.csv.gz';old.write_csv(path,saved);states.append(old.artifact(path))
        exposure.append(pd.DataFrame(dict(decision_time=clocks[window],eligible=frame.ready.to_numpy()[window].astype(int))))
        print('prepared',ji+1,len(jobs),job['asset'],flush=True)
    products={'signals.csv.gz':pd.DataFrame(signals),'controls.csv.gz':pd.DataFrame(controls),
        'detections.csv.gz':pd.concat(detections,ignore_index=True),
        'exposure.csv.gz':pd.concat(exposure).groupby('decision_time',as_index=False).eligible.sum()}
    for name,df in products.items():old.write_csv(OUT/name,df)
    shutil.copyfile(prior.PRIOR/'labels.csv.gz',OUT/'labels.csv.gz')
    old.write_json(OUT/'matching.json',dict(jobs=jobs,config=CONFIG,state_artifacts=states))
    if pins()!=source:raise ValueError('Source changed during prepare')
    old._verify_references(refs)
    old.write_json(OUT/'prepared_manifest.json',dict(status='complete',config=CONFIG,source_pins=source,
        sources=refs,artifacts=[old.artifact(OUT/n) for n in list(products)+['labels.csv.gz','matching.json']]+states))


# Outcome cache is read ONLY by evaluation workers, after global prepare.
_CACHE={}
_SIGNALS=_CONTROLS=None
_OUTCOME_KEYS=('valid invalid_reason reason exit_reason signal_i decision_i entry_i exit_i decision_time entry_time exit_time '
    'exit_time_lower exit_time_upper exit_timing exit_time_exact entry_price exit_price initial_stop initial_risk initial_risk_frac '
    'net_return net_r peak_r natural_exit censored hold_bars fee_return fee_bp signal_close signal_atr exit_at_open mfe_r mfe_return '
    'held_hours_lower held_hours_upper hold_seconds gross_return gross_bp net_bp gross_r final_protection trail_armed peak_return').split()


def init_evaluation():
    global _CACHE,_SIGNALS,_CONTROLS
    _SIGNALS=pd.read_csv(OUT/'signals.csv.gz',float_precision='round_trip')
    _SIGNALS=_SIGNALS[_SIGNALS.stage.eq('early')]
    _CONTROLS=pd.read_csv(OUT/'controls.csv.gz',float_precision='round_trip')
    for name in ('trade_events.csv.gz','trade_controls.csv.gz'):
        for row in pd.read_csv(prior.PRIOR/name,float_precision='round_trip').to_dict('records'):
            key=(row['instrument'],int(row['decision_i']))
            _CACHE[key]={k:row[k] for k in _OUTCOME_KEYS if k in row}


def score_job(job):
    frame=load_feature(job['features_path'],job['features_sha256'],60,old.END)
    local={};outputs=[];new=0
    for table in (_SIGNALS,_CONTROLS):
        rows=[]
        for row in table[table.instrument.eq(job['instrument'])].to_dict('records'):
            i=int(row['decision_i']);key=(job['instrument'],i)
            if i not in local:
                if key in _CACHE: outcome=dict(_CACHE[key])
                else:
                    outcome=simulate_trade(frame,i,float(job['tick']),old.END);new+=1
                local[i]=dict(outcome,**v3.forward_outcome(frame,i))
            rows.append(dict(row,**local[i],relative_volume=float(frame.rv.iloc[i]),tr_expansion=float(frame.expansion.iloc[i])))
        outputs.append(rows)
    return outputs,new,job['asset']


def retention_summary(labels,det,signals,exposure):
    rows=[]
    for stage in ('early','confirmed'):
        base=det[det.arm.eq('v3')&det.stage.eq(stage)][['instrument','event_i','hit_1']].rename(columns={'hit_1':'baseline_hit_1'})
        for arm in ARMS:
            d=labels.merge(det[det.arm.eq(arm)&det.stage.eq(stage)],on=['instrument','event_i'],validate='one_to_one').merge(base,on=['instrument','event_i'],validate='one_to_one')
            for period,(start,end) in old.period_windows().items():
                p=d[d.label.eq('positive')&d.decision_time.ge(start)&d.decision_time.lt(end)]
                s=signals[signals.arm.eq(arm)&signals.stage.eq(stage)&signals.decision_time.ge(start)&signals.decision_time.lt(end)]
                b=signals[signals.arm.eq('v3')&signals.stage.eq(stage)&signals.decision_time.ge(start)&signals.decision_time.lt(end)]
                big=p[p.large_peak.eq(True)];oldhit=p[p.baseline_hit_1.eq(True)]
                actualset=set(zip(s.instrument,s.decision_i));baseset=set(zip(b.instrument,b.decision_i))
                allstage=signals[signals.arm.eq(arm)&signals.decision_time.ge(start)&signals.decision_time.lt(end)]
                allbase=signals[signals.arm.eq('v3')&signals.decision_time.ge(start)&signals.decision_time.lt(end)]
                nlabels=len(allstage.drop_duplicates(['instrument','decision_i']))
                base_nlabels=len(allbase.drop_duplicates(['instrument','decision_i']))
                hrs=float(exposure.loc[exposure.decision_time.ge(start)&exposure.decision_time.lt(end),'eligible'].sum())
                rows.append(dict(arm=arm,stage=stage,period=period,signals=len(s),alerts_per_day=len(s)/((end-start)/pd.Timedelta(days=1)),
                    distinct_labels=nlabels,label_drop=1-nlabels/base_nlabels if base_nlabels else np.nan,
                    labels_per_100_asset_days=2400*nlabels/hrs if hrs else np.nan,
                    baseline_signals=len(b),same_time_kept=len(actualset&baseset),same_time_removed=len(baseset-actualset),new_times=len(actualset-baseset),
                    positive_events=len(p),hits_1=int(p.hit_1.sum()),recall_1=p.hit_1.mean(),recall_6=p.hit_6.mean(),
                    baseline_hit_events=len(oldhit),retained_hit_events=int(oldhit.hit_1.sum()),retention=oldhit.hit_1.mean(),
                    newly_caught=int((~p.baseline_hit_1&p.hit_1).sum()),tracking_positive_events=int(p.already_tracking.sum()),
                    large_positive_events=len(big),large_hits_1=int(big.hit_1.sum()),large_recall_1=big.hit_1.mean(),
                    large_baseline_hits=int(big.baseline_hit_1.sum()),large_retained_hits=int((big.baseline_hit_1&big.hit_1).sum()),
                    matched=int(s.match_status.eq('matched').sum()),unmatched=int(s.match_status.eq('unmatched').sum()),
                    duplicates=int(s.match_status.eq('duplicate').sum()),unknown=int(s.match_status.eq('unknown').sum())))
    return pd.DataFrame(rows)


def evaluate(workers=3):
    source=pins();prepared=json.loads((OUT/'prepared_manifest.json').read_text())
    if prepared['status']!='complete' or prepared['source_pins']!=source:raise ValueError('Source mismatch')
    if (OUT/'evaluation_started.json').exists():raise ValueError('No rescoring')
    for a in prepared['artifacts']:old.checked(a['path'],a['sha256'])
    old._verify_references(prepared['sources'])
    old.write_json(OUT/'evaluation_started.json',dict(source_pins=source,prepared_sha=old.sha(OUT/'prepared_manifest.json')))
    jobs=json.loads((OUT/'matching.json').read_text())['jobs']
    actual,random=[],[];new=0
    with ProcessPoolExecutor(max_workers=workers,initializer=init_evaluation) as pool:
        for k,(output,n,asset) in enumerate(pool.map(score_job,jobs)):
            actual.extend(output[0]);random.extend(output[1]);new+=n
            print('evaluated',k+1,len(jobs),asset,'new paths',n,flush=True)
    a,c=pd.DataFrame(actual),pd.DataFrame(random)
    for df in (a,c):df['decision_time']=pd.to_datetime(df.decision_time,utc=True)
    summaries,scores=[],[]
    for arm in ARMS:
        for period,(start,end) in old.period_windows().items():
            aa=a[a.arm.eq(arm)&a.decision_time.ge(start)&a.decision_time.lt(end)]
            cc=c[c.matched_event_id.isin(aa.event_id)]
            stats,features=old.trade_summary(aa,cc)
            known=aa[aa.forward_known.eq(True)]
            summaries.append(dict(arm=arm,period=period,**stats,forward_success=pd.to_numeric(known.forward_success).mean()))
            if period=='full':scores.extend(dict(arm=arm,**r) for r in features)
    summary=pd.DataFrame(summaries);mask=summary.period.eq('full')&~summary.arm.eq('v3')
    summary.loc[mask,'holm_p']=v3.holm(summary.loc[mask,'permutation_p'].fillna(1).to_numpy())
    tables={n:pd.read_csv(OUT/(n+'.csv.gz'),float_precision='round_trip') for n in ('signals','labels','detections','exposure')}
    for n in ('signals','labels','exposure'):tables[n]['decision_time']=pd.to_datetime(tables[n].decision_time,utc=True)
    retention=retention_summary(tables['labels'],tables['detections'],tables['signals'],tables['exposure'])
    products={'trade_events.csv.gz':a,'trade_controls.csv.gz':c,'trade_summary.csv':summary,
              'score_summary.csv':pd.DataFrame(scores),'retention_summary.csv':retention}
    for name,df in products.items():old.write_csv(OUT/name,df)
    old._verify_references(prepared['sources'])
    if pins()!=source:raise ValueError('Source changed during evaluation')
    old.write_json(OUT/'validation_manifest.json',dict(status='complete',config=CONFIG,source_pins=source,
        prepared_sha=old.sha(OUT/'prepared_manifest.json'),new_execution_paths=new,
        artifacts=[old.artifact(OUT/n) for n in products],
        limitations=['Seen historical pool; not blind OOS','Independent parent-event economics, not account NAV',
                     'Reference occupancy uses signal close; economic fills are next open',
                     'Complete structural interventions, not frozen event filtering']))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('phase',choices=('prepare','evaluate'));p.add_argument('--workers',type=int,default=3)
    args=p.parse_args()
    prepare() if args.phase=='prepare' else evaluate(args.workers)
