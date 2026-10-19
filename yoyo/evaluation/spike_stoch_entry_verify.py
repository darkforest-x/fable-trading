"""Independent standard-library scalar, membership, queue and summary audit.

Does not import the builder, its queue/eligibility/statistics helpers or pandas.
Checks all raw rows, three separately replayed queues, exported pair identities
and sums, full trade statistics and conditional common-fill arithmetic. Price
paths are separately traced independently of the frozen execution engine by
the builder; native TradingView parity is not claimed.
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


def read(path):
    with gzip.open(path,'rt',newline='') as f:return list(csv.DictReader(f))


def yes(x):return str(x).lower()=='true'
def num(x):return float(x) if x not in ('',None) else math.nan
def sec(x):return int(datetime.fromisoformat(x.replace('Z','+00:00')).timestamp())


def equal(a,b):
    if b is None:assert a is None;return
    assert math.isclose(float(a),float(b),rel_tol=1e-9,abs_tol=1e-7),(a,b)


def mature(row,flag):
    if not yes(row['eligible']):return True,0.,sec(row['anchor_time'])
    if yes(row['filled']) and not yes(row[flag]):return True,0.,sec(row['decision_time'])
    if yes(row[flag]):return row['status']=='closed',num(row['net_r']),sec(row['mature_time'])
    status=row['status']
    known=status in ('closed','expired','risk_invalid','delayed_risk_invalid','next_bar_is_gap') or status.startswith('cancel_')
    return known,0.,sec(row['decision_time'])


def verify(root,name):
    root=Path(root);out=root/name;cfg=json.loads((root/'identity.json').read_text())['config']
    receipt=json.loads((root/'receipt.json').read_text());result=json.loads((out/'result.json').read_text())
    assert receipt['complete'] and not receipt['errors']
    counts=defaultdict(int);full=defaultdict(list);pair_sums=defaultdict(lambda:defaultdict(float));pair_n=defaultdict(int)
    opportunities=defaultdict(lambda:dict(original_opportunities=0,eligible=0,raw_filled=0,
        eligible_statuses=defaultdict(int),executed_statuses=defaultdict(int)))
    start=sec(cfg['analysis_start']);cut=sec(cfg['split'])
    for m in cfg['timeframes']:
        exported={}
        for group,scopes in [('standalone',('primary','state')),('spike',('primary',))]:
            for scope in scopes:
                path=out/f'pairs_{m}_{group}_{scope}.csv.gz'
                exported[group,scope]={r['trade_key']:r for r in read(path)} if path.exists() else {}
        for receipt_row in receipt['receipts']:
            rows=read(root/'streams'/receipt_row['symbol']/f'{m}m.csv.gz')
            grouped=defaultdict(list);bykey=defaultdict(list)
            for r in rows:
                grouped[r['policy'],r['cohort']].append(r);bykey[r['group'],r['trade_key']].append(r)
                if r['cohort']=='signal':
                    op=opportunities[m,r['policy']]
                    if sec(r['anchor_time'])>=start:
                        op['original_opportunities']+=1
                        if yes(r['eligible']):
                            op['eligible']+=1;op['raw_filled']+=yes(r['filled'])
                            op['eligible_statuses'][r['status']]+=1
                    if yes(r['serial_all']) and sec(r['entry_time'])>=start:op['executed_statuses'][r['status']]+=1
                if yes(r['filled']):
                    e,s,risk=map(num,(r['entry_price'],r['initial_stop'],r['initial_risk']))
                    assert risk>0;equal(risk,e-s);equal(num(r['cost_r']),.002*e/risk);counts['scalar']+=2
                    assert int(num(r['entry_i']))==int(r['decision_i'])+1
                    assert int(r['decision_i'])>=int(r['anchor_i'])
                    if r['policy'].startswith('pullback_'):
                        assert int(r['decision_i'])<=int(r['anchor_i'])+24
                        if 'decision_stoch' in r:
                            assert yes(r['decision_stoch'])
                            if r['policy']=='pullback_wvf':assert yes(r['decision_green'])
                    if r['status']=='closed':
                        x=num(r['exit_price']);equal(num(r['net_r']),(x-e-.002*e)/risk)
                        equal(num(r['mae_bp']),num(r['mae_r'])*risk/e*10000)
                        equal(num(r['mae_anchor_r']),num(r['mae_r'])*risk/num(r['anchor_risk']))
                        assert num(r['mae_r'])>=0 and num(r['mfe_upper_r'])+1e-9>=num(r['mfe_lower_r'])>=0
                        equal(num(r['held_bars']),num(r['exit_i'])-num(r['entry_i'])+1);counts['scalar']+=5
                if r['policy']=='stoch_wvf' and r['cohort']=='signal':assert yes(r['eligible'])==yes(r['anchor_green'])
                if r['group']=='standalone' and r['cohort']=='signal':assert yes(r['anchor_stoch'])
                if r['cohort']=='signal' and yes(r['serial_all']) and r['status']=='closed' and sec(r['entry_time'])>=start:
                    period='later' if sec(r['entry_time'])>=cut else 'early' if sec(r['mature_time'])<cut else 'crossing'
                    for period_key in ('all',period):
                        for tf in (0,m):full[tf,period_key,r['policy']].append(r)
            for (policy,cohort),subset in grouped.items():
                order=sorted(subset,key=lambda r:(int(r['decision_i']),int(r['anchor_i']),r['trade_key']))
                for flag,gate in [('serial_all',None),('serial_pair','pair_eligible'),('serial_state','state_pair')]:
                    if flag=='serial_state' and subset[0]['group']=='spike':continue
                    flat=-1
                    for r in order:
                        eligible=yes(r['eligible']) and (gate is None or yes(r[gate]))
                        take=eligible and yes(r['filled']) and int(r['decision_i'])>=flat
                        assert take==yes(r[flag]),(receipt_row['symbol'],m,policy,flag,r['trade_key'])
                        if take:flat=10**20 if r['status']=='censored_boundary' else int(num(r['exit_i']))
                        counts['queue']+=1
            for (group,key),rr in bykey.items():
                expected_policies=('stoch','stoch_wvf') if group=='standalone' else ('rebreak','pullback','pullback_stoch','pullback_wvf')
                assert len(rr) in (len(expected_policies),2*len(expected_policies))
                for scope in (('primary','state') if group=='standalone' else ('primary',)):
                    flag='serial_state' if scope=='state' else 'serial_pair';gate='state_pair' if scope=='state' else 'pair_eligible'
                    required=[r for r in rr if yes(r[gate])]
                    valid=len(required)==2*len(expected_policies)
                    values={};last=0;first=10**20
                    for r in required:
                        known,value,time=mature(r,flag);valid=valid and known
                        first=min(first,sec(r['anchor_time']));last=max(last,time)
                        values[r['cohort']+'_'+r['policy']]=value
                    valid=valid and first>=start
                    actual=exported[group,scope].pop(key,None)
                    assert valid==(actual is not None),(key,scope,valid)
                    counts['pair_membership']+=1
                    if not valid:continue
                    period='later' if first>=cut else 'early' if last<cut else 'crossing'
                    assert actual['period']==period;equal(first,actual['first']);equal(last,actual['last'])
                    for field,v in values.items():equal(v,actual[field]);counts['pair_value']+=1
                    for tf in (0,m):
                        for period_key in ('all',period):
                            tag=(tf,period_key,group,scope);pair_n[tag]+=1
                            for field,v in values.items():pair_sums[tag][field]+=v
        assert not any(exported.values())
    for r in result['serial']:
        rows=full[r['timeframe_min'],r['period'],r['policy']];v=[num(x['net_r']) for x in rows]
        assert r['n']==len(v);equal(sum(v),r['sum']);counts['aggregates']+=2
        if not v:continue
        equal(sum(v)/len(v),r['mean']);equal(sum(x>0 for x in v)/len(v),r['win_rate'])
        profit=sum(x for x in v if x>0);loss=-sum(x for x in v if x<0)
        equal(profit/loss if loss else None,r['pf'])
        for field in ('mae_r','mae_bp','mae_anchor_r','first_bar_mae_r','risk_fraction'):
            equal(sum(num(x[field]) for x in rows)/len(rows),r[field]['mean']);counts['aggregates']+=1
        eq=peak=dd=0.
        for row in sorted(rows,key=lambda x:(x['exit_time'],x['trade_key'])):
            eq+=num(row['net_r']);peak=max(peak,eq);dd=max(dd,peak-eq)
        equal(dd,r['realized_r_drawdown']);counts['aggregates']+=4
    for r in result['comparisons']:
        tag=(r['timeframe_min'],r['period'],r['group'],r['scope']);values=pair_sums[tag]
        assert r['n']==pair_n[tag]
        a=values['signal_'+r['policy']];b=values['control_'+r['policy']]
        if r['baseline']:a-=values['signal_'+r['baseline']];b-=values['control_'+r['baseline']]
        for label,value in [('target',a),('random',b),('excess',a-b)]:equal(value,r[label]['sum']);counts['aggregates']+=1
    for r in result['common_fill_diagnostics']:
        equal(r['variant_mean']-r['baseline_mean'],r['delta']['mean']);counts['aggregates']+=1
    for r in result.get('opportunities',[]):
        expected=opportunities[r['timeframe_min'],r['policy']]
        for field,value in expected.items():assert value==r[field],(field,value,r[field]);counts['aggregates']+=1
    family=[r for r in result['comparisons'] if r['timeframe_min'] and r['period']=='later']
    assert len(family)==result['holm_family_n']
    pvalue=lambda r:r['excess_uncertainty']['weekly']['p'] if r['excess_uncertainty']['weekly']['p'] is not None else 1.
    previous=0.
    for i,r in enumerate(sorted(family,key=pvalue)):
        previous=max(previous,min(1.,pvalue(r)*(len(family)-i)))
        equal(previous,r['later_family_holm_p']);counts['aggregates']+=1
    summary=dict(status='passed',checks=dict(counts),
        limitations='Independent all-row accounting, three queues, pair exports and aggregate sums. Raw paths independently traced in builder; no native TradingView parity claim.')
    (out/'verification.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--name',default='analysis_v1')
    a=p.parse_args();verify(a.root,a.name)
