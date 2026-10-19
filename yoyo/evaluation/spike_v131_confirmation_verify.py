"""Independent standard-library audit of confirmation money bridges and denominators.

No imports from the analysis builder. Verify raw prices, costs, risk quantities,
all summary populations, fixed feature bins, chronological joins and archived
current-trade parity. This checks accounting, not prospective profitability.
"""
import argparse
import csv
from datetime import datetime
import gzip
import json
import math
from pathlib import Path

COMPONENTS=['selection_effect_r','entry_price_effect_r','exit_price_effect_r','fee_change_r','risk_sizing_effect_r']
PATHS=Path('experiments/active/exp-spike-v131-staged-entry-search-20261019-v1/results_v2/leg_paths.csv.gz')


def load(path):
    with gzip.open(path,'rt') as f:return list(csv.DictReader(f))


def time(s):return datetime.fromisoformat(s)


def verify(root):
    root=Path(root);events=load(root/'events.csv.gz');diag=load(root/'diagnostics.csv.gz')
    result=json.loads((root/'result.json').read_text());cut=time(result['config']['split'].replace('Z','+00:00'))
    checks=0
    def close(a,b):
        nonlocal checks
        assert math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=1e-7),(a,b)
        checks+=1
    def flag(v):return v=='True'
    for r in events:
        s=int(r['side']);e=float(r['parent_entry']);stop=float(r['stop']);x=float(r['parent_exit']);risk=s*(e-stop)
        b=(s*(x-e)-.002*e)/risk;close(r['parent_r'],b)
        if flag(r['confirmed']):
            d=float(r['delayed_entry']);y=float(r['delayed_exit']);dr=s*(d-stop)
            final=(s*(y-d)-.002*d)/dr;units=(s*(y-d)-.002*d)/risk
            close(r['delayed_r'],final);close(r['delayed_anchor_r'],units)
            values=[0.,-s*(d-e)/risk,s*(y-x)/risk,-.002*(d-e)/risk,final-units]
            close(r['delayed_gross_r'],s*(y-d)/dr)
            close(r['delayed_fee_r'],.002*d/dr);close(r['risk_ratio'],dr/risk)
        else:
            close(r['delayed_r'],0);values=[-b,0.,0.,0.,0.]
        for k,v in zip(COMPONENTS,values):close(r[k],v)
        close(float(r['delayed_r'])-b,math.fsum(float(r[k]) for k in COMPONENTS))
        a,m=time(r['anchor_time']),time(r['mature_time'])
        r['period']='later' if a>=cut else 'early' if m<cut else 'crossing'
    keys={(r['cohort'],r['trade_key']) for r in events};assert len(keys)==len(events)
    originals={(r['cohort'],r['trade_key']):r for r in load(PATHS)}
    for r in diag:
        old=originals[r['cohort'],r['trade_key']]
        close(r['net_r'],old['net_r']);close(r['entry_price'],old['entry_price']);close(r['initial_stop'],old['initial_stop'])
        s=int(r['side']);e=float(r['entry_price']);stop=float(r['initial_stop']);a=float(r['anchor_entry']);ar=s*(a-stop)
        close(r['fee_fill_r'],.002*e/(s*(e-stop)))
        close(r['gross_r'],float(r['net_r'])+float(r['fee_fill_r']))
        close(r['chase_fill_r'],s*(e-a)/ar)
        close(r['chase_decision_r'],s*(float(r['confirmation_close'])-a)/ar)
        close(r['risk_ratio'],s*(e-stop)/ar)
        e,x=time(r['entry_time']),time(r['exit_time'])
        r['period']='later' if e>=cut else 'early' if x<cut else 'crossing'
    if not result['subset']:
        assert len(diag)==len(originals)==24678
        assert sum(r['cohort']=='signal' for r in diag)==21547
    def select(rows,spec,cohort):
        return [r for r in rows if r['cohort']==cohort and int(r['timeframe_min'])==spec['timeframe_min']
                and (spec['side']==0 or int(r['side'])==spec['side']) and (spec['period']=='all' or r['period']==spec['period'])]
    def stat(rows,column,expected):
        vals=[float(r[column]) for r in rows];assert len(vals)==expected['n']
        close(math.fsum(vals),expected['sum'])
        if vals:
            close(math.fsum(vals)/len(vals),expected['mean'])
            close(sum(v>0 for v in vals)/len(vals),expected['win_rate'])
    def paircheck(t,c,expected,columns):
        cd={r['trade_key']:r for r in c};keep=[r for r in t if r['trade_key'] in cd]
        assert len(keep)==expected['n']
        for k in columns:
            stat(keep,k,expected[k]['target']);stat([cd[r['trade_key']] for r in keep],k,expected[k]['random'])
            close(math.fsum(float(r[k])-float(cd[r['trade_key']][k]) for r in keep),expected[k]['excess']['sum'])
    for z in result['attribution']:
        t=select(events,z,'signal');c=select(events,z,'control')
        stat(t,'parent_r',z['parent']);stat(t,'delayed_r',z['delayed'])
        take=[r for r in t if flag(r['confirmed'])];skip=[r for r in t if not flag(r['confirmed'])]
        stat(take,'parent_r',z['selected_parent']);stat(take,'delayed_r',z['selected_delayed']);stat(skip,'parent_r',z['skipped_parent'])
        for k in COMPONENTS:stat(t,k,z['components'][k])
        stat([r for r in t if flag(r['parent_serial'])],'parent_r',z['archived_parent_serial'])
        stat([r for r in t if flag(r['delayed_serial'])],'delayed_r',z['archived_delayed_serial'])
        paircheck(t,c,z['matched'],['parent_r','delayed_r'])
    for z in result['current_trade_diagnostics']:
        t=select(diag,z,'signal');c=select(diag,z,'control')
        for k,n in [('net_r','net'),('gross_r','gross'),('fee_fill_r','fees')]:stat(t,k,z[n])
        paircheck(t,c,z['matched'],['net_r'])
    for z in result['feature_bins']:
        t=select(diag,z,'signal');c=select(diag,z,'control')
        left,right=map(float,z['bin'].strip('()[]').split(','))
        t=[r for r in t if r[z['feature']] and left<float(r[z['feature']])<=right]
        stat(t,'net_r',z['net']);stat(t,'gross_r',z['gross']);stat(t,'fee_fill_r',z['fees'])
        paircheck(t,c,z['matched'],['net_r'])
    out=dict(status='passed',scalar_checks=checks,event_rows=len(events),diagnostic_rows=len(diag),
        unique_events=True,original_closed_cohort_parity=True,all_summary_populations=True,
        accounting_bridge=True,chronological_pairs=True,feature_bins=True,
        limitations='Independent arithmetic/aggregation audit; does not validate counterfactual execution or establish an economic edge.')
    (root/'verification.json').write_text(json.dumps(out,indent=2));print(json.dumps(out))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);verify(p.parse_args().root)
