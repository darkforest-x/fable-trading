"""Measure the effect of removing only the entry liquidity ceiling from the exact same v1.7 account."""
import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_cash_giveback as cash

EXP=cash.ROOT/'exp-cloud-close-capacity-counterfactual-20261109-v1'


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed same-roster unlimited-liquidity sensitivity')
    data,market,quotes,parents,caps,ratios=cash.account()
    unlimited=caps.where(caps.eq(0),np.inf)
    outcomes={}
    for name,slip in [('fee',0.),('stress',.0005)]:
        result=cash.queue.engine.simulate(data,market,quotes,ratios,slip,EXP,name,entry_limits=unlimited)
        if 'metrics' not in result:
            raise ValueError('Native margin audit unresolved')
        funded=pd.read_csv(EXP/f'funded_{name}.csv.gz',index_col='event_id')
        if set(funded.index)!=set(caps[caps.gt(0)].index):
            raise ValueError('Hypothetical full fill changed the original positive source roster')
        excess=(funded.notional-caps.reindex(funded.index)).clip(lower=0)
        result['parents_exceeding_original_first_capacity']=int(excess.gt(1e-6).sum())
        result['sum_unavailable_entry_notional_usdt']=float(excess.sum())
        outcomes[name]=result
        print(name,result['metrics']['all'],flush=True)
    cash.queue.repair.archive.prices.safe_json(EXP/'summary.json',outcomes)


if __name__=='__main__':
    main()
