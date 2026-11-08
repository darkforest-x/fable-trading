"""Attribute the change in funded parent returns to the frozen native exit decision."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_efficient_trend as study
from yoyo.evaluation.cloud_close_pair_uncertainty import draws_by_week


def main():
    output = study.EXP/'incremental_returns.json'
    if output.is_file():
        raise FileExistsError('Trend-efficiency return attribution already recorded')
    result = {}
    for venue in ['OKX','Binance']:
        baseline = study.repair.EXP/'thirds' if venue=='OKX' else study.thirds.EXP
        def read(folder):
            return pd.read_csv(folder/f'funded_{venue}.csv.gz',dtype={'symbol':str},float_precision='round_trip').set_index('event_id')
        parent,actual = read(baseline),read(study.EXP)
        if not parent.index.is_unique or set(parent.index)!=set(actual.index):
            raise ValueError('Mismatch in frozen funded parent identities')
        parent = parent.loc[actual.index]
        decisions = pd.read_csv(study.EXP/f'decisions_{venue}.csv.gz',usecols=['event_id','choice']).set_index('event_id')
        switched = decisions.loc[actual.index,'choice'].eq('full_runner')
        diff = actual.net_return-parent.net_return
        if not np.allclose(diff[~switched],0.,atol=1e-10):
            raise ValueError('A parent with unchanged native exits changed its equal-notional return')
        diff = diff.where(switched,0.).mask(diff.abs().lt(1e-10),0.)
        rows = []
        for ix,delta in diff[diff.ne(0)].sort_values(ascending=False).items():
            row = actual.loc[ix]
            rows.append(dict(event_id=ix,symbol=row.symbol,entry_utc=pd.Timestamp(row.entry_ms,unit='ms',tz='UTC').isoformat(),
                             difference_pp=float(100*delta),base_return_pct=float(100*parent.loc[ix,'net_return']),
                             full_runner_return_pct=float(100*row.net_return)))
        ci,weeks = draws_by_week(diff,actual.entry_ms)
        result[venue] = dict(funded=len(actual),switched=int(switched.sum()),improved=int(diff.gt(0).sum()),
                             worsened=int(diff.lt(0).sum()),same_after_switch=int((diff.eq(0)&switched).sum()),
                             mean_funded_difference_pp=float(100*diff.mean()),four_week_block_interval95_pp=ci,
                             funded_entry_weeks=weeks,changed_returns=rows)
    result['method'] = 'Equal-initial-notional realized return differences on identical funded parents; synchronized entry weeks sampled in 4-week circular blocks, 5000 draws. Exploratory, unadjusted for prior strategy search. Not an interval for compounded terminal wealth.'
    output.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print({k:{s:v[s] for s in ['switched','improved','worsened','four_week_block_interval95_pp']} for k,v in result.items() if isinstance(v,dict)})


if __name__=='__main__':
    main()
