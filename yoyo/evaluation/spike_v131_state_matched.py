"""Same-causal-state matched controls for the preregistered V13.1 study.

Added before inspecting complete primary returns. Original independent-cohort
gating can change opportunity availability differently for signal and random.
Here BOTH pre-entry feature values must belong to the same frozen band. Rebuild
both queues on those paired IDs; this is a diagnostic comparison population,
not an executable signal rule that observes its random counterpart in advance.
No redraw, changed feature, exit, cost or selection of profitable outcomes.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v131_rule_summary import read,eligibility,serial_flags,effective,epoch,infer,four_week_blocks
from yoyo.evaluation.spike_v131_rule_facts import FEATURES
from yoyo.evaluation.spike_v131_entry_stages import dump,known
from yoyo.evaluation.spike_v131_rule_study import CONFIG
from yoyo.evaluation.spike_v131_excursions import provenance
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python


def same_membership(f,allowed):
    """Require both fixed pair anchors to pass the same past-only feature gate."""
    s=f.index[f.cohort.eq('signal') & f.pair_eligible].to_numpy()
    controls=dict(zip(f.loc[f.cohort.eq('control') & f.pair_eligible,'trade_key'],f.index[f.cohort.eq('control') & f.pair_eligible]))
    c=np.array([controls[k] for k in f.loc[s,'trade_key']],int)
    use=allowed[s]&allowed[c]
    member=np.zeros(len(f),bool);member[s[use]]=True;member[c[use]]=True
    return member,s[use],c[use]


def run(root,name='analysis_v3'):
    root=Path(root);out=root/name;cfg=json.loads(CONFIG.read_text())
    assert json.loads((root/'receipt.json').read_text())['complete']
    sources=sorted(set([Path(__file__),CONFIG,*_local_transitive_python((Path(__file__),))]),key=str)
    for p in sources:subprocess.run(['git','ls-files','--error-unmatch',str(p)],check=True,stdout=subprocess.DEVNULL)
    subprocess.run(['git','diff','--exit-code','HEAD','--',*map(str,sources)],check=True)
    assert not (out/'state_matched.json').exists()
    identity=json.loads((root/'identity.json').read_text());q=json.loads((out/'thresholds.json').read_text())
    fields=('target','random','excess','target_fills','random_fills','target_bar_exposure_minutes','random_bar_exposure_minutes')
    policies=[f'{f}_{band}' for f in FEATURES for band in ['known','low','mid','high']]
    weeks=list(dict.fromkeys(pd.date_range(cfg['warmup_start'],cfg['end'],freq='D').strftime('%G-W%V')))
    blocks=defaultdict(lambda:np.zeros((len(weeks),len(fields)+1)))
    coverage=defaultdict(lambda:dict(target_pass=0,random_pass=0,both_pass=0,mature=0))
    start=int(pd.Timestamp(cfg['analysis_start']).timestamp());cut=int(pd.Timestamp(cfg['split']).timestamp());checks=0
    columns=['trade_key','cohort','members','pair_eligible','anchor_i','decision_i','exit_i','entry_i','filled','status','net_r','anchor_time','decision_time','mature_time',*FEATURES]
    for ni,symbol in enumerate(identity['symbols']):
        for m in cfg['timeframes']:
            f=read(root/'streams'/symbol/f'{m}m.csv.gz',columns)
            if not len(f):continue
            f=f.loc[(f.members.to_numpy(int)&1)!=0].reset_index(drop=True)
            if not len(f):continue
            for field in ['entry_i','exit_i','net_r']:
                if field not in f:f[field]=np.nan
            e=eligibility(f,q[str(m)]);target=f.cohort.eq('signal').to_numpy();pair=f.pair_eligible.to_numpy(bool)
            order=f.sort_values(['decision_i','anchor_i','trade_key']).index.to_numpy()
            fill=f.filled.to_numpy(bool);decision=f.decision_i.to_numpy(int);exits=f.exit_i.fillna(-1).to_numpy(int);statuses=f.status.to_numpy()
            times=tuple(epoch(f[x]) for x in ['mature_time','decision_time','anchor_time']);ks=f.status.map(known).to_numpy(bool)
            anchor=times[2];wk=pd.to_datetime(f.anchor_time,utc=True).dt.strftime('%G-W%V')
            codes=pd.Categorical(wk,categories=weeks).codes
            for policy in policies:
                member,si,ci=same_membership(f,e[policy])
                assert np.array_equal(f.loc[si,'trade_key'].to_numpy(),f.loc[ci,'trade_key'].to_numpy())
                # Check all paired-state members against both eligibility flags.
                assert e[policy][si].all() and e[policy][ci].all();checks+=len(si)
                taken=serial_flags(f,member&target,order)|serial_flags(f,member&~target,order)
                values,k,mat=effective(f,member,taken,times,ks)
                # Independently rebuild the actually admitted ID sequence.
                for cohort in (True,False):
                    flat=-1;expected=[]
                    for i in order:
                        if bool(target[i])!=cohort or not member[i] or not fill[i]:continue
                        if decision[i]>=flat:
                            expected.append(int(i));flat=float('inf') if statuses[i]=='censored_boundary' else exits[i]
                    assert set(expected)==set(np.flatnonzero(taken & (target==cohort)));checks+=len(expected)
                first=np.minimum(anchor[si],anchor[ci]);last=np.maximum(mat[si],mat[ci]);valid=k[si]&k[ci]&(first>=start)
                hold=m*np.where(taken,np.maximum(0,f.exit_i.fillna(0).to_numpy(float)-f.entry_i.fillna(0).to_numpy(float)+1),0.)
                vals=np.column_stack([values[si],values[ci],values[si]-values[ci],taken[si],taken[ci],hold[si],hold[ci]])
                scopes={'all':valid,'early':valid&(last<cut),'later':valid&(first>=cut)}
                for tf in [m,0]:
                    cv=coverage[tf,policy];cv['target_pass']+=int((target&pair&e[policy]&(anchor>=start)).sum());cv['random_pass']+=int((~target&pair&e[policy]&(anchor>=start)).sum());cv['both_pass']+=int((first>=start).sum());cv['mature']+=int(valid.sum())
                    for period,use in scopes.items():
                        if not use.any():continue
                        wc=codes[si[use]]
                        blocks[tf,period,policy]+=np.column_stack([np.bincount(wc,minlength=len(weeks)),*[np.bincount(wc,weights=vals[use,j],minlength=len(weeks)) for j in range(len(fields))]])
        if ni==0 or (ni+1)%100==0 or ni+1==len(identity['symbols']):print(json.dumps(dict(state_matched=ni+1,total=len(identity['symbols']))),flush=True)
    rows=[];weekly=[]
    for (m,period,policy),arr in blocks.items():
        b={weeks[i]:dict(zip(('n',*fields),row.tolist())) for i,row in enumerate(arr) if row[0]>0}
        sums={f:sum(x[f] for x in b.values()) for f in ('n',*fields)}
        rows.append(dict(timeframe_min=m,period=period,policy=policy,sums=sums,
            mean_excess=sums['excess']/sums['n'],inference=infer(b,'excess',cfg),four_week_inference=infer(four_week_blocks(b),'excess',cfg)))
        weekly.append(dict(timeframe_min=m,period=period,policy=policy,blocks=b))
    candidates=[r for r in rows if r['timeframe_min'] and r['period']=='later' and not r['policy'].endswith('_known')]
    previous=0.
    for i,r in enumerate(sorted(candidates,key=lambda x:x['inference']['p'] if x['inference']['p'] is not None else 1.)):
        p=r['inference']['p'];previous=max(previous,min(1.,(1 if p is None else p)*(len(candidates)-i)));r['holm_p']=previous;r['holm_family_n']=len(candidates)
    output=dict(builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),code=[provenance(p) for p in sources],
        definitions='Same causal band on both fixed anchors, fresh restricted queues, no redraw. Diagnostic population, not a live policy. Required unknowns excluded; nonfills/skips zero. Period by paired anchors and maturity. Exposure minutes count whole entry-through-exit bars, an OHLC upper-bound proxy.',
        rows=rows,coverage=[dict(timeframe_min=k[0],policy=k[1],**v) for k,v in coverage.items()],membership_and_serial_checks=checks,
        limitations=['Sparse shared-state pairs may leave very few actual fills.','Fills and holding time may still differ; this does not enforce identical market exposure.','History reused and non-PIT universe; no production qualification.'])
    dump(out/'state_matched.json',output);dump(out/'state_matched_weekly_blocks.json',weekly)
    print(json.dumps(dict(state_match='complete',rows=len(rows),checks=checks)),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--name',default='analysis_v3');a=p.parse_args();run(a.root,a.name)
