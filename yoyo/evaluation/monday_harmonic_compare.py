"""Compare the two authorized higher-timeframe arms using saved results only.

No execution, fitting or parameter search. Original and prior5m rows are read
unchanged. Holm adjustment covers the16 full-history strategy/interval tests;
those tests concern signal versus matched random, not the gate's increment.
Annual rows and retained-minus-rejected bootstrap contrasts are descriptive.
"""
from __future__ import annotations

import json
import numpy as np
import pandas as pd

from yoyo.evaluation.monday_harmonic import EXP
from yoyo.evaluation.monday_order_only import TRACKS
from yoyo.evaluation.range_framework import ROOT,dump
from yoyo.evaluation.monday_limit_be_scalein_compare import clean

STUDIES={tf:ROOT/f'experiments/active/exp-monday-harmonic-{tf}-confluence-20261031-v1' for tf in ('15m','1h')}


def holm(values):
    p=np.asarray([1. if v is None or pd.isna(v) else v for v in values],dtype=float)
    order=np.argsort(p,kind='stable');out=np.empty_like(p)
    out[order]=np.minimum(1.,np.maximum.accumulate(p[order]*(len(p)-np.arange(len(p)))))
    return out


def compare():
    old=pd.read_csv(EXP/'headline.csv');reference=old[old.arm.eq('baseline')].set_index(['strategy','period'])
    rows=[reference.reset_index().assign(timeframe='none'),old[old.arm.eq('harmonic')].assign(timeframe='5m')]
    receipts={};tests=[]
    for tf,path in STUDIES.items():
        table=pd.read_csv(path/'headline.csv');base=table[table.arm.eq('baseline')].set_index(['strategy','period'])
        for col in ('n','mean_net_bp','account_equity','account_max_drawdown_pct'):
            np.testing.assert_allclose(base.loc[reference.index,col],reference[col],equal_nan=True)
        gate=pd.read_csv(path/'admission.csv.gz')
        s=json.loads((path/'summary.json').read_text());m=json.loads((path/'allin_monthly.json').read_text())
        rows.append(table[table.arm.eq('harmonic')].assign(timeframe=tf))
        receipts[tf]=dict(events=len(gate),observable=int(gate.observable.sum()),admitted=int(gate.admitted.sum()),
            retention_pct=float(100*gate.admitted.mean()),monthly=m,
            differences={t:s['strategies'][t]['difference_all'] for t in TRACKS})
        for t in TRACKS:
            r=table[table.strategy.eq(t)&table.period.eq('all')&table.arm.eq('harmonic')].iloc[0]
            tests.append(dict(timeframe=tf,strategy=t,matched_week_p=r.matched_week_p))
    for r,p in zip(tests,holm([r['matched_week_p'] for r in tests])):r['matched_holm_p']=float(p)
    result=pd.concat(rows,ignore_index=True)
    result.to_csv(STUDIES['1h']/'timeframe-comparison.csv',index=False)
    dump(STUDIES['1h']/'timeframe-comparison.json',clean(dict(
        original_baselines_exact=True,authorized_arms=['15m','1h'],prior_5m_reused=True,
        full_history_matched_tests=tests,studies=receipts,
        interpretation='Holm over16 primary full-history matched-random tests. These are not tests of incremental gate efficacy. Annual and incremental bootstrap contrasts remain descriptive.',
        production_eligible=False,training_eligible=False)))


if __name__=='__main__':compare()
