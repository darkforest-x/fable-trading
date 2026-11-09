"""Change only the original V1.7 2R allocation in the frozen, shared-cash OKX confirmation account."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_cash_giveback as source

EXP = source.ROOT / 'exp-cloud-close-native-target-fraction-20261109-v1'


def opening_weights(fills):
    """Recover each opening child fraction from the venue units actually bought or sold at entry."""
    openings = fills.loc[fills.net_units.gt(0)].copy()
    openings['child'] = openings.col % 4
    by_child = openings.pivot(index='underlying_event_id', columns='child', values='net_units')
    if list(by_child.columns) != [0, 1, 2, 3] or by_child.isna().any().any() or (by_child <= 0).any().any():
        raise ValueError('Missing opening child or duplicate native opening')
    return by_child.div(by_child.sum(axis=1), axis=0)


def study():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed native target fraction research')
    data, market, quotes, parents, caps, ratios = source.account()
    frozen = json.loads((source.SOURCE/'summary.json').read_text())['arms']['aligned']
    result = {}
    for status,slip in [('median',0.), ('median_stress',.0005)]:
        existing = pd.read_csv(source.EXP/f'orders_{status}.csv.gz')
        baseline = opening_weights(existing)
        if not np.allclose(baseline, [1/3,1/3,1/6,1/6], rtol=0., atol=1e-8):
            raise ValueError('Frozen reference weights do not match the declared original strategy')
        result[status] = dict(metrics=frozen[status]['metrics'], funded=len(baseline), reused_control=True)
        for label,f in [('sell_one_third',1/3), ('sell_five_sixths',5/6)]:
            weights = np.array([f/2, f/2, (1-f)/2, (1-f)/2])
            key = label+('_stress' if slip else '')
            info = {}
            def check_units(fills):
                actual = opening_weights(fills)
                if (set(actual.index) != set(baseline.index) or
                        not np.allclose(actual, weights, rtol=0., atol=1e-8)):
                    raise ValueError('Actual entry universe or native allocation differs')
                total = fills.groupby('underlying_event_id').notional.sum()
                first = parents.entry_ms.reindex(total.index).idxmin()
                ref = pd.read_csv(source.SOURCE/'aligned'/f'funded_{status}.csv.gz', index_col='event_id')
                firstbatch = parents.entry_ms.reindex(total.index).eq(parents.at[first,'entry_ms'])
                if not np.allclose(total[firstbatch],ref.notional.reindex(total[firstbatch].index),rtol=1e-8,atol=1e-6):
                    raise ValueError('Initial portfolio notional risk changed')
                info.update(funded=len(actual), fills=len(fills), usd_fees=float(fills.fees.sum()),
                            max_weight_error=float(np.max(np.abs(actual.to_numpy()-weights))),
                            first_batch_count=int(firstbatch.sum()))
            outcome = source.queue.engine.simulate(data,market,quotes,ratios,slip,EXP,key,
                            entry_limits=caps, fill_sink=check_units, child_weights=weights)
            if 'metrics' not in outcome or not info:
                raise ValueError('Incomplete account or unresolved maintenance margin')
            result[key] = dict(**outcome, **info)
            print(key, outcome['metrics']['all'], flush=True)
    source.queue.repair.archive.prices.safe_json(EXP/'summary.json',dict(arms=result,
        funded_parent_pool_identical=True, omitted_costs=['historic funding','exit market impact']))


if __name__ == '__main__':
    study()
