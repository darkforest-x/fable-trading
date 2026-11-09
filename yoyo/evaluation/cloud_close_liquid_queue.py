"""Trade the same volume-capped opportunity pool under three simultaneous-entry priorities."""
import gc

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_liquid_priority_history as history

rank = history.local.candidates
repair = rank.repair
engine = rank.reference.engine
EXP = history.EXP.parent/'exp-cloud-close-liquid-queue-20261109-v1'


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed native liquidity queue account')
    prices = pd.read_csv(history.EXP/'opportunities.csv.gz',index_col='event_id',float_precision='round_trip')
    rows,quotes,src,_ = repair.pool()
    original = rows[rows.tp_r.eq(0)].set_index('event_id')
    if set(original.index)!=set(prices.index) or not prices.index.is_unique:
        raise ValueError('Prior-volume grid does not match actual eligible parents')
    pool = original[['entry_order','entry_ms','symbol']].copy()
    pool['volume'] = prices.prior_turnover_usdt
    pool = pool[pool.volume/600>=1e-6].copy()
    # Under one common currency budget, require estimated full parent notional above rounding dust.
    pool['liquid'] = pool.groupby('entry_ms').volume.rank(ascending=False,method='average')
    pool['quiet'] = pool.groupby('entry_ms').entry_order.rank(ascending=True,method='average')
    pool['balanced'] = (pool.quiet+pool.liquid)/2
    outcomes = {}
    regime = rank.reference.study.season.direction(src)
    baseline = None
    for name,score in [('original_atr','entry_order'),('liquid_first','liquid'),('balanced','balanced')]:
        ordered = pool.sort_values(['entry_ms',score] if name=='original_atr' else ['entry_ms',score,'entry_order'])
        keys = ['entry_ms',score] if name=='original_atr' else ['entry_ms',score,'entry_order']
        order = pd.Series(pd.MultiIndex.from_frame(ordered[keys]).factorize(sort=False)[0],index=ordered.index)
        plans = rows[rows.event_id.isin(pool.index)].copy()
        plans['entry_order'] = plans.event_id.map(order)
        roster,skips = repair.wallet.admit(plans[plans.tp_r.eq(0)],9)
        directory = EXP/name
        directory.mkdir(exist_ok=True)
        tape,settlement = rank.settle(plans[plans.event_id.isin(roster.event_id)],src,directory=directory)
        repair.parent.save_execution_tape(tape,directory)
        data,_ = repair.old.parent.assemble(tape,repair.old.changed_limits(tape,quotes),'OKX',quotes)
        market = engine.base.underlying(data,quotes)
        parents = market[-1].set_index('event_id')
        ratios = 1.5-.5*parents.side*parents.entry_ms.map(regime)
        cap = pool.volume.reindex(parents.index)/600
        if cap.isna().any() or set(parents.index)!=set(roster.event_id):
            raise ValueError('Cap or native roster missing')
        if baseline is None:
            baseline=set(parents.index)
        cases={}
        for key,slip in [('fees',0.),('stress',.0005)]:
            cases[key]=engine.simulate(data,market,quotes,ratios,slip,directory,key,entry_limits=cap)
            print(name,key,cases[key].get('metrics',{}).get('all'),flush=True)
            gc.collect()
        outcomes[name]=dict(cases=cases,signals=len(roster),overlap=len(set(parents.index)&baseline),
                   symbols=parents.symbol.nunique(),skips=skips,settlement=settlement,
                   missing_holding_hours=data['gap_marks'],gap_events=int(tape[tape.gap_flag].event_id.nunique()))
    repair.archive.prices.safe_json(EXP/'summary.json',dict(eligible=len(original),liquid_eligible=len(pool),arms=outcomes,
               limits='Entry capacity only. Positive prior-volume venue coverage; funding and actual exit depth remain unmodeled.'))


if __name__=='__main__':
    main()
