"""Independent event-leg arithmetic audit; does not rerun the strategy logic."""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def close(a,b,label):
    if not np.isclose(float(a),float(b),rtol=1e-9,atol=1e-7):
        raise AssertionError(f'{label}: {a} != {b}')


def audit(run_dir):
    run_dir=Path(run_dir);paths=list((run_dir/'streams').glob('*/trades.csv.gz'))
    if not paths:
        raise ValueError('Expected completed cross-asset streams')
    checked=0;legs_checked=0;admissions=0;unknown=0
    for path in paths:
        try:results=pd.read_csv(path);events=pd.read_csv(path.with_name('events.csv.gz'))
        except pd.errors.EmptyDataError:continue
        rules=json.loads(path.with_name('completion.json').read_text())['quantity_rules']
        groups={key:g for key,g in events.groupby(['event_key','scope','arm'],sort=False)}
        for row in results.itertuples(index=False):
            key=(row.event_key,row.scope,row.arm);e=groups[key]
            legs=e[e.kind.isin(['entry','add'])];fund=e[e.kind.eq('funding')]
            if legs.empty:
                assert row.status=='rejected_initial'
                continue
            q=legs.quantity.sum();cost=(legs.quantity*legs.price).sum()
            funding=fund.payment.sum() if 'payment' in fund else 0.
            fees=.001*cost
            close(q,row.final_quantity,'sum quantity');close(fees,row.fees_paid,'entry fees')
            close(funding,row.funding_paid,'funding')
            close(legs.iloc[0].quantity*(row.entry_price-row.initial_stop),row.initial_actual_stop_risk,'fixed initial risk')
            close(len(legs)-1,row.adds_count,'adds count')
            for leg in legs.itertuples(index=False):
                close(leg.quantity/rules['quantity_step'],round(leg.quantity/rules['quantity_step']),'quantity grid')
                assert leg.quantity>=rules['min_quantity']-1e-8
                assert leg.quantity*leg.price>=rules['min_notional']-1e-8
                assert leg.quantity<=rules['max_order_quantity']+1e-8
                if leg.kind=='entry' or row.arm!='continuous_unprotected':
                    assert leg.protective_equity>=leg.protective_required-1e-6
                    admissions+=1
                legs_checked+=1
            if row.status=='complete' and not row.censored:
                expected=100+q*row.exit_price-cost-fees-.001*q*row.exit_price-funding
                close(expected,row.final_balance,'terminal balance')
            elif row.status=='ambiguous':
                assert pd.isna(row.final_balance)
                unknown+=1
            checked+=1
        for (event_key,scope),part in events.groupby(['event_key','scope']):
            extract=lambda arm:list(part[part.arm.eq(arm)&part.kind.eq('add')][['time_utc','price','quantity']].itertuples(index=False,name=None))[:2]
            assert extract('two')==extract('continuous')
    result={'checked_paths':checked,'checked_entry_add_legs':legs_checked,
        'checked_protective_admissions':admissions,'unresolved_paths_kept_null':unknown,'passed':True}
    target=run_dir/'independent_audit.json'
    with target.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run-dir',type=Path,required=True);a=p.parse_args();audit(a.run_dir)
