"""Distinguish capacity saturation from weaker later signals on identical January-started books."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_liquid_queue as queue

EXP = queue.EXP.parent/'exp-cloud-close-capital-restarts-20261109-v1'


def main():
    hourly=pd.read_csv(queue.EXP/'balanced/hourly_fees.csv.gz',index_col='hour_end_utc',parse_dates=True)
    events=pd.read_csv(queue.history.EXP/'opportunities.csv.gz',index_col='event_id')
    rows,_,_,_=queue.repair.pool()
    clocks=rows.loc[rows.tp_r.eq(0),['event_id','entry_ms']].set_index('event_id').entry_ms
    arms={}
    scales={}
    starting={}
    for year in [2025,2026]:
        boundary=pd.Timestamp(f'{year}-01-01',tz='UTC')
        existing=float(hourly.loc[boundary,'nav'])
        if not np.isfinite(existing) or existing<=1000:
            raise ValueError('Missing carry-in wallet size')
        candidates=clocks.index[clocks>=boundary.value//1_000_000]
        for label,size in [('fresh',1000.),('grown',existing)]:
            name=f'{year}_{label}'
            arms[name]=candidates
            scales[name]=1000./size
            starting[name]=size
    if set(events.index)!=set(clocks.index):
        raise ValueError('Opportunity roster differs from the ranked native source')
    queue.main(experiment=EXP,policies=[(n,'balanced') for n in arms],eligible_by_arm=arms,
               cap_scales=scales,fill_cases=[('fees',0.)])
    out=json.loads((EXP/'summary.json').read_text())
    out['cash_reference_usdt']=starting
    for year in [2025,2026]:
        a=out['arms'][f'{year}_fresh']['cases']['fees']['metrics'][str(year)]
        b=out['arms'][f'{year}_grown']['cases']['fees']['metrics'][str(year)]
        path1=EXP/f'{year}_fresh'/'funded_fees.csv.gz'
        path2=EXP/f'{year}_grown'/'funded_fees.csv.gz'
        da=pd.read_csv(path1,index_col='event_id')
        db=pd.read_csv(path2,index_col='event_id')
        if set(da.index)!=set(db.index) or a['trades']!=b['trades']:
            raise ValueError('Comparative positive-size account rosters differ')
    queue.repair.archive.prices.safe_json(EXP/'summary.json',out)


if __name__=='__main__':
    main()
