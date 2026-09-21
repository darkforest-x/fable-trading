"""Audit the matched WIF case's final-hour ordering without changing its policy.

Uses the frozen hourly execution ledger and public last/mark 1m bars solely to
resolve exit chronology. No price features, entries or add quantities are changed.
Peak/stop counterfactuals are accounting exhibits, never realized trade outcomes.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

import pandas as pd

from yoyo.evaluation.wif_screenshot_case import EXP,sha,stamp,tier_for

START=1787374800000
END=START+3_600_000


def audit(payload,run_dir):
    price=sorted([[int(r[0]),*map(float,r[1:5])] for r in payload['price'] if START<=int(r[0])<END])
    mark=sorted([[int(r[0]),*map(float,r[1:5])] for r in payload['mark'] if START<=int(r[0])<END])
    expected=list(range(START,END,60_000))
    if [r[0] for r in price]!=expected or [r[0] for r in mark]!=expected:
        raise ValueError('Incomplete minute coverage')
    run_dir=Path(run_dir);events=pd.read_csv(run_dir/'events.csv');results=json.loads((run_dir/'results.json').read_text())
    records=[]
    for r in results:
        d=events[(events.profile==r['profile'])&(events.arm==r['arm'])]
        legs=d[d.kind.isin(['entry','add'])]
        q=float(legs.quantity.sum());cost=float((legs.quantity*legs.price).sum())
        fees=.001*cost;funding=float(d[d.kind=='funding'].payment.sum())
        assert abs(q-r['final_quantity'])<1e-8 and abs(cost-r['entry_notional'])<1e-7
        assert abs(fees-r['entry_fees'])<1e-8 and abs(funding-r['funding_paid'])<1e-8
        stop=float(d.iloc[-1].common_stop);mm,_=tier_for(q,payload['tiers'])
        threshold=(cost+fees+funding-100)/(q*(1-mm-.001))
        crossing=next((x for x in mark if x[3]<=threshold),None)
        stopping=next((x for x in price if x[3]<=stop),None)
        if stopping is None:raise ValueError('Hourly stop does not reconcile with minute bars')
        order='stop_first_no_maintenance_crossing' if crossing is None else (
            'maintenance_first' if crossing[0]<stopping[0] else
            'stop_first' if crossing[0]>stopping[0] else 'same_minute_unresolved')
        balance=lambda p,f:100+q*p-cost-fees-.001*q*p-f
        peakfund=float(d[(d.kind=='funding')&(d.time_utc<=stamp(START-3_600_000))].payment.sum())
        if r['status']=='complete':assert abs(balance(r['exit_price'],funding)-r['final_balance'])<1e-7
        records.append({'profile':r['profile'],'arm':r['arm'],'quantity':q,'mmr_current':mm,
            'maintenance_threshold_current':threshold,'stop':stop,'minute_order':order,
            'first_maintenance_minute':None if crossing is None else stamp(crossing[0]),
            'first_stop_minute':stamp(stopping[0]),
            'hypothetical_balance_at_stop_ignoring_forced_reduction':balance(stop,funding),
            'hindsight_balance_at_02296_ignoring_slippage':balance(.2296,peakfund),
            'stop_1tick_slippage_cost':q*.0001})
    result={'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'generated_at':pd.Timestamp.now(tz='UTC').isoformat(),'price_1m_count':len(price),'mark_1m_count':len(mark),
        'price_1m_sha256':sha(price),'mark_1m_sha256':sha(mark),'historical_risk_tiers_verified':False,
        'raw_candles_persisted_locally':False,'records':records}
    out=run_dir/'crash_audit.json'
    with out.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--run-dir',type=Path,default=EXP/'run_v1');args=parser.parse_args()
    from yoyo.evaluation.spike_v1_v8_be05 import _committed
    if not _committed((Path(__file__).resolve(),)):raise ValueError('Commit audit source before calculating')
    audit(json.load(sys.stdin),args.run_dir)
