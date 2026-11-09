"""Apply the preassigned one-bar breakout delay to the entire observed historical cohort."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_breakout_fixed_delay as delayed
from yoyo.evaluation import cloud_close_breakout_liquid_ten as liquidity

branch = delayed.branch
EXP = branch.EXP.parent/'exp-cloud-close-breakout-delay-historical-20261109-v1'
CACHE = branch.ROOT/'data/research/cloud_close_breakout_delay_historical'


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed 360-name fixed-delay study')
    hist = branch.HISTORICAL_EXP
    cfg = json.loads((hist/'config.json').read_text())
    sample = pd.read_csv(hist/cfg['outputs']['candidate_audit_csv_gz'],dtype={'symbol':str},float_precision='round_trip')
    parents = sample[sample.arm.eq('breakout') & ~sample.is_control & sample.risk_fraction.gt(0)]
    ref = json.loads((hist/'summary.json').read_text())
    if len(parents)!=ref['candidate_events']['breakout']['executable_actual_paths']:
        raise ValueError('Full parent count differs from the native immediate account')
    first = pd.read_csv(delayed.EXP/'paths.csv.gz',dtype={'symbol':str},float_precision='round_trip')
    other = parents[~parents.symbol.isin(first.symbol.unique())]
    books = [first]
    CACHE.mkdir(parents=True,exist_ok=True)
    for n,(sym,group) in enumerate(other.groupby('symbol'),1):
        dest = CACHE/(sym+'.csv.gz')
        if not dest.exists():
            priced = delayed.delayed_symbol(sym,group,cfg,branch.STEP)
            tmp = dest.with_suffix('.tmp.gz')
            priced.to_csv(tmp,index=False)
            tmp.replace(dest)
        x = pd.read_csv(dest,dtype={'symbol':str},float_precision='round_trip')
        if set(x.parent_id)!=set(group.parent_id):
            raise ValueError(f'Unexpected cached parent coverage for {sym}')
        books.append(x)
        if n%50==0:
            print('completed added markets',n,flush=True)
    all_paths = pd.concat(books,ignore_index=True)
    if not all_paths.parent_id.is_unique or set(all_paths.parent_id)!=set(parents.parent_id):
        raise ValueError('Delayed study lost or duplicated parent opportunities')
    valid = all_paths[all_paths.status.eq('executed')].copy()
    if not valid.entry_ms.sub(valid.confirmation_ms).eq(branch.STEP).all():
        raise ValueError('Delayed executable path not exactly one bar later')
    ranks = pd.read_csv(liquidity.RANKS,dtype={'symbol':str}).rename(columns={'entry_ms':'confirmation_ms'})
    valid = valid.merge(ranks,on=['confirmation_ms','symbol'],how='left',validate='many_to_one')
    all_paths.to_csv(EXP/'delayed_paths.csv.gz',index=False)
    quotes = branch._quote_loader(cfg)
    result = {}
    for arm,events in [('full',valid),('top10',valid[valid.rank_status.eq('top10')])]:
        data = branch.wallet.prepare(events,9,quote_loader=quotes)
        for slip in [0.,.0005]:
            label = arm+('_stress' if slip else '')
            funded,eq = branch.wallet.account(data,slip=slip)
            if not np.isclose(1000+funded.cash_pnl.sum(),eq.nav.iat[-1],atol=.005):
                raise ValueError('Historical delay wallet cash does not reconcile')
            result[label] = dict(metrics={p:branch.wallet.summarize(funded,eq,*bounds)
                                    for p,bounds in branch.PERIODS.items()},opportunities=len(events),
                                    data_exits=funded.exit_reason.value_counts().to_dict(),
                                    unquoted_position_hours=data['gap_marks'])
            eq.to_csv(EXP/f'hourly_{label}.csv.gz',index_label='hour_end_utc')
            funded.to_csv(EXP/f'funded_{label}.csv.gz',index=False)
            print(label,result[label]['metrics']['all'],flush=True)
    branch._write_json(EXP/'summary.json',dict(cases=result,initial_parents=len(parents),exact_delayed_opens=len(valid),
        unavailable_later_opens=all_paths.loc[~all_paths.status.eq('executed'),'exit_reason'].value_counts().to_dict()))


if __name__=='__main__':
    main()
