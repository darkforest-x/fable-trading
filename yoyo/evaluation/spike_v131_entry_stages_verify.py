"""Independent standard-library accounting and occupancy audit of stage ledgers.

No signal generation, policy decision helper or pandas aggregation is imported.
Checks raw price/cost identities, stage-specific next-open clocks, occupied
intervals, all grouped serial metrics and stored matched-pair sums/means.
"""
import argparse
import csv
from collections import defaultdict
import gzip
import json
import math
from pathlib import Path


def audit(root):
    root=Path(root);result=json.loads((root/'result.json').read_text())
    cfg=result['config'];checks=0;serial=defaultdict(list);pair_values={};occupancy=0
    def close(a,b):
        nonlocal checks
        assert math.isclose(float(a),float(b),abs_tol=1e-7,rel_tol=1e-10),(a,b)
        checks+=1
    def timepart(s):return s[:19].replace('T',' ')
    cut=timepart(cfg['split']);start=timepart(cfg['analysis_start'])
    for minutes in cfg['timeframes']:
        streams=defaultdict(list)
        current_symbol=None
        def flush():
            nonlocal occupancy,checks
            for (cohort,policy,mode),rows in streams.items():
                flat=-1
                for r in sorted(rows,key=lambda x:(int(x['decision_i']),int(x['anchor_i']),x['trade_key'])):
                    expected=r['filled']=='True' and int(r['decision_i'])>=flat
                    assert (r[mode]=='True')==expected,(r['trade_key'],policy,mode)
                    checks+=1
                    if expected:
                        if flat>=0:occupancy+=1
                        flat=math.inf if r['status']=='censored_boundary' else int(float(r['exit_i']))
            streams.clear()
        with gzip.open(root/f'events_{minutes}m.csv.gz','rt') as f:
            for r in csv.DictReader(f):
                if current_symbol is not None and current_symbol!=r['symbol']:flush()
                current_symbol=r['symbol']
                filled=r['filled']=='True';taken=r['serial_all']=='True'
                if filled:
                    close(float(r['entry_i']),float(r['decision_i'])+1)
                    risk=int(r['side'])*(float(r['entry_price'])-float(r['initial_stop']))
                    close(risk,r['initial_risk'])
                    if r['status']=='closed':
                        gross=int(r['side'])*(float(r['exit_price'])-float(r['entry_price']))/risk
                        fee=.002*float(r['entry_price'])/risk
                        close(gross,r['gross_r']);close(fee,r['cost_r']);close(gross-fee,r['net_r'])
                for mode in ['serial_all','serial_pair']:
                    eligible=(r['cohort']=='signal') if mode=='serial_all' else r['pair_eligible']=='True'
                    if eligible:streams[r['cohort'],r['policy'],mode].append(r)
                if not (taken and r['cohort']=='signal' and r['status']=='closed' and timepart(r['entry_time'])>=start):continue
                period='later' if timepart(r['entry_time'])>=cut else ('early' if timepart(r['mature_time'])<cut else 'crossing')
                for side in [0,int(r['side'])]:
                    for scope in ['all',period]:serial[minutes,side,scope,r['policy']].append(float(r['net_r']))
        flush()
        for mode in ['independent','serial']:
            groups=defaultdict(lambda:defaultdict(list))
            with gzip.open(root/f'pairs_{minutes}m_{mode}.csv.gz','rt') as f:
                for r in csv.DictReader(f):
                    for side in [0,int(r['side'])]:
                        for scope in ['all',r['period']]:
                            for policy in cfg['policies']:
                                a=float(r['signal_'+policy]);b=float(r['control_'+policy])
                                g=groups[side,scope,policy]
                                g['target'].append(a);g['random'].append(b);g['excess'].append(a-b)
                                g['versus_rebreak'].append(a-float(r['signal_rebreak']))
            pair_values[minutes,'paired_serial' if mode=='serial' else mode]=groups
    def metrics(values,actual):
        close(len(values),actual['n']);close(sum(values),actual['sum'])
        if values:
            close(sum(values)/len(values),actual['mean']);close(sum(x>0 for x in values)/len(values),actual['win_rate'])
            neg=-sum(x for x in values if x<0)
            if neg:close(sum(x for x in values if x>0)/neg,actual['pf'])
    for r in result['serial']:
        metrics(serial[r['timeframe_min'],r['side'],r['period'],r['policy']],r)
    for r in result['paired']:
        g=pair_values[r['timeframe_min'],r['mode']][r['side'],r['period'],r['policy']]
        for field in ['target','random','excess','versus_rebreak']:metrics(g[field],r[field])
    out=dict(status='passed',independent_scalar_checks=checks,serial_nonoverlap_checks=occupancy)
    (root/'verification.json').write_text(json.dumps(out,indent=2))
    print(json.dumps(out))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);audit(p.parse_args().root)
