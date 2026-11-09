"""Replay the actual nine-slot cash wallet under a causal entry-notional cap."""
import gc

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_prior_minute_liquidity as liquidity

source = liquidity.audit.source
EXP = source.EXP.parent/'exp-cloud-close-liquid-entry-wallet-20261109-v1'


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed entry-liquidity constrained cash account')
    prior = pd.read_csv(liquidity.EXP/'prior_liquidity.csv.gz',index_col='event_id')
    data,market,quotes = source.account()
    parents = market[-1].set_index('event_id')
    ratios = source.study.weights(parents)['contrarian_half']
    if (set(prior.index)!=set(parents.index) or not prior.index.is_unique or
            not prior.entry_ms.reindex(parents.index).equals(parents.entry_ms)):
        raise ValueError('Causal liquidity score missing an original event or entry clock')
    previous = prior.median_usdt.where(prior.status.eq('observed'),0).fillna(0)
    if not np.isfinite(previous).all() or (previous<0).any():
        raise ValueError('Invalid prior turnover')
    actual = pd.read_csv(liquidity.audit.EXP/'entries.csv.gz',index_col='event_id').quote_usdt
    result = {}
    for label,fraction in [('unrestricted',None),('positive_volume_control',None),('ten_pct',.1),('one_pct',.01)]:
        cap = pd.Series(np.inf,index=parents.index) if fraction is None else previous.mul(fraction)
        if label=='positive_volume_control':
            cap = cap.where(previous.reindex(cap.index).mul(.1).ge(1e-6),0.)
        cap = cap.where(cap.ge(1e-6),0.)
        for slip in ([0.] if fraction is None else [0.,.0005]):
            case = label+('_stress' if slip else '')
            def verify(pf,inputs,funds,curve,weights,slippage):
                original = pd.read_csv(source.EXP/'funded_contrarian_half.csv.gz',index_col='event_id')
                hourly = pd.read_csv(source.EXP/'hourly_contrarian_half.csv.gz',index_col=0)
                for field in ['notional','cash_pnl']:
                    if not np.allclose(funds[field].reindex(original.index),original[field],atol=.00001,rtol=1e-9):
                        raise ValueError(f'Unrestricted original {field} differs')
                if not np.allclose(curve[hourly.columns],hourly,atol=.0001,rtol=1e-9):
                    raise ValueError('Unrestricted historical hour/stage mismatch')
            out = source.engine.simulate(data,market,quotes,ratios,slip,EXP,case,entry_limits=cap,
                                         snapshot=verify if label=='unrestricted' else None)
            if out.get('unresolved'):
                raise ValueError('Original roster unexpectedly failed its verified margin screen')
            if label!='unrestricted':
                f = pd.read_csv(EXP/f'funded_{case}.csv.gz',index_col='event_id')
                f['cap'] = cap.reindex(f.index)
                if set(f.index)!=set(cap[cap.gt(0)].index):
                    raise ValueError('Funded receipts do not match positive-cap parents')
                q = actual.reindex(f.index)
                share = f.notional/q
                out.update(at_cap=int(np.isclose(f.notional,f.cap,atol=1e-7,rtol=1e-8).sum()),
                           skipped=len(cap)-len(f),observed_after_entry=int(q.notna().sum()),
                           exceeding_next_minute=int(share.gt(1).sum()),exceeding_ten_pct=int(share.gt(.1).sum()))
            result[case] = out
            print(case,out['metrics']['all'],flush=True)
            gc.collect()
    source.repair.archive.prices.safe_json(EXP/'summary.json',dict(arms=result,
              limits='Causal entry-turnover budget. Unobserved exit depth, impact and funding. No replacement of zero-budget reserved signals.'))


if __name__=='__main__':
    main()
