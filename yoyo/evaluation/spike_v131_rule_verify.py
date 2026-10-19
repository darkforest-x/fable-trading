"""Independent stdlib audit of eligibility, full queues, accounting and pairs.

Does not import any study admission, occupancy or aggregation helper. Rebuilds
the flags from raw paths, cutpoints and scalar policy definitions, including
previously skipped candidates and unknown endpoints. Weekly paired sums are
checked before trusting downstream confidence intervals or selection.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
import csv
from datetime import datetime
import gzip
import json
import math
from pathlib import Path


def rows(path):
    with gzip.open(path,'rt') as f:return list(csv.DictReader(f))


def timekey(s):return s[:19].replace('T',' ')


def number(s):
    try:return float(s)
    except (TypeError,ValueError):return math.nan


def audit(root,name='analysis_v1'):
    root=Path(root);out=root/name;result=json.loads((out/'result.json').read_text());cfg=result['config']
    arms=cfg['arms'];features=cfg['features'];policies=arms+[f'{f}_{b}' for f in features for b in ['known','low','mid','high']]
    thresholds=json.loads((out/'thresholds.json').read_text());identity=json.loads((root/'identity.json').read_text())
    start=timekey(cfg['analysis_start']);cut=timekey(cfg['split']);checks=0;occupancy=0;eligibility_checks=0
    metrics=defaultdict(lambda:dict(n=0,sum=0.,gross_r=0.,cost_r=0.,wins=0,profit=0.,loss=0.,events=[]))
    weekly=defaultdict(lambda:defaultdict(lambda:defaultdict(float)))
    extremes={}
    def close(a,b):
        nonlocal checks
        assert math.isclose(float(a),float(b),rel_tol=2e-9,abs_tol=1e-6),(a,b)
        checks+=1
    def applicable(r,p,m):
        mask=int(r['members'])
        if p in arms:return bool(mask&(1<<arms.index(p)))
        feature,band=p.rsplit('_',1);v=number(r[feature]);q=thresholds[str(m)][feature]
        if not mask&1 or not math.isfinite(v):return False
        if band=='known':return True
        if q['lower'] is None:return False
        return v<=q['lower'] if band=='low' else v>q['upper'] if band=='high' else q['lower']<v<=q['upper']
    def effect(r,bit):
        eligible=bool(r['_eligible']&bit);taken=bool(r['_pair']&bit);filled=r['filled']=='True'
        status=r['status']
        if not eligible:return 0.,True,timekey(r['anchor_time'])
        if filled and not taken:return 0.,True,timekey(r['decision_time'])
        if taken:return number(r['net_r']),status=='closed',timekey(r['mature_time'])
        known=status=='expired' or status.startswith('cancel_') or status in ['risk_invalid','delayed_risk_invalid','next_bar_is_gap','closed']
        return 0.,known,timekey(r['decision_time'])
    for ni,symbol in enumerate(identity['symbols']):
        for m in cfg['timeframes']:
            raw=rows(root/'streams'/symbol/f'{m}m.csv.gz')
            if not raw:continue
            flags=rows(out/'decisions'/symbol/f'{m}m.csv.gz');assert len(raw)==len(flags)
            for r,f in zip(raw,flags):
                assert (r['trade_key'],r['cohort'])==(f['trade_key'],f['cohort'])
                r['_eligible']=int(f['eligible_bits']);r['_full']=int(f['full_bits']);r['_pair']=int(f['pair_bits'])
                for j,p in enumerate(policies):
                    assert bool(r['_eligible']&(1<<j))==applicable(r,p,m),(symbol,m,p,'eligibility');eligibility_checks+=1
                if r['filled']=='True' and r['status']=='closed':
                    side=int(r['side']);entry=number(r['entry_price']);stop=number(r['initial_stop']);risk=side*(entry-stop)
                    close(number(r['entry_i']),number(r['decision_i'])+1);close(risk,r['initial_risk'])
                    gross=side*(number(r['exit_price'])-entry)/risk;cost=.002*entry/risk
                    close(gross,r['gross_r']);close(cost,r['cost_r']);close(gross-cost,r['net_r'])
            ordered=sorted(raw,key=lambda r:(int(r['decision_i']),int(r['anchor_i']),r['trade_key']))
            for j,p in enumerate(policies):
                bit=1<<j
                for mode,cohort in [('_full','signal'),('_pair','signal'),('_pair','control')]:
                    flat=-1
                    for r in ordered:
                        if r['cohort']!=cohort:continue
                        allowed=bool(r['_eligible']&bit) and (mode=='_full' or r['pair_eligible']=='True')
                        take=allowed and r['filled']=='True' and int(r['decision_i'])>=flat
                        assert bool(r[mode]&bit)==take,(symbol,m,p,mode,r['trade_key']);occupancy+=1
                        if take:flat=math.inf if r['status']=='censored_boundary' else int(number(r['exit_i']))
            pairs=defaultdict(dict)
            for r in raw:
                if r['pair_eligible']=='True':pairs[r['trade_key']][r['cohort']]=r
                if r['cohort']!='signal' or r['status']!='closed' or not r['_full'] or timekey(r['entry_time'])<start:continue
                net=number(r['net_r']);gross=number(r['gross_r']);cost=number(r['cost_r'])
                period='later' if timekey(r['entry_time'])>=cut else 'early' if timekey(r['mature_time'])<cut else 'crossing'
                for j,p in enumerate(policies):
                    if not r['_full']&(1<<j):continue
                    for tf in [m,0]:
                        for period2 in ['all',period]:
                            b=metrics[tf,period2,p];b['n']+=1;b['sum']+=net;b['gross_r']+=gross;b['cost_r']+=cost
                            b['wins']+=net>0;b['profit']+=max(0.,net);b['loss']-=min(0.,net)
                            b['events'].append((r['exit_time'],r['trade_key'],net))
                    for direction in ['max','min']:
                        key=(m,p,direction);old=extremes.get(key)
                        if old is None or (net>number(old['net_r']) if direction=='max' else net<number(old['net_r'])):
                            extremes[key]={k:v for k,v in r.items() if not k.startswith('_')}|dict(audit_policy=p,audit_direction=direction)
            for key,pair in pairs.items():
                assert set(pair)=={'signal','control'}
                s,c=pair['signal'],pair['control'];first=min(timekey(s['anchor_time']),timekey(c['anchor_time']))
                if first<start:continue
                week=datetime.fromisoformat(s['anchor_time']).strftime('%G-W%V')
                assert datetime.fromisoformat(c['anchor_time']).strftime('%G-W%V')==week
                for j,p in enumerate(policies):
                    baseline=p.rsplit('_',1)[0]+'_known' if p not in arms else 'baseline';bj=policies.index(baseline)
                    if not (s['_eligible']|c['_eligible'])&((1<<j)|(1<<bj)):continue
                    sv,sk,sm=effect(s,1<<j);cv,ck,cm=effect(c,1<<j);bs,bsk,bsm=effect(s,1<<bj);bc,bck,bcm=effect(c,1<<bj)
                    if not (sk and ck and bsk and bck):continue
                    last=max(sm,cm,bsm,bcm);period='later' if first>=cut else 'early' if last<cut else 'crossing'
                    values=dict(n=1,target=sv,random=cv,baseline_target=bs,baseline_random=bc,delta=sv-bs,excess=sv-cv,incremental_excess=sv-cv-bs+bc)
                    for tf in [m,0]:
                        for period2 in ['all',period]:
                            block=weekly[tf,period2,p][week]
                            for field,value in values.items():block[field]+=value
        if ni==0 or (ni+1)%50==0 or ni+1==len(identity['symbols']):print(json.dumps(dict(verified=ni+1,total=len(identity['symbols']))),flush=True)
    for r in result['serial']:
        if r['side']!=0 or r['period'] not in ['all','early','later']:continue
        b=metrics[r['timeframe_min'],r['period'],r['policy']]
        for field in ['n','sum','gross_r','cost_r','wins','profit','loss']:close(b[field],r[field])
        if b['n']:
            close(b['sum']/b['n'],r['mean']);close(b['wins']/b['n'],r['win_rate'])
            if b['loss']:close(b['profit']/b['loss'],r['pf'])
            total=peak=dd=0.
            for _,_,v in sorted(b['events']):total+=v;peak=max(peak,total);dd=max(dd,peak-total)
            close(dd,r['realized_r_drawdown'])
    for r in json.loads((out/'weekly_blocks.json').read_text()):
        if r['side']!=0:continue
        b=weekly[r['timeframe_min'],r['period'],r['policy']]
        assert set(b)==set(r['blocks'])
        for week,fields in r['blocks'].items():
            for field,value in fields.items():close(b[week][field],value)
    for r in result['paired']:
        if r['side']!=0:continue
        b=weekly[r['timeframe_min'],r['period'],r['policy']]
        n=sum(x['n'] for x in b.values());close(n,r['n'])
        for field,value in r['sums'].items():
            actual=sum(x[field] for x in b.values());close(actual,value);close(actual/n,r['means'][field])
    # Nonpooled side and year rows must reconcile with their separately stored totals.
    sm={(r['timeframe_min'],r['side'],r['period'],r['policy']):r for r in result['serial']}
    for (tf,s,period,p),r in sm.items():
        if s!=0:continue
        for field in ['n','sum','gross_r','cost_r','wins']:
            close(r[field],sm[tf,1,period,p][field]+sm[tf,-1,period,p][field])
            if period=='all':close(r[field],sum(sm[tf,0,str(y),p][field] for y in [2023,2024,2025,2026]))
    output=dict(status='passed',scalar_checks=checks,eligibility_checks=eligibility_checks,serial_decisions=occupancy,
        limitations='Independent accounting/queue/pair audit; whole-frame signal parity and selected prefix checks are separately recorded by the path builder.')
    (out/'verification.json').write_text(json.dumps(output,indent=2)+'\n')
    # For a bounded independent price-path audit, one max/min per TF per rule.
    (out/'extreme_cases.json').write_text(json.dumps(list(extremes.values()),indent=2)+'\n')
    print(json.dumps(output),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--name',default='analysis_v1')
    a=p.parse_args();audit(a.root,a.name)
