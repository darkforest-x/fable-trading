"""Compare the new ranking under causal median-minute caps and a coverage-matched mean cap."""
import gc
import json
import time
from concurrent.futures import ThreadPoolExecutor
from zipfile import BadZipFile

import numpy as np
import pandas as pd
import requests

from yoyo.evaluation import cloud_close_liquid_queue as queue
from yoyo.evaluation import cloud_close_prior_minute_liquidity as volume

audit = volume.audit
EXP = queue.EXP.parent/'exp-cloud-close-liquid-queue-medians-20261109-v1'
ARMS = ['original_atr','balanced']


def inputs(tapes,quotes,directory):
    file = directory/'source_rows.csv.gz'
    rows = pd.concat([f[f.tp_r.eq(0)][['event_id','symbol','entry_ms','entry_price']] for f in tapes.values()]).drop_duplicates()
    if not rows.event_id.is_unique:
        raise ValueError('Concurrent ranking arms differ on the same entry definition')
    if file.is_file():
        out=pd.read_csv(file,index_col='event_id',float_precision='round_trip')
        if set(out.index)!=set(rows.event_id):
            raise ValueError('Stored liquidity sources reference a different native roster')
        return out
    def collect(row):
        for attempt in range(4):
            try:
                prev=volume.prior(row,quotes)
                first=audit.observation(row,quotes)
                break
            except (requests.RequestException,BadZipFile):
                if attempt==3:
                    raise
                time.sleep(2**attempt)
        if (prev['entry_ms']!=row.entry_ms or first['entry_ms']!=row.entry_ms or
                prev['event_id']!=row.event_id or first['event_id']!=row.event_id):
            raise ValueError('A native source cache row refers to another event')
        return dict(event_id=row.event_id,entry_ms=row.entry_ms,symbol=row.symbol,prior_status=prev['status'],
                    prior_median=prev.get('median_usdt'),prior_mean=prev.get('mean_usdt'),
                    next_status=first['status'],next_turnover=first.get('quote_usdt'))
    with ThreadPoolExecutor(max_workers=4) as workers:
        records=[]
        for r in workers.map(collect,rows.itertuples()):
            records.append(r)
            if len(records)%500==0:
                print('native source rows',len(records),flush=True)
    out=pd.DataFrame(records).set_index('event_id')
    out.to_csv(file)
    return out


def main(*,experiment=EXP,source_dir=queue.EXP,arms=ARMS):
    if (experiment/'summary.json').exists():
        raise FileExistsError('Completed mean/median ranking account sensitivity')
    quotes,src=queue.repair.checked_quotes()
    grid=pd.read_csv(queue.history.EXP/'opportunities.csv.gz',index_col='event_id').prior_turnover_usdt
    tapes={s:pd.read_csv(source_dir/s/'selected_paths.csv.gz',dtype={'symbol':str},float_precision='round_trip') for s in arms}
    sources=inputs(tapes,quotes,experiment)
    regime=queue.rank.reference.study.season.direction(src)
    results={}
    for arm,tape in tapes.items():
        data,_=queue.repair.old.parent.assemble(tape,queue.repair.old.changed_limits(tape,quotes),'OKX',quotes)
        market=queue.engine.base.underlying(data,quotes)
        parents=market[-1].set_index('event_id')
        pts=sources.reindex(parents.index)
        amount_median=pts.prior_median.mul(.1).where(pts.prior_status.eq('observed'),0.).fillna(0)
        amount_mean=grid.reindex(parents.index)/600
        if (amount_mean.isna().any() or not parents.entry_ms.equals(pts.entry_ms) or
                set(parents.index)!=set(pd.read_csv(source_dir/arm/'funded_fees.csv.gz').event_id)):
            raise ValueError('Unchanged roster mismatch in the capacity comparison')
        ok=amount_median.ge(1e-6)
        ratios=1.5-.5*parents.side*parents.entry_ms.map(regime)
        folder=experiment/arm
        folder.mkdir(exist_ok=True)
        cases={}
        for label,caps in [('covered_mean',amount_mean.where(ok,0)),('median',amount_median.where(ok,0))]:
            for slip in ([0.,.0005] if label=='median' else [0.]):
                key=label+('_stress' if slip else '')
                cases[key]=queue.engine.simulate(data,market,quotes,ratios,slip,folder,key,entry_limits=caps)
                if 'metrics' in cases[key]:
                    funds=pd.read_csv(folder/f'funded_{key}.csv.gz',index_col='event_id')
                    post=pts.next_turnover.reindex(funds.index)
                    cases[key].update(ex_post_count=int(post.notna().sum()),
                                      over_next_minute=int(funds.notional.gt(post).sum()),positive_caps=int(ok.sum()))
                    print(arm,key,cases[key]['metrics']['all'],flush=True)
                gc.collect()
        results[arm]=cases
    queue.repair.archive.prices.safe_json(experiment/'summary.json',dict(arms=results,unique_sources=len(sources),
                 prior_status=sources.prior_status.value_counts().to_dict(),next_status=sources.next_status.value_counts().to_dict(),
                 caveat='Entry capacity scenario only; no funding or exit market depth modeling.'))


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--vix-priority',action='store_true')
    if parser.parse_args().vix_priority:
        main(experiment=EXP.parent/'exp-cloud-close-vix-priority-medians-20261109-v1',
             source_dir=EXP.parent/'exp-cloud-close-vix-priority-20261109-v1',arms=['aligned','lagged'])
    else:
        main()
