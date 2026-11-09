"""Price-constrain confirmed 12H orders with a limit known before the next native open."""
import gc

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_cash_giveback as cash

EXP=cash.ROOT/'exp-cloud-close-opening-price-limit-20261109-v1'


def prices(parents,quotes):
    """Read the final native H1 closing trade before the order and the next H1 open used to model execution."""
    panel=parents[['symbol','side','entry_ms','entry_price']].copy()
    panel['signal_close']=np.nan
    for sym,sample in panel.groupby('symbol'):
        stamps,px=quotes(sym)
        at=stamps.searchsorted(sample.entry_ms)
        if ((at<=0).any() or (at>=len(stamps)).any()
                or not np.array_equal(stamps[at],sample.entry_ms)
                or not np.array_equal(stamps[at-1],sample.entry_ms-cash.H)
                or not np.allclose(px[at,0],sample.entry_price,atol=1e-12,rtol=1e-9)):
            raise ValueError('Missing adjacent closing and opening trades')
        panel.loc[sample.index,'signal_close']=px[at-1,3]
    return panel


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed close-price limit study')
    data,market,quotes,parents,caps,ratios=cash.account()
    panel=prices(parents,quotes)
    panel['original_positive_capacity']=caps>0
    results={}
    for name,allow in [('at_close',0.),('room_5bp',.0005)]:
        limit=panel.signal_close*(1+panel.side*allow)
        for slip in (0.,.0005):
            key=name+('_stress' if slip else '')
            simulated=panel.entry_price*(1+panel.side*slip)
            accepted=(simulated-limit)*panel.side<=1e-12*limit
            revised=caps.where(accepted,0.)
            panel[key]=accepted
            case=cash.queue.engine.simulate(data,market,quotes,ratios,slip,EXP,key,entry_limits=revised)
            if 'metrics' in case:
                if case['metrics']['all']['trades']!=int(revised.gt(0).sum()):
                    raise ValueError('Funded parent count differs from price- and size-eligible orders')
                print(key,case['metrics']['all'],flush=True)
            results[key]=dict(case=case,price_rejected_with_positive_capacity=int(((caps>0)&~accepted).sum()))
            gc.collect()
    panel.to_csv(EXP/'opening_checks.csv.gz')
    cash.queue.repair.archive.prices.safe_json(EXP/'summary.json',dict(arms=results))


if __name__=='__main__':
    main()
